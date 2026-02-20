"""
Athena — Tier 1 company data gap fill.

Conservatively fills missing fields for highest-value (Tier 1) companies.
Only adds data when confident — leaves fields empty if uncertain.

Usage:
    python fix_tier1.py
"""

import re
import sys
import os
import time
import ssl
import urllib.request
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.database import get_connection
from fix_sectors import classify_text

FETCH_TIMEOUT = 5
FETCH_DELAY = 1.5
MIN_DESC_LEN = 30

# ── Guaranteed program → city mappings ──
# Only used when the program guarantees a specific city.
PROGRAM_CITY = {
    "EPFL": "Lausanne",
    "ETH AI Center": "Zurich",
    "University of Zurich": "Zurich",
    "University of Oxford": "Oxford",
    "Imperial College": "London",
    "Cambridge Enterprise": "Cambridge",
    "KTH Innovation": "Stockholm",
    "DTU Science Park": "Copenhagen",
}


# ── Description extraction (same as fix_descriptions.py) ──

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


def fetch_description(url):
    if not url:
        return None
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


def fetch_website_from_url(url):
    """Try to follow a URL and extract the final domain as a website."""
    if not url:
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; AthenaBot/1.0)",
        "Accept": "text/html",
    })
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT, context=ctx) as resp:
            return resp.url
    except Exception:
        return None


