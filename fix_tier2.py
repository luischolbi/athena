"""
Athena — Tier 2 company data gap fill (conservative).

Only adds data from program profile pages and company websites.
Does NOT guess domains or generate descriptions.

Usage:
    python fix_tier2.py
"""

import json
import re
import sys
import os
import time
import ssl
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urlparse
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.database import get_connection
from fix_sectors import classify_text

FETCH_TIMEOUT = 8
FETCH_DELAY = 1.0
MIN_DESC_LEN = 30

# ── Program → city mappings (only when program is explicitly in one city) ──
# Only fill city when the program is physically in a specific city
# and all its companies are based there.
# Excluded: Seedcamp (London HQ, companies across EU),
#           Antler (multi-city), 500 Global (global), EWOR (multi-city)
PROGRAM_CITY = {
    "KTH Innovation": "Stockholm",
    "Sting": "Stockholm",
    "Hetch": "Helsingborg",
    "Minc": "Malmö",
    "LU Innovation": "Lund",
    "Uminova": "Umeå",
    "Brewhouse": "Gothenburg",
    "Innovatum": "Trollhättan",
    "Inkubera": "Borås",
    "Chalmers Ventures": "Gothenburg",
    "Krinova": "Kristianstad",
    "LEAD": "Linköping",
}

# Sources with individual profile pages that may contain company website links
PROFILE_SOURCES = {
    "Sting": {
        "url_pattern": "sting.co/companies/",
        "link_filter": lambda url: (
            "sting.co" not in url and "cdn." not in url and "hsforms" not in url
            and "zencdn" not in url
        ),
    },
    "KTH Innovation": {
        "url_pattern": "kthventures.se/en/portfolio/",
        "link_filter": lambda url: (
            "kthventures" not in url and "cdn." not in url
            and "mmra.re" not in url  # tracking links
        ),
    },
    "Chalmers Ventures": {
        "url_pattern": "chalmersventures.com/startups/",
        "link_filter": lambda url: (
            "chalmersventures" not in url and "cdn." not in url
            and "cookieinformation" not in url and "wwsc.se" not in url
        ),
    },
    "EWOR": {
        "url_pattern": "ewor.com/startups/",
        "link_filter": lambda url: (
            "ewor.com" not in url and "cdn." not in url
        ),
    },
}

# Social/CDN domains to skip when looking for company websites
SKIP_DOMAINS = {
    "linkedin.com", "twitter.com", "x.com", "facebook.com", "instagram.com",
    "youtube.com", "github.com", "medium.com", "tiktok.com",
    "google.com", "gstatic.com", "googleapis.com", "gravatar.com",
    "wp.com", "wordpress.com", "w3.org", "schema.org",
}


def _is_company_link(url):
    """Check if a URL looks like a company website (not social/CDN)."""
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    for skip in SKIP_DOMAINS:
        if host == skip or host.endswith("." + skip):
            return False
    return True


class DescriptionExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta_desc = None
        self.og_desc = None
        self.in_p = False
        self.first_p = None
        self.current_data = []

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

    def handle_endtag(self, tag):
        if tag == "p" and self.in_p:
            self.in_p = False
            text = " ".join(self.current_data).strip()
            if len(text) >= MIN_DESC_LEN:
                self.first_p = text

    def handle_data(self, data):
        if self.in_p:
            self.current_data.append(data.strip())

    def get_description(self):
        for candidate in [self.og_desc, self.meta_desc, self.first_p]:
            if candidate and len(candidate) >= MIN_DESC_LEN:
                text = re.sub(r"\s+", " ", candidate).strip()
                if len(text) > 500:
                    text = text[:497] + "..."
                return text
        return None


def fetch_html(url):
    """Fetch HTML from a URL. Returns HTML string or None."""
    if not url:
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; AthenaBot/1.0)",
        "Accept": "text/html", "Accept-Language": "en",
    })
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT, context=ctx) as resp:
            ct = resp.headers.get("Content-Type", "")
            if "text/html" not in ct and "application/xhtml" not in ct:
                return None
            return resp.read(200_000).decode("utf-8", errors="replace")
    except Exception:
        return None


def fetch_description(url):
    """Fetch and extract description from a company website."""
    html = fetch_html(url)
    if not html:
        return None
    try:
        parser = DescriptionExtractor()
        parser.feed(html)
        desc = parser.get_description()
        # Reject garbage descriptions
        if desc and any(bad in desc.lower() for bad in [
            "forgot your password", "domain is available for sale",
            "lorem ipsum", "integer ac vehicula", "cookie", "404",
        ]):
            return None
        return desc
    except Exception:
        return None


