"""
Techstars European portfolio scraper for Athena.

Queries the Techstars Typesense search API (powers their /portfolio page)
to fetch all European companies.

API: GET https://8gbms7c94riane0lp-1.a1.typesense.net/collections/companies/documents/search
Filter: worldregion:Europe
Paginate: 250 per page, ~4 pages for 886 companies.
"""

import json
import sys
import os
import time

import requests
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

API_URL = "https://8gbms7c94riane0lp-1.a1.typesense.net/collections/companies/documents/search"
API_KEY = os.environ.get("TECHSTARS_API_KEY", "")

# Map Techstars industry verticals to Athena sectors
SECTOR_MAP = {
    "Artificial intelligence and machine learning": "AI / ML",
    "Big Data": "AI / ML",
    "Fintech": "Fintech",
    "Insurtech": "Fintech",
    "B2B payments": "Fintech",
    "Cryptocurrency/Blockchain": "Fintech",
    "Digital health": "Health / Bio",
    "Healthtech": "Health / Bio",
    "Life sciences": "Health / Bio",
    "Oncology": "Health / Bio",
    "Femtech": "Health / Bio",
    "Cleantech": "Climate",
    "Climate tech": "Climate",
    "SaaS": "SaaS",
    "Cloudtech and DevOps": "SaaS",
    "Cybersecurity": "SaaS",
    "Marketing tech": "SaaS",
    "HRtech": "SaaS",
    "Legal tech": "SaaS",
    "Edtech": "SaaS",
    "Real estate tech": "SaaS",
    "Robotics and Drones": "Deep Tech",
    "Space tech": "Deep Tech",
    "Nanotechnology": "Deep Tech",
    "Manufacturing": "Deep Tech",
    "Internet of Things": "Deep Tech",
    "3D printing": "Deep Tech",
    "Augmented reality": "Deep Tech",
    "Virtual reality": "Deep Tech",
    "E-commerce": "Consumer",
    "Foodtech": "Consumer",
    "Gaming": "Consumer",
    "Beauty": "Consumer",
    "Pet tech": "Consumer",
    "eSports": "Consumer",
    "Mobility tech": "Other",
    "Supply chain technology": "Other",
    "Construction technology": "Other",
    "Adtech": "Other",
    "Infrastructure": "Other",
}

# Priority order for picking the best sector from multiple verticals
SECTOR_PRIORITY = ["AI / ML", "Health / Bio", "Fintech", "Climate", "Deep Tech", "SaaS", "Consumer"]


def log(msg):
    print(msg, flush=True)


def find_existing(name):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM companies WHERE LOWER(name) = LOWER(?)", (name,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def map_sector(verticals):
    """Pick the best Athena sector from a list of Techstars verticals."""
    if not verticals:
        return "Other"
    mapped = set()
    for v in verticals:
        s = SECTOR_MAP.get(v)
        if s:
            mapped.add(s)
    if not mapped:
        return "Other"
    for preferred in SECTOR_PRIORITY:
        if preferred in mapped:
            return preferred
    return mapped.pop()


def fetch_page(page_num, per_page=250):
    """Fetch a page of European companies from the Typesense API."""
    r = requests.get(API_URL, headers={
        "X-TYPESENSE-API-KEY": API_KEY,
        "Accept": "application/json",
    }, params={
        "page": page_num,
        "per_page": per_page,
        "q": "*",
        "query_by": "company_name",
        "filter_by": "worldregion:Europe",
    }, timeout=30)
    r.raise_for_status()
    return r.json()


def main():
    init_db()

    log("Techstars European Portfolio Scraper")
    log("=" * 50)

    # Paginate through all European companies
    all_companies = []
    page = 1

    while True:
        log(f"\n  Fetching page {page}...")
        try:
            data = fetch_page(page)
        except requests.RequestException as e:
            log(f"  ERROR: {e}")
            break

        hits = data.get("hits", [])
        total = data.get("found", 0)
        log(f"    Got {len(hits)} companies (total: {total})")

        if not hits:
            break

        for hit in hits:
            all_companies.append(hit["document"])

        if len(all_companies) >= total:
            break

        page += 1
        time.sleep(1.5)

    log(f"\n  Total European companies fetched: {len(all_companies)}")

    # Store in database
    new_count = 0
    existing_count = 0
    sectors = {}
    countries = {}

    for doc in all_companies:
        name = doc["company_name"]
        if not name:
            continue

        country = doc.get("country", "")
        city = doc.get("city", "")
        sector = map_sector(doc.get("industry_vertical", []))
        website = doc.get("website", "")
        if website and not website.startswith("http"):
            website = f"https://{website}"

        sectors[sector] = sectors.get(sector, 0) + 1
        countries[country] = countries.get(country, 0) + 1

        program_names = doc.get("program_names", [])
        cohort = doc.get("first_session_year_s", "")

        metadata = json.dumps({
            "techstars_id": doc.get("company_id"),
            "verticals": doc.get("industry_vertical", []),
            "programs": program_names,
            "year": doc.get("first_session_year"),
            "subregion": doc.get("worldsubregion"),
            "is_exit": doc.get("is_exit"),
            "is_1b": doc.get("is_1b"),
        })

        existing = find_existing(name)

        if existing:
            company_id = existing["id"]
            updates = {}
            if doc.get("brief_description") and not existing.get("description"):
                updates["description"] = doc["brief_description"]
            if website and not existing.get("website"):
                updates["website"] = website
            if existing.get("sector") in (None, "Other") and sector != "Other":
                updates["sector"] = sector
            if city and not existing.get("city"):
                updates["city"] = city
            if updates:
                update_company(company_id, **updates)
            update_company_stage(company_id, "Seed", "Techstars", date.today().isoformat())
            existing_count += 1
        else:
            company_id = insert_company(
                name=name,
                description=doc.get("brief_description"),
                sector=sector,
                geography=country,
                city=city,
                website=website,
                stage="Seed",
                heat_score=2,
                stage_source="Techstars",
                stage_detected_date=date.today().isoformat(),
            )
            new_count += 1

        insert_signal(
            company_id=company_id,
            source_type="program",
            source_name="Techstars",
            source_url=f"https://www.techstars.com/portfolio?name={name}",
            signal_layer="curated",
            title=f"{name} — Techstars ({cohort})" if cohort else f"{name} — Techstars",
            metadata=metadata,
        )

        # Insert a program entry for each Techstars program the company attended
        for prog in program_names:
            insert_program(
                company_id=company_id,
                program_name="Techstars",
                program_type="Accelerator",
                program_country=country,
                cohort=cohort,
            )

    log(f"\nTechstars Europe: {len(all_companies)} companies. "
        f"{new_count} new, {existing_count} already existed.")

    log(f"\n  Country breakdown (top 20):")
    for c, n in sorted(countries.items(), key=lambda x: -x[1])[:20]:
        log(f"    {c:25s} {n:>4}")

    log(f"\n  Sector breakdown:")
    for s, n in sorted(sectors.items(), key=lambda x: -x[1]):
        log(f"    {s:20s} {n:>4}")


if __name__ == "__main__":
    main()
