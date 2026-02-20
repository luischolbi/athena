"""
DTU Science Park companies scraper for Athena.

Scrapes https://dtusciencepark.com/community/companies-in-dtu-science-park/
— a single static page with ~275 company links inside a #companies-grid div.

Each company is an <a class="company"> tag with:
  - href: company website
  - data-branche: sector (biotech, cleantech, software, etc.)
  - data-lokation: campus location
  - text content: company name
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

PAGE_URL = "https://dtusciencepark.com/community/companies-in-dtu-science-park/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# Map DTU data-branche values to Athena sectors
DTU_SECTOR_MAP = {
    "biotech": "Health / Bio",
    "biotek": "Health / Bio",
    "medico": "Health / Bio",
    "cleantech": "Climate",
    "software": "SaaS",
    "ict": "SaaS",
    "ikt": "SaaS",
    "hardware": "Deep Tech",
    "instrumentation": "Deep Tech",
    "consumer-goods": "Other",
    "service": "Other",
    "raadgivning": "Other",
    "other": "Other",
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


def map_sector(branche):
    """Map DTU data-branche value to Athena sector.

    data-branche can be space-separated for multi-sector companies
    (e.g. "hardware instrumentation"). We take the first recognized one.
    """
    if not branche:
        return "Other"
    for token in branche.strip().split():
        mapped = DTU_SECTOR_MAP.get(token)
        if mapped and mapped != "Other":
            return mapped
    return "Other"


def main():
    init_db()

    log("DTU Science Park Scraper")
    log("=" * 50)

    log(f"\nFetching {PAGE_URL}...")
    try:
        resp = fetch(PAGE_URL, headers=HEADERS)
    except requests.RequestException as e:
        log(f"ERROR: {e}")
        return

    soup = BeautifulSoup(resp.text, "html.parser")
    entries = soup.find_all("a", class_="company")
    log(f"  Found {len(entries)} company entries")

    new_count = 0
    existing_count = 0
    skipped = 0
    sectors = {}

    for entry in entries:
        name = entry.get_text(strip=True)
        if not name:
            skipped += 1
            continue

        website = (entry.get("href") or "").strip() or None
        branche = (entry.get("data-branche") or "").strip()
        lokation = (entry.get("data-lokation") or "").strip()

        sector = map_sector(branche)
        sectors[sector] = sectors.get(sector, 0) + 1

        metadata = json.dumps({
            "dtu_branche": branche or None,
            "dtu_lokation": lokation or None,
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
            update_company_stage(company_id, "Pre-seed", "DTU Science Park", date.today().isoformat())
            existing_count += 1
        else:
            company_id = insert_company(
                name=name,
                sector=sector,
                geography="Denmark",
                city="Copenhagen",
                website=website,
                stage="Pre-seed",
                heat_score=2,
                stage_source="DTU Science Park",
                stage_detected_date=date.today().isoformat(),
            )
            new_count += 1

        insert_signal(
            company_id=company_id,
            source_type="program",
            source_name="DTU Science Park",
            source_url=PAGE_URL,
            signal_layer="curated",
            title=f"{name} — DTU Science Park",
            metadata=metadata,
        )

        insert_program(
            company_id=company_id,
            program_name="DTU Science Park",
            program_type="University Spin-off",
            program_country="Denmark",
        )

    log(f"\nDTU Science Park: Found {len(entries)} companies. "
        f"{new_count} new, {existing_count} already existed"
        + (f", {skipped} skipped (no name)" if skipped else "")
        + ".")

    log(f"\n  Sector breakdown:")
    for s, c in sorted(sectors.items(), key=lambda x: -x[1]):
        log(f"    {s:20s} {c:>4}")


if __name__ == "__main__":
    main()
