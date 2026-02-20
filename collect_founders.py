"""
Backfill founders table from existing signal metadata (EF) and print summary.

Usage: python collect_founders.py
"""

import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.database import init_db, get_connection, insert_founder


def backfill_ef_founders():
    """Extract founders from existing EF signal metadata and insert into founders table."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT s.company_id, s.metadata, c.name AS company_name
        FROM signals s
        JOIN companies c ON c.id = s.company_id
        WHERE s.source_name = 'Entrepreneur First'
        AND s.metadata IS NOT NULL
    """).fetchall()
    conn.close()

    count = 0
    companies_with = 0

    for row in rows:
        try:
            meta = json.loads(row["metadata"])
        except (json.JSONDecodeError, TypeError):
            continue

        founders = meta.get("founders", [])
        if not founders:
            continue

        has_any = False
        for f in founders:
            name = (f.get("name") or "").strip()
            if not name:
                continue
            insert_founder(
                company_id=row["company_id"],
                name=name,
                title=(f.get("role") or "").strip() or None,
                linkedin_url=(f.get("linkedin") or "").strip() or None,
                source="Entrepreneur First",
            )
            count += 1
            has_any = True

        if has_any:
            companies_with += 1

    return count, companies_with


def print_summary():
    """Print founder counts per source and overall stats."""
    conn = get_connection()

    total = conn.execute("SELECT COUNT(*) FROM founders").fetchone()[0]
    total_companies = conn.execute(
        "SELECT COUNT(DISTINCT company_id) FROM founders"
    ).fetchone()[0]
    total_all_companies = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]

    by_source = conn.execute("""
        SELECT source, COUNT(*) as cnt, COUNT(DISTINCT company_id) as companies
        FROM founders
        GROUP BY source
        ORDER BY cnt DESC
    """).fetchall()

    with_linkedin = conn.execute(
        "SELECT COUNT(*) FROM founders WHERE linkedin_url IS NOT NULL"
    ).fetchone()[0]

    conn.close()

    print("\n" + "=" * 60)
    print("FOUNDER DATA SUMMARY")
    print("=" * 60)

    print(f"\nTotal founders collected: {total}")
    print(f"Companies with founder data: {total_companies} / {total_all_companies} "
          f"({100 * total_companies / max(total_all_companies, 1):.1f}%)")
    print(f"Founders with LinkedIn URL: {with_linkedin}")

    print("\nBy source:")
    print(f"  {'Source':<25s} {'Founders':>10s} {'Companies':>10s}")
    print(f"  {'-' * 25} {'-' * 10} {'-' * 10}")
    for row in by_source:
        print(f"  {row['source']:<25s} {row['cnt']:>10d} {row['companies']:>10d}")

    no_founder_sources = [
        "Techstars", "Antler", "Venture Kick", "EPFL",
        "Oxford", "Imperial College London",
    ]
    print(f"\nSources without founder data on their pages:")
    for s in no_founder_sources:
        print(f"  - {s}")


def main():
    init_db()

    print("Founder Data Collection")
    print("=" * 60)

    # Backfill from existing EF signal metadata
    print("\nPhase 1: Backfilling from existing EF signal metadata...")
    ef_count, ef_companies = backfill_ef_founders()
    print(f"  Extracted {ef_count} founders from {ef_companies} EF companies")

    print("\nPhase 2: Run individual scrapers to collect fresh data:")
    print("  - python scrapers/entrepreneur_first.py  (EF — founders from portfolio tiles)")
    print("  - python scrapers/ycombinator.py          (YC — founders from company pages)")
    print("  - python scrapers/eth_ai_center.py        (ETH — founders from prose)")

    print_summary()


if __name__ == "__main__":
    main()
