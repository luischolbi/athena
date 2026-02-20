"""
University of Zurich spin-off/startup scraper for Athena.

Scrapes https://www.innovation.uzh.ch/en/stories/uzh-spinoffs-startups.html
— a single HTML table with ~187 companies.

Columns: Company (name + link), Type (Spin-off/Startup), Sector, Founded.
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

PAGE_URL = "https://www.innovation.uzh.ch/en/stories/uzh-spinoffs-startups.html"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# Map UZH sector strings to Athena sectors
# UZH has messy casing and "DigICTal" artifacts
UZH_SECTOR_MAP = {
    "medtech": "Health / Bio",
    "biotech": "Health / Bio",
    "healthtech": "Health / Bio",
    "pharmatech": "Health / Bio",
    "pharmaceutical": "Health / Bio",
    "life science": "Health / Bio",
    "ict": "SaaS",
    "software": "SaaS",
    "edtech": "SaaS",
    "legaltech": "SaaS",
    "proptech": "SaaS",
    "hrtech": "SaaS",
    "martech": "SaaS",
    "govtech": "SaaS",
    "regtech": "SaaS",
    "fintech": "Fintech",
    "insurtech": "Fintech",
    "cleantech": "Climate",
    "sustainability": "Climate",
    "agritech": "Climate",
    "foodtech": "Climate",
}

# Fallback keyword detection
SECTOR_RULES = [
    ("AI / ML",      [r"\bAI\b", r"\bML\b", r"machine learning", r"\bLLM\b",
                      r"artificial intelligence", r"deep learning"]),
    ("Fintech",      [r"fintech", r"banking", r"payments?\b", r"financial"]),
    ("Climate",      [r"climate", r"carbon", r"\benergy\b", r"clean\s*tech",
                      r"sustainability"]),
    ("Health / Bio", [r"health", r"medical", r"biotech", r"pharma",
                      r"diagnostic", r"therapeutics"]),
    ("SaaS",         [r"\bSaaS\b", r"\bB2B\b", r"\bplatform\b",
                      r"software"]),
    ("Deep Tech",    [r"quantum", r"photon", r"sensor", r"robotic",
                      r"semiconductor"]),
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


def map_sector(uzh_sector):
    """Map UZH sector string to Athena sector.

    UZH sectors have messy casing (FinTech, MedTech, DigICTal/AI)
    and sometimes compound values with slashes.
    """
    if not uzh_sector or uzh_sector.lower() in ("n/a", ""):
        return "Other"

    # Normalize: lowercase, strip, handle "DigICTal/AI" → "digital/ai"
    normalized = uzh_sector.lower().strip()

    # Check for AI keywords first (e.g. "DigICTal/AI", "AI")
    if re.search(r'\bai\b', normalized):
        return "AI / ML"

    # Try direct map on slash-separated parts
    for part in re.split(r'[/,]', normalized):
        part = part.strip()
        mapped = UZH_SECTOR_MAP.get(part)
        if mapped:
            return mapped

    # Keyword fallback
    for sector, patterns in SECTOR_RULES:
        for pat in patterns:
            if re.search(pat, uzh_sector, re.IGNORECASE):
                return sector

    return "Other"


def main():
    init_db()

    log("University of Zurich Spin-offs Scraper")
    log("=" * 50)

    log(f"\nFetching {PAGE_URL}...")
    try:
        resp = fetch(PAGE_URL, headers=HEADERS)
    except requests.RequestException as e:
        log(f"ERROR: {e}")
        return

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", class_="tablesorter")
    if not table:
        log("ERROR: Could not find company table")
        return

    rows = table.find_all("tr")[1:]  # skip header
    log(f"  Found {len(rows)} companies in table")

    new_count = 0
    existing_count = 0
    skipped = 0
    sectors = {}

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 4:
            skipped += 1
            continue

        # Column 0: Company name + link
        name_cell = cells[0]
        link = name_cell.find("a", href=True)
        name = name_cell.get_text(strip=True)
        website = link["href"].strip() if link else None

        if not name:
            skipped += 1
            continue

        # Column 1: Type (Spin-off / Startup)
        company_type = cells[1].get_text(strip=True)

        # Column 2: Sector
        uzh_sector = cells[2].get_text(strip=True)

        # Column 3: Founded (may be wrapped in <p>)
        year = cells[3].get_text(strip=True)

        sector = map_sector(uzh_sector)
        sectors[sector] = sectors.get(sector, 0) + 1

        metadata = json.dumps({
            "uzh_sector": uzh_sector or None,
            "uzh_type": company_type,
            "year": year or None,
        })

        existing = find_existing(name)

        if existing:
            company_id = existing["id"]
            updates = {}
            if website and not existing.get("website"):
                updates["website"] = website
            if existing.get("sector") in (None, "Other") and sector != "Other":
                updates["sector"] = sector
            if updates:
                update_company(company_id, **updates)
            update_company_stage(company_id, "Pre-seed", "University of Zurich", date.today().isoformat())
            existing_count += 1
        else:
            company_id = insert_company(
                name=name,
                sector=sector,
                geography="Switzerland",
                city="Zurich",
                website=website,
                stage="Pre-seed",
                heat_score=2,
                stage_source="University of Zurich",
                stage_detected_date=date.today().isoformat(),
            )
            new_count += 1

        insert_signal(
            company_id=company_id,
            source_type="program",
            source_name="University of Zurich",
            source_url=PAGE_URL,
            signal_layer="curated",
            title=f"{name} — UZH {company_type}" + (f" ({year})" if year else ""),
            metadata=metadata,
        )

        insert_program(
            company_id=company_id,
            program_name="University of Zurich",
            program_type="University Spin-off",
            program_country="Switzerland",
            cohort=year or None,
        )

    log(f"\nUniversity of Zurich: Found {len(rows)} companies. "
        f"{new_count} new, {existing_count} already existed"
        + (f", {skipped} skipped" if skipped else "")
        + ".")

    log(f"\n  Sector breakdown:")
    for s, c in sorted(sectors.items(), key=lambda x: -x[1]):
        log(f"    {s:20s} {c:>4}")


if __name__ == "__main__":
    main()
