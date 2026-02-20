"""
Athena — Full deduplication audit on the companies table.

Finds and merges obvious duplicates (Tier 1), exports fuzzy matches
for manual review (Tier 2).

Usage:
    python dedup_audit.py
"""

import sys
import os
import csv
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.database import get_connection
from scoring.matcher import (
    _normalize_name,
    _bigram_similarity,
    _extract_domain,
    _company_richness,
    _merge_companies,
    GENERIC_DOMAINS,
)
from scoring.scorer import score_all_companies


# ── Data Loading ──

def load_companies():
    """Load all companies with signal and program counts."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT c.*,
               COALESCE(sc.sig_count, 0) AS signals_count,
               COALESCE(pc.prog_count, 0) AS programs_count
        FROM companies c
        LEFT JOIN (
            SELECT company_id, COUNT(*) AS sig_count
            FROM signals GROUP BY company_id
        ) sc ON sc.company_id = c.id
        LEFT JOIN (
            SELECT company_id, COUNT(*) AS prog_count
            FROM programs GROUP BY company_id
        ) pc ON pc.company_id = c.id
        ORDER BY c.id
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Indexing ──

def build_indexes(companies):
    """Build domain and name-prefix indexes for efficient matching."""
    domain_index = defaultdict(list)
    prefix_index = defaultdict(list)

    for c in companies:
        # Domain index
        dom = _extract_domain(c["website"])
        if dom and dom not in GENERIC_DOMAINS:
            domain_index[dom].append(c)

        # Name prefix index (first 4 chars of normalized name)
        norm = _normalize_name(c["name"])
        if len(norm) >= 4:
            prefix_index[norm[:4]].append(c)

    return domain_index, prefix_index


# ── Duplicate Detection ──

def find_duplicates(companies, domain_index, prefix_index):
    """Find Tier 1 (auto-merge) and Tier 2 (manual review) duplicate groups.

    Returns (obvious_groups, fuzzy_groups) where each group is a list of
    (company_list, reason) tuples.
    """
    obvious = []   # Tier 1: auto-merge
    fuzzy = []     # Tier 2: manual CSV review
    seen_pairs = set()
    assigned = set()  # companies already in an obvious group

    def pair_key(a, b):
        return (min(a["id"], b["id"]), max(a["id"], b["id"]))

    def classify_pair(ca, cb):
        """Classify a company pair into tier 1, tier 2, or no match."""
        pk = pair_key(ca, cb)
        if pk in seen_pairs or ca["id"] == cb["id"]:
            return
        seen_pairs.add(pk)

        na = _normalize_name(ca["name"])
        nb = _normalize_name(cb["name"])
        dom_a = _extract_domain(ca["website"])
        dom_b = _extract_domain(cb["website"])

        # ── Tier 1: Obvious duplicates ──

        # Exact normalized name match
        if na == nb:
            _add_to_obvious(ca, cb, "exact normalized name")
            return

        # Same non-generic domain + reasonable name similarity
        if (dom_a and dom_b and dom_a == dom_b
                and dom_a not in GENERIC_DOMAINS):
            sim = _bigram_similarity(na, nb)
            if sim >= 0.7:
                _add_to_obvious(ca, cb, f"same domain ({dom_a}) + high name sim ({sim:.2f})")
                return
            elif sim >= 0.3:
                _add_to_fuzzy(ca, cb, f"same domain ({dom_a}) + name sim ({sim:.2f})")
                return
            else:
                _add_to_fuzzy(ca, cb, f"same domain ({dom_a}) — possible rebrand (sim {sim:.2f})")
                return

        # ── Tier 2: Fuzzy matches ──

        # Name containment with short extra
        if len(na) >= 6 and len(nb) >= 6:
            if na in nb and (len(nb) - len(na)) <= 5:
                _add_to_fuzzy(ca, cb, "name containment")
                return
            if nb in na and (len(na) - len(nb)) <= 5:
                _add_to_fuzzy(ca, cb, "name containment")
                return

        # High bigram similarity
        sim = _bigram_similarity(na, nb)
        if sim >= 0.7:
            _add_to_fuzzy(ca, cb, f"high name similarity ({sim:.2f})")
            return

    def _add_to_obvious(ca, cb, reason):
        # Try to merge into an existing group
        for group, _ in obvious:
            ids = {c["id"] for c in group}
            if ca["id"] in ids:
                if cb["id"] not in ids:
                    group.append(cb)
                    assigned.add(cb["id"])
                return
            if cb["id"] in ids:
                if ca["id"] not in ids:
                    group.append(ca)
                    assigned.add(ca["id"])
                return
        # New group
        obvious.append(([ca, cb], reason))
        assigned.add(ca["id"])
        assigned.add(cb["id"])

    def _add_to_fuzzy(ca, cb, reason):
        if ca["id"] in assigned or cb["id"] in assigned:
            return  # already handled as obvious
        for group, _ in fuzzy:
            ids = {c["id"] for c in group}
            if ca["id"] in ids:
                if cb["id"] not in ids:
                    group.append(cb)
                return
            if cb["id"] in ids:
                if ca["id"] not in ids:
                    group.append(ca)
                return
        fuzzy.append(([ca, cb], reason))

    # ── Pass 1: Domain groups ──
    for dom, dom_companies in domain_index.items():
        if len(dom_companies) < 2:
            continue
        for i in range(len(dom_companies)):
            for j in range(i + 1, len(dom_companies)):
                classify_pair(dom_companies[i], dom_companies[j])

    # ── Pass 2: Name prefix groups ──
    for prefix, bucket in prefix_index.items():
        if len(bucket) < 2:
            continue
        for i in range(len(bucket)):
            for j in range(i + 1, len(bucket)):
                classify_pair(bucket[i], bucket[j])

    # ── Pass 3: Description similarity (within same sector, unmatched only) ──
    matched_ids = set()
    for group, _ in obvious:
        matched_ids.update(c["id"] for c in group)
    for group, _ in fuzzy:
        matched_ids.update(c["id"] for c in group)

    sector_index = defaultdict(list)
    for c in companies:
        if c["id"] in matched_ids:
            continue
        desc = c.get("description") or ""
        if len(desc) < 50 or not c.get("sector"):
            continue
        sector_index[c["sector"]].append(c)

    for sector, sector_companies in sector_index.items():
        if len(sector_companies) < 2:
            continue
        for i in range(len(sector_companies)):
            desc_a = (sector_companies[i].get("description") or "")[:200]
            for j in range(i + 1, len(sector_companies)):
                pk = pair_key(sector_companies[i], sector_companies[j])
                if pk in seen_pairs:
                    continue
                seen_pairs.add(pk)
                desc_b = (sector_companies[j].get("description") or "")[:200]
                sim = _bigram_similarity(desc_a.lower(), desc_b.lower())
                if sim >= 0.6:
                    _add_to_fuzzy(
                        sector_companies[i], sector_companies[j],
                        f"similar description in {sector} (sim {sim:.2f})"
                    )

    return obvious, fuzzy


# ── Reporting ──

def print_report(obvious, fuzzy):
    """Print top duplicate groups and stats."""
    print("\n" + "=" * 64)
    print("  ATHENA — Deduplication Audit Report")
    print("=" * 64)

    # Tier 1
    total_obvious = sum(len(g) - 1 for g, _ in obvious)
    print(f"\n  Tier 1 — Obvious Duplicates: {len(obvious)} groups ({total_obvious} merges)")
    if obvious:
        show = obvious[:20]
        for i, (group, reason) in enumerate(show, 1):
            names = [c["name"] for c in group]
            richest = max(group, key=_company_richness)
            print(f"\n    [{i}] Keep: \"{richest['name']}\" (id={richest['id']})")
            for c in group:
                if c["id"] != richest["id"]:
                    print(f"        Merge: \"{c['name']}\" (id={c['id']})")
            print(f"        Reason: {reason}")
        if len(obvious) > 20:
            print(f"\n    ... and {len(obvious) - 20} more groups")

    # Tier 2
    total_fuzzy = sum(len(g) for g, _ in fuzzy)
    print(f"\n  Tier 2 — Fuzzy Matches (manual review): {len(fuzzy)} groups ({total_fuzzy} companies)")
    if fuzzy:
        show = fuzzy[:20]
        for i, (group, reason) in enumerate(show, 1):
            names = [f"\"{c['name']}\" (id={c['id']})" for c in group]
            print(f"\n    [{i}] {' | '.join(names)}")
            print(f"        Reason: {reason}")
        if len(fuzzy) > 20:
            print(f"\n    ... and {len(fuzzy) - 20} more groups")

    print()


# ── Auto-Merge ──

def merge_obvious(obvious_groups):
    """Auto-merge Tier 1 duplicates. Returns merge count."""
    count = 0
    for group, reason in obvious_groups:
        # Sort by richness descending — keep the richest
        ranked = sorted(group, key=_company_richness, reverse=True)
        keep = ranked[0]
        for remove in ranked[1:]:
            print(f"    MERGE  \"{remove['name']}\" (id={remove['id']})  "
                  f"→  \"{keep['name']}\" (id={keep['id']})  [{reason}]")
            _merge_companies(keep, remove)
            count += 1
    return count


# ── CSV Export ──

def export_fuzzy(fuzzy_groups, path):
    """Export Tier 2 matches to CSV for manual review."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "group_id", "company_id", "name", "website",
            "stage", "sector", "athena_score", "signals_count", "match_reason",
        ])
        for gid, (group, reason) in enumerate(fuzzy_groups, 1):
            for c in group:
                writer.writerow([
                    gid,
                    c["id"],
                    c["name"],
                    c.get("website") or "",
                    c.get("stage") or "",
                    c.get("sector") or "",
                    c.get("athena_score", 0),
                    c.get("signals_count", 0),
                    reason,
                ])


