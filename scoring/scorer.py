"""
Athena — Heat scoring engine (1–10 scale).

Calculates a heat score for every company based on four components:
  1. Program Pedigree (0–4 pts)
  2. Community Buzz (0–3 pts)
  3. Cross-Source Appearances (0–2 pts)
  4. Recency Boost (0–1 pt)

Also computes a 'rising' flag for companies whose score increased
by 2+ points since the last scoring run.

Usage:
    python -m scoring.scorer
"""

import json
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.database import get_connection, update_company

# ── Program Tier Dictionary ──
# Edit this to change tier assignments.
# Tier A = 4pts, B = 3pts, C = 2pts, D = 1pt.
# Companies in 2+ programs get a +1 bonus (capped at 4 total for pedigree).
# VK Stage 2/3 are automatically upgraded from B → A in code.

PROGRAM_TIERS = {
    # Tier A — highly selective accelerators
    "Entrepreneur First": "A",
    "Seedcamp": "A",
    "Y Combinator": "A",
    "Techstars": "A",

    # Tier B — strong programs
    "Venture Kick": "B",       # Stage 1 default; Stage 2/3 → A
    "ETH AI Center": "B",

    # Tier C — university spinout offices
    "Cambridge Enterprise": "C",
    "Imperial Enterprise Lab": "C",

    # Anything not listed → Tier D (1 point)
}

TIER_POINTS = {"A": 4, "B": 3, "C": 2, "D": 1}

PRESS_SOURCES = {"Sifted", "Tech.eu", "TechCrunch", "EU-Startups"}

SEVEN_DAYS_AGO = datetime.now() - timedelta(days=7)


def _parse_ts(ts_str):
    """Parse a DB timestamp string to datetime, or None."""
    if not ts_str:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(ts_str, fmt)
        except ValueError:
            continue
    return None


def _get_program_tier(program):
    """Determine the tier letter for a program entry."""
    name = program["program_name"]
    tier = PROGRAM_TIERS.get(name, "D")

    # VK Stage 2/3 upgrades from B → A
    if name == "Venture Kick":
        cohort = (program["cohort"] or "").lower()
        if cohort in ("stage 2", "stage 3"):
            tier = "A"

    return tier


def _hn_stats(signal):
    """Extract HN points and comment count from signal metadata."""
    meta = signal["metadata"]
    if not meta:
        return 0, 0
    try:
        data = json.loads(meta)
        points = int(data.get("points", 0))
        comments = int(data.get("num_comments", 0))
        return points, comments
    except (json.JSONDecodeError, TypeError, ValueError):
        return 0, 0


