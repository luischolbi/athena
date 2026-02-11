"""
Athena — Database verification and summary report.

Usage:
    python verify_data.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.database import get_connection

SOURCES = ["HackerNews", "Venture Kick", "ETH AI Center", "Entrepreneur First", "Seedcamp"]

BAR_CHAR = "█"
MAX_BAR = 30


def bar(count, max_count):
    """Return a simple ASCII bar scaled to max_count."""
    if max_count == 0:
        return ""
    length = int((count / max_count) * MAX_BAR)
    return BAR_CHAR * max(length, 1) if count > 0 else ""


def section(title):
    print()
    print(f"  {'─' * 56}")
    print(f"  {title}")
    print(f"  {'─' * 56}")


def main():
    conn = get_connection()

    # ── Totals ──
    total_companies = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    total_signals = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    total_programs = conn.execute("SELECT COUNT(*) FROM programs").fetchone()[0]

    print()
    print("=" * 60)
    print("  ATHENA — Data Verification Report")
    print("=" * 60)

    section("Overview")
    print(f"  {'Companies':20s}  {total_companies:>6}")
    print(f"  {'Signals':20s}  {total_signals:>6}")
    print(f"  {'Program entries':20s}  {total_programs:>6}")

    # ── Companies per source ──
    section("Companies per Source")
    rows = conn.execute("""
        SELECT s.source_name, COUNT(DISTINCT s.company_id) AS cnt
        FROM signals s
        GROUP BY s.source_name
        ORDER BY cnt DESC
    """).fetchall()
    max_cnt = max((r[1] for r in rows), default=0)
    for r in rows:
        print(f"  {r[0]:24s}  {r[1]:>5}  {bar(r[1], max_cnt)}")

    # ── Signals per source ──
    section("Signals per Source")
    rows = conn.execute("""
        SELECT source_name, COUNT(*) AS cnt
        FROM signals
        GROUP BY source_name
        ORDER BY cnt DESC
    """).fetchall()
    max_cnt = max((r[1] for r in rows), default=0)
    for r in rows:
        print(f"  {r[0]:24s}  {r[1]:>5}  {bar(r[1], max_cnt)}")

    # ── Companies per geography ──
    section("Companies per Geography")
    rows = conn.execute("""
        SELECT COALESCE(geography, 'Unknown') AS geo, COUNT(*) AS cnt
        FROM companies
        GROUP BY geo
        ORDER BY cnt DESC
    """).fetchall()
    max_cnt = max((r[1] for r in rows), default=0)
    for r in rows:
        print(f"  {r[0]:24s}  {r[1]:>5}  {bar(r[1], max_cnt)}")

    # ── Companies per sector ──
    section("Companies per Sector")
    rows = conn.execute("""
        SELECT COALESCE(sector, 'Unknown') AS sec, COUNT(*) AS cnt
        FROM companies
        GROUP BY sec
        ORDER BY cnt DESC
    """).fetchall()
    max_cnt = max((r[1] for r in rows), default=0)
    for r in rows:
        print(f"  {r[0]:24s}  {r[1]:>5}  {bar(r[1], max_cnt)}")

    # ── Companies per stage ──
    section("Companies per Stage")
    rows = conn.execute("""
        SELECT COALESCE(stage, 'Unknown') AS stg, COUNT(*) AS cnt
        FROM companies
        GROUP BY stg
        ORDER BY cnt DESC
    """).fetchall()
    max_cnt = max((r[1] for r in rows), default=0)
    for r in rows:
        print(f"  {r[0]:24s}  {r[1]:>5}  {bar(r[1], max_cnt)}")

    # ── Multi-source companies (cross-layer potential) ──
    section("Top 10 Multi-Source Companies (Cross-Layer)")
    rows = conn.execute("""
        SELECT c.name, c.sector, c.geography,
               COUNT(DISTINCT s.source_name) AS source_count,
               GROUP_CONCAT(DISTINCT s.source_name) AS sources
        FROM companies c
        JOIN signals s ON s.company_id = c.id
        GROUP BY c.id
        HAVING source_count > 1
        ORDER BY source_count DESC, c.name
        LIMIT 10
    """).fetchall()

    if rows:
        print(f"  {'Company':30s}  {'Sources':5s}  {'Sector':14s}  {'From'}")
        print(f"  {'─' * 30}  {'─' * 5}  {'─' * 14}  {'─' * 30}")
        for r in rows:
            print(f"  {r[0][:30]:30s}  {r[3]:>5}  {(r[2] or '?'):14s}  {r[4]}")
    else:
        print("  No companies appear in multiple sources yet.")

    # ── 5 most recent companies per source ──
    section("5 Most Recent Companies per Source")
    for source in SOURCES:
        rows = conn.execute("""
            SELECT c.name, c.sector, c.geography, c.created_at
            FROM companies c
            JOIN signals s ON s.company_id = c.id
            WHERE s.source_name = ?
            ORDER BY c.created_at DESC, c.id DESC
            LIMIT 5
        """, (source,)).fetchall()

        print(f"\n  {source}:")
        if not rows:
            print("    (no data)")
            continue
        for r in rows:
            geo = r[2] or "?"
            sector = r[1] or "?"
            print(f"    {r[0][:35]:35s}  {sector:14s}  {geo}")

    # ── Done ──
    print()
    print("=" * 60)
    print("  Verification complete.")
    print("=" * 60)
    print()

    conn.close()


if __name__ == "__main__":
    main()
