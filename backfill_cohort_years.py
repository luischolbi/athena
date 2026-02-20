"""
Athena — Backfill cohort years for Venture Kick, 500 Global, and EWOR.

Venture Kick:  Fetches profile pages to extract "Incorporated: DD.MM.YYYY".
500 Global:    Parses invest_date from stored signal metadata JSON.
EWOR:          Parses year from stored signal metadata JSON.

Usage:
    python backfill_cohort_years.py
"""

import json
import re
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.database import get_connection

YEAR_RE = re.compile(r"(20\d{2})")
INC_RE = re.compile(r"Incorporated:\s*\d{2}\.\d{2}\.(\d{4})")


def log(msg):
    print(msg, flush=True)


def backfill_500_global():
    """Backfill 500 Global cohort from invest_date in signal metadata."""
    log("\n  500 Global: parsing invest_date from signal metadata...")
    conn = get_connection()

    # Get all 500G programs with their signal metadata
    rows = conn.execute("""
        SELECT p.id AS prog_id, p.company_id, p.cohort,
               s.metadata
        FROM programs p
        JOIN signals s ON s.company_id = p.company_id AND s.source_name = '500 Global'
        WHERE p.program_name = '500 Global'
        GROUP BY p.id
    """).fetchall()

    updated = 0
    for r in rows:
        meta = {}
        if r["metadata"]:
            try:
                meta = json.loads(r["metadata"])
            except (json.JSONDecodeError, TypeError):
                pass

        invest_date = meta.get("invest_date")
        if not invest_date:
            continue

        year = invest_date[:4]
        if not YEAR_RE.match(year):
            continue

        # Only update if current cohort doesn't have a year
        current = r["cohort"] or ""
        if YEAR_RE.search(current):
            continue

        conn.execute("UPDATE programs SET cohort = ? WHERE id = ?", (year, r["prog_id"]))
        updated += 1

    conn.commit()
    conn.close()
    log(f"    Updated {updated} / {len(rows)} program records")
    return updated


def backfill_ewor():
    """Backfill EWOR cohort from year in signal metadata."""
    log("\n  EWOR: parsing year from signal metadata...")
    conn = get_connection()

    rows = conn.execute("""
        SELECT p.id AS prog_id, p.company_id, p.cohort,
               s.metadata
        FROM programs p
        JOIN signals s ON s.company_id = p.company_id AND s.source_name = 'EWOR'
        WHERE p.program_name = 'EWOR'
        GROUP BY p.id
    """).fetchall()

    updated = 0
    for r in rows:
        current = r["cohort"] or ""
        if YEAR_RE.search(current):
            continue

        meta = {}
        if r["metadata"]:
            try:
                meta = json.loads(r["metadata"])
            except (json.JSONDecodeError, TypeError):
                pass

        year = meta.get("year")
        if year:
            conn.execute("UPDATE programs SET cohort = ? WHERE id = ?",
                         (str(year), r["prog_id"]))
            updated += 1

    conn.commit()
    conn.close()
    log(f"    Updated {updated} / {len(rows)} program records")
    return updated


def backfill_venture_kick():
    """Backfill Venture Kick cohort by fetching profile pages."""
    log("\n  Venture Kick: fetching profile pages for incorporation year...")

    # Lazy imports for web fetching
    import requests
    from bs4 import BeautifulSoup

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }

    conn = get_connection()

    # Get VK programs that don't have a year in cohort, with their signal URLs
    rows = conn.execute("""
        SELECT p.id AS prog_id, p.company_id, p.cohort, c.name,
               s.source_url
        FROM programs p
        JOIN companies c ON c.id = p.company_id
        JOIN signals s ON s.company_id = p.company_id AND s.source_name = 'Venture Kick'
        WHERE p.program_name = 'Venture Kick'
        GROUP BY p.company_id
        ORDER BY c.athena_score DESC
    """).fetchall()

    # Filter to only those missing a year
    need_backfill = []
    already_have = 0
    for r in rows:
        current = r["cohort"] or ""
        if YEAR_RE.search(current):
            already_have += 1
        else:
            need_backfill.append(r)

    log(f"    {already_have} already have year, {len(need_backfill)} need backfill")

    updated = 0
    failed = 0
    for i, r in enumerate(need_backfill, 1):
        url = r["source_url"]
        if not url:
            failed += 1
            continue

        try:
            resp = requests.get(url, headers=HEADERS, timeout=8)
            resp.raise_for_status()
        except Exception as e:
            failed += 1
            if i <= 5:
                log(f"    ERROR: {r['name']}: {e}")
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        page_text = soup.get_text()
        m = INC_RE.search(page_text)
        if m:
            year = m.group(1)
            conn.execute("UPDATE programs SET cohort = ? WHERE id = ?",
                         (year, r["prog_id"]))
            updated += 1
            if updated <= 10:
                log(f"    + {r['name'][:35]:35s} -> {year}")
        else:
            failed += 1

        if i % 100 == 0:
            conn.commit()
            log(f"    ... {i}/{len(need_backfill)} "
                f"(found={updated}, miss={failed})")

        time.sleep(0.8)

    conn.commit()
    conn.close()
    log(f"    Updated {updated} / {len(need_backfill)} "
        f"({failed} failed/no date)")
    return updated


