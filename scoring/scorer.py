"""
Athena Score — Pre-screening score for deal intelligence (0–5.0).

Tells scouts "how worth investigating is this company based on
what we know from public data."  NOT an investment recommendation.

Five components, weighted:
  1. Thesis Fit      (25%)  — AI/deep-tech, European, early-stage
  2. Team Signal     (25%)  — founder technical strength
  3. Program Pedigree (20%) — accelerator / program quality
  4. Traction Signal  (20%) — signal diversity & buzz
  5. Data Completeness (10%) — field coverage

When team data is unavailable the 25 % is redistributed
proportionally across the other four components.

Decision thresholds:
  >= 4.0  High Priority
  3.5–3.9 Worth Investigating
  3.0–3.4 On Radar
  <  3.0  Low Priority

Usage:
    python -m scoring.scorer
"""

import json
import re
import sys
import os
from datetime import datetime, date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.database import get_connection, update_company


# ── Weights ──────────────────────────────────────────────────

WEIGHTS_WITH_TEAM = {
    "thesis": 0.25, "team": 0.25, "program": 0.20,
    "traction": 0.20, "data": 0.10,
}
WEIGHTS_WITHOUT_TEAM = {
    "thesis": 0.33, "program": 0.27, "traction": 0.27, "data": 0.13,
}

# ── Thesis-fit keyword lists ─────────────────────────────────

AI_DEEP_TECH_PATTERNS = [
    r"\bAI\b", r"\bML\b", r"machine\s+learning", r"deep\s+learning",
    r"neural\s+net", r"\bLLM\b", r"\bGPT\b", r"computer\s+vision",
    r"\bNLP\b", r"natural\s+language", r"reinforcement\s+learning",
    r"generative\s+AI", r"foundation\s+model",
    r"artificial\s+intelligence",
    # deep tech beyond AI
    r"\brobotics?\b", r"\bquantum\b", r"\bbiotech\b",
    r"\bgenomic", r"\bsynthetic\s+biology",
    r"\bnano(?:tech|material)", r"\bphotonics?\b",
    r"\bsemiconductor", r"\blidar\b", r"\bradar\b",
    r"\bedge\s+computing", r"\bcomputer\s+architecture",
]
AI_DEEP_TECH_RE = re.compile("|".join(AI_DEEP_TECH_PATTERNS), re.IGNORECASE)

# Sectors that count as AI / deep-tech by default
AI_SECTORS = {"AI / ML"}

# Wrapper / thin-AI detector: generic claims with no depth
WRAPPER_SIGNALS = [
    r"AI[\s-]*powered\b",
    r"leverag(?:es?|ing)\s+AI",
    r"uses?\s+AI\s+to\b",
    r"built\s+(?:on|with)\s+AI",
]
WRAPPER_RE = re.compile("|".join(WRAPPER_SIGNALS), re.IGNORECASE)

DEPTH_SIGNALS = [
    r"\bmodel\b", r"\btrain(?:ed|ing)\b", r"\binference\b",
    r"\btransformer\b", r"\bdiffusion\b", r"\bfine[\s-]?tun",
    r"\bembedding", r"\bvector\b", r"\bneural\b",
    r"\bproprietary\s+(?:model|algorithm|data)",
    r"\bfrom\s+scratch\b", r"\bpre[\s-]?train",
    r"\bresearch\b", r"\bnovel\b", r"\bpatent",
    r"\bpeer[\s-]reviewed", r"\bpublished\b",
    r"\bPh\.?D", r"\bspinout\b", r"\bspin[\s-]off",
]
DEPTH_RE = re.compile("|".join(DEPTH_SIGNALS), re.IGNORECASE)

EARLY_STAGES = {"Pre-seed", "Seed", "Grant"}
LATE_STAGES = {"Series A", "Series B", "Series C", "Series D", "Growth", "IPO"}

# ── Program tiers (for pedigree) ─────────────────────────────

