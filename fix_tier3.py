"""
Athena — Tier 3 enrichment for companies with websites.

For the top 500 most recent Tier 3 companies that have a website:
  1. Fetch meta description from website
  2. Extract city if clearly stated
  3. Classify sector from description
  4. Promote to Tier 2 if description + sector + city all filled

For Tier 3 companies with no website and no real description:
  Flag as unverified (set unverified=1)

Usage:
    python fix_tier3.py
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.database import get_connection
from fix_sectors import classify_text

FETCH_TIMEOUT = 6
FETCH_DELAY = 0.8
MIN_DESC_LEN = 30
BATCH_SIZE = 500

# Known European cities for extraction from website text
KNOWN_CITIES = {
    "london", "berlin", "paris", "amsterdam", "stockholm", "zurich", "zürich",
    "munich", "münchen", "copenhagen", "copenhagen", "helsinki", "oslo", "dublin",
    "barcelona", "madrid", "lisbon", "vienna", "wien", "brussels", "milan",
    "milano", "rome", "roma", "prague", "warsaw", "budapest", "athens",
    "lausanne", "geneva", "genève", "bern", "basel", "hamburg", "frankfurt",
    "cologne", "köln", "düsseldorf", "stuttgart", "lyon", "marseille",
    "toulouse", "bordeaux", "lille", "edinburgh", "manchester", "birmingham",
    "leeds", "bristol", "cambridge", "oxford", "glasgow", "göteborg",
    "gothenburg", "malmö", "lund", "linköping", "malmö", "tallinn",
    "riga", "vilnius", "bucharest", "sofia", "zagreb", "ljubljana",
    "bratislava", "krakow", "gdansk", "wroclaw", "poznan", "porto",
    "antwerp", "ghent", "rotterdam", "utrecht", "eindhoven", "the hague",
    "copenhagen", "aarhus", "turku", "tampere", "bergen", "trondheim",
    "lyngby", "espoo", "delft", "leiden", "groningen",
}

# Canonical city names (lowercase → proper case)
CITY_CANONICAL = {
    "zürich": "Zurich", "zurich": "Zurich", "münchen": "Munich",
    "munich": "Munich", "wien": "Vienna", "vienna": "Vienna",
    "genève": "Geneva", "geneva": "Geneva", "köln": "Cologne",
    "cologne": "Cologne", "göteborg": "Gothenburg", "gothenburg": "Gothenburg",
    "malmö": "Malmö", "milano": "Milan", "milan": "Milan",
    "roma": "Rome", "rome": "Rome", "the hague": "The Hague",
    "düsseldorf": "Düsseldorf",
}


class EnrichmentExtractor(HTMLParser):
    """Extract description and city hints from HTML."""
    def __init__(self):
        super().__init__()
        self.meta_desc = None
        self.og_desc = None
        self.in_p = False
        self.first_p = None
        self.current_data = []
        self.all_text = []
        self.json_ld = []
        self.in_script = False
        self.script_type = None
        self.script_data = []
        self.in_address = False
        self.address_data = []

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
        elif tag == "script":
            stype = (attrs_dict.get("type") or "").lower()
            if "ld+json" in stype:
                self.in_script = True
                self.script_type = "json-ld"
                self.script_data = []
        elif tag == "address":
            self.in_address = True
            self.address_data = []

    def handle_endtag(self, tag):
        if tag == "p" and self.in_p:
            self.in_p = False
            text = " ".join(self.current_data).strip()
            if len(text) >= MIN_DESC_LEN:
                self.first_p = text
        elif tag == "script" and self.in_script:
            self.in_script = False
            if self.script_type == "json-ld":
                raw = "".join(self.script_data)
                try:
                    self.json_ld.append(json.loads(raw))
                except (json.JSONDecodeError, ValueError):
                    pass
            self.script_data = []
        elif tag == "address" and self.in_address:
            self.in_address = False

    def handle_data(self, data):
        if self.in_p:
            self.current_data.append(data.strip())
        if self.in_script:
            self.script_data.append(data)
        if self.in_address:
            self.address_data.append(data.strip())
        # Collect visible text for city detection
        stripped = data.strip()
        if stripped:
            self.all_text.append(stripped)

    def get_description(self):
        for candidate in [self.og_desc, self.meta_desc, self.first_p]:
            if candidate and len(candidate) >= MIN_DESC_LEN:
                text = re.sub(r"\s+", " ", candidate).strip()
                if len(text) > 500:
                    text = text[:497] + "..."
                # Reject garbage
                lower = text.lower()
                if any(bad in lower for bad in [
                    "forgot your password", "domain is available for sale",
                    "lorem ipsum", "integer ac vehicula", "just a moment",
                    "access denied", "404 not found", "page not found",
                    "website is under construction", "coming soon",
                    "cookie", "javascript is required",
                ]):
                    continue
                return text
        return None

    def get_city(self):
        """Try to extract city from structured data or address patterns."""
        # Try JSON-LD first (most reliable)
        for ld in self.json_ld:
            city = self._extract_city_from_jsonld(ld)
            if city:
                return city

        # Try address tag
        if self.address_data:
            addr_text = " ".join(self.address_data).lower()
            for city in KNOWN_CITIES:
                if city in addr_text:
                    return CITY_CANONICAL.get(city, city.title())

        # Try "Based in [City]" / "Headquartered in [City]" patterns in description
        desc = self.og_desc or self.meta_desc or ""
        for pattern in [
            r"(?:based|headquartered|located|offices?) in ([A-Z][a-zé]+(?:\s[A-Z][a-z]+)?)",
        ]:
            m = re.search(pattern, desc)
            if m:
                candidate = m.group(1).strip()
                if candidate.lower() in KNOWN_CITIES:
                    return CITY_CANONICAL.get(candidate.lower(), candidate)

        return None

    def _extract_city_from_jsonld(self, data):
        """Extract city from JSON-LD structured data."""
        if isinstance(data, list):
            for item in data:
                city = self._extract_city_from_jsonld(item)
                if city:
                    return city
            return None
        if not isinstance(data, dict):
            return None

        # Check for address.addressLocality
        address = data.get("address")
        if isinstance(address, dict):
            locality = address.get("addressLocality", "")
            if locality and isinstance(locality, str):
                loc_lower = locality.strip().lower()
                if loc_lower in KNOWN_CITIES:
                    return CITY_CANONICAL.get(loc_lower, locality.strip().title())

        # Check nested @graph
        graph = data.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                city = self._extract_city_from_jsonld(item)
                if city:
                    return city

        return None


def fetch_and_extract(url):
    """Fetch a URL and extract description + city."""
    if not url:
        return None, None

    if url.startswith("http://"):
        url = "https://" + url[7:]
    elif not url.startswith("https://"):
        url = "https://" + url

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
                return None, None
            html = resp.read(200_000).decode("utf-8", errors="replace")
    except Exception:
        return None, None

    try:
        parser = EnrichmentExtractor()
        parser.feed(html)
        return parser.get_description(), parser.get_city()
    except Exception:
        return None, None


def main():
    conn = get_connection()

    print()
    print("=" * 64)
    print("  ATHENA — Tier 3 Enrichment & Cleanup")
    print("=" * 64)

    total_t3 = conn.execute(
        "SELECT COUNT(*) FROM companies WHERE data_tier = 3"
    ).fetchone()[0]

    with_web = conn.execute("""
        SELECT COUNT(*) FROM companies
        WHERE data_tier = 3 AND website IS NOT NULL AND website != ''
    """).fetchone()[0]

    print(f"\n  Tier 3 companies: {total_t3}")
    print(f"  With website:     {with_web}")
    print(f"  Without website:  {total_t3 - with_web}")

    # ── Step 0: Add 'unverified' column if it doesn't exist ──
    cols = [c["name"] for c in conn.execute("PRAGMA table_info(companies)").fetchall()]
    if "unverified" not in cols:
        conn.execute("ALTER TABLE companies ADD COLUMN unverified INTEGER DEFAULT 0")
        conn.commit()
        print("\n  Added 'unverified' column to companies table")

    # ── Step 1: Enrich top 500 most recent T3 companies with website ──
    print(f"\n  Step 1: Enriching top {BATCH_SIZE} most recent T3 companies with website...")

    rows = conn.execute(f"""
        SELECT id, name, website, description, sector, city
        FROM companies
        WHERE data_tier = 3
          AND website IS NOT NULL AND website != ''
        ORDER BY last_updated DESC, id DESC
        LIMIT {BATCH_SIZE}
    """).fetchall()

    print(f"    Processing {len(rows)} companies...")

    enriched_desc = 0
    enriched_city = 0
    enriched_sector = 0
    skipped_down = 0      # Website down/empty
    skipped_unclear = 0   # Content unclear, skipped
    already_complete = 0  # Already had real description

    for i, r in enumerate(rows):
        cid = r["id"]
        has_real_desc = (
            r["description"]
            and r["description"].strip()
            and "Detected via" not in r["description"]
        )

        # If already has real description, just try sector classification
        if has_real_desc:
            already_complete += 1
            if r["sector"] in (None, "", "Other"):
                new_sector = classify_text(r["description"])
                if new_sector:
                    conn.execute(
                        "UPDATE companies SET sector = ? WHERE id = ?",
                        (new_sector, cid),
                    )
                    enriched_sector += 1
            continue

        # Fetch from website
        desc, city = fetch_and_extract(r["website"])

        if desc:
            conn.execute(
                "UPDATE companies SET description = ? WHERE id = ?",
                (desc, cid),
            )
            enriched_desc += 1

            # Classify sector from new description
            if r["sector"] in (None, "", "Other"):
                new_sector = classify_text(desc)
                if new_sector:
                    conn.execute(
                        "UPDATE companies SET sector = ? WHERE id = ?",
                        (new_sector, cid),
                    )
                    enriched_sector += 1

            if enriched_desc <= 5:
                print(f"    + {r['name'][:35]:35s} <- {desc[:55]}...")
        else:
            skipped_down += 1

        # Fill city only if we found one and company doesn't have one
        if city and not r["city"]:
            conn.execute(
                "UPDATE companies SET city = ? WHERE id = ?",
                (city, cid),
            )
            enriched_city += 1
            if enriched_city <= 5:
                print(f"    + {r['name'][:35]:35s} city <- {city}")

        if not desc and not city:
            skipped_unclear += 1

        time.sleep(FETCH_DELAY)

        processed = i + 1
        if processed % 50 == 0:
            conn.commit()
            print(f"    ... {processed}/{len(rows)} "
                  f"(desc={enriched_desc}, city={enriched_city}, "
                  f"sector={enriched_sector}, down={skipped_down})")

    conn.commit()
    print(f"\n    Enrichment complete:")
    print(f"      Descriptions fetched:    {enriched_desc}")
    print(f"      Cities extracted:        {enriched_city}")
    print(f"      Sectors classified:      {enriched_sector}")
    print(f"      Already had description: {already_complete}")
    print(f"      Website down/empty:      {skipped_down}")
    print(f"      Skipped (unclear):       {skipped_unclear}")

    # ── Step 2: Promote qualified T3 → T2 ──
    print("\n  Step 2: Promoting qualified companies from Tier 3 to Tier 2...")

    # Promotion criteria: has real description + sector + city
    promotable = conn.execute("""
        SELECT id, name, description, sector, city FROM companies
        WHERE data_tier = 3
          AND description IS NOT NULL AND description != ''
          AND description NOT LIKE '%Detected via%'
          AND sector IS NOT NULL AND sector != '' AND sector != 'Other'
          AND city IS NOT NULL AND city != ''
    """).fetchall()

    promoted = 0
    for r in promotable:
        conn.execute(
            "UPDATE companies SET data_tier = 2 WHERE id = ?",
            (r["id"],),
        )
        promoted += 1
        if promoted <= 10:
            print(f"    + {r['name'][:35]:35s} ({r['city']}, {r['sector']})")

    conn.commit()
    if promoted > 10:
        print(f"    ... and {promoted - 10} more")
    print(f"    Promoted {promoted} companies to Tier 2")

    # ── Step 3: Flag unverified T3 companies ──
    print("\n  Step 3: Flagging unverified Tier 3 companies...")

    # Unverified = no website AND no real description
    cur = conn.execute("""
        UPDATE companies SET unverified = 1
        WHERE data_tier = 3
          AND (website IS NULL OR website = '')
          AND (description IS NULL OR description = ''
               OR description LIKE '%Detected via%')
    """)
    flagged = cur.rowcount
    conn.commit()
    print(f"    Flagged {flagged} companies as unverified")

    # Also ensure non-flagged companies have unverified = 0
    conn.execute("UPDATE companies SET unverified = 0 WHERE unverified IS NULL")
    conn.commit()

    # ── Summary ──
    # Recount tiers
    t2_count = conn.execute("SELECT COUNT(*) FROM companies WHERE data_tier = 2").fetchone()[0]
    t3_count = conn.execute("SELECT COUNT(*) FROM companies WHERE data_tier = 3").fetchone()[0]
    t3_unverified = conn.execute(
        "SELECT COUNT(*) FROM companies WHERE data_tier = 3 AND unverified = 1"
    ).fetchone()[0]
    t3_verified = t3_count - t3_unverified

    print(f"\n{'─' * 64}")
    print(f"  Summary:")
    print(f"    Descriptions fetched:    {enriched_desc}")
    print(f"    Cities extracted:        {enriched_city}")
    print(f"    Sectors classified:      {enriched_sector}")
    print(f"    Promoted T3 → T2:        {promoted}")
    print(f"    Flagged as unverified:   {flagged}")
    print(f"    Skipped (down/unclear):  {skipped_down}")

    print(f"\n  Updated tier counts:")
    print(f"    Tier 2:              {t2_count:>6}")
    print(f"    Tier 3 (verified):   {t3_verified:>6}")
    print(f"    Tier 3 (unverified): {t3_unverified:>6}")
    print(f"{'─' * 64}")
    print()

    conn.close()


if __name__ == "__main__":
    main()
