"""
Athena — Data Quality Report (v2).

Measures completeness, duplication, sector coverage, stage freshness.
Reports both curated-only (excluding EU-Startups) and full metrics.
Assigns source tiers and a data_tier column to companies.

Usage:
    python eval/data_quality_report.py
"""

import sys
import os
import csv
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from database.database import get_connection

REPORT_PATH = os.path.join(os.path.dirname(__file__), "data_quality_report.txt")
SAMPLE_PATH = os.path.join(os.path.dirname(__file__), "precision_sample.csv")

FIELDS = ["name", "description", "sector", "geography", "city", "website", "stage"]

GENERATED_DESC_MARKER = "Detected via"

KNOWN_TOTALS = {
    "Venture Kick": 957,
    "ETH AI Center": 30,
    "Seedcamp": 400,
    "Entrepreneur First": 300,
    "DTU Science Park": 275,      # actual page count (single page, no pagination)
    "EPFL": 474,                  # 392 Incorporated + 39 In creation + 43 M&A (excl. 129 Stopped)
    "University of Zurich": 187,  # actual table rows on UZH page
    "University of Oxford": 280,  # ~280 across 14 pages of CSV export
    "Cambridge Enterprise": 150,
    "Imperial College": 88,       # actual entries on portfolio page
    "KTH Innovation": 64,         # actual entries on portfolio page
}

# Tier thresholds (on average field fill rate)
TIER_1_THRESHOLD = 0.90   # 90%+
TIER_2_THRESHOLD = 0.60   # 60-90%


