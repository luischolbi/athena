"""
HackerNews scraper for Athena.

Searches the HN Algolia API for "Show HN" and "Launch HN" posts from the
last 30 days, identifies European companies, and stores them as signals.
"""

import argparse
import json
import re
import sys
import os
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse

import requests

# Allow running as `python scrapers/hackernews.py` from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.database import (
    init_db,
    get_connection,
    insert_company,
    insert_signal,
    update_company,
)

HN_SEARCH_URL = "http://hn.algolia.com/api/v1/search"
HN_USER_URL = "http://hn.algolia.com/api/v1/users"
HN_ITEM_BASE = "https://news.ycombinator.com/item?id="

HITS_PER_PAGE = 100
REQUEST_DELAY = 0.25  # seconds between API calls to be polite

# Primary queries — we keep ALL results from these
PRIMARY_QUERIES = ["show hn", "launch hn"]

# Startup-signal queries — we only keep results that match European geography
STARTUP_QUERIES = [
    "pre-seed", "seed round", "just launched",
    "we're hiring", "building in stealth", "open source",
]

# --- Sector detection ---

SECTOR_RULES = [
    ("AI / ML",      [r"\bAI\b", r"\bML\b", r"machine learning", r"\bLLM\b",
                      r"\bGPT\b", r"neural net", r"deep learning"]),
    ("Fintech",      [r"fintech", r"banking", r"payments?\b", r"neobank"]),
    ("Climate",      [r"climate", r"carbon", r"\benergy\b", r"\bsolar\b",
                      r"clean\s*tech", r"sustainability"]),
    ("Health / Bio", [r"health", r"medical", r"biotech", r"pharma",
                      r"genomic", r"diagnostic"]),
    ("SaaS",         [r"\bSaaS\b", r"\bB2B\b", r"\bplatform\b"]),
]


def detect_sector(text):
    """Classify sector from title/description text."""
    if not text:
        return "Other"
    for sector, patterns in SECTOR_RULES:
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                return sector
    return "Other"

# --- European geography data ---

EUROPEAN_COUNTRIES = {
    "albania", "andorra", "armenia", "austria", "azerbaijan", "belarus",
    "belgium", "bosnia", "bulgaria", "croatia", "cyprus", "czech republic",
    "czechia", "denmark", "estonia", "finland", "france", "georgia", "germany",
    "greece", "hungary", "iceland", "ireland", "italy", "kazakhstan", "kosovo",
    "latvia", "liechtenstein", "lithuania", "luxembourg", "malta", "moldova",
    "monaco", "montenegro", "netherlands", "north macedonia", "norway",
    "poland", "portugal", "romania", "russia", "san marino", "serbia",
    "slovakia", "slovenia", "spain", "sweden", "switzerland", "turkey",
    "uk", "united kingdom", "ukraine", "vatican",
}