TIER_5_PROGRAMS = {
    "Y Combinator", "Entrepreneur First", "Techstars",
}
TIER_5_UNIVERSITIES = {
    "EPFL", "ETH AI Center",
    "University of Oxford", "Cambridge Enterprise", "Imperial Enterprise Lab",
}
TIER_4_PROGRAMS = {
    "Antler", "Seedcamp", "Venture Kick",
}
TIER_3_PROGRAMS = {
    "500 Global", "EWOR",
    # Swedish accelerators
    "Chalmers Ventures", "Sting", "LEAD", "GU Ventures",
    "LU Innovation", "KTH Innovation",
    # Other university programs
    "DTU Science Park", "University of Zurich",
}

PRESS_SOURCES = {"Sifted", "Tech.eu", "TechCrunch", "EU-Startups"}

# ── Technical-title detection for team scoring ───────────────

TECH_TITLE_RE = re.compile(
    r"\bph\.?d\b|\bdr\.?\s|\bdoctor\b"
    r"|\bcto\b|chief\s+technology"
    r"|\bprincipal\s+engineer\b|\bstaff\s+engineer\b",
    re.IGNORECASE,
)


# ═══════════════════════════════════════════════════════════════
# Component scorers — each returns (score 1–5, label)
# ═══════════════════════════════════════════════════════════════

def _score_thesis(company):
    """Thesis Fit: binary 5 / 3 / 1 / 0.  Capped at 1.0 for late-stage."""
    desc = (company.get("description") or "").strip()
    sector = company.get("sector") or ""
    geography = company.get("geography")
    stage = company.get("stage")

    # Late-stage gate: Series A/B+ are past Ellipsis's target stage
    if stage in LATE_STAGES:
        return 1.0, f"Late stage ({stage}) — past target"

    combined = f"{desc} {sector}"

    # Is AI / deep-tech?
    is_ai = sector in AI_SECTORS or bool(AI_DEEP_TECH_RE.search(combined))

    # Wrapper check (only if it claims AI)
    is_wrapper = False
    if is_ai and desc:
        has_wrapper_claim = bool(WRAPPER_RE.search(desc))
        has_depth = bool(DEPTH_RE.search(desc))
        if has_wrapper_claim and not has_depth:
            is_wrapper = True

    if is_wrapper:
        return 0.0, "Wrapper — no technical depth"
    if not is_ai:
        return 1.0, "Not AI-first / deep-tech"
    if not geography:
        return 3.0, "AI/deep-tech, geography unknown"

    is_early = stage in EARLY_STAGES
    if not is_early:
        return 4.0, f"AI/deep-tech, {geography}, stage: {stage or 'unknown'}"

    return 5.0, f"AI/deep-tech, {geography}, {stage}"


def _score_team(founders):
    """Team Signal 1–5 (or None when no data → weight redistributed)."""
    if not founders:
        return None, "Unknown — needs research"

    n = len(founders)

    # Count technically-credentialed founders
    tech_count = 0
    for f in founders:
        text = f"{f.get('name', '')} {f.get('title', '')}".strip()
        if TECH_TITLE_RE.search(text):
            tech_count += 1

    # Build label
    parts = [f"{n} founder{'s' if n != 1 else ''}"]
    if tech_count:
        parts.append(f"{tech_count} technical")
    linkedin_count = sum(1 for f in founders if f.get("linkedin_url"))
    if linkedin_count:
        parts.append(f"{linkedin_count} LinkedIn")
    label = ", ".join(parts)

    if n >= 2 and tech_count >= 2:
        return 5, label
    if tech_count >= 1:
        return 4, label
    if n >= 2:
        return 3, label
    # Single founder, no technical signals
    return 2, label


