"""
Athena — FastAPI backend.

Usage:
    uvicorn api.main:app --reload --port 8000
"""

import json
import sys
import os
from datetime import datetime, date, timedelta
from typing import Optional

import csv
import io

from fastapi import FastAPI, HTTPException, Query, Body, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.database import get_connection, init_db
from scoring.scorer import get_score_breakdown

app = FastAPI(title="Athena API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    """Ensure database tables exist on startup."""
    init_db()


class FounderCreate(BaseModel):
    name: str
    title: str
    linkedin_url: Optional[str] = None
    email: Optional[str] = None

class FounderUpdate(BaseModel):
    name: Optional[str] = None
    title: Optional[str] = None
    linkedin_url: Optional[str] = None
    email: Optional[str] = None


# ── Helpers ──

def _parse_metadata(raw):
    """Parse a JSON metadata string, returning dict or empty dict."""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def _format_stage_display(stage, stage_source, stage_detected_date):
    """Format stage with age-based decay info."""
    if not stage:
        return stage, "current", None
    if not stage_detected_date:
        return stage, "current", None

    try:
        detected = datetime.strptime(stage_detected_date, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return stage, "current", None

    age_days = (date.today() - detected).days
    year = stage_detected_date[:4]

    if age_days < 365:
        source_suffix = f" \u00b7 {stage_source} {year}" if stage_source else ""
        return f"{stage}{source_suffix}", "current", None
    elif age_days < 730:
        return f"{stage} (may have changed)", "stale", None
    else:
        return f"Unknown (last known: {stage}, {year})", "outdated", stage


def _build_company_response(company_row, conn, include_breakdown=True):
    """Build the full company response dict from a DB row."""
    cid = company_row["id"]

    signals = conn.execute(
        "SELECT * FROM signals WHERE company_id = ? ORDER BY detected_at DESC",
        (cid,),
    ).fetchall()

    programs = conn.execute(
        "SELECT * FROM programs WHERE company_id = ? ORDER BY detected_at DESC",
        (cid,),
    ).fetchall()

    layers = {s["signal_layer"] for s in signals}
    is_cross_layer = "curated" in layers and "realtime" in layers

    stage_display, stage_confidence, _ = _format_stage_display(
        company_row["stage"],
        company_row["stage_source"],
        company_row["stage_detected_date"],
    )

    # Count distinct sources for this company
    source_count = conn.execute(
        "SELECT COUNT(DISTINCT source_name) FROM signals WHERE company_id = ?",
        (cid,),
    ).fetchone()[0]

    row_keys = company_row.keys()

    result = {
        "id": cid,
        "name": company_row["name"],
        "description": company_row["description"],
        "sector": company_row["sector"],
        "geography": company_row["geography"],
        "city": company_row["city"],
        "stage": company_row["stage"],
        "stage_display": stage_display,
        "stage_confidence": stage_confidence,
        "stage_source": company_row["stage_source"],
        "stage_detected_date": company_row["stage_detected_date"],
        "website": company_row["website"],
        "athena_score": company_row["athena_score"] if "athena_score" in row_keys else None,
        "heat_score": company_row["heat_score"],  # deprecated
        "company_status": company_row["company_status"] if "company_status" in row_keys else "unknown",
        "data_tier": company_row["data_tier"] if "data_tier" in row_keys else None,
        "freshness_score": company_row["freshness_score"] if "freshness_score" in row_keys else None,
        "source_count": source_count,
        "is_cross_layer": is_cross_layer,
        "first_detected": company_row["first_detected"],
        "last_updated": company_row["last_updated"],
        "signals": [
            {
                "source_type": s["source_type"],
                "source_name": s["source_name"],
                "signal_layer": s["signal_layer"],
                "source_url": s["source_url"],
                "detected_at": s["detected_at"],
                "metadata": _parse_metadata(s["metadata"]),
            }
            for s in signals
        ],
        "programs": [
            {
                "program_name": p["program_name"],
                "program_type": p["program_type"],
                "program_country": p["program_country"],
                "cohort": p["cohort"],
            }
            for p in programs
        ],
    }

    # Thesis detail from llm_evaluation JSON
    llm_eval = company_row["llm_evaluation"] if "llm_evaluation" in row_keys else None
    if llm_eval:
        try:
            eval_data = json.loads(llm_eval) if isinstance(llm_eval, str) else llm_eval
            result["thesis_reasoning"] = eval_data.get("thesis_reasoning") or eval_data.get("reasoning", "")
            result["ai_core"] = eval_data.get("is_ai_core")
            result["platform_risk"] = eval_data.get("platform_risk")
        except (json.JSONDecodeError, TypeError):
            pass

    # Thesis override
    result["thesis_override"] = company_row["thesis_override"] if "thesis_override" in row_keys else None
    result["thesis_override_reason"] = company_row["thesis_override_reason"] if "thesis_override_reason" in row_keys else None

    if result["thesis_override"] is not None:
        original_reasoning = result.get("thesis_reasoning", "")
        result["thesis_reasoning_original"] = original_reasoning
        result["thesis_reasoning"] = result["thesis_override_reason"]

    # Team detail
    result["team_reasoning"] = company_row["team_reasoning"] if "team_reasoning" in row_keys else None
    te = company_row["team_technical_excellence"] if "team_technical_excellence" in row_keys else None
    if te is not None:
        result["team_metrics"] = {
            "technical_excellence": company_row["team_technical_excellence"],
            "builder_track_record": company_row["team_builder_track_record"],
            "domain_expertise": company_row["team_domain_expertise"],
            "complementarity": company_row["team_complementarity"],
        }

    # Founders
    founders = conn.execute(
        "SELECT id, name, title, linkedin_url FROM founders WHERE company_id = ? ORDER BY id",
        (cid,),
    ).fetchall()
    result["founders"] = [
        {
            "id": f["id"],
            "name": f["name"],
            "title": f["title"],
            "linkedin_url": f["linkedin_url"],
        }
        for f in founders
    ]

    if include_breakdown:
        breakdown = get_score_breakdown(cid)
        result["score_breakdown"] = breakdown
        result["priority"] = breakdown.get("priority", "low")
        result["cohort"] = breakdown.get("cohort")

    return result


# ── Endpoints ──

@app.get("/api/health")
def health():
    """Health check endpoint."""
    return {"status": "ok", "name": "Athena API", "version": "1.0"}


@app.get("/api/signals")
def list_signals(
    program: Optional[str] = Query(None, description="Filter by program_name"),
    source: Optional[str] = Query(None, description="Filter by source_name"),
    sector: Optional[str] = Query(None, description="Filter by sector"),
    geography: Optional[str] = Query(None, description="Filter by geography"),
    min_score: Optional[float] = Query(None, ge=0, le=5, description="Minimum Athena Score"),
    stage: Optional[str] = Query(None, description="Filter by stage"),
    cohort_year: Optional[str] = Query(None, description="Filter by cohort year"),
    search: Optional[str] = Query(None, description="Search name/description"),
    hide_inactive: Optional[bool] = Query(None, description="Hide inactive/unknown companies"),
    hide_unverified: Optional[bool] = Query(None, description="Hide T3/unverified companies"),
    data_tier: Optional[int] = Query(None, ge=1, le=3, description="Filter by data tier"),
    sort: Optional[str] = Query("score", description="score, date, or name"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    conn = get_connection()

    # Build filtered company ID set
    where = []
    params = []

    if program:
        where.append(
            "c.id IN (SELECT company_id FROM programs WHERE program_name = ?)"
        )
        params.append(program)

    if source:
        where.append(
            "c.id IN (SELECT company_id FROM signals WHERE source_name = ?)"
        )
        params.append(source)

    if sector:
        where.append("c.sector = ?")
        params.append(sector)

    if geography:
        where.append("c.geography = ?")
        params.append(geography)

    if min_score:
        where.append("c.athena_score >= ?")
        params.append(min_score)

    if stage:
        where.append("c.stage = ?")
        params.append(stage)

    if cohort_year:
        where.append(
            "c.id IN (SELECT company_id FROM programs WHERE cohort LIKE ?)"
        )
        # Match "2024", "Stage 2" entries containing year, or exact year
        params.append(f"%{cohort_year}%")

    if search:
        where.append(
            "(LOWER(c.name) LIKE ? OR LOWER(c.description) LIKE ?)"
        )
        term = f"%{search.lower()}%"
        params.extend([term, term])

    if hide_inactive:
        where.append("c.company_status = 'active'")

    if hide_unverified:
        where.append("c.data_tier IN (1, 2)")

    if data_tier:
        where.append("c.data_tier = ?")
        params.append(data_tier)

    where_clause = (" WHERE " + " AND ".join(where)) if where else ""

    # Sort
    sort_map = {
        "score": "c.athena_score DESC, c.name ASC",
        "date": "c.last_updated DESC, c.athena_score DESC",
        "name": "c.name ASC",
    }
    order = sort_map.get(sort, sort_map["score"])

    # Total count for pagination
    count_sql = f"SELECT COUNT(*) FROM companies c{where_clause}"
    total = conn.execute(count_sql, params).fetchone()[0]

    # Fetch page
    query = f"SELECT * FROM companies c{where_clause} ORDER BY {order} LIMIT ? OFFSET ?"
    rows = conn.execute(query, params + [limit, offset]).fetchall()

    results = [_build_company_response(row, conn) for row in rows]

    total_unfiltered = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    conn.close()

    return {
        "total": total,
        "total_unfiltered": total_unfiltered,
        "limit": limit,
        "offset": offset,
        "results": results,
    }


@app.get("/api/export")
def export_csv(
    program: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    sector: Optional[str] = Query(None),
    geography: Optional[str] = Query(None),
    min_score: Optional[float] = Query(None, ge=0, le=5),
    stage: Optional[str] = Query(None),
    cohort_year: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    hide_inactive: Optional[bool] = Query(None),
    hide_unverified: Optional[bool] = Query(None),
    data_tier: Optional[int] = Query(None, ge=1, le=3),
):
    conn = get_connection()

    where = []
    params = []

    if program:
        where.append("c.id IN (SELECT company_id FROM programs WHERE program_name = ?)")
        params.append(program)
    if source:
        where.append("c.id IN (SELECT company_id FROM signals WHERE source_name = ?)")
        params.append(source)
    if sector:
        where.append("c.sector = ?")
        params.append(sector)
    if geography:
        where.append("c.geography = ?")
        params.append(geography)
    if min_score:
        where.append("c.athena_score >= ?")
        params.append(min_score)
    if stage:
        where.append("c.stage = ?")
        params.append(stage)
    if cohort_year:
        where.append("c.id IN (SELECT company_id FROM programs WHERE cohort LIKE ?)")
        params.append(f"%{cohort_year}%")
    if search:
        where.append("(LOWER(c.name) LIKE ? OR LOWER(c.description) LIKE ?)")
        term = f"%{search.lower()}%"
        params.extend([term, term])
    if hide_inactive:
        where.append("c.company_status = 'active'")
    if hide_unverified:
        where.append("c.data_tier IN (1, 2)")
    if data_tier:
        where.append("c.data_tier = ?")
        params.append(data_tier)

    where_clause = (" WHERE " + " AND ".join(where)) if where else ""

    rows = conn.execute(f"""
        SELECT c.name, c.website, c.athena_score, c.sector, c.geography, c.city,
               (SELECT s.source_name FROM signals s WHERE s.company_id = c.id
                ORDER BY s.detected_at ASC LIMIT 1) AS source,
               (SELECT p.cohort FROM programs p WHERE p.company_id = c.id
                ORDER BY p.detected_at DESC LIMIT 1) AS cohort_year,
               c.data_tier
        FROM companies c{where_clause}
        ORDER BY c.athena_score DESC, c.name ASC
        LIMIT 5000
    """, params).fetchall()

    conn.close()

    tier_labels = {1: "Curated", 2: "Standard", 3: "Discovery"}
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Company Name", "Website", "Athena Score", "Sector",
                     "Geography", "City", "Source", "Cohort Year", "Tier"])
    for r in rows:
        writer.writerow([
            r["name"],
            r["website"] or "",
            f"{r['athena_score']:.1f}" if r["athena_score"] else "",
            r["sector"] or "",
            r["geography"] or "",
            r["city"] or "",
            r["source"] or "",
            r["cohort_year"] or "",
            tier_labels.get(r["data_tier"], ""),
        ])

    today = date.today().isoformat()
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="athena-export-{today}.csv"'},
    )


@app.get("/api/stats")
def stats():
    conn = get_connection()

    total_companies = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    total_signals = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]

    # New this week
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    new_this_week = conn.execute(
        "SELECT COUNT(*) FROM companies WHERE first_detected >= ?", (week_ago,)
    ).fetchone()[0]

    # Cross-layer matches
    cross_layer = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT company_id FROM signals
            GROUP BY company_id
            HAVING COUNT(DISTINCT signal_layer) > 1
        )
    """).fetchone()[0]

    # Source count
    source_count = conn.execute(
        "SELECT COUNT(DISTINCT source_name) FROM signals"
    ).fetchone()[0]

    # Breakdowns
    by_source = {
        r[0]: r[1]
        for r in conn.execute("""
            SELECT source_name, COUNT(DISTINCT company_id)
            FROM signals GROUP BY source_name ORDER BY 2 DESC
        """).fetchall()
    }

    by_sector = {
        r[0]: r[1]
        for r in conn.execute("""
            SELECT COALESCE(sector, 'Unknown'), COUNT(*)
            FROM companies GROUP BY 1 ORDER BY 2 DESC
        """).fetchall()
    }

    by_geography = {
        r[0]: r[1]
        for r in conn.execute("""
            SELECT COALESCE(geography, 'Unknown'), COUNT(*)
            FROM companies GROUP BY 1 ORDER BY 2 DESC
        """).fetchall()
    }

    by_priority = {}
    for label, low, high in [("high", 4.0, 5.1), ("investigate", 3.5, 4.0),
                              ("radar", 3.0, 3.5), ("low", 0, 3.0)]:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM companies WHERE athena_score >= ? AND athena_score < ?",
            (low, high),
        ).fetchone()[0]
        by_priority[label] = cnt

    by_stage = {
        r[0]: r[1]
        for r in conn.execute("""
            SELECT COALESCE(stage, 'Unknown'), COUNT(*)
            FROM companies GROUP BY 1 ORDER BY 2 DESC
        """).fetchall()
    }

    # Active companies (excluding unverified/tier3)
    active_verified = conn.execute(
        "SELECT COUNT(*) FROM companies WHERE data_tier IN (1, 2) AND company_status = 'active'"
    ).fetchone()[0]

    # By tier
    by_tier = {
        str(r[0]): r[1]
        for r in conn.execute("""
            SELECT COALESCE(data_tier, 0), COUNT(*)
            FROM companies GROUP BY 1 ORDER BY 1
        """).fetchall()
    }

    conn.close()

    return {
        "total_companies": total_companies,
        "total_signals": total_signals,
        "new_this_week": new_this_week,
        "cross_layer_matches": cross_layer,
        "source_count": source_count,
        "active_verified": active_verified,
        "by_source": by_source,
        "by_sector": by_sector,
        "by_geography": by_geography,
        "by_priority": by_priority,
        "by_stage": by_stage,
        "by_tier": by_tier,
    }