# Map of major European cities to their country
EUROPEAN_CITIES = {
    # Switzerland
    "zurich": "Switzerland", "zürich": "Switzerland", "geneva": "Switzerland",
    "genève": "Switzerland", "basel": "Switzerland", "bern": "Switzerland",
    "lausanne": "Switzerland", "lugano": "Switzerland",
    # UK
    "london": "UK", "edinburgh": "UK", "manchester": "UK",
    "cambridge": "UK", "oxford": "UK", "bristol": "UK", "glasgow": "UK",
    "birmingham": "UK", "leeds": "UK", "belfast": "UK", "cardiff": "UK",
    # Germany
    "berlin": "Germany", "munich": "Germany", "münchen": "Germany",
    "hamburg": "Germany", "frankfurt": "Germany", "cologne": "Germany",
    "köln": "Germany", "stuttgart": "Germany", "düsseldorf": "Germany",
    "leipzig": "Germany", "dresden": "Germany", "hannover": "Germany",
    # France
    "paris": "France", "lyon": "France", "marseille": "France",
    "toulouse": "France", "nice": "France", "bordeaux": "France",
    "lille": "France", "strasbourg": "France", "nantes": "France",
    "montpellier": "France", "grenoble": "France",
    # Netherlands
    "amsterdam": "Netherlands", "rotterdam": "Netherlands",
    "the hague": "Netherlands", "eindhoven": "Netherlands",
    "utrecht": "Netherlands", "delft": "Netherlands",
    # Spain
    "madrid": "Spain", "barcelona": "Spain", "valencia": "Spain",
    "seville": "Spain", "bilbao": "Spain", "málaga": "Spain",
    # Italy
    "rome": "Italy", "milan": "Italy", "milano": "Italy",
    "turin": "Italy", "torino": "Italy", "florence": "Italy",
    "bologna": "Italy", "naples": "Italy",
    # Nordics
    "stockholm": "Sweden", "gothenburg": "Sweden", "malmö": "Sweden",
    "copenhagen": "Denmark", "aarhus": "Denmark",
    "oslo": "Norway", "bergen": "Norway", "trondheim": "Norway",
    "helsinki": "Finland", "espoo": "Finland", "tampere": "Finland",
    "reykjavik": "Iceland",
    # Ireland
    "dublin": "Ireland", "cork": "Ireland", "galway": "Ireland",
    # Portugal
    "lisbon": "Portugal", "porto": "Portugal", "braga": "Portugal",
    # Eastern Europe
    "warsaw": "Poland", "krakow": "Poland", "kraków": "Poland",
    "wroclaw": "Poland", "wrocław": "Poland", "gdansk": "Poland",
    "prague": "Czech Republic", "brno": "Czech Republic",
    "budapest": "Hungary",
    "bucharest": "Romania", "cluj": "Romania",
    "bratislava": "Slovakia",
    "vienna": "Austria", "wien": "Austria", "graz": "Austria",
    "zagreb": "Croatia",
    "ljubljana": "Slovenia",
    "sofia": "Bulgaria",
    "tallinn": "Estonia", "tartu": "Estonia",
    "riga": "Latvia",
    "vilnius": "Lithuania", "kaunas": "Lithuania",
    # Belgium / Luxembourg
    "brussels": "Belgium", "antwerp": "Belgium", "ghent": "Belgium",
    "leuven": "Belgium",
    "luxembourg": "Luxembourg",
    # Others
    "athens": "Greece", "thessaloniki": "Greece",
    "istanbul": "Turkey", "ankara": "Turkey",
    "kyiv": "Ukraine", "lviv": "Ukraine",
    "belgrade": "Serbia",
    "minsk": "Belarus",
    "tbilisi": "Georgia",
    "yerevan": "Armenia",
}


# City names that are also common English words — require exact capitalization
AMBIGUOUS_CITIES = {"nice", "bath", "reading", "hull", "cork", "essen", "split"}


def detect_europe(text):
    """Check text for European country/city references.

    Returns (geography, city) or (None, None) if no match found.
    """
    if not text:
        return None, None

    text_lower = text.lower()

    # Check cities first (more specific)
    for city, country in EUROPEAN_CITIES.items():
        if city in AMBIGUOUS_CITIES:
            # Case-sensitive: only match capitalized form (e.g. "Nice" not "nice")
            if re.search(r'\b' + re.escape(city.title()) + r'\b', text):
                return country, city.title()
        else:
            if re.search(r'\b' + re.escape(city) + r'\b', text_lower):
                return country, city.title()

    # Check countries
    for country in EUROPEAN_COUNTRIES:
        if re.search(r'\b' + re.escape(country) + r'\b', text_lower):
            # Normalize common variants
            normalized = country.title()
            if country == "uk":
                normalized = "UK"
            elif country == "united kingdom":
                normalized = "UK"
            elif country == "czechia":
                normalized = "Czech Republic"
            return normalized, None

    return None, None


def extract_company_name(title):
    """Extract company/project name from a Show HN or Launch HN title."""
    # Strip "Show HN:" or "Launch HN:" prefix
    name = re.sub(r'^(Show|Launch)\s+HN:\s*', '', title, flags=re.IGNORECASE)
    # Take text before the first dash/en-dash/em-dash or pipe separator
    name = re.split(r'\s[–—\-|]\s', name, maxsplit=1)[0]
    return name.strip()


def extract_domain(url):
    """Extract the base domain from a URL, stripping www."""
    if not url:
        return None
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host or None
    except Exception:
        return None


def find_existing_company(name, url):
    """Check if a company already exists by name or URL domain."""
    conn = get_connection()

    # Match by exact name (case-insensitive)
    row = conn.execute(
        "SELECT * FROM companies WHERE LOWER(name) = LOWER(?)", (name,)
    ).fetchone()
    if row:
        conn.close()
        return dict(row)

    # Match by website domain
    domain = extract_domain(url)
    if domain:
        rows = conn.execute("SELECT * FROM companies WHERE website IS NOT NULL").fetchall()
        for r in rows:
            if extract_domain(r["website"]) == domain:
                conn.close()
                return dict(r)

    conn.close()
    return None