def _score_program(programs, n_distinct_sources):
    """Program Pedigree 1–5."""
    if not programs:
        if n_distinct_sources >= 2:
            return 2, f"No program, {n_distinct_sources} sources"
        return 1, "Single source, minimal data"

    program_names = {p["program_name"] for p in programs}

    # Find best tier
    best = 1
    best_name = None
    for name in program_names:
        if name in TIER_5_PROGRAMS or name in TIER_5_UNIVERSITIES:
            if best < 5:
                best, best_name = 5, name
        elif name in TIER_4_PROGRAMS:
            if best < 4:
                best, best_name = 4, name
        elif name in TIER_3_PROGRAMS:
            if best < 3:
                best, best_name = 3, name
        else:
            if best < 2:
                best, best_name = 2, name

    # Venture Kick Stage 2/3 → tier 5 (detect via funding_amount since
    # cohort now stores the year, not the stage)
    for p in programs:
        if p["program_name"] == "Venture Kick":
            funding = (p.get("funding_amount") or "").lower()
            if ("40,000" in funding or "150,000" in funding) and best < 5:
                stage_label = "Stage 3" if "150,000" in funding else "Stage 2"
                best, best_name = 5, f"Venture Kick {stage_label}"

    # Multi-source multiplier: bump by 1 if 3+ distinct sources, capped at 5
    if n_distinct_sources >= 3 and best < 5:
        best = min(best + 1, 5)
        label = f"{best_name or list(program_names)[0]} + {n_distinct_sources} sources"
    else:
        label = best_name or list(program_names)[0]
        if len(program_names) > 1:
            label += f" + {len(program_names) - 1} more"

    return best, label


def _hn_stats(signal):
    """Extract HN points and comment count from signal metadata."""
    meta = signal.get("metadata")
    if not meta:
        return 0, 0
    try:
        data = json.loads(meta) if isinstance(meta, str) else meta
        return int(data.get("points", 0)), int(data.get("num_comments", 0))
    except (json.JSONDecodeError, TypeError, ValueError):
        return 0, 0


def _score_traction(signals, programs):
    """Traction Signal 1–5."""
    distinct_sources = {s["source_name"] for s in signals}
    n_sources = len(distinct_sources)
    n_programs = len({p["program_name"] for p in programs})

    has_hn = any(s["source_name"] == "HackerNews" for s in signals)
    best_hn_pts = 0
    for s in signals:
        if s["source_name"] == "HackerNews":
            pts, _ = _hn_stats(s)
            best_hn_pts = max(best_hn_pts, pts)
    has_ph = any(s["source_name"] == "ProductHunt" for s in signals)
    press_hits = distinct_sources & PRESS_SOURCES
    has_press = len(press_hits) > 0

    parts = []
    if has_press:
        parts.append(f"press ({', '.join(sorted(press_hits))})")
    if has_hn:
        parts.append(f"HN {best_hn_pts}pts" if best_hn_pts else "HN")
    if has_ph:
        parts.append("ProductHunt")
    if n_programs >= 2:
        parts.append(f"{n_programs} programs")

    strong_signals = sum([has_press, has_hn and best_hn_pts >= 50, has_ph, n_programs >= 2])

    if has_press and (has_hn or has_ph) and n_programs >= 2:
        score = 5
    elif strong_signals >= 2:
        score = 4
    elif n_sources >= 2:
        score = 3
    elif n_sources == 1 and (programs or has_press):
        score = 2
    else:
        score = 1

    label = ", ".join(parts) if parts else f"{n_sources} source{'s' if n_sources != 1 else ''}"
    return score, label


def _score_data(company, has_founders):
    """Data Completeness 1–5."""
    fields = [
        ("description", bool(company.get("description"))),
        ("website", bool(company.get("website"))),
        ("geography", bool(company.get("geography"))),
        ("city", bool(company.get("city"))),
        ("sector", bool(company.get("sector"))),
        ("founders", has_founders),
        ("stage", bool(company.get("stage"))),
    ]
    present = sum(1 for _, ok in fields if ok)
    missing = [name for name, ok in fields if not ok]

    if present == 7:
        return 5, "Complete"
    elif present == 6:
        return 4, f"Missing: {missing[0]}"
    elif present == 5:
        return 3, f"Missing: {', '.join(missing)}"
    elif present >= 4:
        return 2, f"Missing: {', '.join(missing)}"
    else:
        return 1, f"Only {present}/7 fields"


