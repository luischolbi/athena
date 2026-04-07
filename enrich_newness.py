"""
Athena — Newness enrichment via crt.sh (Certificate Transparency logs).

For each curated company with a website, queries crt.sh for the earliest SSL
certificate to determine when the website first went live.

Uses asyncio + aiohttp for 3 concurrent requests per batch.

Usage:
    python enrich_newness.py                    # Process unchecked companies
    python enrich_newness.py --limit 20         # Test on 20 companies
    python enrich_newness.py --force            # Re-check all companies
    python enrich_newness.py --min-score 3.5    # Only companies scoring >= 3.5
"""

import argparse
import asyncio
import sys
from datetime import datetime, date
from urllib.parse import urlparse

import aiohttp

from database.database import get_connection, init_db

# Domains that are platforms, not company websites
PLATFORM_DOMAINS = {
    "github.com", "github.io", "gitlab.com", "bitbucket.org",
    "notion.so", "notion.site",
    "medium.com", "substack.com",
    "linkedin.com", "twitter.com", "x.com", "facebook.com", "instagram.com",
    "youtube.com", "tiktok.com",
    "google.com", "docs.google.com", "drive.google.com",
    "apple.com", "apps.apple.com",
    "play.google.com",
    "crunchbase.com", "pitchbook.com", "angellist.com",
    "wikipedia.org",
    "slack.com",
    "wordpress.com", "wix.com", "squarespace.com", "webflow.io",
    "vercel.app", "netlify.app", "herokuapp.com",
    "figma.com", "miro.com",
    "discord.com", "discord.gg",
    "t.me", "telegram.org",
}

CRT_SH_URL = "https://crt.sh/"
REQUEST_TIMEOUT = 45
BATCH_SIZE = 3
DELAY_BETWEEN_BATCHES = 3
LOG_INTERVAL = 25


def extract_domain(url):
    """Extract the registrable domain from a URL, stripping protocol/www/paths."""
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        parsed = urlparse(url)
        domain = parsed.hostname
        if not domain:
            return None
        if domain.startswith("www."):
            domain = domain[4:]
        return domain.lower()
    except Exception:
        return None


def is_platform_domain(domain):
    """Check if a domain is a known platform (not a company's own website)."""
    if not domain:
        return True
    for platform in PLATFORM_DOMAINS:
        if domain == platform or domain.endswith("." + platform):
            return True
    return False


def parse_earliest_date(data):
    """From crt.sh JSON response, find the earliest not_before date."""
    earliest = None
    for cert in data:
        not_before = cert.get("not_before")
        if not not_before:
            continue
        try:
            dt = datetime.fromisoformat(not_before.replace("Z", "+00:00").split("+")[0])
            d = dt.date()
            if earliest is None or d < earliest:
                earliest = d
        except (ValueError, TypeError):
            continue
    return earliest


