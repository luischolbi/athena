"""
Seedcamp portfolio scraper for Athena.

Scrapes https://seedcamp.com/our-companies/ — a single static page
with 338 company cards containing name, description, website, year,
and sector CSS classes.
"""

import json
import re
import sys
import os

import requests
from bs4 import BeautifulSoup
from scrapers import fetch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.database import (
    init_db,
    get_connection,
    insert_company,
    insert_signal,
    insert_program,
    update_company,
)

PAGE_URL = "https://seedcamp.com/our-companies/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# Map Seedcamp CSS tag classes to Athena sectors
TAG_TO_SECTOR = {
    "ai": "AI / ML",
    "fintech": "Fintech",
    "health-bio": "Health / Bio",
    "climate": "Climate",
    "developer-tools": "SaaS",
    "enterprise": "SaaS",
    "security": "SaaS",
    "consumer": "Other",
    "marketplaces": "Other",
    "crypto": "Fintech",
}

# Fallback: keyword detection from description (same rules as HN scraper)
SECTOR_RULES = [
    ("AI / ML",      [r"\bAI\b", r"\bML\b", r"machine learning", r"\bLLM\b",
                      r"\bGPT\b", r"neural net", r"deep learning"]),
    ("Fintech",      [r"fintech", r"banking", r"payments?\b", r"neobank",
                      r"insurance", r"lending"]),
    ("Climate",      [r"climate", r"carbon", r"\benergy\b", r"\bsolar\b",
                      r"clean\s*tech", r"sustainability"]),
    ("Health / Bio", [r"health", r"medical", r"biotech", r"pharma",
                      r"genomic", r"diagnostic", r"therapeutics"]),
    ("SaaS",         [r"\bSaaS\b", r"\bB2B\b", r"\bplatform\b",
                      r"developer", r"infrastructure"]),
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


def detect_sector_from_tags(css_classes):
    """Map Seedcamp CSS classes to a sector. Returns best match or None."""
    skip = {"company__item", "mix"}
    tags = [c for c in css_classes if c not in skip]
    for tag in tags:
        sector = TAG_TO_SECTOR.get(tag)
        if sector and sector != "Other":
            return sector
    # Return "Other" if we had tags but none mapped to a specific sector
    if tags:
        return "Other"
    return None


def detect_sector_from_text(text):
    """Fallback keyword-based sector detection from description."""
    if not text:
        return "Other"
    for sector, patterns in SECTOR_RULES:
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                return sector
    return "Other"


def parse_item(item):
    """Parse a single company__item div. Returns dict."""
    result = {
        "name": None,
        "description": None,
        "website": None,
        "year": None,
        "sector_tags": [],
        "sector": "Other",
    }

    # Name
    name_el = item.select_one("span.company__item__name")
    if name_el:
        result["name"] = name_el.get_text(strip=True)
    if not result["name"]:
        return None

    # Description
    desc_el = item.select_one("div.company__item__description__content")
    if desc_el:
        result["description"] = desc_el.get_text(strip=True)

    # Website
    link = item.select_one("a.company__item__link")
    if link and link.get("href"):
        result["website"] = link["href"].strip()

    # Year
    year_el = item.select_one("h6.company__item__year")
    if year_el:
        result["year"] = year_el.get_text(strip=True)

    # Sector from CSS tag classes, fallback to description keywords
    css_classes = item.get("class", [])
    skip = {"company__item", "mix"}
    result["sector_tags"] = [c for c in css_classes if c not in skip]

    sector = detect_sector_from_tags(css_classes)
    if sector:
        result["sector"] = sector
    else:
        result["sector"] = detect_sector_from_text(result["description"])

    return result


def main():
    init_db()

    log("Seedcamp Scraper")
    log("=" * 50)

    log(f"\nFetching {PAGE_URL}...")
    try:
        resp = fetch(PAGE_URL, headers=HEADERS)
    except requests.RequestException as e:
        log(f"ERROR: {e}")
        return

    soup = BeautifulSoup(resp.text, "html.parser")
    items = soup.find_all("div", class_="company__item")
    log(f"  Found {len(items)} company cards")

    new_count = 0
    existing_count = 0

    for item in items:
        data = parse_item(item)
        if data is None:
            continue

        name = data["name"]
        existing = find_existing(name)

        metadata = json.dumps({
            "sector_tags": data["sector_tags"],
            "year": data["year"],
        })

        if existing:
            company_id = existing["id"]
            updates = {}
            if data["website"] and not existing.get("website"):
                updates["website"] = data["website"]
            if data["description"] and not existing.get("description"):
                updates["description"] = data["description"]
            if existing.get("sector") in (None, "Other") and data["sector"] != "Other":
                updates["sector"] = data["sector"]
            update_company(company_id, **updates)
            existing_count += 1
        else:
            company_id = insert_company(
                name=name,
                description=data["description"],
                sector=data["sector"],
                geography="Europe",
                website=data["website"],
                stage="Seed",
                heat_score=2,
            )
            new_count += 1

        insert_signal(
            company_id=company_id,
            source_type="program",
            source_name="Seedcamp",
            source_url=PAGE_URL,
            signal_layer="curated",
            title=f"{name} — Seedcamp portfolio",
            metadata=metadata,
        )

        insert_program(
            company_id=company_id,
            program_name="Seedcamp",
            program_type="Accelerator",
            program_country="UK",
            cohort=data["year"],
        )

    log(f"\nSeedcamp: Found {len(items)} companies. "
        f"{new_count} new, {existing_count} already existed.")


if __name__ == "__main__":
    main()
