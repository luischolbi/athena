"""
Antler portfolio scraper for Athena.

Scrapes https://www.antler.co/portfolio — a Webflow CMS page
with portfolio company cards, paginated via ?0b933bfd_page={n}.

Each card div.portco_card contains:
  - p[fs-cmsfilter-field="name"]: company name
  - p[fs-cmsfilter-field="description"]: description
  - Tags in div.portco_card_tags (3 in order): location, sector, year
  - a.clickable_link: company website
"""

import json
import sys
import os
import time

import requests
from bs4 import BeautifulSoup
from scrapers import fetch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date

from database.database import (
    init_db,
    get_connection,
    insert_company,
    insert_signal,
    insert_program,
    update_company,
    update_company_stage,
)

BASE_URL = "https://www.antler.co/portfolio"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# European locations from Antler's filter options
EUROPEAN_LOCATIONS = {
    "UK", "Germany", "Netherlands", "Sweden", "Norway", "Denmark",
    "Finland", "France", "Spain", "Italy", "Portugal", "Austria",
    "Switzerland", "Belgium", "Ireland", "Poland", "Czech Republic",
    "Hungary", "Romania", "Greece", "Estonia", "Latvia", "Lithuania",
    "Croatia", "Slovakia", "Slovenia", "Bulgaria", "Luxembourg",
    "Malta", "Cyprus", "Iceland",
}

# Map Antler sectors to Athena sectors
ANTLER_SECTOR_MAP = {
    "B2B Software": "SaaS",
    "ConsumerTech": "Consumer",
    "FinTech": "Fintech",
    "Health & BioTech": "Health / Bio",
    "Energy": "Climate",
    "Climate": "Climate",
    "Industrials": "Deep Tech",
    "DeepTech": "Deep Tech",
    "Real Estate": "Other",
    "EdTech": "Other",
    "FoodTech": "Other",
}

# Keyword fallback for sector detection
SECTOR_RULES = [
    ("AI / ML", ["artificial intelligence", "machine learning", "deep learning",
                 "nlp", "computer vision", "llm", "generative ai"]),
    ("Health / Bio", ["health", "biotech", "medical", "pharma", "clinical",
                      "diagnostics", "therapeutics", "drug"]),
    ("Fintech", ["fintech", "banking", "payments", "insurance", "financial",
                 "lending", "neobank"]),
    ("Climate", ["climate", "carbon", "renewable", "solar", "energy",
                 "sustainability", "cleantech", "green"]),
    ("SaaS", ["saas", "software", "platform", "b2b", "enterprise", "cloud",
              "api", "automation"]),
    ("Deep Tech", ["robotics", "quantum", "semiconductor", "hardware",
                   "materials", "space", "drone"]),
    ("Consumer", ["consumer", "marketplace", "e-commerce", "retail", "food",
                  "delivery"]),
]


def log(msg):
    print(msg, flush=True)


def find_existing(name):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM companies WHERE LOWER(name) = LOWER(?)", (name,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def map_sector(antler_sector, description=""):
    """Map Antler sector to Athena sector, with keyword fallback."""
    if antler_sector:
        mapped = ANTLER_SECTOR_MAP.get(antler_sector)
        if mapped:
            return mapped
    # Keyword fallback on description
    if description:
        text = description.lower()
        for sector, keywords in SECTOR_RULES:
            if any(kw in text for kw in keywords):
                return sector
    return "Other"


def fetch_page(page_num):
    """Fetch a single page of the Antler portfolio."""
    if page_num == 1:
        url = BASE_URL
    else:
        url = f"{BASE_URL}?0b933bfd_page={page_num}"
    resp = fetch(url, headers=HEADERS)
    return BeautifulSoup(resp.text, "html.parser")