async def query_crt_sh(session, domain):
    """Query crt.sh for certificates matching a domain. Returns earliest not_before date or None."""
    try:
        async with session.get(
            CRT_SH_URL,
            params={"q": domain, "output": "json"},
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
            if not data:
                return None
            return parse_earliest_date(data)
    except asyncio.TimeoutError:
        return "TIMEOUT"
    except (aiohttp.ClientError, ValueError):
        return None


async def query_with_retry(session, domain, max_retries=2):
    """Query crt.sh with up to max_retries retries on timeout."""
    result = await query_crt_sh(session, domain)
    retries = 0
    while result == "TIMEOUT" and retries < max_retries:
        retries += 1
        print(f"  Timeout for {domain}, retry {retries}/{max_retries}...")
        await asyncio.sleep(DELAY_BETWEEN_BATCHES)
        result = await query_crt_sh(session, domain)
    if result == "TIMEOUT":
        return None, True  # (no data, was_error)
    return result, False


def compute_newness(ssl_date):
    """Compute newness_status from ssl_first_seen date."""
    if ssl_date is None:
        return None
    age_days = (date.today() - ssl_date).days
    if age_days <= 182:  # ~6 months
        return "new"
    elif age_days <= 548:  # ~18 months
        return "recent"
    else:
        return "old"


async def process_batch(session, batch, conn, now, stats):
    """Process a batch of companies concurrently."""
    # Separate skippable from queryable
    tasks = {}  # index -> (company_id, domain)
    for item in batch:
        cid, name, website = item["id"], item["name"], item["website"]
        domain = extract_domain(website)

        if not domain or is_platform_domain(domain):
            stats["skipped"] += 1
            conn.execute(
                "UPDATE companies SET newness_checked_at = ? WHERE id = ?",
                (now, cid),
            )
            continue

        tasks[cid] = domain

    if not tasks:
        conn.commit()
        return

    # Fire all queries concurrently
    query_tasks = {
        cid: asyncio.create_task(query_with_retry(session, domain))
        for cid, domain in tasks.items()
    }
    results = {}
    for cid, task in query_tasks.items():
        results[cid] = await task

    # Write results to DB
    for cid, (ssl_date, was_error) in results.items():
        if was_error:
            stats["errors"] += 1
        if ssl_date is None:
            stats["no_data"] += 1
            conn.execute(
                "UPDATE companies SET newness_checked_at = ? WHERE id = ?",
                (now, cid),
            )
        elif isinstance(ssl_date, date):
            status = compute_newness(ssl_date)
            stats[status] += 1
            conn.execute(
                """UPDATE companies
                   SET ssl_first_seen = ?, newness_status = ?, newness_checked_at = ?
                   WHERE id = ?""",
                (ssl_date.isoformat(), status, now, cid),
            )

    conn.commit()


async def run(args):
    init_db()
    conn = get_connection()

    if args.force:
        conn.execute("UPDATE companies SET newness_checked_at = NULL WHERE data_tier = 1")
        conn.commit()
        print("Reset newness_checked_at for all curated companies.")

    # Reset companies that were checked but got no data (so they get retried)
    reset_count = conn.execute(
        "SELECT COUNT(*) FROM companies WHERE newness_status IS NULL AND newness_checked_at IS NOT NULL AND data_tier = 1"
    ).fetchone()[0]
    if reset_count > 0:
        conn.execute(
            "UPDATE companies SET newness_checked_at = NULL WHERE newness_status IS NULL AND newness_checked_at IS NOT NULL AND data_tier = 1"
        )
        conn.commit()
        print(f"Reset {reset_count} previously failed lookups for retry.\n")

    # Fetch candidates: curated companies with a website, not yet checked
    where_parts = [
        "data_tier = 1",
        "website IS NOT NULL",
        "website != ''",
        "newness_checked_at IS NULL",
    ]
    params = []

    if args.min_score is not None:
        where_parts.append("athena_score >= ?")
        params.append(args.min_score)

    where_clause = " AND ".join(where_parts)
    query = f"""
        SELECT id, name, website FROM companies
        WHERE {where_clause}
        ORDER BY athena_score DESC
    """
    if args.limit > 0:
        query += f" LIMIT {args.limit}"

    candidates = [dict(r) for r in conn.execute(query, params).fetchall()]
    total = len(candidates)

    if total == 0:
        print("No companies to check. Use --force to re-check all.")
        conn.close()
        return

    print(f"Processing {total} companies via crt.sh ({BATCH_SIZE} concurrent)...\n")

    stats = {"new": 0, "recent": 0, "old": 0, "no_data": 0, "skipped": 0, "errors": 0}
    now = datetime.utcnow().isoformat()
    processed = 0

    async with aiohttp.ClientSession() as session:
        for i in range(0, total, BATCH_SIZE):
            batch = candidates[i : i + BATCH_SIZE]
            await process_batch(session, batch, conn, now, stats)
            processed += len(batch)

            if processed % LOG_INTERVAL == 0 or processed == total:
                _log_progress(processed, total, stats)

            # Delay between batches (skip after last batch)
            if i + BATCH_SIZE < total:
                await asyncio.sleep(DELAY_BETWEEN_BATCHES)

    conn.close()

    print(f"\nDone! Processed {total} companies.")
    _log_progress(total, total, stats)


def _log_progress(processed, total, stats):
    print(
        f"  Processed {processed}/{total} — "
        f"{stats['new']} new, {stats['recent']} recent, {stats['old']} old, "
        f"{stats['no_data']} no data, {stats['skipped']} skipped, {stats['errors']} errors"
    )


def main():
    parser = argparse.ArgumentParser(description="Enrich companies with SSL newness data from crt.sh")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of companies to process (0 = all)")
    parser.add_argument("--force", action="store_true", help="Re-check all companies (reset newness_checked_at)")
    parser.add_argument("--min-score", type=float, default=None, help="Only process companies with athena_score >= N")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
