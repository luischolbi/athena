"""
ProductHunt scraper for Athena.

Uses the public Atom feed at https://www.producthunt.com/feed
to discover recent product launches. Filters for European products
by checking the product URL TLD and description text for European
city/country references.

Fetches multiple category feeds: tech, AI, developer-tools, productivity.
"""

import json
import re
import sys
import os
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import requests
from scrapers import fetch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.database import (
    init_db,
    get_connection,
    insert_company,
    insert_signal,
    update_company,
)

FEED_URL = "https://www.producthunt.com/feed"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# Category feeds to scrape (each returns ~50 entries)
CATEGORIES = [
    "tech",
    "artificial-intelligence",
    "developer-tools",
    "productivity",
    "design-tools",
    "fintech",
]

REQUEST_DELAY = 1.0

# Atom namespace
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

# --- European detection (shared with HN scraper) ---

EUROPEAN_COUNTRIES = {
    "albania", "andorra", "armenia", "austria", "azerbaijan", "belarus",
    "belgium", "bosnia", "bulgaria", "croatia", "cyprus", "czech republic",
    "czechia", "denmark", "estonia", "finland", "france", "georgia", "germany",
    "greece", "hungary", "iceland", "ireland", "italy", "kazakhstan", "kosovo",
    "latvia", "liechtenstein", "lithuania", "luxembourg", "malta", "moldova",
    "monaco", "montenegro", "netherlands", "north macedonia", "norway",
    "poland", "portugal", "romania", "san marino", "serbia",
    "slovakia", "slovenia", "spain", "sweden", "switzerland", "turkey",
    "uk", "united kingdom", "ukraine",
}

EUROPEAN_CITIES = {
    "zurich": "Switzerland", "zürich": "Switzerland", "geneva": "Switzerland",
    "basel": "Switzerland", "bern": "Switzerland", "lausanne": "Switzerland",
    "london": "UK", "edinburgh": "UK", "manchester": "UK",
    "cambridge": "UK", "oxford": "UK", "bristol": "UK",
    "berlin": "Germany", "munich": "Germany", "hamburg": "Germany",
    "frankfurt": "Germany", "cologne": "Germany", "stuttgart": "Germany",
    "paris": "France", "lyon": "France", "marseille": "France",
    "toulouse": "France", "bordeaux": "France",
    "amsterdam": "Netherlands", "rotterdam": "Netherlands",
    "eindhoven": "Netherlands",
    "madrid": "Spain", "barcelona": "Spain", "valencia": "Spain",
    "rome": "Italy", "milan": "Italy", "turin": "Italy",
    "stockholm": "Sweden", "copenhagen": "Denmark",
    "oslo": "Norway", "helsinki": "Finland",
    "dublin": "Ireland", "lisbon": "Portugal", "porto": "Portugal",
    "warsaw": "Poland", "krakow": "Poland", "prague": "Czech Republic",
    "budapest": "Hungary", "bucharest": "Romania", "vienna": "Austria",
    "brussels": "Belgium", "tallinn": "Estonia", "riga": "Latvia",
    "vilnius": "Lithuania", "zagreb": "Croatia", "ljubljana": "Slovenia",
}

AMBIGUOUS_CITIES = {"nice", "bath", "reading", "hull", "cork", "essen", "split"}

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

# --- Sector detection ---

SECTOR_RULES = [
    ("AI / ML",      [r"\bAI\b", r"\bML\b", r"machine learning", r"\bLLM\b",
                      r"\bGPT\b", r"neural net", r"deep learning",
                      r"artificial intelligence"]),
    ("Fintech",      [r"fintech", r"banking", r"payments?\b", r"neobank",
                      r"insurance", r"lending", r"invoice"]),
    ("Climate",      [r"climate", r"carbon", r"\benergy\b", r"\bsolar\b",
                      r"clean\s*tech", r"sustainability"]),
    ("Health / Bio", [r"health", r"medical", r"biotech", r"pharma",
                      r"genomic", r"diagnostic", r"wellness"]),
    ("SaaS",         [r"\bSaaS\b", r"\bB2B\b", r"\bplatform\b",
                      r"developer tool", r"infrastructure", r"\bAPI\b"]),
]

# Map PH category slugs to sectors
CATEGORY_TO_SECTOR = {
    "artificial-intelligence": "AI / ML",
    "fintech": "Fintech",
    "developer-tools": "SaaS",
}


