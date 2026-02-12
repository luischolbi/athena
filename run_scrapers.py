"""
Athena — Full pipeline: scrape, match, score.

Usage:
    python run_scrapers.py           # Run everything
    python run_scrapers.py --quick   # Real-time scrapers only (HN, PH, RSS)
"""

import argparse
import subprocess
import sys
import os
import time
from datetime import datetime

# Ensure we're running from the project root
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_ROOT)

# Add project root to path so database imports work
sys.path.insert(0, PROJECT_ROOT)

from database.database import init_db, get_connection

SCRAPERS = [
    {
        "name": "HackerNews",
        "cmd": [sys.executable, "scrapers/hackernews.py", "--skip-profiles"],
        "signal_source": "HackerNews",
        "layer": "realtime",
    },
    {
        "name": "Venture Kick",
        "cmd": [sys.executable, "scrapers/venturekick.py", "--resume"],
        "signal_source": "Venture Kick",
        "layer": "curated",
    },
    {
        "name": "ETH AI Center",
        "cmd": [sys.executable, "scrapers/eth_ai_center.py"],
        "signal_source": "ETH AI Center",
        "layer": "curated",
    },
    {
        "name": "Entrepreneur First",
        "cmd": [sys.executable, "scrapers/entrepreneur_first.py"],
        "signal_source": "Entrepreneur First",
        "layer": "curated",
    },
    {
        "name": "Seedcamp",
        "cmd": [sys.executable, "scrapers/seedcamp.py"],
        "signal_source": "Seedcamp",
        "layer": "curated",
    },
    {
        "name": "Cambridge Enterprise",
        "cmd": [sys.executable, "scrapers/cambridge_enterprise.py"],
        "signal_source": "Cambridge Enterprise",
        "layer": "curated",
    },
    {
        "name": "Imperial College",
        "cmd": [sys.executable, "scrapers/imperial_spinouts.py"],
        "signal_source": "Imperial College",
        "layer": "curated",
    },
    {
        "name": "Y Combinator",
        "cmd": [sys.executable, "scrapers/ycombinator.py"],
        "signal_source": "Y Combinator",
        "layer": "curated",
    },
    {
        "name": "ProductHunt",
        "cmd": [sys.executable, "scrapers/producthunt.py"],
        "signal_source": "ProductHunt",
        "layer": "realtime",
    },
    {
        "name": "RSS Feeds",
        "cmd": [sys.executable, "scrapers/rss_feeds.py"],
        "signal_source": "rss",
        "source_type": "rss",
        "layer": "realtime",
    },
]


def get_counts():
    """Return (total_companies, total_signals) from the database."""
    conn = get_connection()
    companies = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    signals = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
    conn.close()
    return companies, signals


def get_source_counts(source_name=None, source_type=None):
    """Return (signals, companies) for a given source."""
    conn = get_connection()
    if source_type:
        col, val = "source_type", source_type
    else:
        col, val = "source_name", source_name
    signals = conn.execute(
        f"SELECT COUNT(*) FROM signals WHERE {col} = ?", (val,)
    ).fetchone()[0]
    companies = conn.execute(
        f"SELECT COUNT(DISTINCT company_id) FROM signals WHERE {col} = ?",
        (val,),
    ).fetchone()[0]
    conn.close()
    return signals, companies


def run_scrapers(scrapers):
    """Run each scraper subprocess. Returns (results, failed) lists."""
    results = []
    failed = []

    for scraper in scrapers:
        name = scraper["name"]
        source = scraper["signal_source"]
        src_type = scraper.get("source_type")

        # Snapshot before
        sig_before, _ = get_source_counts(source, source_type=src_type)
        total_comp_before, _ = get_counts()

        print("-" * 50)
        print(f"  Running: {name}")
        print("-" * 50)

        env = os.environ.copy()
        env["PYTHONPATH"] = PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")

        start = time.time()
        try:
            result = subprocess.run(
                scraper["cmd"],
                cwd=PROJECT_ROOT,
                env=env,
                timeout=1800,
                capture_output=True,
                text=True,
            )
            elapsed = time.time() - start

            if result.stdout:
                for line in result.stdout.strip().split("\n"):
                    print(f"  {line}")

            if result.returncode != 0:
                print(f"\n  WARNING: {name} exited with code {result.returncode}")
                if result.stderr:
                    for line in result.stderr.strip().split("\n")[-5:]:
                        print(f"  STDERR: {line}")
                failed.append((name, f"exit code {result.returncode}"))

        except subprocess.TimeoutExpired:
            elapsed = time.time() - start
            print(f"  ERROR: {name} timed out after 30 minutes")
            failed.append((name, "timeout"))
        except Exception as e:
            elapsed = time.time() - start
            print(f"  ERROR: {name} failed: {e}")
            failed.append((name, str(e)))

        # Snapshot after
        sig_after, _ = get_source_counts(source, source_type=src_type)
        total_comp_after, _ = get_counts()
        new_signals = sig_after - sig_before
        new_companies = total_comp_after - total_comp_before

        results.append({
            "name": name,
            "layer": scraper["layer"],
            "new_signals": new_signals,
            "new_companies": new_companies,
            "total_signals": sig_after,
            "elapsed": elapsed,
        })

        print(f"\n  +{new_signals} signals, +{new_companies} companies "
              f"({elapsed:.0f}s)\n")

    return results, failed


