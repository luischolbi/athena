"""
500 Global European portfolio scraper for Athena.

Fetches all portfolio companies from the 500.co public API, then filters
for the Europe region (~120 companies).

API: GET https://500.co/api/startups
Returns ~2200 companies in a single JSON response. Each entry has:
  organization.name, organization.businessName, organization.companyUrl,
  organization.countryOfOperation, organization.regionOfOperation,
  oneLiner, stage, industries[], batches[], investments[]
"""

import json
import sys
import os

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

API_URL = "https://500.co/api/startups"

SECTOR_MAP = {
    "AI/Machine Learning": "AI / ML",
    "Artificial Intelligence / Machine Learning": "AI / ML",
    "Big Data": "AI / ML",
    "Data & Analytics": "AI / ML",
    "FinTech": "Fintech",
    "Credit & Lending": "Fintech",
    "InsurTech": "Fintech",
    "Payments": "Fintech",
    "Banking & Exchange": "Fintech",
    "Investing": "Fintech",
    "Crypto": "Fintech",
    "Blockchain": "Fintech",
    "Health Tech": "Health / Bio",
    "Healthcare": "Health / Bio",
    "BioTech & Life Sciences": "Health / Bio",
    "FemTech (women's health)": "Health / Bio",
    "CleanTech": "Climate",
    "Climate Tech": "Climate",
    "Energy": "Climate",
    "Sustainability": "Climate",
    "SaaS": "SaaS",
    "Developer Tools": "SaaS",
    "DevOps": "SaaS",
    "CloudTech": "SaaS",
    "Enterprise": "SaaS",
    "CRM": "SaaS",
    "Cybersecurity": "SaaS",
    "HR Tech": "SaaS",
    "Productivity": "SaaS",
    "Robotics": "Deep Tech",
    "Space Tech": "Deep Tech",
    "Hardware": "Deep Tech",
    "Deep Tech": "Deep Tech",
    "IoT": "Deep Tech",
    "3D Printing": "Deep Tech",
    "Ecommerce": "Consumer",
    "Direct to Consumer": "Consumer",
    "Food": "Consumer",
    "Fashion": "Consumer",
    "Beauty": "Consumer",
    "Gaming": "Consumer",
    "Marketplace": "Consumer",
    "Social Media": "Consumer",
}

SECTOR_PRIORITY = ["AI / ML", "Health / Bio", "Fintech", "Climate", "Deep Tech", "SaaS", "Consumer"]

COUNTRY_MAP = {
    "United Kingdom": "UK",
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


def map_sector(industries):
    if not industries:
        return "Other"
    mapped = set()
    for ind in industries:
        name = ind.get("name", "")
        s = SECTOR_MAP.get(name)
        if s:
            mapped.add(s)
    if not mapped:
        return "Other"
    for preferred in SECTOR_PRIORITY:
        if preferred in mapped:
            return preferred
    return mapped.pop()


def main():
    init_db()

    log("500 Global European Portfolio Scraper")
    log("=" * 50)

    # 1. Fetch all companies from API
    log("\n  Fetching from API...")
    resp = fetch(API_URL, headers={
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }, timeout=60)
    data = resp.json()
    all_companies = data.get("res", [])
    log(f"  Total portfolio: {len(all_companies)} companies")

    # 2. Filter for Europe
    eu_companies = []
    for c in all_companies:
        org = c.get("organization", {})
        region = (org.get("regionOfOperation") or {}).get("name", "")
        if region == "Europe":
            eu_companies.append(c)

    log(f"  European companies: {len(eu_companies)}")

    # 3. Store in database
    new_count = 0
    existing_count = 0
    sectors = {}
    countries = {}

    for c in eu_companies:
        org = c.get("organization", {})

        # Prefer businessName (cleaner), fall back to legal name
        name = (org.get("businessName") or org.get("name") or "").strip()
        if not name:
            continue

        # Capitalize properly if all lowercase
        if name == name.lower():
            name = name.title()

        description = c.get("oneLiner")
        sector = map_sector(c.get("industries", []))
        stage_raw = (c.get("stage") or {}).get("name", "")
        country_raw = (org.get("countryOfOperation") or {}).get("name", "")
        country = COUNTRY_MAP.get(country_raw, country_raw)
        website = org.get("companyUrl", "")
        if website and not website.startswith("http"):
            website = f"https://{website}"

        batches = [b.get("brandName", "") for b in c.get("batches", []) if b.get("brandName")]
        cohort = batches[0] if batches else ""

        invest_date = None
        for inv in c.get("investments", []):
            d = inv.get("initialInvestDate")
            if d:
                invest_date = d[:10]
                break

        sectors[sector] = sectors.get(sector, 0) + 1
        countries[country] = countries.get(country, 0) + 1

        metadata = json.dumps({
            "500g_id": c.get("id"),
            "legal_name": org.get("name"),
            "industries": [i.get("name") for i in c.get("industries", [])],
            "batch": cohort,
            "business_model": (c.get("businessModel") or {}).get("name"),
            "stage": stage_raw,
            "invest_date": invest_date,
            "linkedin": org.get("companyLinkedIn"),
        })

        existing = find_existing(name)

        if existing:
            company_id = existing["id"]
            updates = {}
            if description and not existing.get("description"):
                updates["description"] = description
            if website and not existing.get("website"):
                updates["website"] = website
            if existing.get("sector") in (None, "Other") and sector != "Other":
                updates["sector"] = sector
            if updates:
                update_company(company_id, **updates)
            stage = stage_raw or "Seed"
            if stage != "Unknown":
                update_company_stage(company_id, stage, "500 Global", date.today().isoformat())
            existing_count += 1
        else:
            company_id = insert_company(
                name=name,
                description=description,
                sector=sector,
                geography=country,
                website=website,
                stage=stage_raw or "Seed",
                heat_score=2,
                stage_source="500 Global",
                stage_detected_date=date.today().isoformat(),
            )
            new_count += 1

        insert_signal(
            company_id=company_id,
            source_type="program",
            source_name="500 Global",
            source_url=f"https://500.co/companies",
            signal_layer="curated",
            title=f"{name} — 500 Global ({cohort})" if cohort else f"{name} — 500 Global",
            metadata=metadata,
        )

        # Use invest year as cohort (batch name stays in metadata)
        invest_year = invest_date[:4] if invest_date else None
        insert_program(
            company_id=company_id,
            program_name="500 Global",
            program_type="Accelerator",
            program_country="USA",
            cohort=invest_year,
        )

    log(f"\n500 Global Europe: {len(eu_companies)} companies. "
        f"{new_count} new, {existing_count} already existed.")

    log(f"\n  Country breakdown:")
    for c, n in sorted(countries.items(), key=lambda x: -x[1]):
        log(f"    {c:25s} {n:>4}")

    log(f"\n  Sector breakdown:")
    for s, n in sorted(sectors.items(), key=lambda x: -x[1]):
        log(f"    {s:20s} {n:>4}")


if __name__ == "__main__":
    main()