def log(msg):
    print(msg, flush=True)


def detect_sector(text, category=None):
    """Classify sector from description text and feed category."""
    if category:
        sector = CATEGORY_TO_SECTOR.get(category)
        if sector:
            return sector
    if not text:
        return "Other"
    for sector, patterns in SECTOR_RULES:
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                return sector
    return "Other"


def detect_europe_text(text):
    """Check text for European country/city references."""
    if not text:
        return None, None
    text_lower = text.lower()

    for city, country in EUROPEAN_CITIES.items():
        if city in AMBIGUOUS_CITIES:
            if re.search(r'\b' + re.escape(city.title()) + r'\b', text):
                return country, city.title()
        else:
            if re.search(r'\b' + re.escape(city) + r'\b', text_lower):
                return country, city.title()

    for country in EUROPEAN_COUNTRIES:
        if re.search(r'\b' + re.escape(country) + r'\b', text_lower):
            normalized = country.title()
            if country in ("uk", "united kingdom"):
                normalized = "UK"
            elif country == "czechia":
                normalized = "Czech Republic"
            return normalized, None

    return None, None


def detect_europe_tld(url):
    """Check if URL uses a European country-code TLD."""
    if not url:
        return None
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return None
    if host.startswith("www."):
        host = host[4:]
    if host.endswith(".co.uk") or host.endswith(".org.uk"):
        return "UK"
    tld = host.rsplit(".", 1)[-1] if host else ""
    return TLD_TO_COUNTRY.get(tld)


def extract_domain(url):
    """Extract hostname from URL, stripping www."""
    if not url:
        return None
    try:
        host = urlparse(url).hostname or ""
        if host.startswith("www."):
            host = host[4:]
        return host.lower() if host else None
    except Exception:
        return None


def find_existing(name):
    """Check if company already exists by name (case-insensitive)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM companies WHERE LOWER(name) = LOWER(?)", (name,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def has_signal(company_id, source_url):
    """Check if a signal already exists for this company + URL."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM signals WHERE company_id = ? AND source_url = ?",
        (company_id, source_url),
    ).fetchone()
    conn.close()
    return row is not None


# --- Feed parsing ---

def parse_entry(entry):
    """Parse a single Atom feed entry into a product dict."""
    result = {
        "name": None,
        "tagline": None,
        "ph_url": None,
        "product_url": None,
        "author": None,
        "published": None,
        "topics": [],
    }

    # Title = product name
    title_el = entry.find("atom:title", ATOM_NS)
    if title_el is not None and title_el.text:
        result["name"] = title_el.text.strip()

    if not result["name"]:
        return None

    # PH product page URL
    link_el = entry.find("atom:link[@rel='alternate']", ATOM_NS)
    if link_el is not None:
        result["ph_url"] = link_el.get("href")

    # Author / maker name
    author_el = entry.find("atom:author/atom:name", ATOM_NS)
    if author_el is not None and author_el.text:
        result["author"] = author_el.text.strip()

    # Published date
    pub_el = entry.find("atom:published", ATOM_NS)
    if pub_el is not None and pub_el.text:
        result["published"] = pub_el.text.strip()

    # Content — contains tagline and external product URL
    content_el = entry.find("atom:content", ATOM_NS)
    if content_el is not None and content_el.text:
        content = content_el.text

        # Extract tagline: first text before any HTML tags
        # Content format: "Tagline text<br>...<a href='...'>Discussion...</a>..."
        tagline_match = re.match(r'^(.*?)(?:<|$)', content, re.DOTALL)
        if tagline_match:
            tagline = tagline_match.group(1).strip()
            if tagline:
                result["tagline"] = tagline

        # Extract external product URL from /r/p/ redirect link
        url_match = re.search(
            r'href=["\']?(https?://www\.producthunt\.com/r/[^"\'>\s]+)',
            content,
        )
        if url_match:
            result["product_url"] = url_match.group(1)

    return result


def fetch_feed(category=None):
    """Fetch a PH Atom feed, optionally filtered by category."""
    url = FEED_URL
    params = {}
    if category:
        params["category"] = category

    try:
        resp = fetch(url, params=params, headers=HEADERS)
    except requests.RequestException as e:
        log(f"  ERROR fetching feed{f' ({category})' if category else ''}: {e}")
        return []

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        log(f"  ERROR parsing feed XML: {e}")
        return []

    entries = root.findall("atom:entry", ATOM_NS)
    products = []
    for entry in entries:
        product = parse_entry(entry)
        if product:
            if category:
                product["topics"].append(category.replace("-", " ").title())
            products.append(product)

    return products


