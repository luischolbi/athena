#!/usr/bin/env python3
"""Scout Calibration: fuzzy-match 74 scout submissions against Athena DB."""

import sqlite3, csv, re, json, os, sys

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "athena.db")

# ── Scout submissions ────────────────────────────────────────────────
SCOUT_COMPANIES = [
    "Prefiro", "Mena-ai", "Evoclin", "Manukai", "Spotium", "Avlana",
    "Enlightra", "AeonSelf", "Rhovica Neuroimaging", "Prevision Medicine AG",
    "Tethys Robotics", "Softletics", "Axy", "AskUI", "VectorCat", "Ecomcoder",
    "Binabik", "Lemna Bio", "Prismus", "Lampsy", "Enigma Health", "TightValve",
    "Wellvector", "MotusMed", "Cardiovolt.ai", "AtriAI", "FireDrone", "Mappi",
    "Embodied AI", "Avientus", "Sagittal AI", "Ralio", "elenthos AI",
    "Fluencify", "Elutra", "Kodnyx", "Orthoped.ai", "Pulso", "Kymansis",
    "neurodAIgnostics", "imd BIOTECH", "Hephaistos", "Kirigami", "Her",
    "Kayba.ai", "Natively", "Hevelion", "gridual", "DemoSquare",
    "GetReadyWithMe (GRWM)", "Abstract", "TREASUREBOT", "ET.Verdict",
    "NeuroSpice", "Maven Health", "Med Arcade", "LoCo Quantum", "ItsNotAI",
    "AiAction", "StoreAutopilot", "Patentiv.AI", "sillage", "Reshape.systems",
    "Centinel Analytica GmbH i.G.", "SipNBite", "TalorAI", "SmartMV", "QENDRA",
    "ZuriQX", "SOLAYA", "Endre Tech", "optiverse", "Prysmic AI", "Scailab",
]

# Short / generic names that need exact (case-insensitive) match only
EXACT_ONLY = {"her", "abstract", "axy"}

LEGAL_SUFFIXES = re.compile(
    r"\s*\b(ag|sa|gmbh|ltd|limited|inc|srl|sàrl|sarl|s\.?a\.?s|"
    r"i\.?g\.?|corp|corporation|plc|llc|pty|bv|co)\b\.?",
    re.IGNORECASE,
)

# Domain-like suffixes: .ai, .io, .xyz (NOT .systems/.tech/.health — too aggressive)
DOMAIN_SUFFIX = re.compile(r"\.(ai|io|xyz)$", re.IGNORECASE)

def normalize(name: str) -> str:
    """Lowercase, strip legal suffixes, domain suffixes, and punctuation."""
    n = name.strip()
    # Strip parenthetical suffixes like "(Kickoff)", "(GRWM)"
    n = re.sub(r"\s*\([^)]*\)\s*$", "", n)
    # Strip domain-like suffixes BEFORE removing punctuation
    n = DOMAIN_SUFFIX.sub("", n)
    n = LEGAL_SUFFIXES.sub("", n)
    n = re.sub(r"[^a-z0-9 ]", "", n.lower()).strip()
    n = re.sub(r"\s+", " ", n)
    return n

def bigram_dice(a: str, b: str) -> float:
    """Bigram Dice coefficient."""
    if len(a) < 2 or len(b) < 2:
        return 1.0 if a == b else 0.0
    bg_a = {a[i:i+2] for i in range(len(a)-1)}
    bg_b = {b[i:i+2] for i in range(len(b)-1)}
    if not bg_a or not bg_b:
        return 0.0
    return 2 * len(bg_a & bg_b) / (len(bg_a) + len(bg_b))

def fuzz_ratio(a: str, b: str) -> float:
    """Simple ratio: 2*matching_chars / total_chars (sequence matcher style)."""
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio() * 100

def fuzz_partial_ratio(a: str, b: str) -> float:
    """Partial ratio: best ratio of shorter string in longer string."""
    from difflib import SequenceMatcher
    if len(a) > len(b):
        a, b = b, a
    if not a:
        return 0
    best = 0
    blocks = SequenceMatcher(None, a, b).get_matching_blocks()
    for block in blocks:
        start = max(0, block.b - block.a)
        end = start + len(a)
        substr = b[start:end]
        r = SequenceMatcher(None, a, substr).ratio() * 100
        if r > best:
            best = r
    return best

