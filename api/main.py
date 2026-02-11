"""
Athena — FastAPI backend.

Usage:
    uvicorn api.main:app --reload --port 8000
"""

import json
import sys
import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

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


# ── Helpers ──

def _parse_metadata(raw):
    """Parse a JSON metadata string, returning dict or empty dict."""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


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

    result = {
        "id": cid,
        "name": company_row["name"],
        "description": company_row["description"],
        "sector": company_row["sector"],
        "geography": company_row["geography"],
        "city": company_row["city"],
        "stage": company_row["stage"],
        "website": company_row["website"],
        "heat_score": company_row["heat_score"],
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

    if include_breakdown:
        breakdown = get_score_breakdown(cid)
        result["score_breakdown"] = breakdown
        result["rising"] = breakdown.get("rising", False)

    return result


# ── Endpoints ──

@app.get("/")
def root():
    """Health check — must respond instantly for Render deploy."""
    return {"status": "ok", "name": "Athena API", "version": "1.0"}


@app.get("/api/signals")
def list_signals(
    program: Optional[str] = Query(None, description="Filter by program_name"),
    source: Optional[str] = Query(None, description="Filter by source_name"),
    sector: Optional[str] = Query(None, description="Filter by sector"),
    geography: Optional[str] = Query(None, description="Filter by geography"),
    min_score: Optional[int] = Query(None, ge=1, le=10, description="Minimum heat score"),
    stage: Optional[str] = Query(None, description="Filter by stage"),
    cohort_year: Optional[str] = Query(None, description="Filter by cohort year"),
    search: Optional[str] = Query(None, description="Search name/description"),
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
        where.append("c.heat_score >= ?")
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

    where_clause = (" WHERE " + " AND ".join(where)) if where else ""

    # Sort
    sort_map = {
        "score": "c.heat_score DESC, c.name ASC",
        "date": "c.last_updated DESC, c.heat_score DESC",
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
    conn.close()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "results": results,
    }


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

    by_score = {
        str(r[0]): r[1]
        for r in conn.execute("""
            SELECT heat_score, COUNT(*)
            FROM companies GROUP BY heat_score ORDER BY heat_score
        """).fetchall()
    }

    by_stage = {
        r[0]: r[1]
        for r in conn.execute("""
            SELECT COALESCE(stage, 'Unknown'), COUNT(*)
            FROM companies GROUP BY 1 ORDER BY 2 DESC
        """).fetchall()
    }

    conn.close()

    return {
        "total_companies": total_companies,
        "total_signals": total_signals,
        "new_this_week": new_this_week,
        "cross_layer_matches": cross_layer,
        "source_count": source_count,
        "by_source": by_source,
        "by_sector": by_sector,
        "by_geography": by_geography,
        "by_score": by_score,
        "by_stage": by_stage,
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
