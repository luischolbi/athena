"""
Athena — Freshness scoring for all companies.

For Tier 1 + Tier 2 companies with websites: checks if domain is alive.
For all companies: checks last signal date.

Freshness score (1-5):
  5: Active website + signal in last 3 months
  4: Active website + signal 3-12 months ago
  3: Active website + signal 12+ months ago
  2: Dead/no website + recent signal
  1: Dead/no website + stale signal

Usage:
    python freshness_score.py
"""

import sys
import os
import time
import ssl
import json
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen
from urllib.parse import urlparse
from urllib.error import URLError, HTTPError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.database import get_connection

TIMEOUT = 5
MAX_WORKERS = 20
BATCH_COMMIT = 200

# Date boundaries
TODAY = date.today()
THREE_MONTHS_AGO = TODAY - timedelta(days=90)
TWELVE_MONTHS_AGO = TODAY - timedelta(days=365)


def check_website(url):
    """Check if a website is alive. Returns (status_code, redirect_url, error)."""
    if not url:
        return None, None, "no_url"

    if not url.startswith("http"):
        url = "https://" + url
    elif url.startswith("http://"):
        url = "https://" + url[7:]

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; AthenaBot/1.0)",
        "Accept": "text/html",
    })
    req.method = "HEAD"

    try:
        resp = urlopen(req, timeout=TIMEOUT, context=ctx)
        final_url = resp.url
        redirect = final_url if final_url != url else None
        return resp.status, redirect, None
    except HTTPError as e:
        return e.code, None, str(e.reason)
    except (URLError, OSError, Exception) as e:
        # Try GET as fallback (some servers block HEAD)
        req.method = "GET"
        try:
            resp = urlopen(req, timeout=TIMEOUT, context=ctx)
            final_url = resp.url
            redirect = final_url if final_url != url else None
            resp.read(1024)  # read minimal to confirm
            return resp.status, redirect, None
        except HTTPError as e2:
            return e2.code, None, str(e2.reason)
        except Exception as e2:
            err = str(e2)[:80]
            return None, None, err


def classify_website(status_code, error):
    """Classify website status as 'active', 'redirect', 'dead', or 'unknown'."""
    if status_code is None:
        if error == "no_url":
            return "no_website"
        return "dead"
    if 200 <= status_code < 300:
        return "active"
    if status_code in (301, 302, 303, 307, 308):
        return "redirect"
    # Server responded but blocks bots — site is alive
    if status_code in (401, 402, 403, 405, 429):
        return "active"
    return "dead"


def compute_signal_freshness(last_signal_date):
    """Return 'fresh', 'aging', or 'stale' based on last signal date."""
    if not last_signal_date:
        return "stale"
    try:
        if isinstance(last_signal_date, str):
            # Handle "2026-02-16 13:29:04" datetime strings
            date_part = last_signal_date.split(" ")[0]
            parts = date_part.split("-")
            d = date(int(parts[0]), int(parts[1]), int(parts[2]))
        else:
            d = last_signal_date
    except (ValueError, IndexError):
        return "stale"

    if d >= THREE_MONTHS_AGO:
        return "fresh"
    elif d >= TWELVE_MONTHS_AGO:
        return "aging"
    else:
        return "stale"


def compute_freshness_score(website_status, signal_freshness):
    """Compute freshness score 1-5."""
    web_alive = website_status in ("active", "redirect")

    if web_alive and signal_freshness == "fresh":
        return 5
    elif web_alive and signal_freshness == "aging":
        return 4
    elif web_alive and signal_freshness == "stale":
        return 3
    elif not web_alive and signal_freshness == "fresh":
        return 2
    elif not web_alive and signal_freshness == "aging":
        return 2
    else:
        return 1


