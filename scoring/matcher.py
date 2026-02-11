"""
Athena — Cross-layer matching and deduplication.

Finds companies that appear in both curated and real-time signal layers,
and merges fuzzy-duplicate entries that represent the same company stored
under slightly different names.

Usage:
    python -m scoring.matcher
"""

import sys
import os
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.database import get_connection
from scoring.scorer import score_all_companies, get_score_breakdown


# ── Helpers ──

# Names that are too generic/short to fuzzy-match on containment
MIN_CONTAINMENT_LEN = 6

# Legal suffixes stripped during normalization
LEGAL_SUFFIXES = (" ag", " sa", " gmbh", " ltd", " inc", " inc.", " co.",
                  " llc", " corp", " corp.")

# Generic hosting domains — never match on these
GENERIC_DOMAINS = {
    "github.io", "github.com", "gitlab.com", "vercel.app",
    "netlify.app", "herokuapp.com", "linkedin.com", "twitter.com",
    "facebook.com", "medium.com", "wordpress.com",
}


def log(msg):
    print(msg, flush=True)


def _normalize_name(name):
    """Lowercase, strip legal suffixes and punctuation."""
    n = name.lower().strip()
    for suffix in LEGAL_SUFFIXES:
        if n.endswith(suffix):
            n = n[:-len(suffix)].rstrip()
    return n.rstrip(" .,;:-–—")


def _is_likely_title(name):
    """Detect HN post titles that aren't company names."""
    lower = name.lower()
    # Starts with common HN title patterns
    title_starts = ("i built", "show hn", "launch hn", "ask hn",
                    "tell hn", "a ", "an ", "the ", "my ", "we ",
                    "how ", "why ", "if ", "what ")
    if any(lower.startswith(p) for p in title_starts):
        return True
    # Too many words to be a company name
    if len(name.split()) > 6:
        return True
    return False


def _extract_domain(url):
    """Extract the hostname from a URL, stripping www. Returns None if invalid."""
    if not url:
        return None
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host.lower() if host else None


def _bigram_similarity(a, b):
    """Character bigram similarity (Dice coefficient).

    Much more order-sensitive than character-set Jaccard.
    "teleport" vs "telleroo" → low score because bigrams differ.
    """
    if not a or not b or len(a) < 2 or len(b) < 2:
        return 0.0
    bigrams_a = {a[i:i+2] for i in range(len(a) - 1)}
    bigrams_b = {b[i:i+2] for i in range(len(b) - 1)}
    intersection = bigrams_a & bigrams_b
    total = len(bigrams_a) + len(bigrams_b)
    return (2 * len(intersection)) / total if total else 0.0


def _is_fuzzy_match(name_a, name_b, website_a, website_b):
    """Determine whether two companies are likely the same entity.

    Conservative matching — prefers false negatives over false positives.
    Returns (is_match: bool, reason: str).
    """
    # Skip HN post titles entirely
    if _is_likely_title(name_a) or _is_likely_title(name_b):
        return False, ""

    na = _normalize_name(name_a)
    nb = _normalize_name(name_b)

    # Exact normalized name (e.g. "Resmonics" == "Resmonics AG")
    if na == nb:
        return True, "exact name match (after suffix normalization)"

    # Domain match — strong signal, but exclude generic hosting domains
    dom_a = _extract_domain(website_a)
    dom_b = _extract_domain(website_b)
    if dom_a and dom_b and dom_a == dom_b and dom_a not in GENERIC_DOMAINS:
        # Names must share at least some similarity
        if _bigram_similarity(na, nb) >= 0.3:
            return True, f"same domain ({dom_a})"

    # Containment: "NovaMind" in "NovaMind AI", but only if the shorter
    # name is long enough to be meaningful (>= 6 chars) and the extra
    # part is small (a suffix like " AI", " Tech", " Labs")
    if len(na) >= MIN_CONTAINMENT_LEN and len(nb) >= MIN_CONTAINMENT_LEN:
        if na in nb:
            extra = len(nb) - len(na)
            if extra <= 5:
                return True, "name containment match"
        if nb in na:
            extra = len(na) - len(nb)
            if extra <= 5:
                return True, "name containment match"

    return False, ""