def run_matcher():
    """Run dedup + cross-layer matching. Returns (dupes_merged, cross_matches)."""
    from scoring.matcher import find_potential_matches, find_cross_layer_matches

    print("-" * 50)
    print("  Running: Cross-Layer Matcher")
    print("-" * 50)

    merged = find_potential_matches()
    if merged:
        print(f"  Merged {len(merged)} duplicate(s):")
        for keep, removed, reason in merged:
            print(f"    \"{removed}\" -> \"{keep}\" ({reason})")
    else:
        print("  No duplicates found.")

    matches = find_cross_layer_matches()
    if matches:
        print(f"  Found {len(matches)} cross-layer match(es):")
        for m in matches:
            print(f"    {m['name']} ({m['sources']})")
    else:
        print("  No cross-layer matches yet.")

    print()
    return len(merged), len(matches)


def run_scorer():
    """Recalculate all heat scores. Returns dict of {score: count}."""
    from scoring.scorer import score_all_companies

    print("-" * 50)
    print("  Running: Heat Scorer")
    print("-" * 50)

    total = score_all_companies()
    print(f"  Scored {total} companies")
    print()

    conn = get_connection()
    dist = conn.execute("""
        SELECT heat_score, COUNT(*) AS cnt
        FROM companies GROUP BY heat_score ORDER BY heat_score
    """).fetchall()
    conn.close()
    return {row[0]: row[1] for row in dist}


def print_summary(results, failed, dupes_merged, cross_matches, score_dist, quick):
    """Print the final pipeline summary."""
    total_companies, total_signals = get_counts()

    # Build lookup for source totals
    source_totals = {}
    for r in results:
        source_totals[r["name"]] = r

    # All source names we want to show, with their display labels
    curated_sources = [
        ("Venture Kick", "Venture Kick"),
        ("ETH AI Center", "ETH AI Center"),
        ("Entrepreneur First", "Entrepreneur First"),
        ("Seedcamp", "Seedcamp"),
        ("Cambridge Enterprise", "Cambridge Enterprise"),
        ("Imperial College", "Imperial College"),
        ("Y Combinator", "Y Combinator"),
    ]
    realtime_sources = [
        ("HackerNews", "HackerNews"),
        ("ProductHunt", "ProductHunt"),
        ("RSS Feeds", "RSS Feeds"),
    ]

    # For curated sources we didn't run, get counts from DB
    def source_count(name):
        r = source_totals.get(name)
        if r:
            return r["total_signals"]
        # Wasn't run this time — pull from DB
        src_type = "rss" if name == "RSS Feeds" else None
        src_name = "rss" if name == "RSS Feeds" else name
        sigs, _ = get_source_counts(src_name, source_type=src_type)
        return sigs

    def was_failed(name):
        return any(f[0] == name for f in failed)

    def was_skipped(name):
        return name not in source_totals

    print()
    print("=" * 50)
    print("  ATHENA — Full Pipeline Complete")
    print("=" * 50)
    print()

    # Curated layer
    print("  CURATED LAYER (Programs):")
    for src_name, label in curated_sources:
        count = source_count(src_name)
        suffix = ""
        if was_failed(src_name):
            suffix = " (FAILED)"
        elif was_skipped(src_name):
            suffix = " (skipped)" if quick else ""
        print(f"    {label + ':':26s} {count:>4} companies{suffix}")

    print()

    # Realtime layer
    print("  REAL-TIME LAYER (Signals):")
    for src_name, label in realtime_sources:
        count = source_count(src_name)
        suffix = ""
        if was_failed(src_name):
            suffix = " (FAILED)"
        elif was_skipped(src_name):
            suffix = " (skipped)"
        print(f"    {label + ':':26s} {count:>4} signals{suffix}")

    print()

    # Matching
    print("  MATCHING:")
    print(f"    {'Duplicates merged:':26s} {dupes_merged:>4}")
    print(f"    {'Cross-layer matches:':26s} {cross_matches:>4}")

    print()

    # Scoring
    print("  SCORING:")
    for score in range(1, 11):
        count = score_dist.get(score, 0)
        if count > 0:
            print(f"    {'Score ' + str(score) + ':':26s} {count:>4} companies")

    print()

    # Totals
    print(f"  TOTAL: {total_companies} companies  |  {total_signals} signals")

    # Failures
    if failed:
        print()
        print(f"  WARNINGS: {len(failed)} scraper(s) had issues:")
        for name, reason in failed:
            print(f"    - {name}: {reason}")

    print()
    print("=" * 50)
    print(f"  Finished at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)


def main():
    parser = argparse.ArgumentParser(description="Athena pipeline")
    parser.add_argument(
        "--quick", action="store_true",
        help="Run only real-time scrapers (HN, ProductHunt, RSS)",
    )
    args = parser.parse_args()

    init_db()

    mode = "Quick (real-time only)" if args.quick else "Full"
    print()
    print("=" * 50)
    print(f"  ATHENA — {mode} Pipeline")
    print("=" * 50)
    print(f"  Started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # Filter scrapers
    if args.quick:
        active = [s for s in SCRAPERS if s["layer"] == "realtime"]
    else:
        active = SCRAPERS

    # 1. Run scrapers
    results, failed = run_scrapers(active)

    # 2. Run matcher
    dupes_merged, cross_matches = run_matcher()

    # 3. Run scorer
    score_dist = run_scorer()

    # 4. Print summary
    print_summary(results, failed, dupes_merged, cross_matches, score_dist, args.quick)


if __name__ == "__main__":
    main()