# --- Main ---

def main():
    init_db()

    log("ProductHunt Scraper")
    log("=" * 50)

    # Phase 1: Fetch all category feeds
    log("\nPhase 1: Fetching Atom feeds...")
    all_products = []
    seen_names = set()

    # Fetch default feed first
    log(f"  Fetching default feed...")
    default_products = fetch_feed()
    for p in default_products:
        key = p["name"].lower()
        if key not in seen_names:
            seen_names.add(key)
            all_products.append(p)
    log(f"    {len(default_products)} entries, {len(all_products)} unique")

    # Fetch category feeds
    for cat in CATEGORIES:
        time.sleep(REQUEST_DELAY)
        log(f"  Fetching {cat} feed...")
        products = fetch_feed(category=cat)
        new = 0
        for p in products:
            key = p["name"].lower()
            if key not in seen_names:
                seen_names.add(key)
                all_products.append(p)
                new += 1
            else:
                # Merge topic into existing entry
                for existing in all_products:
                    if existing["name"].lower() == key:
                        for t in p["topics"]:
                            if t not in existing["topics"]:
                                existing["topics"].append(t)
                        break
        log(f"    {len(products)} entries, {new} new unique")

    log(f"\n  Total unique products: {len(all_products)}")

    # Phase 2: Detect European products
    log("\nPhase 2: Filtering for European products...")
    european = []
    for p in all_products:
        # Check tagline text for European references
        geo, city = detect_europe_text(p["tagline"] or "")

        # Check product URL TLD
        if not geo and p["product_url"]:
            geo = detect_europe_tld(p["product_url"])
            if geo:
                city = None

        # Check PH URL (unlikely to help but cheap to try)
        if not geo:
            geo, city = detect_europe_text(p["author"] or "")

        if geo:
            p["geography"] = geo
            p["city"] = city
            european.append(p)

    log(f"  {len(european)} European products identified")

    # Also keep all products without geography (store as Unknown)
    # so they're available for cross-layer matching
    non_european = [p for p in all_products if p not in european]
    for p in non_european:
        p["geography"] = "Unknown"
        p["city"] = None

    # Store European products + non-European for cross-layer potential
    to_store = european + non_european
    log(f"  Storing all {len(to_store)} products ({len(european)} European, "
        f"{len(non_european)} unknown geography)")

    # Phase 3: Store in database
    log(f"\nPhase 3: Storing in database...")
    new_count = 0
    existing_count = 0
    skipped_signals = 0

    for p in to_store:
        name = p["name"]
        tagline = p["tagline"]
        sector = detect_sector(tagline, p["topics"][0].lower().replace(" ", "-")
                               if p["topics"] else None)

        existing = find_existing(name)

        metadata = json.dumps({
            "topics": p["topics"],
            "author": p["author"],
            "published": p["published"],
        })

        ph_url = p["ph_url"] or ""

        if existing:
            company_id = existing["id"]

            # Skip if signal already exists for this PH URL
            if has_signal(company_id, ph_url):
                skipped_signals += 1
                continue

            updates = {}
            if tagline and not existing.get("description"):
                updates["description"] = tagline
            if existing.get("sector") in (None, "Other") and sector != "Other":
                updates["sector"] = sector
            if existing.get("geography") in (None, "Unknown") and p["geography"] != "Unknown":
                updates["geography"] = p["geography"]
                if p["city"]:
                    updates["city"] = p["city"]
            update_company(company_id, **updates)
            existing_count += 1
        else:
            company_id = insert_company(
                name=name,
                description=tagline,
                sector=sector,
                geography=p["geography"],
                city=p["city"],
                stage="Unknown",
                heat_score=1,
            )
            new_count += 1

        insert_signal(
            company_id=company_id,
            source_type="producthunt",
            source_name="ProductHunt",
            source_url=ph_url,
            signal_layer="realtime",
            title=f"{name} — ProductHunt launch",
            metadata=metadata,
        )

    log(f"\nProductHunt: Found {len(all_products)} products total. "
        f"{len(european)} European. "
        f"{new_count} new companies, {existing_count} updated, "
        f"{skipped_signals} duplicate signals skipped.")


if __name__ == "__main__":
    main()