def _company_richness(company):
    """Score how much data a company record has (higher = more complete)."""
    score = 0
    if company["description"]:
        score += 2
    if company["website"]:
        score += 2
    if company["city"]:
        score += 1
    if company["sector"] and company["sector"] != "Other":
        score += 1
    if company["geography"] and company["geography"] not in ("Unknown", "Europe"):
        score += 1
    if company["stage"] and company["stage"] != "Unknown":
        score += 1
    return score


# ── Core Functions ──

def find_potential_matches():
    """Find and merge fuzzy-duplicate companies.

    Returns list of (kept_name, merged_name, reason) tuples.
    """
    conn = get_connection()
    companies = conn.execute(
        "SELECT * FROM companies ORDER BY id"
    ).fetchall()
    conn.close()

    companies = [dict(r) for r in companies]
    merged = []
    deleted_ids = set()

    # Build index by normalized name prefix (first 4 chars) to avoid O(n^2)
    from collections import defaultdict
    buckets = defaultdict(list)
    for c in companies:
        norm = _normalize_name(c["name"])
        if len(norm) >= 4:
            buckets[norm[:4]].append(c)

    # Also build domain index for website-based matching
    domain_index = defaultdict(list)
    for c in companies:
        dom = _extract_domain(c["website"])
        if dom and dom not in GENERIC_DOMAINS:
            domain_index[dom].append(c)

    seen_pairs = set()

    def try_merge(ca, cb):
        """Attempt to merge ca and cb. Returns True if merged."""
        if ca["id"] == cb["id"]:
            return False
        pair = (min(ca["id"], cb["id"]), max(ca["id"], cb["id"]))
        if pair in seen_pairs:
            return False
        seen_pairs.add(pair)

        if ca["id"] in deleted_ids or cb["id"] in deleted_ids:
            return False

        is_match, reason = _is_fuzzy_match(
            ca["name"], cb["name"], ca["website"], cb["website"]
        )
        if not is_match:
            return False

        # Decide which to keep (the one with more data)
        if _company_richness(ca) >= _company_richness(cb):
            keep, remove = ca, cb
        else:
            keep, remove = cb, ca

        _merge_companies(keep, remove)
        deleted_ids.add(remove["id"])
        merged.append((keep["name"], remove["name"], reason))
        return True

    # Check name-based buckets
    for bucket in buckets.values():
        for i in range(len(bucket)):
            for j in range(i + 1, len(bucket)):
                try_merge(bucket[i], bucket[j])

    # Check domain-based matches
    for dom_companies in domain_index.values():
        for i in range(len(dom_companies)):
            for j in range(i + 1, len(dom_companies)):
                try_merge(dom_companies[i], dom_companies[j])

    return merged


def _merge_companies(keep, remove):
    """Merge `remove` into `keep`: move signals/programs, fill gaps, delete."""
    conn = get_connection()

    # Fill missing fields on the keeper from the duplicate
    updates = {}
    for field in ("description", "website", "city"):
        if not keep.get(field) and remove.get(field):
            updates[field] = remove[field]
    if keep.get("sector") in (None, "Other") and remove.get("sector") not in (None, "Other"):
        updates["sector"] = remove["sector"]
    if keep.get("geography") in (None, "Unknown") and remove.get("geography") not in (None, "Unknown"):
        updates["geography"] = remove["geography"]

    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [keep["id"]]
        conn.execute(f"UPDATE companies SET {set_clause} WHERE id = ?", values)

    # Move signals and programs to the keeper
    conn.execute(
        "UPDATE signals SET company_id = ? WHERE company_id = ?",
        (keep["id"], remove["id"]),
    )
    conn.execute(
        "UPDATE programs SET company_id = ? WHERE company_id = ?",
        (keep["id"], remove["id"]),
    )

    # Delete the duplicate company
    conn.execute("DELETE FROM companies WHERE id = ?", (remove["id"],))
    conn.commit()
    conn.close()


