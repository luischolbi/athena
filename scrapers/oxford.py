"""
Oxford University Innovation spin-out scraper for Athena.

Downloads the CSV export from:
  https://innovation.ox.ac.uk/portfolio/companies-formed/?downloadcsv=1

The portfolio page is a dynamic paginated database, but Oxford provides
a full CSV export with all ~296 spin-out companies including name,
description, sector, website, origin department, and founding date.
"""

import csv
import io
import json
import re
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

CSV_URL = "https://innovation.ox.ac.uk/portfolio/companies-formed/?downloadcsv=1"
PAGE_URL = "https://innovation.ox.ac.uk/portfolio/companies-formed/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# Map Oxford sector strings to Athena sectors
OXFORD_SECTOR_MAP = {
    "Therapeutics and Biotech": "Health / Bio",
    "Medtech": "Health / Bio",
    "Software and Internet": "SaaS",
    "Digital and Digital Entertainment": "SaaS",
    "Engineering": "Deep Tech",
    "Electronics and Materials": "Deep Tech",
    "Other Technology": "Other",
}

# Fallback keyword detection from description
SECTOR_RULES = [
    ("AI / ML",      [r"\bAI\b", r"\bML\b", r"machine learning", r"\bLLM\b",
                      r"\bGPT\b", r"neural net", r"deep learning",
                      r"artificial intelligence", r"computer vision"]),
    ("Fintech",      [r"fintech", r"banking", r"payments?\b", r"neobank",
                      r"insurance", r"lending", r"financial"]),
    ("Climate",      [r"climate", r"carbon", r"\benergy\b", r"\bsolar\b",
                      r"clean\s*tech", r"sustainability", r"hydropower"]),
    ("Health / Bio", [r"health", r"medical", r"biotech", r"pharma",
                      r"genomic", r"diagnostic", r"therapeutics",
                      r"drug discovery", r"antibod", r"immuno",
                      r"oncology", r"cancer", r"clinical"]),
    ("SaaS",         [r"\bSaaS\b", r"\bB2B\b", r"\bplatform\b",
                      r"developer", r"infrastructure", r"\bAPI\b",
                      r"software"]),
    ("Deep Tech",    [r"quantum", r"photon", r"sensor", r"robotic",
                      r"semiconductor", r"materials?"]),
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


def map_sector(oxford_sector, description):
    """Map Oxford's sector to Athena sector, with keyword fallback."""
    # Oxford CSV can have comma-separated multi-sectors like
    # "Medtech, Therapeutics and Biotech" — take the first one
    if oxford_sector:
        first = oxford_sector.split(",")[0].strip()
        mapped = OXFORD_SECTOR_MAP.get(first)
        if mapped and mapped != "Other":
            return mapped

    # Keyword fallback on description
    text = " ".join(filter(None, [description, oxford_sector]))
    if text:
        for sector, patterns in SECTOR_RULES:
            for pat in patterns:
                if re.search(pat, text, re.IGNORECASE):
                    return sector

    return "Other"


def parse_year(date_str):
    """Extract year from 'April 2025' format."""
    if not date_str:
        return None
    m = re.search(r"\b((?:19|20)\d{2})\b", date_str)
    return m.group(1) if m else None


def main():
    init_db()

    log("Oxford University Innovation Scraper")
    log("=" * 50)

    # Download CSV export
    log(f"\nFetching CSV export...")
    try:
        resp = fetch(CSV_URL, headers=HEADERS)
    except requests.RequestException as e:
        log(f"ERROR: {e}")
        return

    # Parse CSV from response text
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    log(f"  Got {len(rows)} companies from CSV export")

    new_count = 0
    existing_count = 0
    skipped = 0
    sectors = {}

    for row in rows:
        name = (row.get("title") or "").strip()
        if not name:
            skipped += 1
            continue

        description = (row.get("blurb") or "").strip() or None
        oxford_sector = (row.get("sector") or "").strip() or None
        website = (row.get("url") or "").strip() or None
        origin = (row.get("origin") or "").strip() or None
        date_str = (row.get("date") or "").strip() or None
        year = parse_year(date_str)

        sector = map_sector(oxford_sector, description)
        sectors[sector] = sectors.get(sector, 0) + 1

        metadata = json.dumps({
            "oxford_sector": oxford_sector,
            "origin": origin,
            "founded": date_str,
        })

        existing = find_existing(name)

        if existing:
            company_id = existing["id"]
            updates = {}
            if website and not existing.get("website"):
                updates["website"] = website
            if description and not existing.get("description"):
                updates["description"] = description
            if existing.get("sector") in (None, "Other") and sector != "Other":
                updates["sector"] = sector
            if not existing.get("city"):
                updates["city"] = "Oxford"
            if updates:
                update_company(company_id, **updates)
            update_company_stage(company_id, "Pre-seed", "University of Oxford", date.today().isoformat())
            existing_count += 1
        else:
            company_id = insert_company(
                name=name,
                description=description,
                sector=sector,
                geography="UK",
                city="Oxford",
                website=website,
                stage="Pre-seed",
                heat_score=2,
                stage_source="University of Oxford",
                stage_detected_date=date.today().isoformat(),
            )
            new_count += 1

        insert_signal(
            company_id=company_id,
            source_type="program",
            source_name="University of Oxford",
            source_url=PAGE_URL,
            signal_layer="curated",
            title=f"{name} — Oxford spin-out" + (f" ({year})" if year else ""),
            metadata=metadata,
        )

        insert_program(
            company_id=company_id,
            program_name="University of Oxford",
            program_type="University Spin-off",
            program_country="UK",
            cohort=year,
        )

    log(f"\nOxford: Found {len(rows)} spin-outs. "
        f"{new_count} new, {existing_count} already existed"
        + (f", {skipped} skipped (no name)" if skipped else "")
        + ".")

    log(f"\n  Sector breakdown:")
    for s, c in sorted(sectors.items(), key=lambda x: -x[1]):
        log(f"    {s:20s} {c:>4}")


if __name__ == "__main__":
    main()