@app.get("/api/company/{company_id}")
def get_company(company_id: int):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM companies WHERE id = ?", (company_id,)
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Company not found")
    result = _build_company_response(row, conn)
    conn.close()
    return result


@app.post("/api/companies/{company_id}/founders")
def create_founder(company_id: int, founder: FounderCreate):
    conn = get_connection()
    row = conn.execute("SELECT id FROM companies WHERE id = ?", (company_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Company not found")
    cursor = conn.execute(
        "INSERT INTO founders (company_id, name, title, linkedin_url, email, source) VALUES (?, ?, ?, ?, ?, ?)",
        (company_id, founder.name.strip(), founder.title.strip() if founder.title else None,
         founder.linkedin_url.strip() if founder.linkedin_url else None,
         founder.email.strip() if founder.email else None, "manual"),
    )
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return {"id": new_id, "name": founder.name.strip(), "title": founder.title, "linkedin_url": founder.linkedin_url}


@app.put("/api/founders/{founder_id}")
def update_founder(founder_id: int, founder: FounderUpdate):
    conn = get_connection()
    existing = conn.execute("SELECT * FROM founders WHERE id = ?", (founder_id,)).fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=404, detail="Founder not found")
    updates = {}
    if founder.name is not None:
        updates["name"] = founder.name.strip()
    if founder.title is not None:
        updates["title"] = founder.title.strip()
    if founder.linkedin_url is not None:
        updates["linkedin_url"] = founder.linkedin_url.strip() if founder.linkedin_url else None
    if founder.email is not None:
        updates["email"] = founder.email.strip() if founder.email else None
    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(f"UPDATE founders SET {set_clause} WHERE id = ?", list(updates.values()) + [founder_id])
        conn.commit()
    row = conn.execute("SELECT id, name, title, linkedin_url FROM founders WHERE id = ?", (founder_id,)).fetchone()
    conn.close()
    return {"id": row["id"], "name": row["name"], "title": row["title"], "linkedin_url": row["linkedin_url"]}


@app.delete("/api/founders/{founder_id}")
def delete_founder(founder_id: int):
    conn = get_connection()
    existing = conn.execute("SELECT id FROM founders WHERE id = ?", (founder_id,)).fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=404, detail="Founder not found")
    conn.execute("DELETE FROM founders WHERE id = ?", (founder_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/top20")
def top20():
    """Return the top 20 highest-scoring companies with 2025/2026 cohort."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT DISTINCT c.* FROM companies c
        JOIN programs p ON p.company_id = c.id
        WHERE c.data_tier IN (1, 2)
          AND (
            p.cohort GLOB '*2025*' OR p.cohort GLOB '*2026*'
            OR p.cohort LIKE '%W25' OR p.cohort LIKE '%S25'
            OR p.cohort LIKE '%F25' OR p.cohort LIKE '%X25'
            OR p.cohort LIKE '%W26' OR p.cohort LIKE '%S26'
            OR p.cohort LIKE '%F26' OR p.cohort LIKE '%X26'
          )
        ORDER BY c.athena_score DESC, c.name ASC
        LIMIT 20
    """).fetchall()
    results = [_build_company_response(row, conn) for row in rows]
    conn.close()
    return {"results": results}


@app.get("/api/filters")
def filters():
    conn = get_connection()

    sources = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT source_name FROM signals ORDER BY source_name"
        ).fetchall()
    ]

    sectors = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT sector FROM companies WHERE sector IS NOT NULL ORDER BY sector"
        ).fetchall()
    ]

    geographies = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT geography FROM companies WHERE geography IS NOT NULL ORDER BY geography"
        ).fetchall()
    ]

    stages = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT stage FROM companies WHERE stage IS NOT NULL ORDER BY stage"
        ).fetchall()
    ]

    programs = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT program_name FROM programs ORDER BY program_name"
        ).fetchall()
    ]

    # Cohort years (extract 4-digit years from cohort field)
    cohort_years = [
        r[0]
        for r in conn.execute("""
            SELECT DISTINCT cohort FROM programs
            WHERE cohort GLOB '[0-9][0-9][0-9][0-9]'
            ORDER BY cohort DESC
        """).fetchall()
    ]

    conn.close()

    return {
        "sources": sources,
        "sectors": sectors,
        "geographies": geographies,
        "stages": stages,
        "programs": programs,
        "cohort_years": cohort_years,
    }


# ── Serve React frontend build ──

FRONTEND_BUILD = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "frontend", "build",
)

if os.path.isdir(FRONTEND_BUILD):
    # Serve static assets (JS, CSS, images)
    app.mount("/static", StaticFiles(directory=os.path.join(FRONTEND_BUILD, "static")), name="static")

    @app.get("/{full_path:path}")
    async def serve_frontend(request: Request, full_path: str):
        """Serve React app for any non-API route (SPA client-side routing)."""
        # Try to serve the exact file first (favicon.ico, manifest.json, etc.)
        file_path = os.path.join(FRONTEND_BUILD, full_path)
        if full_path and os.path.isfile(file_path):
            return FileResponse(file_path)
        # Fall back to index.html for SPA routing
        return FileResponse(os.path.join(FRONTEND_BUILD, "index.html"))
