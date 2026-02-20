"""
Athena — Set company_status based on website liveness checks.

Checks T1+T2 company websites and sets:
  active   — HTTP 200 or bot-blocked (401/402/403/405/429)
  inactive — HTTP 404/500/timeout/DNS failure
  redirect — HTTP 301/302 to a different domain
  unknown  — no website on record or T3 (unchecked)

Usage:
    python set_company_status.py
"""

import sys
import os
import ssl
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen
from urllib.parse import urlparse
from urllib.error import URLError, HTTPError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.database import get_connection

TIMEOUT = 5
MAX_WORKERS = 20


def check_website(url):
    """Check website. Returns (status_code, redirect_url, error)."""
    if not url:
        return None, None, "no_url"

    original = url
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
        # Check if redirected to a different domain
        orig_domain = urlparse(url).netloc.replace("www.", "")
        final_domain = urlparse(final_url).netloc.replace("www.", "")
        if orig_domain != final_domain:
            return resp.status, final_url, None
        return resp.status, None, None
    except HTTPError as e:
        return e.code, None, str(e.reason)
    except (URLError, OSError, Exception):
        # Fallback to GET
        req.method = "GET"
        try:
            resp = urlopen(req, timeout=TIMEOUT, context=ctx)
            final_url = resp.url
            resp.read(1024)
            orig_domain = urlparse(url).netloc.replace("www.", "")
            final_domain = urlparse(final_url).netloc.replace("www.", "")
            if orig_domain != final_domain:
                return resp.status, final_url, None
            return resp.status, None, None
        except HTTPError as e2:
            return e2.code, None, str(e2.reason)
        except Exception as e2:
            return None, None, str(e2)[:80]


def classify(status_code, redirect_url, error):
    """Classify into active/inactive/redirect."""
    if status_code is None:
        return "inactive"
    if redirect_url:
        return "redirect"
    if 200 <= status_code < 300:
        return "active"
    if status_code in (401, 402, 403, 405, 429):
        return "active"
    return "inactive"


def main():
    conn = get_connection()

    print(flush=True)
    print("=" * 64, flush=True)
    print("  ATHENA — Company Status Check", flush=True)
    print("=" * 64, flush=True)

    # Ensure column exists
    cols = [c["name"] for c in conn.execute("PRAGMA table_info(companies)").fetchall()]
    if "company_status" not in cols:
        conn.execute("ALTER TABLE companies ADD COLUMN company_status TEXT DEFAULT 'unknown'")
        conn.commit()

    # Get T1+T2 companies with websites
    rows = conn.execute("""
        SELECT id, name, website FROM companies
        WHERE data_tier IN (1, 2)
          AND website IS NOT NULL AND website != ''
    """).fetchall()

    print(f"\n  Checking {len(rows)} T1+T2 websites...", flush=True)

    results = {}
    checked = 0
    start = time.time()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(check_website, r["website"]): r for r in rows}
        for future in as_completed(futures):
            r = futures[future]
            sc, redir, err = future.result()
            status = classify(sc, redir, err)
            results[r["id"]] = (status, sc, redir, err)
            checked += 1
            if checked % 500 == 0:
                elapsed = time.time() - start
                print(f"    ... {checked}/{len(rows)} ({checked/elapsed:.1f}/s)", flush=True)

    elapsed = time.time() - start
    print(f"    Done: {checked} in {elapsed:.0f}s", flush=True)

    # Update DB
    # First: set T1+T2 with websites
    for cid, (status, sc, redir, err) in results.items():
        conn.execute("UPDATE companies SET company_status = ? WHERE id = ?", (status, cid))
    conn.commit()

    # T1+T2 without website -> unknown
    conn.execute("""
        UPDATE companies SET company_status = 'unknown'
        WHERE data_tier IN (1, 2)
          AND (website IS NULL OR website = '')
    """)

    # T3 companies: unknown (not checked)
    conn.execute("""
        UPDATE companies SET company_status = 'unknown'
        WHERE data_tier = 3
    """)
    conn.commit()

    # Summary
    print(f"\n  Status Distribution:", flush=True)
    for status in ["active", "inactive", "redirect", "unknown"]:
        total = conn.execute("SELECT COUNT(*) FROM companies WHERE company_status = ?", (status,)).fetchone()[0]
        t12 = conn.execute("""
            SELECT COUNT(*) FROM companies
            WHERE company_status = ? AND data_tier IN (1, 2)
        """, (status,)).fetchone()[0]
        print(f"    {status:10s}: {total:>6} total  ({t12:>5} T1+T2)", flush=True)

    # Top 20 inactive
    inactive = conn.execute("""
        SELECT name, website FROM companies
        WHERE company_status = 'inactive' AND data_tier IN (1, 2)
        ORDER BY athena_score DESC
        LIMIT 20
    """).fetchall()
    print(f"\n  Top 20 inactive (by score):", flush=True)
    for r in inactive:
        print(f"    {r['name'][:35]:35s}  {r['website'][:50]}", flush=True)

    # Redirects
    redirects = [(cid, r[0], r[1], r[2]) for cid, r in results.items() if r[0] == "redirect"]
    if redirects:
        print(f"\n  Redirected domains ({len(redirects)}):", flush=True)
        for cid, _, redir, _ in redirects[:10]:
            name = conn.execute("SELECT name, website FROM companies WHERE id = ?", (cid,)).fetchone()
            print(f"    {name['name'][:30]:30s}  {name['website'][:35]:35s}  -> {redir[:40]}", flush=True)

    print(f"\n{'─' * 64}", flush=True)
    conn.close()


if __name__ == "__main__":
    main()