# ── Main ──

def main():
    print()
    print("=" * 64)
    print("  ATHENA — Deduplication Audit")
    print("=" * 64)

    # Load
    print("\n  Loading companies...")
    companies = load_companies()
    print(f"  Loaded {len(companies)} companies")

    # Index
    print("  Building indexes...")
    domain_index, prefix_index = build_indexes(companies)
    print(f"  Domain index: {len(domain_index)} unique domains")
    print(f"  Prefix index: {len(prefix_index)} unique prefixes")

    # Find duplicates
    print("  Scanning for duplicates...")
    obvious, fuzzy = find_duplicates(companies, domain_index, prefix_index)

    # Report
    print_report(obvious, fuzzy)

    # Auto-merge Tier 1
    if obvious:
        total_merges = sum(len(g) - 1 for g, _ in obvious)
        print(f"  Auto-merging {total_merges} obvious duplicates...")
        merged = merge_obvious(obvious)
        print(f"  Merged {merged} records")
    else:
        merged = 0

    # Re-score
    if merged > 0:
        print("\n  Re-scoring all companies...")
        scored = score_all_companies()
        print(f"  Scored {scored} companies")

    # Export Tier 2
    csv_path = os.path.join(os.path.dirname(__file__), "data", "duplicate_review.csv")
    if fuzzy:
        export_fuzzy(fuzzy, csv_path)
        print(f"\n  Exported {len(fuzzy)} fuzzy groups to {csv_path}")
    else:
        print("\n  No fuzzy matches to export.")

    # Final summary
    conn = get_connection()
    final_count = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    conn.close()

    print("\n" + "-" * 64)
    print(f"  Summary:")
    print(f"    Before:          {len(companies)} companies")
    print(f"    Obvious merges:  {merged}")
    print(f"    After:           {final_count} companies")
    print(f"    Fuzzy for review: {len(fuzzy)} groups")
    if fuzzy:
        print(f"    CSV:             {csv_path}")
    print("-" * 64)
    print()


if __name__ == "__main__":
    main()
