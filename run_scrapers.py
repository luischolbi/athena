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
        "name": "University of Oxford",
        "cmd": [sys.executable, "scrapers/oxford.py"],
        "signal_source": "University of Oxford",
        "layer": "curated",
    },
    {
        "name": "EPFL",
        "cmd": [sys.executable, "scrapers/epfl.py"],
        "signal_source": "EPFL",
        "layer": "curated",
    },
    {
        "name": "DTU Science Park",
        "cmd": [sys.executable, "scrapers/dtu.py"],
        "signal_source": "DTU Science Park",
        "layer": "curated",
    },
    {
        "name": "KTH Innovation",
        "cmd": [sys.executable, "scrapers/kth.py"],
        "signal_source": "KTH Innovation",
        "layer": "curated",
    },
    {
        "name": "University of Zurich",
        "cmd": [sys.executable, "scrapers/uzh.py"],
        "signal_source": "University of Zurich",
        "layer": "curated",
    },
    {
        "name": "Antler",
        "cmd": [sys.executable, "scrapers/antler.py"],
        "signal_source": "Antler",
        "layer": "curated",
    },
    {
        "name": "EWOR",
        "cmd": [sys.executable, "scrapers/ewor.py"],
        "signal_source": "EWOR",
        "layer": "curated",
    },
    {
        "name": "Techstars",
        "cmd": [sys.executable, "scrapers/techstars.py"],
        "signal_source": "Techstars",
        "layer": "curated",
    },
    {
        "name": "500 Global",
        "cmd": [sys.executable, "scrapers/fivehundred_global.py"],
        "signal_source": "500 Global",
        "layer": "curated",
    },
    {
        "name": "Swedish Accelerators",
        "cmd": [sys.executable, "scrapers/swedish_accelerators.py"],
        "signal_source": "Swedish Accelerators",
        "source_type": "swedish_accelerator",
        "layer": "curated",
    },
    {
        "name": "EU-Startups",
        "cmd": [sys.executable, "scrapers/eu_startups.py", "--since", "2026-01-01"],
        "signal_source": "EU-Startups",
        "layer": "realtime",
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
    """Recalculate all Athena Scores. Returns dict of {priority: count}."""
    from scoring.scorer import score_all_companies

    print("-" * 50)
    print("  Running: Athena Scorer")
    print("-" * 50)

    total = score_all_companies()
    print(f"  Scored {total} companies")
    print()

    conn = get_connection()
    dist = {}
    for label, low, high in [("High Priority", 4.0, 5.1),
                              ("Worth Investigating", 3.5, 4.0),
                              ("On Radar", 3.0, 3.5),
                              ("Low Priority", 0, 3.0)]:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM companies WHERE athena_score >= ? AND athena_score < ?",
            (low, high),
        ).fetchone()[0]
        dist[label] = cnt
    conn.close()
    return dist


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
        ("University of Oxford", "University of Oxford"),
        ("EPFL", "EPFL"),
        ("DTU Science Park", "DTU Science Park"),
        ("KTH Innovation", "KTH Innovation"),
        ("University of Zurich", "University of Zurich"),
        ("Antler", "Antler"),
        ("EWOR", "EWOR"),
        ("Techstars", "Techstars"),
        ("500 Global", "500 Global"),
        ("Swedish Accelerators", "Swedish Accelerators"),
    ]
    realtime_sources = [
        ("HackerNews", "HackerNews"),
        ("EU-Startups", "EU-Startups"),
        ("ProductHunt", "ProductHunt"),
        ("RSS Feeds", "RSS Feeds"),
    ]

    # For curated sources we didn't run, get counts from DB
    def source_count(name):
        r = source_totals.get(name)
        if r:
            return r["total_signals"]
        # Wasn't run this time — pull from DB
        type_map = {"RSS Feeds": "rss", "Swedish Accelerators": "swedish_accelerator"}
        src_type = type_map.get(name)
        src_name = type_map.get(name, name)
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
    print("  ATHENA SCORE:")
    for tier, count in score_dist.items():
        if count > 0:
            print(f"    {tier + ':':26s} {count:>4} companies")

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

    # 0. Import university reference data (full runs only)
    if not args.quick:
        print("-" * 50)
        print("  Running: University Reference Import")
        print("-" * 50)
        env = os.environ.copy()
        env["PYTHONPATH"] = PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        result = subprocess.run(
            [sys.executable, "import_universities.py"],
            cwd=PROJECT_ROOT, env=env, capture_output=True, text=True,
        )
        if result.stdout:
            for line in result.stdout.strip().split("\n"):
                print(f"  {line}")
        if result.returncode != 0 and result.stderr:
            for line in result.stderr.strip().split("\n")[-3:]:
                print(f"  STDERR: {line}")
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