def main():
    log("")
    log("=" * 64)
    log("  ATHENA — Cohort Year Backfill")
    log("=" * 64)

    total = 0
    total += backfill_500_global()
    total += backfill_ewor()
    total += backfill_venture_kick()

    # Summary
    conn = get_connection()

    log("\n" + "=" * 64)
    log("  SUMMARY")
    log("=" * 64)

    # Companies with real cohort years
    has_year = conn.execute("""
        SELECT COUNT(DISTINCT company_id) FROM programs
        WHERE cohort GLOB '*[0-9][0-9][0-9][0-9]*'
    """).fetchone()[0]
    total_with_programs = conn.execute(
        "SELECT COUNT(DISTINCT company_id) FROM programs"
    ).fetchone()[0]
    no_year = total_with_programs - has_year

    log(f"\n  Companies with real cohort year:  {has_year}")
    log(f"  Companies still 'year unknown':   {no_year}")
    log(f"  Total companies with programs:    {total_with_programs}")

    # Year distribution
    log(f"\n  Cohort year distribution:")
    dist = conn.execute("""
        SELECT
            CASE
                WHEN cohort GLOB '*2026*' THEN '2026'
                WHEN cohort GLOB '*2025*' THEN '2025'
                WHEN cohort GLOB '*2024*' THEN '2024'
                WHEN cohort GLOB '*2023*' THEN '2023'
                WHEN cohort GLOB '*2022*' THEN '2022'
                WHEN cohort GLOB '*2021*' THEN '2021'
                WHEN cohort GLOB '*2020*' THEN '2020'
                WHEN cohort GLOB '*201[0-9]*' THEN '2010s'
                WHEN cohort GLOB '*200[0-9]*' THEN '2000s'
                WHEN cohort GLOB '*19[0-9][0-9]*' THEN '1990s'
                ELSE 'unknown'
            END AS year_bucket,
            COUNT(DISTINCT company_id) AS cnt
        FROM programs
        GROUP BY year_bucket
        ORDER BY year_bucket DESC
    """).fetchall()
    for r in dist:
        log(f"    {r[0]:10s}  {r[1]:>6,} companies")

    # Per-source breakdown
    log(f"\n  Per-source cohort coverage:")
    sources = conn.execute("""
        SELECT program_name, COUNT(DISTINCT company_id) AS total,
               SUM(CASE WHEN cohort GLOB '*[0-9][0-9][0-9][0-9]*' THEN 1 ELSE 0 END) AS has_year
        FROM (
            SELECT program_name, company_id, cohort,
                   ROW_NUMBER() OVER (PARTITION BY company_id, program_name ORDER BY detected_at DESC) AS rn
            FROM programs
        )
        WHERE rn = 1
        GROUP BY program_name
        ORDER BY total DESC
    """).fetchall()
    log(f"    {'Source':<30s} {'Has Year':>10s} {'Total':>8s} {'%':>6s}")
    log(f"    {'─' * 30} {'─' * 10} {'─' * 8} {'─' * 6}")
    for r in sources:
        pct = r["has_year"] / r["total"] * 100 if r["total"] else 0
        log(f"    {r['program_name']:<30s} {r['has_year']:>10,} {r['total']:>8,} {pct:>5.0f}%")

    conn.close()
    log(f"\n{'─' * 64}")
    log(f"  Total records updated: {total}")
    log(f"{'─' * 64}")
    log("")


if __name__ == "__main__":
    main()