def fetch_user_about(username):
    """Fetch a HN user's 'about' field for location detection."""
    try:
        resp = requests.get(f"{HN_USER_URL}/{username}", timeout=10)
        if resp.status_code == 200:
            return resp.json().get("about", "")
    except requests.RequestException:
        pass
    return ""


def search_hn(query, since_ts):
    """Search HN Algolia API and paginate through all results."""
    all_hits = []
    page = 0

    while True:
        params = {
            "query": f'"{query}"',
            "tags": "story",
            "numericFilters": f"created_at_i>{since_ts}",
            "hitsPerPage": HITS_PER_PAGE,
            "page": page,
        }
        try:
            resp = requests.get(HN_SEARCH_URL, params=params, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  API error on page {page}: {e}")
            break

        data = resp.json()
        hits = data.get("hits", [])
        if not hits:
            break

        all_hits.extend(hits)
        page += 1

        if page >= data.get("nbPages", 0):
            break

        time.sleep(REQUEST_DELAY)

    return all_hits


TLD_TO_COUNTRY = {
    "de": "Germany", "fr": "France", "nl": "Netherlands",
    "ch": "Switzerland", "se": "Sweden", "dk": "Denmark",
    "no": "Norway", "fi": "Finland", "pl": "Poland",
    "es": "Spain", "it": "Italy", "pt": "Portugal",
    "at": "Austria", "be": "Belgium", "ie": "Ireland",
    "cz": "Czech Republic", "hu": "Hungary", "ro": "Romania",
    "bg": "Bulgaria", "hr": "Croatia", "si": "Slovenia",
    "sk": "Slovakia", "lt": "Lithuania", "lv": "Latvia",
    "ee": "Estonia", "lu": "Luxembourg", "is": "Iceland",
}


def detect_europe_from_tld(url):
    """Check if URL uses a European country-code TLD."""
    domain = extract_domain(url)
    if not domain:
        return None
    if domain.endswith(".co.uk") or domain.endswith(".org.uk"):
        return "UK"
    tld = domain.rsplit(".", 1)[-1]
    return TLD_TO_COUNTRY.get(tld)


def classify_hit(hit):
    """Run fast (no-network) European detection on a hit.

    Returns (geography, city) from title / URL / story_text,
    or (None, None) if a user profile lookup is needed.
    """
    title = hit.get("title", "")
    url = hit.get("url", "")
    story_text = hit.get("story_text", "") or ""

    # 1. Title
    geo, city = detect_europe(title)
    if geo:
        return geo, city

    # 2. URL TLD
    geo = detect_europe_from_tld(url)
    if geo:
        return geo, None

    # 3. Story text (self-post body)
    geo, city = detect_europe(story_text)
    if geo:
        return geo, city

    return None, None


def save_hit(hit, geography, city, user_cache):
    """Insert/update company + signal for one HN hit.

    Returns (company_name, is_new) or None if the hit has no usable name.
    """
    title = hit.get("title", "")
    url = hit.get("url", "")
    author = hit.get("author", "")
    points = hit.get("points", 0)
    num_comments = hit.get("num_comments", 0)
    object_id = hit.get("objectID", "")
    created_at = hit.get("created_at", "")

    company_name = extract_company_name(title)
    if not company_name:
        return None

    hn_url = f"{HN_ITEM_BASE}{object_id}"

    # If geography still unknown, try author profile (uses cache)
    if not geography and author:
        if author not in user_cache:
            time.sleep(REQUEST_DELAY)
            user_cache[author] = fetch_user_about(author)
        about = user_cache[author]
        geography, city = detect_europe(about)

    if not geography:
        geography = "Unknown"

    # Detect sector from title + story text
    story_text = hit.get("story_text", "") or ""
    sector = detect_sector(title + " " + story_text)

    existing = find_existing_company(company_name, url)

    metadata = json.dumps({
        "points": points,
        "num_comments": num_comments,
        "author": author,
        "posted_at": created_at,
    })

    if existing:
        company_id = existing["id"]
        updates = {}
        # Upgrade sector if we now have a better classification
        if sector != "Other" and existing.get("sector") in (None, "Other"):
            updates["sector"] = sector
        # Fill in geography if previously unknown
        if geography and geography != "Unknown" and existing.get("geography") == "Unknown":
            updates["geography"] = geography
            if city:
                updates["city"] = city
        update_company(company_id, **updates)
        is_new = False
    else:
        company_id = insert_company(
            name=company_name,
            website=url or None,
            geography=geography,
            city=city,
            sector=sector,
            stage="Unknown",
        )
        is_new = True

    insert_signal(
        company_id=company_id,
        source_type="hackernews",
        source_name="HackerNews",
        source_url=hn_url,
        signal_layer="realtime",
        title=title,
        metadata=metadata,
    )

    return company_name, is_new


def log(msg):
    print(msg, flush=True)


def main():
    parser = argparse.ArgumentParser(description="Scrape HackerNews for startup signals")
    parser.add_argument(
        "--skip-profiles",
        action="store_true",
        help="Skip slow HN user profile lookups; only use title/URL/story text for geo detection",
    )
    args = parser.parse_args()

    init_db()

    since = datetime.utcnow() - timedelta(days=30)
    since_ts = int(since.timestamp())

    log(f"Searching HN for posts since {since.strftime('%Y-%m-%d')}...\n")

    # Collect hits, deduplicate by objectID
    seen_ids = set()
    all_hits = []

    # Primary queries — keep all results
    log("  Primary queries (keep all):")
    for query in PRIMARY_QUERIES:
        log(f'    Fetching "{query}"...')
        hits = search_hn(query, since_ts)
        for h in hits:
            oid = h.get("objectID")
            if oid not in seen_ids:
                seen_ids.add(oid)
                all_hits.append(h)
        log(f"      {len(hits)} results ({len(seen_ids)} unique so far)")

    # Startup-signal queries — only keep hits with European geography
    log("\n  Startup queries (European-only filter):")
    startup_total = 0
    startup_kept = 0
    for query in STARTUP_QUERIES:
        log(f'    Fetching "{query}"...')
        hits = search_hn(query, since_ts)
        startup_total += len(hits)
        for h in hits:
            oid = h.get("objectID")
            if oid in seen_ids:
                continue
            geo, city = classify_hit(h)
            if geo:
                seen_ids.add(oid)
                all_hits.append(h)
                startup_kept += 1
        log(f"      {len(hits)} results")
    log(f"    Kept {startup_kept}/{startup_total} with European signal")

    log(f"\n  Total unique posts to process: {len(all_hits)}")

    log(f"\nPhase 1: Fast classification (title / URL / story text)...")

    # Phase 1 — fast, offline classification
    fast_matched = []    # (hit, geography, city) — already resolved
    needs_lookup = []    # hits that need a user-profile check

    for hit in all_hits:
        geo, city = classify_hit(hit)
        if geo:
            fast_matched.append((hit, geo, city))
        else:
            needs_lookup.append(hit)

    log(f"  {len(fast_matched)} matched from text/URL, "
        f"{len(needs_lookup)} need profile lookup")

    signals_count = 0
    new_companies = 0
    updated_companies = 0
    user_cache = {}

    # Process fast-matched hits first (no network calls)
    for hit, geo, city in fast_matched:
        result = save_hit(hit, geo, city, user_cache)
        if result is None:
            continue
        signals_count += 1
        if result[1]:
            new_companies += 1
        else:
            updated_companies += 1

    log(f"  Saved {signals_count} fast-matched signals")

    # Phase 2 — user profile lookups (slow, batched)
    if args.skip_profiles:
        log(f"\nSkipping profile lookups (--skip-profiles). "
            f"Saving {len(needs_lookup)} posts with geography=Unknown...")
        for hit in needs_lookup:
            result = save_hit(hit, "Unknown", None, user_cache)
            if result is None:
                continue
            signals_count += 1
            if result[1]:
                new_companies += 1
            else:
                updated_companies += 1
    else:
        log(f"\nPhase 2: Checking {len(needs_lookup)} author profiles...")
        for i, hit in enumerate(needs_lookup, 1):
            result = save_hit(hit, None, None, user_cache)
            if result is None:
                continue
            signals_count += 1
            if result[1]:
                new_companies += 1
            else:
                updated_companies += 1

            if i % 100 == 0:
                log(f"  [{i}/{len(needs_lookup)}] profiles checked "
                    f"({len(user_cache)} cached)")

    log(f"\nFound {signals_count} signals. "
        f"{new_companies} new companies added. "
        f"{updated_companies} existing companies updated.")


if __name__ == "__main__":
    main()