def get_score_breakdown(company_id):
    """Calculate heat score for a company and return breakdown.

    Returns dict with:
      - "total" (int 1–10)
      - "reasons" (list of strings)
      - "components" (dict of component scores with labels)
      - "rising" (bool)
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
    conn.close()

    programs = [dict(r) for r in programs]
    signals = [dict(r) for r in signals]
    now = datetime.now()

    score = 0
    reasons = []

    # ── 1. PROGRAM PEDIGREE (up to 4 points) ──

    pedigree = 0
    pedigree_label = "No program"
    if programs:
        # Find the highest tier across all programs
        best_tier = "D"
        best_label = None
        for p in programs:
            tier = _get_program_tier(p)
            if TIER_POINTS[tier] > TIER_POINTS.get(best_tier, 0):
                best_tier = tier
                best_label = p["program_name"]
                if tier == "A" and p["program_name"] == "Venture Kick":
                    best_label = f"Venture Kick {p['cohort']}"

        pedigree = TIER_POINTS[best_tier]
        pedigree_label = f"Tier {best_tier} — {best_label or programs[0]['program_name']}"
        program_names = sorted({p["program_name"] for p in programs})
        reasons.append(
            f"Program: Tier {best_tier} — {best_label or program_names[0]} (+{pedigree})"
        )

        # Multi-program bonus (+1 if in 2+ distinct programs, still capped at 4)
        unique_programs = {p["program_name"] for p in programs}
        if len(unique_programs) >= 2 and pedigree < 4:
            pedigree = min(pedigree + 1, 4)
            pedigree_label += f" + {len(unique_programs) - 1} more"
            reasons.append(
                f"Multi-program: {', '.join(sorted(unique_programs))} (+1)"
            )

    score += pedigree

    # ── 2. COMMUNITY BUZZ (up to 3 points) ──

    buzz = 0
    buzz_parts = []

    # Best HN signal
    best_hn_pts = 0
    best_hn_comments = 0
    for s in signals:
        if s["source_name"] == "HackerNews":
            pts, cmts = _hn_stats(s)
            best_hn_pts = max(best_hn_pts, pts)
            best_hn_comments = max(best_hn_comments, cmts)

    if best_hn_pts >= 300 or best_hn_comments >= 100:
        buzz += 3
        buzz_parts.append(f"HN {best_hn_pts}pts")
        reasons.append(f"HN viral: {best_hn_pts}pts, {best_hn_comments} comments (+3)")
    elif best_hn_pts >= 100 or best_hn_comments >= 50:
        buzz += 2
        buzz_parts.append(f"HN {best_hn_pts}pts")
        reasons.append(f"HN traction: {best_hn_pts}pts, {best_hn_comments} comments (+2)")
    elif best_hn_pts > 0:
        buzz += 1
        buzz_parts.append(f"HN {best_hn_pts}pts")
        reasons.append(f"HN signal: {best_hn_pts}pts (+1)")

    # ProductHunt
    if any(s["source_name"] == "ProductHunt" for s in signals):
        buzz += 1
        buzz_parts.append("ProductHunt")
        reasons.append("ProductHunt launch (+1)")

    # Press mentions (each +1)
    press_hits = {s["source_name"] for s in signals if s["source_name"] in PRESS_SOURCES}
    for src in sorted(press_hits):
        buzz += 1
        buzz_parts.append(src)
        reasons.append(f"Press: {src} (+1)")

    buzz = min(buzz, 3)
    buzz_label = ", ".join(buzz_parts) if buzz_parts else "No buzz signals"
    score += buzz

    # ── 3. CROSS-SOURCE APPEARANCES (up to 2 points) ──

    distinct_sources = {s["source_name"] for s in signals}
    n_sources = len(distinct_sources)

    cross = 0
    if n_sources >= 3:
        cross = 2
        reasons.append(f"Cross-source: {n_sources} sources (+2)")
    elif n_sources == 2:
        cross = 1
        reasons.append(f"Cross-source: {n_sources} sources (+1)")

    sources_label = f"{n_sources} source{'s' if n_sources != 1 else ''}"
    if n_sources > 0:
        sources_label += f" ({', '.join(sorted(distinct_sources))})"

    score += cross

    # ── 4. RECENCY BOOST (up to 1 point) ──

    recency = 0
    recency_label = "No recent signals"
    best_recent_dt = None

    for s in signals:
        detected = _parse_ts(s["detected_at"])
        if detected and detected >= SEVEN_DAYS_AGO:
            if best_recent_dt is None or detected > best_recent_dt:
                best_recent_dt = detected

    if best_recent_dt is None:
        for p in programs:
            detected = _parse_ts(p["detected_at"])
            if detected and detected >= SEVEN_DAYS_AGO:
                if best_recent_dt is None or detected > best_recent_dt:
                    best_recent_dt = detected

    if best_recent_dt:
        recency = 1
        days_ago = (now - best_recent_dt).days
        if days_ago == 0:
            recency_label = "signal today"
        elif days_ago == 1:
            recency_label = "signal 1 day ago"
        else:
            recency_label = f"signal {days_ago} days ago"
        reasons.append(f"Recent activity: {recency_label} (+1)")

    score += recency

    # Floor at 1, cap at 10
    total = max(1, min(score, 10))

    # ── Components (structured for frontend) ──
    components = {
        "program": {"score": pedigree, "max": 4, "label": pedigree_label},
        "buzz": {"score": buzz, "max": 3, "label": buzz_label},
        "sources": {"score": cross, "max": 2, "label": sources_label},
        "recency": {"score": recency, "max": 1, "label": recency_label},
    }

    # ── Rising flag ──
    rising = False
    if company:
        col_names = company.keys()
        prev = company["previous_heat_score"] if "previous_heat_score" in col_names else None
        if prev is not None and total - prev >= 2:
            rising = True

    return {"total": total, "reasons": reasons, "components": components, "rising": rising}


def calculate_heat_score(company_id):
    """Calculate and return the heat score (1–10) for a company."""
    return get_score_breakdown(company_id)["total"]


def score_all_companies():
    """Recalculate heat scores for every company in the database.

    Snapshots current scores to previous_heat_score before recalculating,
    enabling the 'rising' flag detection.
    """
    conn = get_connection()

    # Snapshot current scores as previous before recalculating
    conn.execute("UPDATE companies SET previous_heat_score = heat_score")
    conn.commit()

    rows = conn.execute("SELECT id FROM companies").fetchall()
    conn.close()

    count = 0
    for row in rows:
        cid = row[0]
        new_score = calculate_heat_score(cid)
        update_company(cid, heat_score=new_score)
        count += 1

    return count


def main():
    print()
    print("=" * 60)
    print("  ATHENA — Heat Score Engine (1–10)")
    print("=" * 60)

    # Score all
    print("\n  Scoring all companies...")
    total = score_all_companies()
    print(f"  Scored {total} companies")

    # Distribution
    conn = get_connection()
    dist = conn.execute("""
        SELECT heat_score, COUNT(*) AS cnt
        FROM companies
        GROUP BY heat_score
        ORDER BY heat_score
    """).fetchall()
    conn.close()

    print("\n  Score Distribution:")
    print(f"  {'Score':>7s}  {'Count':>6s}  {'Bar'}")
    print(f"  {'─' * 7}  {'─' * 6}  {'─' * 30}")
    max_cnt = max((r[1] for r in dist), default=1)
    for row in dist:
        bar_len = int((row[1] / max_cnt) * 30)
        bar = "█" * max(bar_len, 1) if row[1] > 0 else ""
        print(f"  {row[0]:>7}  {row[1]:>6}  {bar}")

    # Rising companies
    conn = get_connection()
    rising = conn.execute("""
        SELECT id, name, heat_score, previous_heat_score
        FROM companies
        WHERE heat_score - previous_heat_score >= 2
        ORDER BY (heat_score - previous_heat_score) DESC, name
        LIMIT 10
    """).fetchall()
    conn.close()

    if rising:
        print(f"\n  Rising Companies (score +2 or more):")
        print(f"  {'─' * 56}")
        for r in rising:
            delta = r["heat_score"] - r["previous_heat_score"]
            print(f"  ↑ {r['name']}  {r['previous_heat_score']} → {r['heat_score']} (+{delta})")

    # Top 10
    conn = get_connection()
    top = conn.execute("""
        SELECT id, name, sector, geography, heat_score
        FROM companies
        ORDER BY heat_score DESC, name
        LIMIT 10
    """).fetchall()
    conn.close()

    print(f"\n  Top 10 Companies:")
    print(f"  {'─' * 56}")
    for row in top:
        bd = get_score_breakdown(row[0])
        geo = row["geography"] or "?"
        sector = row["sector"] or "?"
        rising_mark = " ↑" if bd["rising"] else ""
        print(f"\n  [{bd['total']}]{rising_mark} {row['name']}")
        print(f"      {sector} | {geo}")
        for reason in bd["reasons"]:
            print(f"      - {reason}")

    print()
    print("=" * 60)
    print()


if __name__ == "__main__":
    main()