def parse_companies(soup):
    """Parse portfolio cards from the page."""
    companies = []

    for card in soup.find_all("div", class_="portco_card"):
        # Name
        name_p = card.find("p", attrs={"fs-cmsfilter-field": "name"})
        name = name_p.get_text(strip=True) if name_p else None
        if not name:
            continue

        # Description
        desc_p = card.find("p", attrs={"fs-cmsfilter-field": "description"})
        description = desc_p.get_text(strip=True) if desc_p else None

        # Tags: location, sector, year (in order within portco_card_tags)
        tags_div = card.find("div", class_="portco_card_tags")
        location = None
        sector = None
        year = None

        if tags_div:
            tag_texts = []
            for tag in tags_div.find_all("div", class_="tag_small_text"):
                tag_texts.append(tag.get_text(strip=True))

            # Tags appear in order: location, sector, year
            if len(tag_texts) >= 1:
                location = tag_texts[0]
            if len(tag_texts) >= 2:
                sector = tag_texts[1]
            if len(tag_texts) >= 3:
                year = tag_texts[2]

        # Website URL
        link = card.find("a", class_="clickable_link")
        website = None
        if link and link.get("href"):
            href = link["href"]
            if href.startswith("http"):
                website = href

        companies.append({
            "name": name,
            "description": description,
            "location": location,
            "sector": sector,
            "year": year,
            "website": website,
        })

    return companies


def main():
    init_db()

    log("Antler Portfolio Scraper")
    log("=" * 50)

    # Paginate through all pages
    all_companies = []
    page = 1

    while True:
        log(f"\n  Fetching page {page}...")
        try:
            soup = fetch_page(page)
        except requests.RequestException as e:
            log(f"  ERROR fetching page {page}: {e}")
            break

        companies = parse_companies(soup)
        log(f"    Found {len(companies)} companies on page {page}")

        if not companies:
            break

        all_companies.extend(companies)
        page += 1
        time.sleep(1.5)

    log(f"\n  Total portfolio companies: {len(all_companies)}")

    # Filter for European companies
    european = [c for c in all_companies if c.get("location") in EUROPEAN_LOCATIONS]
    log(f"  European companies: {len(european)}")

    new_count = 0
    existing_count = 0
    sectors = {}
    locations = {}

    for data in european:
        name = data["name"]
        location = data["location"]
        sector = map_sector(data["sector"], data.get("description", ""))

        sectors[sector] = sectors.get(sector, 0) + 1
        locations[location] = locations.get(location, 0) + 1

        metadata = json.dumps({
            "antler_sector": data["sector"],
            "year": data["year"],
            "website": data["website"],
        })

        existing = find_existing(name)

        if existing:
            company_id = existing["id"]
            updates = {}
            if data["description"] and not existing.get("description"):
                updates["description"] = data["description"]
            if data["website"] and not existing.get("website"):
                updates["website"] = data["website"]
            if existing.get("sector") in (None, "Other") and sector != "Other":
                updates["sector"] = sector
            if updates:
                update_company(company_id, **updates)
            update_company_stage(company_id, "Pre-seed", "Antler", date.today().isoformat())
            existing_count += 1
        else:
            company_id = insert_company(
                name=name,
                description=data["description"],
                sector=sector,
                geography=location,
                website=data["website"],
                stage="Pre-seed",
                heat_score=2,
                stage_source="Antler",
                stage_detected_date=date.today().isoformat(),
            )
            new_count += 1

        insert_signal(
            company_id=company_id,
            source_type="program",
            source_name="Antler",
            source_url=BASE_URL,
            signal_layer="curated",
            title=f"{name} — Antler" + (f" ({data['year']})" if data.get("year") else ""),
            metadata=metadata,
        )

        insert_program(
            company_id=company_id,
            program_name="Antler",
            program_type="Accelerator",
            program_country=location,
            cohort=data.get("year"),
        )

    log(f"\nAntler: {len(european)} European companies. "
        f"{new_count} new, {existing_count} already existed.")

    log(f"\n  Location breakdown:")
    for loc, c in sorted(locations.items(), key=lambda x: -x[1]):
        log(f"    {loc:20s} {c:>4}")

    log(f"\n  Sector breakdown:")
    for s, c in sorted(sectors.items(), key=lambda x: -x[1]):
        log(f"    {s:20s} {c:>4}")


if __name__ == "__main__":
    main()