def main():
    conn = get_connection()

    print()
    print("=" * 64)
    print("  ATHENA — Tier 1 Data Gap Fill (Conservative)")
    print("=" * 64)

    total_t1 = conn.execute("SELECT COUNT(*) FROM companies WHERE data_tier = 1").fetchone()[0]
    print(f"\n  Tier 1 companies: {total_t1}")

    filled = {"city": 0, "description": 0, "sector": 0, "website": 0}
    skipped = {"city": 0, "description": 0, "sector": 0, "website": 0}

    # ── Step 1: Fill city from guaranteed program mappings ──
    print("\n  Step 1: Filling city from program mappings...")

    for program, city in PROGRAM_CITY.items():
        cur = conn.execute("""
            UPDATE companies SET city = ?
            WHERE data_tier = 1
              AND (city IS NULL OR city = '')
              AND id IN (
                  SELECT c.id FROM companies c
                  JOIN programs p ON p.company_id = c.id
                  WHERE p.program_name = ?
              )
        """, (city, program))
        if cur.rowcount:
            print(f"    {program:30s} → {city:15s} ({cur.rowcount} companies)")
            filled["city"] += cur.rowcount
    conn.commit()

    remaining_city = conn.execute("""
        SELECT COUNT(*) FROM companies
        WHERE data_tier = 1 AND (city IS NULL OR city = '')
    """).fetchone()[0]
    skipped["city"] = remaining_city
    print(f"    Filled: {filled['city']}, Skipped (uncertain): {remaining_city}")

    # ── Step 2: Fetch descriptions from company websites ──
    print("\n  Step 2: Fetching descriptions from websites...")

    rows = conn.execute("""
        SELECT id, name, website, description FROM companies
        WHERE data_tier = 1
          AND website IS NOT NULL AND website != ''
          AND (description IS NULL OR description = '' OR description LIKE '%Detected via%')
    """).fetchall()

    print(f"    {len(rows)} companies have website but no real description")

    for r in rows:
        desc = fetch_description(r["website"])
        if desc:
            conn.execute("UPDATE companies SET description = ? WHERE id = ?",
                         (desc, r["id"]))
            filled["description"] += 1
            if filled["description"] <= 5:
                print(f"    + {r['name'][:35]:35s} <- {desc[:55]}...")
        else:
            skipped["description"] += 1
        time.sleep(FETCH_DELAY)

    conn.commit()

    # Also count those with no website and no description
    no_web_no_desc = conn.execute("""
        SELECT COUNT(*) FROM companies
        WHERE data_tier = 1
          AND (website IS NULL OR website = '')
          AND (description IS NULL OR description = '' OR description LIKE '%Detected via%')
    """).fetchone()[0]
    skipped["description"] += no_web_no_desc
    print(f"    Fetched: {filled['description']}, "
          f"Skipped: {skipped['description']} (no website or fetch failed)")

    # ── Step 3: Classify sector from descriptions ──
    print("\n  Step 3: Classifying sectors from descriptions...")

    rows = conn.execute("""
        SELECT id, name, description FROM companies
        WHERE data_tier = 1
          AND (sector IS NULL OR sector = '' OR sector = 'Other')
          AND description IS NOT NULL AND description != ''
          AND description NOT LIKE '%Detected via%'
    """).fetchall()

    for r in rows:
        new_sector = classify_text(r["description"])
        if new_sector:
            conn.execute("UPDATE companies SET sector = ? WHERE id = ?",
                         (new_sector, r["id"]))
            filled["sector"] += 1
        else:
            skipped["sector"] += 1
    conn.commit()

    # Also try signal titles for remaining
    rows2 = conn.execute("""
        SELECT c.id, c.name, GROUP_CONCAT(s.title, ' | ') AS titles
        FROM companies c
        JOIN signals s ON s.company_id = c.id
        WHERE c.data_tier = 1
          AND (c.sector IS NULL OR c.sector = '' OR c.sector = 'Other')
        GROUP BY c.id
    """).fetchall()

    from_titles = 0
    for r in rows2:
        new_sector = classify_text(r["titles"])
        if new_sector:
            conn.execute("UPDATE companies SET sector = ? WHERE id = ?",
                         (new_sector, r["id"]))
            filled["sector"] += 1
            from_titles += 1
        else:
            skipped["sector"] += 1
    conn.commit()

    print(f"    Classified: {filled['sector']} "
          f"({filled['sector'] - from_titles} from desc, {from_titles} from titles), "
          f"Skipped: {skipped['sector']} (no clear signal)")

    # ── Step 4: Try to find websites from signal source_urls ──
    print("\n  Step 4: Checking signal source URLs for company websites...")

    rows = conn.execute("""
        SELECT c.id, c.name, s.source_url, s.source_name
        FROM companies c
        JOIN signals s ON s.company_id = c.id
        WHERE c.data_tier = 1
          AND (c.website IS NULL OR c.website = '')
          AND s.source_url IS NOT NULL AND s.source_url != ''
        GROUP BY c.id
    """).fetchall()

    # For VK/EPFL companies, the source_url is the profile page — try to extract website
    for r in rows:
        source_url = r["source_url"]
        source_name = r["source_name"]

        # VK profile pages contain the company website in HTML comments
        if source_name == "Venture Kick" and "venturekick.ch" in source_url:
            try:
                req = urllib.request.Request(source_url, headers={
                    "User-Agent": "Mozilla/5.0 (compatible; AthenaBot/1.0)",
                })
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT, context=ctx) as resp:
                    html = resp.read(200_000).decode("utf-8", errors="replace")
                import re
                comments = re.findall(r'<!--\s*(.*?)\s*-->', html)
                if len(comments) > 9 and comments[9].strip():
                    website = comments[9].strip()
                    if not website.startswith("http"):
                        website = "http://" + website
                    # Validate it looks like a real URL (has a dot, no spaces)
                    from urllib.parse import urlparse
                    parsed = urlparse(website)
                    if parsed.hostname and "." in parsed.hostname and " " not in website:
                        conn.execute("UPDATE companies SET website = ? WHERE id = ?",
                                     (website, r["id"]))
                        filled["website"] += 1
                        if filled["website"] <= 5:
                            print(f"    + {r['name'][:35]:35s} <- {website[:40]}")
                    else:
                        skipped["website"] += 1
                else:
                    skipped["website"] += 1
            except Exception:
                skipped["website"] += 1
            time.sleep(FETCH_DELAY)
        else:
            skipped["website"] += 1

    conn.commit()

    remaining_website = conn.execute("""
        SELECT COUNT(*) FROM companies
        WHERE data_tier = 1 AND (website IS NULL OR website = '')
    """).fetchone()[0]
    print(f"    Found: {filled['website']}, Still missing: {remaining_website}")

    # ── Step 5: Fetch descriptions for newly-found websites ──
    if filled["website"] > 0:
        print("\n  Step 5: Fetching descriptions for newly-found websites...")
        rows = conn.execute("""
            SELECT id, name, website FROM companies
            WHERE data_tier = 1
              AND website IS NOT NULL AND website != ''
              AND (description IS NULL OR description = '' OR description LIKE '%Detected via%')
        """).fetchall()

        extra_desc = 0
        for r in rows:
            desc = fetch_description(r["website"])
            if desc:
                conn.execute("UPDATE companies SET description = ? WHERE id = ?",
                             (desc, r["id"]))
                extra_desc += 1
            time.sleep(FETCH_DELAY)
        conn.commit()
        filled["description"] += extra_desc
        print(f"    Fetched {extra_desc} more descriptions")

        # Re-classify sectors for newly described companies
        rows = conn.execute("""
            SELECT id, description FROM companies
            WHERE data_tier = 1
              AND (sector IS NULL OR sector = '' OR sector = 'Other')
              AND description IS NOT NULL AND description != ''
              AND description NOT LIKE '%Detected via%'
        """).fetchall()
        extra_sector = 0
        for r in rows:
            new_sector = classify_text(r["description"])
            if new_sector:
                conn.execute("UPDATE companies SET sector = ? WHERE id = ?",
                             (new_sector, r["id"]))
                extra_sector += 1
        conn.commit()
        filled["sector"] += extra_sector
        if extra_sector:
            print(f"    Classified {extra_sector} more sectors from new descriptions")

    # ── Summary ──
    # Recompute completeness
    field_counts = {}
    for field, condition in [
        ("description", "description IS NOT NULL AND description != '' AND description NOT LIKE '%Detected via%'"),
        ("website", "website IS NOT NULL AND website != ''"),
        ("city", "city IS NOT NULL AND city != ''"),
        ("sector", "sector IS NOT NULL AND sector != '' AND sector != 'Other'"),
    ]:
        cnt = conn.execute(f"""
            SELECT COUNT(*) FROM companies WHERE data_tier = 1 AND {condition}
        """).fetchone()[0]
        field_counts[field] = cnt

    conn.close()

    total_filled = sum(filled.values())
    total_skipped = sum(skipped.values())

    print(f"\n{'─' * 64}")
    print(f"  Summary:")
    print(f"    {'Field':15s} {'Filled':>8s} {'Skipped':>8s}")
    print(f"    {'─' * 15} {'─' * 8} {'─' * 8}")
    for field in ["city", "description", "sector", "website"]:
        print(f"    {field:15s} {filled[field]:>8} {skipped[field]:>8}")
    print(f"    {'─' * 15} {'─' * 8} {'─' * 8}")
    print(f"    {'TOTAL':15s} {total_filled:>8} {total_skipped:>8}")

    print(f"\n  Tier 1 completeness after fix:")
    for field in ["description", "website", "city", "sector"]:
        cnt = field_counts[field]
        pct = cnt / total_t1 * 100
        bar_len = round(pct / 5)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        print(f"    {field:15s} {cnt:>5}/{total_t1}  ({pct:>5.1f}%)  {bar}")

    print(f"{'─' * 64}")
    print()


if __name__ == "__main__":
    main()