# ═══════════════════════════════════════════════════════════════
# Cohort recency helpers
# ═══════════════════════════════════════════════════════════════

_YEAR_RE = re.compile(r"(20\d{2})")
# YC-style 2-digit batch codes: W24, S25, F24, X25
_YC_BATCH_RE = re.compile(r"[WSFX](\d{2})$")


def _parse_cohort_year(cohort):
    """Parse a year from a cohort string.

    Handles both 4-digit years ("2025") and YC-style batch codes ("W24" → 2024).
    Returns int year or None.
    """
    if not cohort:
        return None
    # Try 4-digit year first
    m = _YEAR_RE.search(cohort)
    if m:
        return int(m.group(1))
    # Try YC-style 2-digit batch code
    m = _YC_BATCH_RE.search(cohort.strip())
    if m:
        yy = int(m.group(1))
        return 2000 + yy
    return None


def _extract_cohort_year(programs):
    """Extract the most recent cohort year from program records."""
    best = None
    for p in programs:
        year = _parse_cohort_year(p.get("cohort"))
        if year is not None:
            if best is None or year > best:
                best = year
    return best


def _recency_adjustment(programs, signals):
    """Return (adjustment, label, cap_or_None) for cohort recency.

    Hard caps:
      2024–2026  → no penalty
      2023       → -0.2
      ≤ 2022     → total capped at 2.5
      No data    → no penalty (benefit of the doubt)
    """
    current_year = date.today().year

    cohort_year = _extract_cohort_year(programs)
    if cohort_year is not None:
        if cohort_year >= current_year - 2:   # 2024, 2025, 2026
            return 0.0, f"Cohort {cohort_year}", None
        elif cohort_year == current_year - 3:  # 2023
            return -0.2, f"Cohort {cohort_year} (-0.2)", None
        else:                                  # ≤ 2022
            return 0.0, f"Old cohort ({cohort_year}), capped at 2.5", 2.5

    return 0.0, "No cohort data", None


def _compute_cohort_display(programs):
    """Build a cohort display string like 'Seedcamp 2025' or 'YC W26'.

    Only uses the cohort field for year — detected_at is the scrape date,
    not the actual program batch year, so we never use it as a year source.
    Handles both 4-digit years and YC-style batch codes (W24, S25).
    """
    if not programs:
        return None

    # Split programs into those with a parseable year and those without
    with_year = []
    without_year = []
    for p in programs:
        cohort = p.get("cohort") or ""
        year = _parse_cohort_year(cohort)
        if year is not None:
            with_year.append((p, year, cohort))
        else:
            without_year.append(p)

    # If any program has a real batch year, pick the most recent one
    if with_year:
        best_prog, best_year, display = max(with_year, key=lambda x: x[1])
        return f"{best_prog['program_name']} {display}".strip()

    # No program has a year — deduplicate by program_name, pick first
    seen = set()
    unique = []
    for p in programs:
        if p["program_name"] not in seen:
            seen.add(p["program_name"])
            unique.append(p)

    return f"{unique[0]['program_name']} (year unknown)"


# ═══════════════════════════════════════════════════════════════
# Main scoring API
# ═══════════════════════════════════════════════════════════════