class Report:
    def __init__(self):
        self.lines = []

    def print(self, text=""):
        self.lines.append(text)
        print(text, flush=True)

    def save(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("\n".join(self.lines) + "\n")


def pct(n, total):
    return f"{n / total * 100:.1f}%" if total > 0 else "N/A"


def bar(n, total, width=20):
    filled = round(n / total * width) if total > 0 else 0
    return "█" * filled + "░" * (width - filled)


def field_filled(value, field):
    if value is None or value == "":
        return False
    if field == "sector" and value == "Other":
        return False
    if field == "geography" and value in ("Unknown", "Europe"):
        return False
    if field == "stage" and value in ("Unknown", "NA"):
        return False
    if field == "description" and GENERATED_DESC_MARKER in str(value):
        return False
    return True


def source_avg_fill(comps):
    """Average field fill rate for a set of companies."""
    n = len(comps)
    if n == 0:
        return 0.0
    total_filled = sum(
        1 for c in comps for f in FIELDS if field_filled(c.get(f), f)
    )
    return total_filled / (n * len(FIELDS))


def compute_quality_score(comps, dup_rate=0.0):
    """Compute overall quality score for a set of companies."""
    n = len(comps)
    if n == 0:
        return 0.0, {}

    field_counts = {f: sum(1 for c in comps if field_filled(c.get(f), f)) for f in FIELDS}
    avg_completeness = sum(field_counts[f] / n for f in FIELDS) / len(FIELDS)
    sector_coverage = 1 - (sum(1 for c in comps if c.get("sector") == "Other") / n)
    has_stage = sum(1 for c in comps
                    if c.get("stage") and c["stage"] not in ("", "Unknown", "NA"))
    stage_score = has_stage / n

    today = datetime.now().date()
    twelve_months_ago = today.replace(year=today.year - 1).isoformat()
    fresh = sum(1 for c in comps
                if c.get("first_detected") and c["first_detected"] >= twelve_months_ago)
    freshness_score = fresh / n
    dedup_score = 1 - dup_rate

    overall = (avg_completeness * 0.40 +
               sector_coverage * 0.20 +
               stage_score * 0.15 +
               freshness_score * 0.15 +
               dedup_score * 0.10)

    components = {
        "field_completeness": avg_completeness,
        "sector_coverage": sector_coverage,
        "stage_coverage": stage_score,
        "data_freshness": freshness_score,
        "dedup_cleanliness": dedup_score,
    }
    return overall, components


def print_quality_table(r, overall, components):
    weights = [
        ("Field completeness", "field_completeness", 0.40),
        ("Sector coverage", "sector_coverage", 0.20),
        ("Stage coverage", "stage_coverage", 0.15),
        ("Data freshness", "data_freshness", 0.15),
        ("Dedup cleanliness", "dedup_cleanliness", 0.10),
    ]
    r.print(f"  {'Component':25s} {'Score':>8s} {'Weight':>8s} {'Weighted':>8s}")
    r.print(f"  {'─' * 25} {'─' * 8} {'─' * 8} {'─' * 8}")
    for label, key, weight in weights:
        score = components[key]
        r.print(f"  {label:25s} {score * 100:>7.1f}% {weight * 100:>7.0f}% "
                f"{score * weight * 100:>7.1f}%")
    r.print(f"  {'─' * 25} {'─' * 8} {'─' * 8} {'─' * 8}")
    r.print(f"  {'OVERALL':25s}                   {overall * 100:>7.1f}%")
    r.print(f"  {bar(int(overall * 100), 100, width=40)}")


def print_field_completeness(r, comps, label):
    n = len(comps)
    r.print(f"\n  {label} ({n:,} companies):\n")
    r.print(f"  {'Field':15s} {'Filled':>8s} {'/ Total':>8s} {'Pct':>7s}  {'':20s}")
    r.print(f"  {'─' * 15} {'─' * 8} {'─' * 8} {'─' * 7}  {'─' * 20}")
    for f in FIELDS:
        cnt = sum(1 for c in comps if field_filled(c.get(f), f))
        r.print(f"  {f:15s} {cnt:>8,} {n:>8,} {pct(cnt, n):>7s}  {bar(cnt, n)}")


def print_sector_dist(r, comps, label):
    n = len(comps)
    sectors = defaultdict(int)
    for c in comps:
        sectors[c.get("sector") or "NULL"] += 1
    r.print(f"\n  {label} ({n:,} companies):\n")
    r.print(f"  {'Sector':25s} {'Count':>8s} {'Pct':>7s}  {'':20s}")
    r.print(f"  {'─' * 25} {'─' * 8} {'─' * 7}  {'─' * 20}")
    for sector, cnt in sorted(sectors.items(), key=lambda x: -x[1]):
        r.print(f"  {sector:25s} {cnt:>8,} {pct(cnt, n):>7s}  {bar(cnt, n)}")


def main():
    conn = get_connection()
    r = Report()

    now = datetime.now()
    r.print()
    r.print("=" * 78)
    r.print("  ATHENA — Data Quality Report (v2)")
    r.print(f"  Generated: {now.strftime('%Y-%m-%d %H:%M')}")
    r.print("=" * 78)

    # ── Load all data ──
    companies = [dict(c) for c in conn.execute("SELECT * FROM companies").fetchall()]
    total = len(companies)
    r.print(f"\n  Total companies: {total:,}")

    # Build primary source mapping
    source_companies = defaultdict(list)
    company_source = {}
    company_all_sources = defaultdict(set)

    rows = conn.execute("""
        SELECT company_id, source_name FROM signals
    """).fetchall()
    for row in rows:
        company_all_sources[row["company_id"]].add(row["source_name"])

    # Primary source = earliest signal
    rows = conn.execute("""
        SELECT c.id, s.source_name
        FROM companies c
        JOIN signals s ON s.company_id = c.id
        GROUP BY c.id
        HAVING s.source_name = (
            SELECT s2.source_name FROM signals s2
            WHERE s2.company_id = c.id
            ORDER BY s2.detected_at ASC LIMIT 1
        )
    """).fetchall()
    for row in rows:
        company_source[row["id"]] = row["source_name"]

    for c in companies:
        src = company_source.get(c["id"], "Unknown")
        source_companies[src].append(c)

    # ── Compute source scores ──
    source_scores = {}
    for src, comps in source_companies.items():
        source_scores[src] = source_avg_fill(comps)

    # ── Assign tiers ──
    source_tiers = {}
    for src, score in source_scores.items():
        if score >= TIER_1_THRESHOLD:
            source_tiers[src] = 1
        elif score >= TIER_2_THRESHOLD:
            source_tiers[src] = 2
        else:
            source_tiers[src] = 3

    # ── Add data_tier column to DB ──
    r.print("\n  Adding data_tier column to companies table...")
    try:
        conn.execute("SELECT data_tier FROM companies LIMIT 1")
    except Exception:
        conn.execute("ALTER TABLE companies ADD COLUMN data_tier INTEGER DEFAULT 3")
        conn.commit()

    # Assign tier per company (best tier from any source)
    tier_updates = {1: 0, 2: 0, 3: 0}
    for c in companies:
        sources = company_all_sources.get(c["id"], set())
        best_tier = 3
        for src in sources:
            t = source_tiers.get(src, 3)
            best_tier = min(best_tier, t)
        conn.execute("UPDATE companies SET data_tier = ? WHERE id = ?",
                      (best_tier, c["id"]))
        c["data_tier"] = best_tier
        tier_updates[best_tier] += 1
    conn.commit()

    r.print(f"  Assigned tiers: T1={tier_updates[1]:,}  T2={tier_updates[2]:,}  T3={tier_updates[3]:,}")

    # ── Split datasets ──
    curated = [c for c in companies
               if company_source.get(c["id"]) != "EU-Startups"]
    eu_startups = [c for c in companies
                   if company_source.get(c["id"]) == "EU-Startups"]

    tier1_comps = [c for c in companies if c["data_tier"] == 1]
    tier2_comps = [c for c in companies if c["data_tier"] == 2]
    tier3_comps = [c for c in companies if c["data_tier"] == 3]

    # ── Duplication rate ──
    merged_count = 206
    review_csv = os.path.join(os.path.dirname(__file__), "..", "data", "duplicate_review.csv")
    review_groups = 0
    review_companies = 0
    if os.path.exists(review_csv):
        with open(review_csv) as f:
            reader = csv.DictReader(f)
            groups_seen = set()
            for row in reader:
                review_companies += 1
                groups_seen.add(row["group_id"])
            review_groups = len(groups_seen)
    original_count = total + merged_count
    dup_rate = (merged_count + review_groups) / original_count

    # ════════════════════════════════════════════════════════
    # PART A: CURATED SOURCES ONLY (excl. EU-Startups)
    # ════════════════════════════════════════════════════════
    r.print()
    r.print("╔" + "═" * 76 + "╗")
    r.print("║  PART A: CURATED / VERIFIED SOURCES (excluding EU-Startups)" +
            " " * 15 + "║")
    r.print("╚" + "═" * 76 + "╝")
    r.print(f"\n  Companies: {len(curated):,}  (from {len(source_companies) - 1} sources)")

    print_field_completeness(r, curated, "Field completeness — Curated only")
    print_sector_dist(r, curated, "Sector distribution — Curated only")

    overall_c, components_c = compute_quality_score(curated, dup_rate)
    r.print(f"\n  Quality Score — Curated only:\n")
    print_quality_table(r, overall_c, components_c)

    # ════════════════════════════════════════════════════════
    # PART B: ALL SOURCES (incl. EU-Startups)
    # ════════════════════════════════════════════════════════
    r.print()
    r.print("╔" + "═" * 76 + "╗")
    r.print("║  PART B: ALL SOURCES (including EU-Startups)" +
            " " * 30 + "║")
    r.print("╚" + "═" * 76 + "╝")
    r.print(f"\n  Companies: {total:,}  (from {len(source_companies)} sources)")

    print_field_completeness(r, companies, "Field completeness — All sources")
    print_sector_dist(r, companies, "Sector distribution — All sources")

    overall_a, components_a = compute_quality_score(companies, dup_rate)
    r.print(f"\n  Quality Score — All sources:\n")
    print_quality_table(r, overall_a, components_a)

    # ════════════════════════════════════════════════════════
    # SIDE-BY-SIDE COMPARISON
    # ════════════════════════════════════════════════════════
    r.print()
    r.print("╔" + "═" * 76 + "╗")
    r.print("║  SIDE-BY-SIDE COMPARISON" +
            " " * 51 + "║")
    r.print("╚" + "═" * 76 + "╝")

    r.print(f"\n  {'Metric':30s} {'Curated':>12s} {'All Sources':>12s} {'Delta':>10s}")
    r.print(f"  {'─' * 30} {'─' * 12} {'─' * 12} {'─' * 10}")

    comparisons = [
        ("Companies", len(curated), total),
        ("Field completeness",
         components_c["field_completeness"] * 100,
         components_a["field_completeness"] * 100),
        ("Sector coverage",
         components_c["sector_coverage"] * 100,
         components_a["sector_coverage"] * 100),
        ("Stage coverage",
         components_c["stage_coverage"] * 100,
         components_a["stage_coverage"] * 100),
        ("Data freshness",
         components_c["data_freshness"] * 100,
         components_a["data_freshness"] * 100),
        ("OVERALL SCORE", overall_c * 100, overall_a * 100),
    ]
    for label, curated_val, all_val in comparisons:
        delta = curated_val - all_val
        if label == "Companies":
            r.print(f"  {label:30s} {curated_val:>11,} {all_val:>11,} "
                    f"{delta:>+10,}")
        else:
            r.print(f"  {label:30s} {curated_val:>11.1f}% {all_val:>11.1f}% "
                    f"{delta:>+9.1f}%")

    # ════════════════════════════════════════════════════════
    # SOURCE TIER CLASSIFICATION
    # ════════════════════════════════════════════════════════
    r.print()
    r.print("╔" + "═" * 76 + "╗")
    r.print("║  SOURCE TIER CLASSIFICATION" +
            " " * 48 + "║")
    r.print("╚" + "═" * 76 + "╝")

    for tier_num, tier_label, threshold_desc in [
        (1, "TIER 1 — Verified", "90%+ completeness"),
        (2, "TIER 2 — Standard", "60-90% completeness"),
        (3, "TIER 3 — Discovery", "<60% completeness"),
    ]:
        tier_sources = [(s, source_scores[s], len(source_companies[s]))
                        for s, t in source_tiers.items() if t == tier_num]
        tier_sources.sort(key=lambda x: -x[1])

        r.print(f"\n  {tier_label} ({threshold_desc}):")
        r.print(f"  {'Source':25s} {'Fill%':>7s} {'Count':>7s}")
        r.print(f"  {'─' * 25} {'─' * 7} {'─' * 7}")
        for src, score, cnt in tier_sources:
            r.print(f"  {src:25s} {score * 100:>6.1f}% {cnt:>7,}")

    # ════════════════════════════════════════════════════════
    # QUALITY METRICS PER TIER
    # ════════════════════════════════════════════════════════
    r.print()
    r.print("╔" + "═" * 76 + "╗")
    r.print("║  QUALITY METRICS PER TIER" +
            " " * 50 + "║")
    r.print("╚" + "═" * 76 + "╝")

    for tier_num, tier_label, comps in [
        (1, "Tier 1 — Verified", tier1_comps),
        (2, "Tier 2 — Standard", tier2_comps),
        (3, "Tier 3 — Discovery", tier3_comps),
    ]:
        n = len(comps)
        if n == 0:
            continue
        r.print(f"\n  {tier_label} ({n:,} companies)")
        r.print(f"  {'─' * 60}")

        # Field completeness
        r.print(f"  {'Field':15s} {'Pct':>7s}  {'':20s}")
        for f in FIELDS:
            cnt = sum(1 for c in comps if field_filled(c.get(f), f))
            r.print(f"  {f:15s} {pct(cnt, n):>7s}  {bar(cnt, n)}")

        # Sector
        other = sum(1 for c in comps if c.get("sector") == "Other")
        classified = n - other
        r.print(f"  Sector classified: {pct(classified, n):>7s}  "
                f"{bar(classified, n)}")

        # Quality score
        ov, comp = compute_quality_score(comps, dup_rate)
        r.print(f"  Quality score:     {ov * 100:>6.1f}%  "
                f"{bar(int(ov * 100), 100)}")

    # ════════════════════════════════════════════════════════
    # TIER SUMMARY TABLE
    # ════════════════════════════════════════════════════════
    r.print()
    r.print(f"  {'Tier':22s} {'Count':>8s} {'Fill%':>8s} {'Sector%':>8s} {'Score':>8s}")
    r.print(f"  {'─' * 22} {'─' * 8} {'─' * 8} {'─' * 8} {'─' * 8}")
    for tier_num, tier_label, comps in [
        (1, "T1 Verified", tier1_comps),
        (2, "T2 Standard", tier2_comps),
        (3, "T3 Discovery", tier3_comps),
    ]:
        n = len(comps)
        fill = source_avg_fill(comps) * 100
        other = sum(1 for c in comps if c.get("sector") == "Other")
        sector_pct = (n - other) / n * 100 if n > 0 else 0
        ov, _ = compute_quality_score(comps, dup_rate)
        r.print(f"  {tier_label:22s} {n:>8,} {fill:>7.1f}% {sector_pct:>7.1f}% "
                f"{ov * 100:>7.1f}%")

    # ════════════════════════════════════════════════════════
    # PRECISION SAMPLE + RECALL + DUPLICATION (unchanged)
    # ════════════════════════════════════════════════════════
    r.print()
    r.print("╔" + "═" * 76 + "╗")
    r.print("║  DUPLICATION RATE" + " " * 58 + "║")
    r.print("╚" + "═" * 76 + "╝")

    r.print(f"\n  Original company count:    {original_count:>8,}")
    r.print(f"  Obvious duplicates merged: {merged_count:>8}  ({pct(merged_count, original_count)})")
    r.print(f"  Suspected dupes in review: {review_groups:>8} groups ({review_companies:,} entries)")
    r.print(f"  Current company count:     {total:>8,}")
    r.print(f"  Estimated duplication rate: {dup_rate * 100:.1f}%")

    r.print()
    r.print("╔" + "═" * 76 + "╗")
    r.print("║  RECALL ESTIMATE" + " " * 59 + "║")
    r.print("╚" + "═" * 76 + "╝")

    r.print(f"\n  {'Source':25s} {'Known':>7s} {'Captured':>8s} {'Recall':>8s}  {'':20s}")
    r.print(f"  {'─' * 25} {'─' * 7} {'─' * 8} {'─' * 8}  {'─' * 20}")
    for src, known in sorted(KNOWN_TOTALS.items(), key=lambda x: -x[1]):
        captured = len(source_companies.get(src, []))
        recall = min(captured / known, 1.0) if known > 0 else 0
        r.print(f"  {src:25s} {known:>7} {captured:>8} {recall * 100:>7.0f}%  "
                f"{bar(captured, known)}")

    r.print()
    r.print("╔" + "═" * 76 + "╗")
    r.print("║  PRECISION SAMPLE" + " " * 58 + "║")
    r.print("╚" + "═" * 76 + "╝")

    sample_rows = conn.execute("""
        SELECT c.id, c.name, c.sector, c.geography, c.city, c.website, c.stage,
               (SELECT s.source_name FROM signals s
                WHERE s.company_id = c.id LIMIT 1) AS source
        FROM companies c
        WHERE c.description IS NOT NULL AND c.description != ''
          AND c.description NOT LIKE '%Detected via%'
          AND c.sector IS NOT NULL AND c.sector != '' AND c.sector != 'Other'
          AND c.geography IS NOT NULL AND c.geography NOT IN ('', 'Unknown', 'Europe')
          AND c.city IS NOT NULL AND c.city != ''
          AND c.website IS NOT NULL AND c.website != ''
          AND c.stage IS NOT NULL AND c.stage NOT IN ('', 'Unknown', 'NA')
        ORDER BY RANDOM()
        LIMIT 50
    """).fetchall()

    with open(SAMPLE_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "sector", "geography", "city", "website", "stage", "source"])
        for row in sample_rows:
            writer.writerow([
                row["name"], row["sector"], row["geography"],
                row["city"], row["website"], row["stage"], row["source"],
            ])

    r.print(f"\n  Exported {len(sample_rows)} fully-complete companies to {SAMPLE_PATH}")
    r.print(f"\n  {'Name':25s} {'Sector':15s} {'Geo':15s} {'City':15s} {'Source':15s}")
    r.print(f"  {'─' * 25} {'─' * 15} {'─' * 15} {'─' * 15} {'─' * 15}")
    for row in sample_rows[:10]:
        r.print(f"  {row['name'][:25]:25s} {row['sector'][:15]:15s} "
                f"{row['geography'][:15]:15s} {row['city'][:15]:15s} "
                f"{(row['source'] or '?')[:15]:15s}")

    r.print()
    r.print("=" * 78)

    conn.close()
    r.save(REPORT_PATH)
    print(f"\n  Report saved to: {REPORT_PATH}")
    print(f"  Precision sample: {SAMPLE_PATH}")
    print()


if __name__ == "__main__":
    main()