def find_cross_layer_matches():
    """Find companies that have signals in both curated and realtime layers.

    Returns list of dicts with company info and their signals.
    """
    conn = get_connection()
    rows = conn.execute("""
        SELECT c.id, c.name, c.sector, c.geography, c.heat_score,
               GROUP_CONCAT(DISTINCT s.source_name) AS sources,
               GROUP_CONCAT(DISTINCT s.signal_layer) AS layers,
               COUNT(DISTINCT s.source_name) AS source_count
        FROM companies c
        JOIN signals s ON s.company_id = c.id
        GROUP BY c.id
        HAVING COUNT(DISTINCT s.signal_layer) > 1
        ORDER BY source_count DESC, c.name
    """).fetchall()

    matches = []
    for r in rows:
        sigs = conn.execute(
            "SELECT source_name, signal_layer, title FROM signals WHERE company_id = ?",
            (r["id"],)
        ).fetchall()
        matches.append({
            "id": r["id"],
            "name": r["name"],
            "sector": r["sector"],
            "geography": r["geography"],
            "heat_score": r["heat_score"],
            "sources": r["sources"],
            "signals": [dict(s) for s in sigs],
        })

    conn.close()
    return matches


# ── Main ──

def main():
    print()
    print("=" * 60)
    print("  ATHENA — Cross-Layer Matcher")
    print("=" * 60)

    # Phase 1: Fuzzy deduplication
    print("\n  Phase 1: Finding fuzzy duplicates...")
    merged = find_potential_matches()
    if merged:
        print(f"  Merged {len(merged)} duplicate(s):\n")
        for keep, removed, reason in merged:
            print(f"    MERGE  \"{removed}\"  →  \"{keep}\"")
            print(f"           reason: {reason}")
    else:
        print("  No duplicates found.")

    # Phase 2: Cross-layer matches
    print(f"\n  Phase 2: Finding cross-layer matches...")
    matches = find_cross_layer_matches()
    if matches:
        print(f"  Found {len(matches)} cross-layer match(es):\n")
        for m in matches:
            sector = m["sector"] or "?"
            geo = m["geography"] or "?"
            print(f"    {m['name']}")
            print(f"      {sector} | {geo} | sources: {m['sources']}")
            curated = [s for s in m["signals"] if s["signal_layer"] == "curated"]
            realtime = [s for s in m["signals"] if s["signal_layer"] == "realtime"]
            if curated:
                print(f"      curated ({len(curated)}):")
                for s in curated[:3]:
                    print(f"        - [{s['source_name']}] {s['title']}")
                if len(curated) > 3:
                    print(f"        ... and {len(curated) - 3} more")
            if realtime:
                print(f"      realtime ({len(realtime)}):")
                for s in realtime[:3]:
                    print(f"        - [{s['source_name']}] {s['title']}")
                if len(realtime) > 3:
                    print(f"        ... and {len(realtime) - 3} more")
            print()
    else:
        print("  No cross-layer matches found yet.")
        print("  (These emerge when a company appears in both an accelerator")
        print("   AND a real-time source like HackerNews or press.)")

    # Phase 3: Recalculate scores
    print(f"\n  Phase 3: Recalculating heat scores...")
    total = score_all_companies()
    print(f"  Scored {total} companies")

    # Show updated distribution
    conn = get_connection()
    dist = conn.execute("""
        SELECT heat_score, COUNT(*) AS cnt
        FROM companies
        GROUP BY heat_score
        ORDER BY heat_score
    """).fetchall()
    conn.close()

    print("\n  Updated Score Distribution:")
    print(f"  {'Score':>7s}  {'Count':>6s}")
    print(f"  {'─' * 7}  {'─' * 6}")
    for row in dist:
        print(f"  {row[0]:>7}  {row[1]:>6}")

    # Show cross-layer matches with score breakdowns
    if matches:
        print("\n  Cross-Layer Score Breakdowns:")
        print(f"  {'─' * 56}")
        for m in matches:
            bd = get_score_breakdown(m["id"])
            print(f"\n  [{bd['total']}] {m['name']}")
            for reason in bd["reasons"]:
                print(f"      - {reason}")

    print()
    print("=" * 60)
    print()


if __name__ == "__main__":
    main()
