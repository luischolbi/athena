"""
Y Combinator European companies scraper for Athena.

Fetches European companies from the YC public API:
  GET https://api.ycombinator.com/v0.1/companies?regions=Europe&page={n}

Paginates through all results (20 per page), extracts company details,
and stores them as curated signals.
"""

import json
import re
import sys
import os
import time

import requests
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

API_URL = "https://api.ycombinator.com/v0.1/companies"

SECTOR_RULES = [
    ("AI / ML",      [r"\bAI\b", r"\bML\b", r"machine learning", r"\bLLM\b",
                      r"\bGPT\b", r"neural net", r"deep learning",
                      r"artificial intelligence", r"computer vision"]),
    ("Fintech",      [r"fintech", r"banking", r"payments?\b", r"neobank",
                      r"insurance", r"lending", r"financial"]),
    ("Climate",      [r"climate", r"carbon", r"\benergy\b", r"\bsolar\b",
                      r"clean\s*tech", r"sustainability"]),
    ("Health / Bio", [r"health", r"medical", r"biotech", r"pharma",
                      r"genomic", r"diagnostic", r"therapeutics", r"drug discovery"]),
    ("SaaS",         [r"\bSaaS\b", r"\bB2B\b", r"\bplatform\b",
                      r"developer", r"infrastructure", r"\bAPI\b"]),
]

# Map YC region names to shorter Athena geography values
COUNTRY_MAP = {
    "United Kingdom": "UK",
    "Great Britain": "UK",
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


def detect_sector(one_liner, tags, industries):
    """Keyword-based sector detection from combined text fields."""
    text = " ".join(filter(None, [one_liner] + (tags or []) + (industries or [])))
    if not text:
        return "Other"
    for sector, patterns in SECTOR_RULES:
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                return sector
    return "Other"


def parse_geography(regions):
    """Pick the most specific country from the regions list.

    The API returns ["Europe", "Netherlands"] — we want "Netherlands".
    Falls back to "Europe" if only "Europe" is present.
    """
    if not regions:
        return "Europe"
    for r in regions:
        if r != "Europe":
            return COUNTRY_MAP.get(r, r)
    return "Europe"


def parse_city(locations):
    """Extract city from locations like "London, England, United Kingdom"."""
    if not locations:
        return None
    first = locations[0]
    if isinstance(first, str) and first:
        return first.split(",")[0].strip()
    return None


def fetch_all_companies():
    """Paginate through the YC API and return all European company dicts."""
    all_companies = []
    page = 1

    while True:
        log(f"  Fetching page {page}...")
        try:
            resp = fetch(API_URL, params={"regions": "Europe", "page": page})
        except requests.RequestException as e:
            log(f"  ERROR fetching page {page}: {e}")
            break

        data = resp.json()
        companies = data.get("companies", [])
        total_pages = data.get("totalPages", 1)

        all_companies.extend(companies)
        log(f"    Got {len(companies)} companies (page {page}/{total_pages})")

        if page >= total_pages or not data.get("nextPage"):
            break

        page += 1
        time.sleep(1)

    return all_companies


def main():
    init_db()

    log("Y Combinator Scraper")
    log("=" * 50)

    companies = fetch_all_companies()
    log(f"\n  Total fetched: {len(companies)} European companies")

    new_count = 0
    existing_count = 0

    for c in companies:
        name = (c.get("name") or "").strip()
        if not name:
            continue

        one_liner = c.get("oneLiner") or ""
        website = c.get("website") or ""
        batch = c.get("batch") or ""
        tags = c.get("tags") or []
        industries = c.get("industries") or []
        regions = c.get("regions") or []
        locations = c.get("locations") or []
        yc_url = c.get("url") or ""
        team_size = c.get("teamSize")
        yc_id = c.get("id")

        sector = detect_sector(one_liner, tags, industries)
        geography = parse_geography(regions)
        city = parse_city(locations)

        metadata = json.dumps({
            "yc_id": yc_id,
            "batch": batch,
            "team_size": team_size,
            "tags": tags,
            "industries": industries,
        })

        existing = find_existing(name)

        if existing:
            company_id = existing["id"]
            updates = {}
            if website and not existing.get("website"):
                updates["website"] = website
            if one_liner and not existing.get("description"):
                updates["description"] = one_liner
            if existing.get("sector") in (None, "Other") and sector != "Other":
                updates["sector"] = sector
            if existing.get("geography") in (None, "Europe") and geography != "Europe":
                updates["geography"] = geography
            if city and not existing.get("city"):
                updates["city"] = city
            if updates:
                update_company(company_id, **updates)
            existing_count += 1
        else:
            company_id = insert_company(
                name=name,
                description=one_liner,
                sector=sector,
                geography=geography,
                city=city,
                website=website,
                stage="Seed",
                heat_score=2,
            )
            new_count += 1

        insert_signal(
            company_id=company_id,
            source_type="program",
            source_name="Y Combinator",
            source_url=yc_url,
            signal_layer="curated",
            title=f"{name} — Y Combinator {batch}",
            metadata=metadata,
        )

        insert_program(
            company_id=company_id,
            program_name="Y Combinator",
            program_type="Accelerator",
            cohort=batch,
            funding_amount="$500k",
        )

    log(f"\nY Combinator: Found {len(companies)} European companies. "
        f"{new_count} new, {existing_count} already existed.")


if __name__ == "__main__":
    main()