def get_score_breakdown(company_id):
    """Calculate Athena Score for a company.

    Returns dict with:
      - "total" (float 0.0–5.0, 1 decimal)
      - "priority" ("high" / "investigate" / "radar" / "low")
      - "components" (dict of component name → {score, max, label, weight})
      - "reasons" (list of short explanation strings)
    """
    conn = get_connection()
    company = conn.execute(
        "SELECT * FROM companies WHERE id = ?", (company_id,)
    ).fetchone()
    programs = conn.execute(
        "SELECT * FROM programs WHERE company_id = ?", (company_id,)
    ).fetchall()
    signals = conn.execute(
        "SELECT * FROM signals WHERE company_id = ?", (company_id,)
    ).fetchall()
    founders = conn.execute(
        "SELECT name, title, linkedin_url FROM founders WHERE company_id = ?",
        (company_id,),
    ).fetchall()
    conn.close()

    if not company:
        return {"total": 0.0, "priority": "low", "components": {}, "reasons": []}

    company = dict(company)
    programs = [dict(r) for r in programs]
    signals = [dict(r) for r in signals]
    founders = [dict(r) for r in founders]

    distinct_sources = {s["source_name"] for s in signals}

    # ── Score each component ──

    thesis_score, thesis_label = _score_thesis(company)
    team_score, team_label = _score_team(founders)
    program_score, program_label = _score_program(programs, len(distinct_sources))
    traction_score, traction_label = _score_traction(signals, programs)
    data_score, data_label = _score_data(company, len(founders) > 0)

    has_team = team_score is not None
    weights = WEIGHTS_WITH_TEAM if has_team else WEIGHTS_WITHOUT_TEAM

    # ── Weighted total ──

    raw = {
        "thesis": thesis_score,
        "team": team_score,
        "program": program_score,
        "traction": traction_score,
        "data": data_score,
    }
    weighted_sum = 0.0
    for comp, w in weights.items():
        weighted_sum += (raw[comp] / 5.0) * w

    total = round(weighted_sum * 5.0, 1)

    # ── Cohort recency adjustment ──

    recency_adj, recency_label, recency_cap = _recency_adjustment(programs, signals)
    total = round(total + recency_adj, 1)
    if recency_cap is not None:
        total = min(total, recency_cap)
    total = max(0.0, min(total, 5.0))

    # ── Cohort display ──

    cohort_display = _compute_cohort_display(programs)

    # ── Priority badge ──

    if total >= 4.0:
        priority = "high"
    elif total >= 3.5:
        priority = "investigate"
    elif total >= 3.0:
        priority = "radar"
    else:
        priority = "low"

    # ── Components for API / display ──

    components = {
        "thesis": {
            "score": thesis_score, "max": 5,
            "label": thesis_label, "weight": weights.get("thesis", 0),
        },
        "team": {
            "score": team_score, "max": 5,
            "label": team_label, "weight": weights.get("team", 0),
        },
        "program": {
            "score": program_score, "max": 5,
            "label": program_label, "weight": weights.get("program", 0),
        },
        "traction": {
            "score": traction_score, "max": 5,
            "label": traction_label, "weight": weights.get("traction", 0),
        },
        "data": {
            "score": data_score, "max": 5,
            "label": data_label, "weight": weights.get("data", 0),
        },
    }

    # ── Reasons summary ──

    reasons = []
    reasons.append(f"Thesis: {thesis_label} ({thesis_score}/5)")
    if has_team:
        reasons.append(f"Team: {team_label} ({team_score}/5)")
    else:
        reasons.append(f"Team: {team_label}")
    reasons.append(f"Program: {program_label} ({program_score}/5)")
    reasons.append(f"Traction: {traction_label} ({traction_score}/5)")
    reasons.append(f"Data: {data_label} ({data_score}/5)")
    if recency_adj != 0:
        reasons.append(f"Recency: {'+' if recency_adj > 0 else ''}{recency_adj} ({recency_label})")

    return {
        "total": total,
        "priority": priority,
        "components": components,
        "recency": {"adjustment": recency_adj, "label": recency_label},
        "cohort": cohort_display,
        "reasons": reasons,
    }


def calculate_athena_score(company_id):
    """Calculate and return the Athena Score (0.0–5.0)."""
    return get_score_breakdown(company_id)["total"]


def score_all_companies():
    """Recalculate Athena Score for every company.

    Stores the score and full JSON breakdown in the DB.
    Returns number of companies scored.
    """
    conn = get_connection()
    rows = conn.execute("SELECT id FROM companies").fetchall()
    conn.close()

    count = 0
    for row in rows:
        cid = row[0]
        bd = get_score_breakdown(cid)
        update_company(
            cid,
            athena_score=bd["total"],
            athena_score_breakdown=json.dumps(bd),
        )
        count += 1

    return count


# ── CLI ──────────────────────────────────────────────────────