def priority_label(score):
    """Convert athena_score to priority label."""
    if score is None:
        return ""
    if score >= 4.0:
        return "High Priority"
    elif score >= 3.5:
        return "Worth Investigating"
    elif score >= 3.0:
        return "On Radar"
    else:
        return "Low Priority"

def get_tier(breakdown_json):
    """Extract program tier from breakdown JSON."""
    if not breakdown_json:
        return ""
    try:
        bd = json.loads(breakdown_json)
        # Check components for program info
        prog = bd.get("components", {}).get("program", {})
        tier_val = prog.get("tier", "")
        if tier_val:
            return f"Tier {tier_val}"
        return ""
    except (json.JSONDecodeError, AttributeError):
        return ""

def get_sources(conn, company_id):
    """Get distinct source names for a company."""
    cur = conn.execute(
        "SELECT DISTINCT source_name FROM signals WHERE company_id = ?",
        (company_id,),
    )
    sources = [r[0] for r in cur.fetchall() if r[0]]
    if not sources:
        # Check programs
        cur = conn.execute(
            "SELECT DISTINCT program_name FROM programs WHERE company_id = ?",
            (company_id,),
        )
        sources = [r[0] for r in cur.fetchall() if r[0]]
    return ", ".join(sources[:3]) if sources else ""


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Load all companies
    rows = conn.execute(
        "SELECT id, name, athena_score, athena_score_breakdown FROM companies"
    ).fetchall()

    # Build lookup: normalized_name -> list of (id, original_name, score, breakdown)
    db_entries = []
    for r in rows:
        db_entries.append({
            "id": r["id"],
            "name": r["name"],
            "norm": normalize(r["name"]),
            "score": r["athena_score"],
            "breakdown": r["athena_score_breakdown"],
        })

    # Also build exact lowercase lookup
    exact_lookup = {}
    for e in db_entries:
        key = e["name"].strip().lower()
        if key not in exact_lookup or (e["score"] or 0) > (exact_lookup[key]["score"] or 0):
            exact_lookup[key] = e

    print(f"  Loaded {len(db_entries)} companies from DB\n")

    results = []
    for scout_name in SCOUT_COMPANIES:
        # Clean the submission name
        clean = scout_name.strip()
        # Strip parenthetical suffixes for matching
        clean_base = re.sub(r"\s*\([^)]*\)\s*$", "", clean).strip()
        norm_scout = normalize(clean_base)

        is_exact_only = norm_scout in EXACT_ONLY or len(norm_scout) <= 3

        best_match = None
        best_score_val = 0

        if is_exact_only:
            # Exact match only (case-insensitive)
            key = clean_base.strip().lower()
            if key in exact_lookup:
                best_match = exact_lookup[key]
            else:
                # Try normalized
                for e in db_entries:
                    if e["norm"] == norm_scout:
                        if (e["score"] or 0) > best_score_val:
                            best_match = e
                            best_score_val = e["score"] or 0
        else:
            # Fuzzy matching
            # 1. Try exact normalized match first
            for e in db_entries:
                if e["norm"] == norm_scout:
                    if (e["score"] or 0) > best_score_val:
                        best_match = e
                        best_score_val = e["score"] or 0

            if not best_match:
                # 2. Space-stripped exact match
                #    "reshapesystems" == "reshape systems" → match
                ns_nospace = norm_scout.replace(" ", "")
                for e in db_entries:
                    en_nospace = e["norm"].replace(" ", "")
                    if ns_nospace and en_nospace == ns_nospace:
                        if (e["score"] or 0) > best_score_val:
                            best_match = e
                            best_score_val = e["score"] or 0

            if not best_match:
                # 3. Containment: one name starts with the other
                #    e.g. "endre tech" in "endre technologies"
                for e in db_entries:
                    en = e["norm"]
                    if not en or len(en) < 5:
                        continue
                    shorter_len = min(len(norm_scout), len(en))
                    longer_len = max(len(norm_scout), len(en))
                    if shorter_len < 5:
                        continue
                    ratio = shorter_len / longer_len
                    accepted = False

                    if en.startswith(norm_scout):
                        # DB name is longer (e.g. "endre technologies" starts with "endre tech")
                        scout_words = norm_scout.split()
                        if len(scout_words) >= 2 and ratio >= 0.45:
                            accepted = True
                        elif ratio >= 0.85:
                            accepted = True
                    elif norm_scout.startswith(en):
                        # Scout name is longer, DB name is shorter prefix
                        # Stricter: DB name must be >= 8 chars and high overlap
                        if len(en) >= 8 and ratio >= 0.75:
                            accepted = True

                    if accepted and (e["score"] or 0) > best_score_val:
                        best_match = e
                        best_score_val = e["score"] or 0

            if not best_match:
                # 4. Fuzzy: fuzz_ratio > 88 with strict guards
                candidates = []
                for e in db_entries:
                    en = e["norm"]
                    if not en or len(en) < 5:
                        continue
                    shorter = min(len(norm_scout), len(en))
                    longer = max(len(norm_scout), len(en))
                    len_ratio = shorter / longer

                    if len_ratio < 0.75:
                        continue

                    r = fuzz_ratio(norm_scout, en)
                    if r <= 88:
                        continue

                    dice = bigram_dice(norm_scout, en)
                    if dice < 0.65:
                        continue

                    candidates.append((r, dice, e))

                if candidates:
                    candidates.sort(key=lambda x: (-x[0], -x[1], -(x[2]["score"] or 0)))
                    best_match = candidates[0][2]

        # Build result row
        if best_match:
            athena_score = best_match["score"]
            plabel = priority_label(athena_score)
            tier = get_tier(best_match["breakdown"])
            source = get_sources(conn, best_match["id"])
            results.append({
                "scout": scout_name,
                "found": "Yes",
                "matched": best_match["name"],
                "athena_score": f"{athena_score:.1f}" if athena_score is not None else "—",
                "priority": plabel,
                "source": source,
                "tier": tier,
                "_score_num": athena_score or 0,
            })
        else:
            results.append({
                "scout": scout_name,
                "found": "No",
                "matched": "",
                "athena_score": "—",
                "priority": "",
                "source": "",
                "tier": "",
                "_score_num": None,
            })

    conn.close()

    # ── Print table ──────────────────────────────────────────────────
    print(f"{'#':>3}  {'Scout Submission':<35} {'Found?':<8} {'Matched DB Name':<35} {'Score':<7} {'Priority Label':<22} {'Source':<30} {'Tier':<8}")
    print(f"{'—'*3}  {'—'*35} {'—'*8} {'—'*35} {'—'*7} {'—'*22} {'—'*30} {'—'*8}")

    for i, r in enumerate(results, 1):
        marker = "✓" if r["found"] == "Yes" else "✗"
        print(
            f"{i:>3}  {r['scout']:<35} {marker:<8} {r['matched']:<35} "
            f"{r['athena_score']:<7} {r['priority']:<22} {r['source']:<30} {r['tier']:<8}"
        )

    # ── Summary ──────────────────────────────────────────────────────
    total = len(results)
    found = sum(1 for r in results if r["found"] == "Yes")
    not_found = total - found
    high = sum(1 for r in results if r["_score_num"] is not None and r["_score_num"] >= 4.0)
    investigate = sum(1 for r in results if r["_score_num"] is not None and 3.5 <= r["_score_num"] < 4.0)
    below = sum(1 for r in results if r["_score_num"] is not None and r["_score_num"] < 3.5)

    print(f"\n{'='*80}")
    print(f"  SUMMARY")
    print(f"{'='*80}")
    print(f"  {found}/{total} found in Athena ({found*100//total}%)")
    print(f"  {high} flagged as High Priority (≥4.0)")
    print(f"  {investigate} flagged as Worth Investigating (3.5–3.9)")
    print(f"  {below} scored below 3.5")
    print(f"  {not_found} not found at all")
    print(f"{'='*80}")

    # ── Save CSV ─────────────────────────────────────────────────────
    csv_path = os.path.join(os.path.dirname(__file__), "scout_calibration.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["scout_submission", "found_in_athena", "matched_db_name",
                     "athena_score", "priority_label", "source", "tier"])
        for r in results:
            w.writerow([r["scout"], r["found"], r["matched"],
                        r["athena_score"], r["priority"], r["source"], r["tier"]])
    print(f"\n  Saved: {csv_path}")


if __name__ == "__main__":
    main()
