"""
Athena — Description audit and fix.

1. Cleans garbage short descriptions (<20 chars)
2. Fetches descriptions from company websites (meta tags / first paragraph)
3. Generates basic descriptions from metadata for companies without websites

Usage:
    python fix_descriptions.py
"""

import re
import sys
import os
import time
import urllib.request
import urllib.error
import ssl
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.database import get_connection


FETCH_LIMIT = 500
FETCH_TIMEOUT = 5      # seconds per request
FETCH_DELAY = 1.5      # seconds between requests
MIN_DESC_LEN = 30      # minimum useful description length


# ── HTML Parser for meta descriptions and first paragraph ──

class DescriptionExtractor(HTMLParser):
    """Extract meta description or first substantial <p> from HTML."""

    def __init__(self):
        super().__init__()
        self.meta_desc = None
        self.og_desc = None
        self.in_p = False
        self.first_p = None
        self.current_data = []
        self.in_title = False
        self.title = None

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "meta":
            name = (attrs_dict.get("name") or "").lower()
            prop = (attrs_dict.get("property") or "").lower()
            content = attrs_dict.get("content", "")
            if name == "description" and content:
                self.meta_desc = content.strip()
            elif prop == "og:description" and content:
                self.og_desc = content.strip()
        elif tag == "p" and self.first_p is None:
            self.in_p = True
            self.current_data = []
        elif tag == "title" and self.title is None:
            self.in_title = True
            self.current_data = []

    def handle_endtag(self, tag):
        if tag == "p" and self.in_p:
            self.in_p = False
            text = " ".join(self.current_data).strip()
            # Only keep substantial paragraphs
            if len(text) >= MIN_DESC_LEN:
                self.first_p = text
        elif tag == "title" and self.in_title:
            self.in_title = False
            self.title = " ".join(self.current_data).strip()

    def handle_data(self, data):
        if self.in_p or self.in_title:
            self.current_data.append(data.strip())

    def get_description(self):
        """Return best description found, or None."""
        for candidate in [self.og_desc, self.meta_desc, self.first_p]:
            if candidate and len(candidate) >= MIN_DESC_LEN:
                # Clean up: collapse whitespace, truncate at 500 chars
                text = re.sub(r"\s+", " ", candidate).strip()
                if len(text) > 500:
                    text = text[:497] + "..."
                return text
        return None


def fetch_description(url):
    """Fetch a website and extract a description. Returns str or None."""
    if not url:
        return None

    # Ensure https
    if url.startswith("http://"):
        url = "https://" + url[7:]
    elif not url.startswith("https://"):
        url = "https://" + url

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; AthenaBot/1.0)",
        "Accept": "text/html",
        "Accept-Language": "en",
    })

    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT, context=ctx) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "text/html" not in content_type and "application/xhtml" not in content_type:
                return None
            html = resp.read(200_000).decode("utf-8", errors="replace")
    except Exception:
        return None

    try:
        parser = DescriptionExtractor()
        parser.feed(html)
        return parser.get_description()
    except Exception:
        return None


def generate_description(company):
    """Generate a basic description from metadata."""
    name = company["name"]
    sector = company["sector"]
    city = company["city"]
    geography = company["geography"]
    source = company.get("source_name")

    parts = []

    # Sector
    if sector and sector != "Other":
        parts.append(f"{name} is a {sector} company")
    else:
        parts.append(f"{name} is a startup")

    # Location
    if city and geography and geography not in ("Unknown", "Europe"):
        parts.append(f"based in {city}, {geography}")
    elif geography and geography not in ("Unknown", "Europe"):
        parts.append(f"based in {geography}")
    elif city:
        parts.append(f"based in {city}")

    desc = " ".join(parts) + "."

    # Source
    if source:
        desc += f" Detected via {source}."

    return desc if len(desc) >= MIN_DESC_LEN else None


def main():
    conn = get_connection()

    print()
    print("=" * 64)
    print("  ATHENA — Description Audit & Fix")
    print("=" * 64)

    total = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    no_desc = conn.execute(
        "SELECT COUNT(*) FROM companies WHERE description IS NULL OR description = ''"
    ).fetchone()[0]
    short_desc = conn.execute(
        "SELECT COUNT(*) FROM companies WHERE description IS NOT NULL "
        "AND description != '' AND LENGTH(description) < 20"
    ).fetchone()[0]
    good_desc = total - no_desc - short_desc

    print(f"\n  Total companies:     {total}")
    print(f"  Good descriptions:   {good_desc}")
    print(f"  No description:      {no_desc}")
    print(f"  Garbage (<20 chars): {short_desc}")

    # ── Step 1: Clean garbage descriptions ──
    print(f"\n  Step 1: Cleaning garbage descriptions (<20 chars)...")
    cur = conn.execute(
        "UPDATE companies SET description = NULL "
        "WHERE description IS NOT NULL AND description != '' "
        "AND LENGTH(description) < 20"
    )
    cleaned = cur.rowcount
    conn.commit()
    print(f"    Cleared {cleaned} garbage descriptions")

    # ── Step 2: Fetch from websites ──
    print(f"\n  Step 2: Fetching descriptions from websites (limit {FETCH_LIMIT})...")
    rows = conn.execute("""
        SELECT id, name, website FROM companies
        WHERE (description IS NULL OR description = '')
          AND website IS NOT NULL AND website != ''
        ORDER BY athena_score DESC
        LIMIT ?
    """, (FETCH_LIMIT,)).fetchall()

    fetched = 0
    failed = 0
    for i, r in enumerate(rows):
        desc = fetch_description(r["website"])
        if desc:
            conn.execute(
                "UPDATE companies SET description = ? WHERE id = ?",
                (desc, r["id"])
            )
            fetched += 1
            if fetched <= 10 or fetched % 50 == 0:
                print(f"    [{fetched:>3}] {r['name'][:35]:35s} <- {desc[:60]}...")
        else:
            failed += 1

        # Commit in batches
        if (i + 1) % 50 == 0:
            conn.commit()
            print(f"    ... progress: {i + 1}/{len(rows)} "
                  f"(fetched={fetched}, failed={failed})")

        time.sleep(FETCH_DELAY)

    conn.commit()
    print(f"    Fetched {fetched} descriptions from {len(rows)} websites "
          f"({failed} failed/empty)")

    # ── Step 3: Generate from metadata ──
    print(f"\n  Step 3: Generating descriptions from metadata...")
    rows = conn.execute("""
        SELECT c.id, c.name, c.sector, c.city, c.geography,
               (SELECT s.source_name FROM signals s
                WHERE s.company_id = c.id LIMIT 1) AS source_name
        FROM companies c
        WHERE (c.description IS NULL OR c.description = '')
    """).fetchall()

    generated = 0
    for r in rows:
        desc = generate_description(dict(r))
        if desc:
            conn.execute(
                "UPDATE companies SET description = ? WHERE id = ?",
                (desc, r["id"])
            )
            generated += 1
    conn.commit()
    print(f"    Generated {generated} descriptions from metadata")

    # ── Summary ──
    no_desc_after = conn.execute(
        "SELECT COUNT(*) FROM companies WHERE description IS NULL OR description = ''"
    ).fetchone()[0]
    conn.close()

    total_added = fetched + generated
    print(f"\n" + "-" * 64)
    print(f"  Summary:")
    print(f"    Garbage cleaned:      {cleaned}")
    print(f"    Fetched from web:     {fetched}")
    print(f"    Generated from meta:  {generated}")
    print(f"    Total added:          {total_added}")
    print(f"    Still missing:        {no_desc_after}")
    print("-" * 64)
    print()


if __name__ == "__main__":
    main()
