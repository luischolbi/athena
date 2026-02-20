"""
KTH Innovation portfolio scraper for Athena.

Scrapes https://kthventures.se/en/portfolio — a Webflow CMS page
with ~64 company cards.

Each card is a div.w-dyn-item containing:
  - div.name: company name
  - div.small-text (inside div.vertical-content): description
  - category badges: sector tags
  - year badge: founding year
  - a.link-block: detail page link
"""

import json
import re
import sys
import os

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

PAGE_URL = "https://kthventures.se/en/portfolio"
BASE_URL = "https://kthventures.se"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# Map KTH category tags to Athena sectors
KTH_SECTOR_MAP = {
    "Artificial Intelligence": "AI / ML",
    "Software": "SaaS",
    "Life Science": "Health / Bio",
    "CleanTech": "Climate",
    "Energy": "Climate",
    "Hardware": "Deep Tech",
    "Semiconductor": "Deep Tech",
    "Materials": "Deep Tech",
    "Robotics": "Deep Tech",
    "Manufacturing": "Deep Tech",
    "Logistics / Mobility": "Other",
    "RIP": None,  # defunct marker, not a sector
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


def map_sector(categories):
    """Pick best Athena sector from a list of KTH category tags."""
    if not categories:
        return "Other"
    for cat in categories:
        mapped = KTH_SECTOR_MAP.get(cat)
        if mapped and mapped != "Other":
            return mapped
    return "Other"


def parse_companies(soup):
    """Parse all portfolio cards from the Webflow CMS page."""
    companies = []

    # Find top-level w-dyn-item divs (that contain logo-box, not nested tag items)
    for item in soup.find_all("div", class_="w-dyn-item"):
        logo_box = item.find("div", class_="logo-box")
        if not logo_box:
            continue

        # Name
        name_div = logo_box.find("div", class_="name")
        name = name_div.get_text(strip=True) if name_div else None
        if not name:
            continue

        # Description
        vert = logo_box.find("div", class_="vertical-content")
        description = None
        if vert:
            desc_div = vert.find("div", class_="small-text")
            if desc_div:
                description = desc_div.get_text(strip=True)

        # Categories (sector tags)
        categories = []
        badge_wrapper = logo_box.find("div", class_="badge-wrapper")
        if badge_wrapper:
            # Category badges are in the first div.badge.bg-light-blue
            cat_badge = badge_wrapper.find("div", class_="bg-light-blue")
            if cat_badge:
                for tag_div in cat_badge.find_all("div", class_="logo-box-caption"):
                    tag_text = tag_div.get_text(strip=True)
                    if tag_text:
                        categories.append(tag_text)

            # Year badge is in div.badge.bg-natural-2
            year_badge = badge_wrapper.find("div", class_="bg-natural-2")
            year = None
            if year_badge:
                year_div = year_badge.find("div", class_="logo-box-caption")
                if year_div:
                    year = year_div.get_text(strip=True)

        # Detail page link
        detail_link = logo_box.find("a", class_="link-block")
        detail_url = None
        if detail_link and detail_link.get("href"):
            href = detail_link["href"]
            if href.startswith("/"):
                detail_url = BASE_URL + href
            else:
                detail_url = href

        companies.append({
            "name": name,
            "description": description,
            "categories": categories,
            "year": year,
            "detail_url": detail_url,
        })

    return companies


def main():
    init_db()

    log("KTH Innovation Portfolio Scraper")
    log("=" * 50)

    log(f"\nFetching {PAGE_URL}...")
    try:
        resp = fetch(PAGE_URL, headers=HEADERS)
    except requests.RequestException as e:
        log(f"ERROR: {e}")
        return

    soup = BeautifulSoup(resp.text, "html.parser")
    companies = parse_companies(soup)
    log(f"  Found {len(companies)} portfolio companies")

    new_count = 0
    existing_count = 0
    sectors = {}

    for data in companies:
        name = data["name"]
        # Skip defunct companies
        if "RIP" in data["categories"]:
            continue

        sector = map_sector(data["categories"])
        sectors[sector] = sectors.get(sector, 0) + 1

        metadata = json.dumps({
            "categories": data["categories"],
            "year": data["year"],
            "detail_url": data["detail_url"],
        })

        existing = find_existing(name)

        if existing:
            company_id = existing["id"]
            updates = {}
            if data["description"] and not existing.get("description"):
                updates["description"] = data["description"]
            if existing.get("sector") in (None, "Other") and sector != "Other":
                updates["sector"] = sector
            if updates:
                update_company(company_id, **updates)
            update_company_stage(company_id, "Pre-seed", "KTH Innovation", date.today().isoformat())
            existing_count += 1
        else:
            company_id = insert_company(
                name=name,
                description=data["description"],
                sector=sector,
                geography="Sweden",
                city="Stockholm",
                stage="Pre-seed",
                heat_score=2,
                stage_source="KTH Innovation",
                stage_detected_date=date.today().isoformat(),
            )
            new_count += 1

        insert_signal(
            company_id=company_id,
            source_type="program",
            source_name="KTH Innovation",
            source_url=data["detail_url"] or PAGE_URL,
            signal_layer="curated",
            title=f"{name} — KTH Innovation" + (f" ({data['year']})" if data.get("year") else ""),
            metadata=metadata,
        )

        insert_program(
            company_id=company_id,
            program_name="KTH Innovation",
            program_type="University Spin-off",
            program_country="Sweden",
            cohort=data.get("year"),
        )

    log(f"\nKTH Innovation: Found {len(companies)} companies. "
        f"{new_count} new, {existing_count} already existed.")

    log(f"\n  Sector breakdown:")
    for s, c in sorted(sectors.items(), key=lambda x: -x[1]):
        log(f"    {s:20s} {c:>4}")


if __name__ == "__main__":
    main()
