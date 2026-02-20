"""
Athena — Tier 1 website & description gap fill.

For companies missing websites, tries to discover them by:
  1. Constructing likely domain URLs from the company name
  2. Testing if they resolve (HEAD request)
  3. Fetching description from discovered websites

Also fetches descriptions for companies that have websites but no descriptions.

Usage:
    python fix_tier1_websites.py
"""

import re
import sys
import os
import time
import ssl
import urllib.request
import urllib.error
from html.parser import HTMLParser
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.database import get_connection
from fix_sectors import classify_text
from scoring.matcher import _normalize_name, GENERIC_DOMAINS

FETCH_TIMEOUT = 5
PROBE_TIMEOUT = 4
FETCH_DELAY = 1.0
MIN_DESC_LEN = 30

# Legal suffixes to strip when constructing domain guesses
LEGAL_SUFFIXES = [" ag", " sa", " gmbh", " ltd", " inc", " sàrl", " sarl",
                  " ltd.", " inc.", " co.", " llc", " corp"]


def name_to_slug(name):
    """Convert company name to a domain-friendly slug."""
    slug = name.lower().strip()
    for suffix in LEGAL_SUFFIXES:
        if slug.endswith(suffix):
            slug = slug[:-len(suffix)].strip()
    # Remove punctuation and spaces
    slug = re.sub(r'[^a-z0-9]', '', slug)
    return slug


def generate_domain_guesses(name):
    """Generate plausible domain URLs for a company name."""
    slug = name_to_slug(name)
    if len(slug) < 3:
        return []

    # Also try a version with dots/hyphens for multi-word names
    words = re.sub(r'[^a-z0-9\s]', '', name.lower().strip()).split()
    for suffix in LEGAL_SUFFIXES:
        s = suffix.strip()
        if words and words[-1] == s:
            words = words[:-1]

    hyphenated = "-".join(words) if len(words) > 1 else None

    guesses = []
    for base in [slug, hyphenated]:
        if not base or len(base) < 3:
            continue
        for tld in [".com", ".io", ".ch", ".de", ".ai", ".co"]:
            guesses.append(f"https://{base}{tld}")
    return guesses


def probe_url(url):
    """Test if a URL resolves to a real website. Returns final URL or None."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(url, method="HEAD", headers={
        "User-Agent": "Mozilla/5.0 (compatible; AthenaBot/1.0)",
    })
    try:
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT, context=ctx) as resp:
            final_url = resp.url
            # Check it didn't redirect to a generic parking page
            domain = urlparse(final_url).hostname or ""
            if domain in GENERIC_DOMAINS:
                return None
            # Check for parking/redirect patterns
            if "parking" in domain or "sedoparking" in domain or "godaddy" in domain:
                return None
            return final_url
    except Exception:
        return None


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
            html = resp.read(200_000).decode("utf-8", errors="replace")
    except Exception:
        return None
    try:
        parser = DescriptionExtractor()
        parser.feed(html)
        return parser.get_description()
    except Exception:
        return None


def main():
    conn = get_connection()

    print()
    print("=" * 64)
    print("  ATHENA — Tier 1 Website & Description Gap Fill")
    print("=" * 64)

    # ── Step 1: Discover websites by domain guessing ──
    print("\n  Step 1: Discovering websites by domain guessing...")

    rows = conn.execute("""
        SELECT id, name FROM companies
        WHERE data_tier = 1
          AND (website IS NULL OR website = '')
        ORDER BY athena_score DESC
    """).fetchall()

    print(f"    {len(rows)} Tier 1 companies missing websites")

    found_websites = 0
    skipped_websites = 0

    for r in rows:
        guesses = generate_domain_guesses(r["name"])
        found = None
        for url in guesses:
            result = probe_url(url)
            if result:
                found = result
                break
            time.sleep(0.3)

        if found:
            conn.execute("UPDATE companies SET website = ? WHERE id = ?",
                         (found, r["id"]))
            found_websites += 1
            if found_websites <= 15:
                print(f"    + {r['name'][:35]:35s} -> {found}")
        else:
            skipped_websites += 1

        if (found_websites + skipped_websites) % 50 == 0:
            conn.commit()
            print(f"    ... {found_websites + skipped_websites}/{len(rows)} "
                  f"(found={found_websites}, miss={skipped_websites})")

        time.sleep(FETCH_DELAY)

    conn.commit()
    print(f"    Found {found_websites} websites, {skipped_websites} could not be discovered")

    # ── Step 2: Fetch descriptions for all companies with website but no desc ──
    print("\n  Step 2: Fetching descriptions from websites...")

    rows = conn.execute("""
        SELECT id, name, website FROM companies
        WHERE data_tier = 1
          AND website IS NOT NULL AND website != ''
          AND (description IS NULL OR description = ''
               OR description LIKE '%Detected via%')
    """).fetchall()

    print(f"    {len(rows)} companies with website but no real description")

    found_descs = 0
    skipped_descs = 0

    for r in rows:
        desc = fetch_description(r["website"])
        if desc:
            conn.execute("UPDATE companies SET description = ? WHERE id = ?",
                         (desc, r["id"]))
            found_descs += 1
            if found_descs <= 5:
                print(f"    + {r['name'][:35]:35s} <- {desc[:55]}...")
        else:
            skipped_descs += 1
        time.sleep(FETCH_DELAY)

        if (found_descs + skipped_descs) % 50 == 0:
            conn.commit()

    conn.commit()
    print(f"    Fetched {found_descs} descriptions, {skipped_descs} empty/failed")

    # ── Step 3: Re-classify sectors for newly described companies ──
    print("\n  Step 3: Re-classifying sectors...")

    rows = conn.execute("""
        SELECT id, description FROM companies
        WHERE data_tier = 1
          AND (sector IS NULL OR sector = '' OR sector = 'Other')
          AND description IS NOT NULL AND description != ''
          AND description NOT LIKE '%Detected via%'
    """).fetchall()

    found_sectors = 0
    for r in rows:
        new_sector = classify_text(r["description"])
        if new_sector:
            conn.execute("UPDATE companies SET sector = ? WHERE id = ?",
                         (new_sector, r["id"]))
            found_sectors += 1
    conn.commit()
    print(f"    Reclassified {found_sectors} from {len(rows)} candidates")

    # ── Summary ──
    field_counts = {}
    total_t1 = conn.execute("SELECT COUNT(*) FROM companies WHERE data_tier = 1").fetchone()[0]
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

    print(f"\n{'─' * 64}")
    print(f"  Results:")
    print(f"    Websites discovered:     {found_websites}")
    print(f"    Descriptions fetched:    {found_descs}")
    print(f"    Sectors classified:      {found_sectors}")

    print(f"\n  Tier 1 completeness ({total_t1} companies):")
    for field in ["description", "website", "city", "sector"]:
        cnt = field_counts[field]
        pct = cnt / total_t1 * 100
        bar_len = round(pct / 5)
        bar_str = "█" * bar_len + "░" * (20 - bar_len)
        print(f"    {field:15s} {cnt:>5}/{total_t1}  ({pct:>5.1f}%)  {bar_str}")
    print(f"{'─' * 64}")
    print()


if __name__ == "__main__":
    main()
