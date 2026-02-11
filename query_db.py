"""Quick database summary for Athena."""

import json
from database.database import get_connection


def main():
    conn = get_connection()

    companies = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    signals = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]

    print("=" * 60)
    print("  ATHENA — Database Summary")
    print("=" * 60)
    print()
    print(f"  Total companies:  {companies}")
    print(f"  Total signals:    {signals}")

    # Top 10 by points
    print()
    print("-" * 60)
    print("  Top 10 Companies by HN Points")
    print("-" * 60)
    print()
    print(f"  {'Pts':>6}  {'Comments':>8}  {'Geography':>14}  Name")
    print(f"  {'---':>6}  {'--------':>8}  {'---------':>14}  ----")

    rows = conn.execute("""
        SELECT c.name, c.geography,
               s.metadata
        FROM signals s
        JOIN companies c ON c.id = s.company_id
        ORDER BY json_extract(s.metadata, '$.points') DESC
        LIMIT 10
    """).fetchall()

    for r in rows:
        meta = json.loads(r["metadata"])
        geo = r["geography"] if r["geography"] != "Unknown" else "-"
        print(f"  {meta['points']:>6}  {meta['num_comments']:>8}  {geo:>14}  {r['name'][:40]}")

    # Geography breakdown
    print()
    print("-" * 60)
    print("  Companies by Geography")
    print("-" * 60)
    print()

    rows = conn.execute("""
        SELECT geography, COUNT(*) as cnt
        FROM companies
        GROUP BY geography
        ORDER BY cnt DESC
    """).fetchall()

    for r in rows:
        bar = "#" * min(r["cnt"], 50)
        print(f"  {r['geography']:>20s}  {r['cnt']:>4}  {bar}")

    print()
    print("=" * 60)

    conn.close()


if __name__ == "__main__":
    main()
