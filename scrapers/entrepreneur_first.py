"""
Entrepreneur First portfolio scraper for Athena.

Scrapes https://www.joinef.com/portfolio/ via the WordPress AJAX API.
Only keeps European companies (London, Paris, Berlin).
"""

import json
import sys
import os
import time

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.database import (
    init_db,
    get_connection,
    insert_company,
    insert_signal,
    insert_program,
    update_company,
)

PORTFOLIO_URL = "https://www.joinef.com/portfolio/"
AJAX_URL = "https://www.joinef.com/wp-admin/admin-ajax.php"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
    "Referer": PORTFOLIO_URL,
}

REQUEST_DELAY = 1.0

EUROPEAN_LOCATIONS = {
    "london": ("UK", "London"),
    "paris": ("France", "Paris"),
    "berlin": ("Germany", "Berlin"),
}

INDUSTRY_TO_SECTOR = {
    "financial services": "Fintech",
    "insurance": "Fintech",
    "banking": "Fintech",
    "healthcare": "Health / Bio",
    "biotechnology": "Health / Bio",
    "pharmaceuticals": "Health / Bio",
    "climate": "Climate",
    "energy & utilities": "Climate",
    "developer tools": "SaaS",
    "enterprise services": "SaaS",
    "enterprise software": "SaaS",
    "aerospace & defence": "Deep Tech",
    "industrial & manufacturing": "Deep Tech",
}


def log(msg):
    print(msg, flush=True)


def find_existing(name):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM companies WHERE LOWER(name) = LOWER(?)", (name,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def map_sector(industry_tags):
    """Map EF industry tags to Athena sector categories."""
    for tag in industry_tags:
        sector = INDUSTRY_TO_SECTOR.get(tag.lower())
        if sector:
            return sector
    return "Other"


def parse_tile(tile):
    """Parse a single company tile div. Returns dict or None if not European."""
    result = {
        "name": None,
        "description": None,
        "location": None,
        "geography": None,
        "city": None,
        "industry_tags": [],
        "founders": [],
        "year_founded": None,
        "funded_by": None,
    }

    # Name
    link_div = tile.select_one("div.tile__link")
    if link_div:
        result["name"] = link_div.get("data-companyname", "").strip()
    if not result["name"]:
        h4 = tile.select_one("h4.tile__name")
        result["name"] = h4.get_text(strip=True) if h4 else None
    if not result["name"]:
        return None

    # Location — filter for European only
    loc_tag = tile.select_one("a.locationtag")
    if loc_tag:
        loc_text = loc_tag.get_text(strip=True).lower()
        if loc_text in EUROPEAN_LOCATIONS:
            result["geography"], result["city"] = EUROPEAN_LOCATIONS[loc_text]
            result["location"] = loc_text.title()
        else:
            return None  # Non-European, skip
    else:
        return None

    # Description
    desc = tile.select_one("div.tile__description")
    if desc:
        result["description"] = desc.get_text(strip=True)

    # Industry tags
    for cat in tile.select("a.categorytag"):
        result["industry_tags"].append(cat.get_text(strip=True))

    # Founders and metadata from meta rows
    for meta_row in tile.select("div.meta__row"):
        cols = meta_row.select("div.row > div.col")
        if len(cols) < 2:
            continue

        label_div = cols[0].select_one(
            "div.meta__row__role, div.meta__row__name"
        )
        value_div = cols[1].select_one(
            "div.meta__row__founder, div.meta__row__name"
        )
        if not label_div or not value_div:
            continue

        label = label_div.get_text(strip=True)
        value = value_div.get_text(strip=True)

        if label == "Founded":
            result["year_founded"] = value
        elif label == "Funded by":
            result["funded_by"] = value
        else:
            # It's a founder row (CEO, CTO, Cofounder, etc.)
            link = value_div.select_one("a")
            founder = {
                "name": value,
                "role": label,
                "linkedin": link.get("href", "") if link else None,
            }
            result["founders"].append(founder)

    return result