def extract_website_from_profile(profile_url, source_config, company_name):
    """Scrape a program profile page for the company's own website link."""
    html = fetch_html(profile_url)
    if not html:
        return None

    link_filter = source_config["link_filter"]
    links = re.findall(r'href="(https?://[^"]+)"', html)

    # Filter to likely company website links
    candidates = []
    for link in links:
        if not _is_company_link(link):
            continue
        if not link_filter(link):
            continue
        # Skip obvious non-company links
        path = urlparse(link).path
        if path and any(ext in path.lower() for ext in [".css", ".js", ".png", ".jpg", ".svg"]):
            continue
        candidates.append(link)

    if not candidates:
        return None

    # Prefer links whose domain matches the company name
    name_slug = re.sub(r'[^a-z0-9]', '', company_name.lower())
    for c in candidates:
        domain = (urlparse(c).hostname or "").replace("www.", "").split(".")[0]
        if name_slug and len(name_slug) >= 3 and name_slug in domain.replace("-", ""):
            return c

    # If only one candidate, use it (likely the company website)
    if len(candidates) == 1:
        return candidates[0]

    # Multiple candidates — can't be confident which is the company website
    return None


def main():
    conn = get_connection()

    print()
    print("=" * 64)
    print("  ATHENA — Tier 2 Data Gap Fill (Conservative)")
    print("=" * 64)

    total_t2 = conn.execute(
        "SELECT COUNT(*) FROM companies WHERE data_tier = 2"
    ).fetchone()[0]
    print(f"\n  Tier 2 companies: {total_t2}")

    filled = {"website": 0, "description": 0, "city": 0, "sector": 0}
    skipped = {"website": 0, "description": 0, "city": 0, "sector": 0}

    # ── Step 1: Fill websites from program profile pages ──
    print("\n  Step 1: Extracting websites from program profile pages...")

    for source_name, config in PROFILE_SOURCES.items():
        rows = conn.execute("""
            SELECT c.id, c.name, s.source_url
            FROM companies c
            JOIN signals s ON s.company_id = c.id
            WHERE c.data_tier = 2
              AND (c.website IS NULL OR c.website = '')
              AND s.source_name = ?
              AND s.source_url IS NOT NULL AND s.source_url != ''
              AND s.source_url LIKE ?
            GROUP BY c.id
        """, (source_name, f"%{config['url_pattern']}%")).fetchall()

        if not rows:
            continue

        source_found = 0
        source_skipped = 0
        for r in rows:
            website = extract_website_from_profile(
                r["source_url"], config, r["name"]
            )
            if website:
                conn.execute(
                    "UPDATE companies SET website = ? WHERE id = ?",
                    (website, r["id"]),
                )
                source_found += 1
                filled["website"] += 1
                if source_found <= 3:
                    print(f"    + [{source_name}] {r['name'][:30]:30s} -> {website[:50]}")
            else:
                source_skipped += 1
                skipped["website"] += 1
            time.sleep(FETCH_DELAY)

        print(f"    {source_name}: found {source_found}, skipped {source_skipped} "
              f"(of {len(rows)} profiles)")
        conn.commit()

    # ── Step 2: Backfill websites from signal metadata ──
    print("\n  Step 2: Backfilling websites from signal metadata...")

    rows = conn.execute("""
        SELECT c.id, c.name, s.metadata, s.source_name
        FROM companies c
        JOIN signals s ON s.company_id = c.id
        WHERE c.data_tier = 2
          AND (c.website IS NULL OR c.website = '')
          AND s.metadata IS NOT NULL AND s.metadata != ''
        GROUP BY c.id
    """).fetchall()

    meta_found = 0
    for r in rows:
        try:
            meta = json.loads(r["metadata"])
        except (json.JSONDecodeError, TypeError):
            continue
        website = meta.get("website")
        if website and isinstance(website, str) and "." in website:
            if not website.startswith("http"):
                website = "https://" + website
            conn.execute(
                "UPDATE companies SET website = ? WHERE id = ?",
                (website, r["id"]),
            )
            meta_found += 1
            filled["website"] += 1
            if meta_found <= 3:
                print(f"    + [{r['source_name']}] {r['name'][:30]:30s} -> {website[:50]}")

    conn.commit()
    print(f"    Found {meta_found} websites in signal metadata")

    # ── Step 3: Fill city from program location ──
    print("\n  Step 3: Filling city from program location...")

    for program, city in PROGRAM_CITY.items():
        cur = conn.execute("""
            UPDATE companies SET city = ?
            WHERE data_tier = 2
              AND (city IS NULL OR city = '')
              AND id IN (
                  SELECT c.id FROM companies c
                  JOIN programs p ON p.company_id = c.id
                  WHERE p.program_name = ?
              )
        """, (city, program))
        if cur.rowcount:
            print(f"    {program:30s} -> {city:15s} ({cur.rowcount} companies)")
            filled["city"] += cur.rowcount
    conn.commit()

    remaining_city = conn.execute("""
        SELECT COUNT(*) FROM companies
        WHERE data_tier = 2 AND (city IS NULL OR city = '')
    """).fetchone()[0]
    skipped["city"] = remaining_city
    print(f"    Filled: {filled['city']}, Skipped (uncertain): {remaining_city}")

    # ── Step 4: Fetch descriptions from company websites ──
    print("\n  Step 4: Fetching descriptions from company websites...")

    rows = conn.execute("""
        SELECT id, name, website FROM companies
        WHERE data_tier = 2
          AND website IS NOT NULL AND website != ''
          AND (description IS NULL OR description = ''
               OR description LIKE '%Detected via%')
    """).fetchall()

    print(f"    {len(rows)} companies with website but no real description")

    for r in rows:
        desc = fetch_description(r["website"])
        if desc:
            conn.execute(
                "UPDATE companies SET description = ? WHERE id = ?",
                (desc, r["id"]),
            )
            filled["description"] += 1
            if filled["description"] <= 5:
                print(f"    + {r['name'][:35]:35s} <- {desc[:55]}...")
        else:
            skipped["description"] += 1
        time.sleep(FETCH_DELAY)

        if (filled["description"] + skipped["description"]) % 50 == 0:
            conn.commit()
            print(f"    ... {filled['description'] + skipped['description']}/{len(rows)} "
                  f"(fetched={filled['description']}, failed={skipped['description']})")

    conn.commit()

    # Also count companies with no website and no description
    no_web_no_desc = conn.execute("""
        SELECT COUNT(*) FROM companies
        WHERE data_tier = 2
          AND (website IS NULL OR website = '')
          AND (description IS NULL OR description = ''
               OR description LIKE '%Detected via%')
    """).fetchone()[0]
    skipped["description"] += no_web_no_desc
    print(f"    Fetched: {filled['description']}, "
          f"Skipped: {skipped['description']} (no website or fetch failed)")

    # ── Step 5: Classify sectors from descriptions ──
    print("\n  Step 5: Classifying sectors from descriptions...")

    rows = conn.execute("""
        SELECT id, name, description FROM companies
        WHERE data_tier = 2
          AND (sector IS NULL OR sector = '' OR sector = 'Other')
          AND description IS NOT NULL AND description != ''
          AND description NOT LIKE '%Detected via%'
    """).fetchall()

    for r in rows:
        new_sector = classify_text(r["description"])
        if new_sector:
            conn.execute(
                "UPDATE companies SET sector = ? WHERE id = ?",
                (new_sector, r["id"]),
            )
            filled["sector"] += 1
        else:
            skipped["sector"] += 1
    conn.commit()

    # Also try signal titles for remaining
    rows2 = conn.execute("""
        SELECT c.id, c.name, GROUP_CONCAT(s.title, ' | ') AS titles
        FROM companies c
        JOIN signals s ON s.company_id = c.id
        WHERE c.data_tier = 2
          AND (c.sector IS NULL OR c.sector = '' OR c.sector = 'Other')
        GROUP BY c.id
    """).fetchall()

    from_titles = 0
    for r in rows2:
        new_sector = classify_text(r["titles"])
        if new_sector:
            conn.execute(
                "UPDATE companies SET sector = ? WHERE id = ?",
                (new_sector, r["id"]),
            )
            filled["sector"] += 1
            from_titles += 1
        else:
            skipped["sector"] += 1
    conn.commit()

    print(f"    Classified: {filled['sector']} "
          f"({filled['sector'] - from_titles} from desc, {from_titles} from titles), "
          f"Skipped: {skipped['sector']} (no clear signal)")

    # ── Summary ──
    field_counts = {}
    for field, condition in [
        ("description", "description IS NOT NULL AND description != '' "
                        "AND description NOT LIKE '%Detected via%'"),
        ("website", "website IS NOT NULL AND website != ''"),
        ("city", "city IS NOT NULL AND city != ''"),
        ("sector", "sector IS NOT NULL AND sector != '' AND sector != 'Other'"),
    ]:
        cnt = conn.execute(f"""
            SELECT COUNT(*) FROM companies WHERE data_tier = 2 AND {condition}
        """).fetchone()[0]
        field_counts[field] = cnt

    conn.close()

    total_filled = sum(filled.values())
    total_skipped = sum(skipped.values())

    print(f"\n{'─' * 64}")
    print(f"  Summary:")
    print(f"    {'Field':15s} {'Filled':>8s} {'Skipped':>8s}")
    print(f"    {'─' * 15} {'─' * 8} {'─' * 8}")
    for field in ["website", "description", "city", "sector"]:
        print(f"    {field:15s} {filled[field]:>8} {skipped[field]:>8}")
    print(f"    {'─' * 15} {'─' * 8} {'─' * 8}")
    print(f"    {'TOTAL':15s} {total_filled:>8} {total_skipped:>8}")

    print(f"\n  Tier 2 completeness ({total_t2} companies):")
    for field in ["description", "website", "city", "sector"]:
        cnt = field_counts[field]
        pct = cnt / total_t2 * 100
        bar_len = round(pct / 5)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        print(f"    {field:15s} {cnt:>5}/{total_t2}  ({pct:>5.1f}%)  {bar}")

    # Overall fill rate
    total_fields = sum(field_counts.values())
    possible = total_t2 * 4
    overall = total_fields / possible * 100
    print(f"\n  Overall field fill:  {total_fields}/{possible} ({overall:.1f}%)")
    sector_pct = field_counts["sector"] / total_t2 * 100
    print(f"  Sector coverage:    {field_counts['sector']}/{total_t2} ({sector_pct:.1f}%)")
    print(f"{'─' * 64}")
    print()


if __name__ == "__main__":
    main()