def main():
    print()
    print("=" * 64)
    print("  ATHENA SCORE ENGINE (0–5.0)")
    print("=" * 64)

    print("\n  Scoring all companies...")
    total = score_all_companies()
    print(f"  Scored {total} companies")

    # ── Distribution by priority tier ──
    conn = get_connection()
    tiers = conn.execute("""
        SELECT
            CASE
                WHEN athena_score >= 4.0 THEN 'High Priority'
                WHEN athena_score >= 3.5 THEN 'Worth Investigating'
                WHEN athena_score >= 3.0 THEN 'On Radar'
                ELSE 'Low Priority'
            END AS tier,
            COUNT(*) AS cnt,
            ROUND(AVG(athena_score), 2) AS avg_score
        FROM companies
        GROUP BY tier
        ORDER BY avg_score DESC
    """).fetchall()

    print("\n  Priority Distribution:")
    print(f"  {'Tier':<24s} {'Count':>7s}  {'Avg':>5s}")
    print(f"  {'─' * 24} {'─' * 7}  {'─' * 5}")
    for t in tiers:
        print(f"  {t[0]:<24s} {t[1]:>7,}  {t[2]:>5.2f}")

    # ── Score histogram ──
    dist = conn.execute("""
        SELECT ROUND(athena_score, 1) AS bucket, COUNT(*) AS cnt
        FROM companies
        GROUP BY bucket
        ORDER BY bucket
    """).fetchall()

    print(f"\n  Score Histogram:")
    max_cnt = max((r[1] for r in dist), default=1)
    for row in dist:
        bar_len = int((row[1] / max_cnt) * 30)
        bar = "█" * max(bar_len, 1) if row[1] > 0 else ""
        print(f"  {row[0]:>5.1f}  {row[1]:>6,}  {bar}")

    # ── Top 20 (2025/2026 cohort only) ──
    top = conn.execute("""
        SELECT DISTINCT c.id, c.name, c.sector, c.geography, c.stage, c.athena_score
        FROM companies c
        JOIN programs p ON p.company_id = c.id
        WHERE p.cohort GLOB '*2025*' OR p.cohort GLOB '*2026*'
           OR p.cohort LIKE '%W25' OR p.cohort LIKE '%S25'
           OR p.cohort LIKE '%F25' OR p.cohort LIKE '%X25'
           OR p.cohort LIKE '%W26' OR p.cohort LIKE '%S26'
           OR p.cohort LIKE '%F26' OR p.cohort LIKE '%X26'
        ORDER BY c.athena_score DESC, c.name ASC
        LIMIT 20
    """).fetchall()
    conn.close()

    print(f"\n  {'─' * 64}")
    print(f"  TOP 20 COMPANIES (2025/2026 cohort)")
    print(f"  {'─' * 64}")
    for i, row in enumerate(top):
        bd = get_score_breakdown(row["id"])
        c = bd["components"]
        geo = row["geography"] or "?"
        sector = row["sector"] or "?"
        stage = row["stage"] or "?"

        badge = {"high": "HIGH", "investigate": "INVESTIGATE",
                 "radar": "RADAR", "low": "LOW"}[bd["priority"]]

        team_str = f"{c['team']['score']}" if c['team']['score'] is not None else "—"
        cohort_str = bd.get("cohort") or "—"
        recency = bd.get("recency", {})
        rec_adj = recency.get("adjustment", 0)
        rec_str = f"  Recency: {'+' if rec_adj > 0 else ''}{rec_adj}" if rec_adj != 0 else ""

        print(f"\n  {i+1:>2}. [{bd['total']:.1f}] {badge:<12s}  {row['name']}")
        print(f"      {sector} | {geo} | {stage} | Cohort: {cohort_str}")
        print(f"      Thesis: {c['thesis']['score']:.1f}"
              f"  Team: {team_str}"
              f"  Program: {c['program']['score']}"
              f"  Traction: {c['traction']['score']}"
              f"  Data: {c['data']['score']}"
              f"{rec_str}")

    print()
    print("=" * 64)
    print()


if __name__ == "__main__":
    main()