def fetch_initial_page():
    """Fetch the portfolio page and parse featured + non-featured tiles."""
    log("  Fetching initial portfolio page...")
    try:
        resp = requests.get(PORTFOLIO_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        log(f"  ERROR: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    tiles = soup.find_all("div", class_="tile--company")
    log(f"    {len(tiles)} tiles on initial page")
    return tiles


def fetch_ajax_pages():
    """Paginate through the AJAX endpoint to get all company tiles."""
    all_tiles = []
    query = json.dumps({
        "post_type": "company",
        "paged": 1,
        "post_status": "publish",
        "posts_per_page": 12,
    })

    for page in range(1, 30):  # safety limit
        time.sleep(REQUEST_DELAY)
        try:
            resp = requests.post(
                AJAX_URL,
                data={"action": "loadmore", "query": query, "page": str(page)},
                headers=HEADERS,
                timeout=30,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            log(f"  ERROR on AJAX page {page}: {e}")
            break

        if not resp.text.strip():
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        tiles = soup.find_all("div", class_="tile--company")
        if not tiles:
            break

        all_tiles.extend(tiles)
        if page % 5 == 0:
            log(f"    Page {page}: {len(all_tiles)} tiles so far")

    return all_tiles


def main():
    init_db()

    log("Entrepreneur First Scraper")
    log("=" * 50)

    # Collect all tiles from initial page + AJAX
    log("\nPhase 1: Fetching company data...")
    initial_tiles = fetch_initial_page()
    ajax_tiles = fetch_ajax_pages()
    log(f"    AJAX: {len(ajax_tiles)} tiles across all pages")

    all_tiles = initial_tiles + ajax_tiles

    # Parse and deduplicate
    log(f"\nPhase 2: Parsing {len(all_tiles)} tiles...")
    seen_names = set()
    companies = []

    for tile in all_tiles:
        data = parse_tile(tile)
        if data is None:
            continue
        name_lower = data["name"].lower()
        if name_lower in seen_names:
            continue
        seen_names.add(name_lower)
        companies.append(data)

    log(f"  {len(companies)} unique European companies")

    # Store in database
    log(f"\nPhase 3: Storing in database...")
    new_count = 0
    existing_count = 0

    for c in companies:
        sector = map_sector(c["industry_tags"])
        existing = find_existing(c["name"])

        metadata = json.dumps({
            "founders": c["founders"],
            "funded_by": c["funded_by"],
            "industry_tags": c["industry_tags"],
        })

        if existing:
            company_id = existing["id"]
            updates = {}
            if c["description"] and not existing.get("description"):
                updates["description"] = c["description"]
            if existing.get("sector") in (None, "Other") and sector != "Other":
                updates["sector"] = sector
            if existing.get("geography") in (None, "Unknown"):
                updates["geography"] = c["geography"]
                updates["city"] = c["city"]
            update_company(company_id, **updates)
            existing_count += 1
        else:
            company_id = insert_company(
                name=c["name"],
                description=c["description"],
                sector=sector,
                geography=c["geography"],
                city=c["city"],
                stage="Pre-seed",
                heat_score=2,
            )
            new_count += 1

        insert_signal(
            company_id=company_id,
            source_type="program",
            source_name="Entrepreneur First",
            source_url=PORTFOLIO_URL,
            signal_layer="curated",
            title=f"{c['name']} — Entrepreneur First portfolio",
            metadata=metadata,
        )

        country_map = {"UK": "United Kingdom", "France": "France", "Germany": "Germany"}
        insert_program(
            company_id=company_id,
            program_name="Entrepreneur First",
            program_type="Accelerator",
            program_country=country_map.get(c["geography"], c["geography"]),
            cohort=c["year_founded"],
        )

        log(f"  {'NEW' if not existing else 'UPD'}  {c['name'][:30]:30s}  "
            f"{c['city']:8s}  {sector:12s}  "
            f"{'y=' + c['year_founded'] if c['year_founded'] else '':7s}  "
            f"{c['funded_by'] or ''}")

    log(f"\nEntrepreneur First: Found {len(companies)} European companies. "
        f"{new_count} new, {existing_count} already existed.")


if __name__ == "__main__":
    main()