def main():
    conn = get_connection()

    print(flush=True)
    print("=" * 64, flush=True)
    print("  ATHENA — Freshness Scoring", flush=True)
    print("=" * 64, flush=True)

    # Add freshness columns if needed
    cols = [c["name"] for c in conn.execute("PRAGMA table_info(companies)").fetchall()]
    if "freshness_score" not in cols:
        conn.execute("ALTER TABLE companies ADD COLUMN freshness_score INTEGER")
        conn.commit()
        print("  Added freshness_score column", flush=True)

    # ── Step 1: Get last signal date for every company ──
    print("\n  Step 1: Computing last signal dates...", flush=True)

    signal_dates = {}
    rows = conn.execute("""
        SELECT company_id, MAX(detected_at) as last_date
        FROM signals
        GROUP BY company_id
    """).fetchall()
    for r in rows:
        signal_dates[r["company_id"]] = r["last_date"]
    print(f"    Signal dates for {len(signal_dates)} companies", flush=True)

    # ── Step 2: Check websites for T1+T2 companies ──
    print("\n  Step 2: Checking websites for Tier 1+2 companies...", flush=True)

    t12_companies = conn.execute("""
        SELECT id, name, website, data_tier
        FROM companies
        WHERE data_tier IN (1, 2)
        ORDER BY data_tier, id
    """).fetchall()

    print(f"    {len(t12_companies)} T1+T2 companies", flush=True)

    # Separate into with/without website
    with_website = [(r["id"], r["name"], r["website"]) for r in t12_companies
                    if r["website"] and r["website"].strip()]
    without_website = [(r["id"], r["name"]) for r in t12_companies
                       if not r["website"] or not r["website"].strip()]

    print(f"    {len(with_website)} with website, {len(without_website)} without", flush=True)
    print(f"    Checking {len(with_website)} websites with {MAX_WORKERS} workers...", flush=True)

    # Check websites concurrently
    website_results = {}  # company_id -> (status_code, redirect_url, error, classification)
    checked = 0
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {}
        for cid, name, url in with_website:
            f = executor.submit(check_website, url)
            futures[f] = (cid, name, url)

        for future in as_completed(futures):
            cid, name, url = futures[future]
            status_code, redirect_url, error = future.result()
            classification = classify_website(status_code, error)
            website_results[cid] = (status_code, redirect_url, error, classification)

            checked += 1
            if checked % 200 == 0:
                elapsed = time.time() - start_time
                rate = checked / elapsed
                eta = (len(with_website) - checked) / rate / 60
                print(f"    ... {checked}/{len(with_website)} "
                      f"({rate:.1f}/s, ETA {eta:.1f}m)", flush=True)

    elapsed = time.time() - start_time
    print(f"    Done: {checked} websites checked in {elapsed:.0f}s "
          f"({checked/elapsed:.1f}/s)", flush=True)

    # Count website statuses
    status_counts = {"active": 0, "redirect": 0, "dead": 0, "no_website": 0}
    for cid, (sc, redir, err, cls) in website_results.items():
        status_counts[cls] += 1
    status_counts["no_website"] = len(without_website)

    print(f"\n    Website status:", flush=True)
    for k, v in status_counts.items():
        print(f"      {k:15s}: {v}", flush=True)

    # ── Step 3: Compute freshness scores ──
    print("\n  Step 3: Computing freshness scores...", flush=True)

    all_companies = conn.execute("SELECT id, data_tier, website FROM companies").fetchall()
    score_dist = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    dead_websites = []  # (id, name, url, status, error)
    stale_count = 0
    updates = []

    for r in all_companies:
        cid = r["id"]
        tier = r["data_tier"]
        has_website = bool(r["website"] and r["website"].strip())

        # Website status
        if cid in website_results:
            _, _, _, web_status = website_results[cid]
        elif has_website and tier in (1, 2):
            web_status = "unknown"
        elif has_website:
            web_status = "unknown"  # T3 not checked
        else:
            web_status = "no_website"

        # Signal freshness
        last_date = signal_dates.get(cid)
        sig_fresh = compute_signal_freshness(last_date)

        # For T3 companies without website checks, assume website is OK if they have one
        if tier == 3 and has_website:
            web_status = "active"  # optimistic for unchecked
        elif tier == 3 and not has_website:
            web_status = "no_website"

        score = compute_freshness_score(web_status, sig_fresh)
        score_dist[score] += 1
        updates.append((score, cid))

        if sig_fresh == "stale":
            stale_count += 1

    # Batch update
    print(f"    Updating {len(updates)} companies...", flush=True)
    for i in range(0, len(updates), BATCH_COMMIT):
        batch = updates[i:i + BATCH_COMMIT]
        for score, cid in batch:
            conn.execute("UPDATE companies SET freshness_score = ? WHERE id = ?",
                         (score, cid))
        conn.commit()

    # Collect dead website details for report
    for cid, name, url in with_website:
        if cid in website_results:
            sc, redir, err, cls = website_results[cid]
            if cls == "dead":
                dead_websites.append((cid, name, url, sc, err))

    dead_websites.sort(key=lambda x: x[1])  # sort by name

    # ── Step 4: Summary ──
    print(f"\n{'─' * 64}", flush=True)
    print("  Freshness Score Distribution:", flush=True)
    labels = {
        5: "Active web + fresh signal",
        4: "Active web + aging signal",
        3: "Active web + stale signal",
        2: "Dead/no web + recent signal",
        1: "Dead/no web + stale signal",
    }
    total = sum(score_dist.values())
    for score in [5, 4, 3, 2, 1]:
        cnt = score_dist[score]
        pct = cnt / total * 100
        bar = "█" * round(pct / 2)
        print(f"    {score}: {cnt:>6}  ({pct:>5.1f}%)  {bar}  {labels[score]}", flush=True)

    print(f"\n  Dead websites (T1+T2): {len(dead_websites)}", flush=True)
    print(f"  Stale companies (no signal 12+ months): {stale_count}", flush=True)
    print(f"  Active/redirect websites: {status_counts['active'] + status_counts['redirect']}", flush=True)

    print(f"\n  Top 20 dead websites:", flush=True)
    for cid, name, url, sc, err in dead_websites[:20]:
        status = f"HTTP {sc}" if sc else "timeout"
        print(f"    {name[:35]:35s}  {url[:45]:45s}  {status} {err or ''}", flush=True)

    # ── Save full report ──
    os.makedirs("eval", exist_ok=True)
    with open("eval/freshness_report.txt", "w") as f:
        f.write("ATHENA — Freshness Report\n")
        f.write(f"Generated: {TODAY}\n")
        f.write(f"{'=' * 70}\n\n")

        f.write("FRESHNESS SCORE DISTRIBUTION\n")
        f.write(f"{'─' * 50}\n")
        for score in [5, 4, 3, 2, 1]:
            cnt = score_dist[score]
            pct = cnt / total * 100
            f.write(f"  {score}: {cnt:>6}  ({pct:>5.1f}%)  {labels[score]}\n")
        f.write(f"\n  Total: {total}\n\n")

        f.write("WEBSITE STATUS (T1+T2 only)\n")
        f.write(f"{'─' * 50}\n")
        for k, v in status_counts.items():
            f.write(f"  {k:15s}: {v}\n")
        f.write(f"\n  Stale companies (12+ months): {stale_count}\n\n")

        f.write(f"DEAD WEBSITES ({len(dead_websites)} total)\n")
        f.write(f"{'─' * 100}\n")
        f.write(f"  {'ID':>6s}  {'Name':35s}  {'URL':50s}  {'Status':10s}  Error\n")
        f.write(f"  {'─'*6}  {'─'*35}  {'─'*50}  {'─'*10}  {'─'*30}\n")
        for cid, name, url, sc, err in dead_websites:
            status = f"HTTP {sc}" if sc else "timeout"
            f.write(f"  {cid:>6d}  {name[:35]:35s}  {url[:50]:50s}  {status:10s}  {err or ''}\n")

        f.write(f"\n\nREDIRECTED WEBSITES\n")
        f.write(f"{'─' * 100}\n")
        redirects = [(cid, name, url, redir)
                     for cid, name, url in with_website
                     if cid in website_results and website_results[cid][3] == "redirect"]
        f.write(f"  {len(redirects)} websites redirect to a different URL\n")
        for cid, name, url, _ in redirects[:50]:
            _, redir, _, _ = website_results[cid]
            f.write(f"  {name[:35]:35s}  {url[:40]:40s}  -> {redir[:40] if redir else '?'}\n")

    print(f"\n  Full report saved to eval/freshness_report.txt", flush=True)
    print(f"{'─' * 64}", flush=True)
    print(flush=True)

    conn.close()


if __name__ == "__main__":
    main()
