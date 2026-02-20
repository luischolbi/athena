"""
EPFL spin-off scraper for Athena.

Extracts startup data from the VPI Startup Compass app at:
  https://vpi-startup-compass.pages.dev/startup-list/

The Astro/AG Grid app embeds a JSON array of 600+ startups directly in
its JavaScript bundle. This scraper downloads the bundle, extracts the
JSON data, and imports active (Incorporated) companies.

Fields per startup: Id, Name, Description, Year, Sectors, Status,
Website, Keywords, Laboratory.
"""

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

PAGE_URL = "https://vpi-startup-compass.pages.dev/startup-list/"
BASE_URL = "https://vpi-startup-compass.pages.dev"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# Map EPFL sectors to Athena sectors
EPFL_SECTOR_MAP = {
    "ICT/AI": "AI / ML",
    "Biotech": "Health / Bio",
    "Medtech": "Health / Bio",
    "Engineering": "Deep Tech",
    "Cleantech": "Climate",
}

# Fallback keyword detection from description
SECTOR_RULES = [
    ("AI / ML",      [r"\bAI\b", r"\bML\b", r"machine learning", r"\bLLM\b",
                      r"\bGPT\b", r"neural net", r"deep learning",
                      r"artificial intelligence", r"computer vision"]),
    ("Fintech",      [r"fintech", r"banking", r"payments?\b", r"neobank",
                      r"insurance", r"lending", r"financial"]),
    ("Climate",      [r"climate", r"carbon", r"\benergy\b", r"\bsolar\b",
                      r"clean\s*tech", r"sustainability"]),
    ("Health / Bio", [r"health", r"medical", r"biotech", r"pharma",
                      r"genomic", r"diagnostic", r"therapeutics"]),
    ("SaaS",         [r"\bSaaS\b", r"\bB2B\b", r"\bplatform\b",
                      r"developer", r"infrastructure", r"\bAPI\b"]),
    ("Deep Tech",    [r"quantum", r"photon", r"sensor", r"robotic",
                      r"semiconductor", r"material"]),
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


def map_sector(epfl_sector, description, keywords):
    """Map EPFL sector to Athena sector, with keyword fallback."""
    if epfl_sector:
        mapped = EPFL_SECTOR_MAP.get(epfl_sector)
        if mapped:
            return mapped

    text = " ".join(filter(None, [description, keywords, epfl_sector]))
    if text:
        for sector, patterns in SECTOR_RULES:
            for pat in patterns:
                if re.search(pat, text, re.IGNORECASE):
                    return sector

    return "Other"


def find_js_bundle_url():
    """Fetch the page HTML and extract the AG Grid JS bundle URL."""
    log("  Fetching page to find JS bundle...")
    resp = fetch(PAGE_URL, headers=HEADERS)
    match = re.search(r'(/_astro/AgGrid\.[^"\'> ]+\.js)', resp.text)
    if not match:
        raise RuntimeError("Could not find AgGrid JS bundle URL in page HTML")
    return BASE_URL + match.group(1)


def extract_startups_from_bundle(js_url):
    """Download the JS bundle and extract the embedded JSON array."""
    log(f"  Fetching JS bundle...")
    resp = fetch(js_url, headers=HEADERS)
    content = resp.text

    # Find the JSON array with startup objects: [{"Id":...,"Name":...}, ...]
    start = content.find('[{"Id":')
    if start == -1:
        raise RuntimeError("Could not find startup JSON data in JS bundle")

    # Find matching closing bracket
    depth = 0
    end = start
    for i in range(start, len(content)):
        if content[i] == '[':
            depth += 1
        elif content[i] == ']':
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    data = json.loads(content[start:end])
    return data


def main():
    init_db()

    log("EPFL Spin-off Scraper")
    log("=" * 50)

    try:
        js_url = find_js_bundle_url()
        all_startups = extract_startups_from_bundle(js_url)
    except (requests.RequestException, RuntimeError) as e:
        log(f"ERROR: {e}")
        return

    log(f"  Total in database: {len(all_startups)}")

    # Filter to active companies (Incorporated + In creation)
    # Exclude "Stopped" (defunct) but keep "M&A" (acquired — still a valid signal)
    ACTIVE_STATUSES = {"Incorporated", "In creation", "M&A"}
    active = [s for s in all_startups if s.get("Status") in ACTIVE_STATUSES]

    status_counts = {}
    for s in all_startups:
        st = s.get("Status") or "Unknown"
        status_counts[st] = status_counts.get(st, 0) + 1
    log(f"  Status breakdown: {status_counts}")
    log(f"  Active (Incorporated + In creation + M&A): {len(active)}")

    new_count = 0
    existing_count = 0
    sectors = {}

    for startup in active:
        name = (startup.get("Name") or "").strip()
        if not name:
            continue

        description = (startup.get("Description") or "").strip() or None
        # Clean escaped newlines from descriptions
        if description:
            description = description.replace("\\n", " ").strip()

        website = (startup.get("Website") or "").strip() or None
        epfl_sector = (startup.get("Sectors") or "").strip() or None
        keywords = (startup.get("Keywords") or "").strip() or None
        year = startup.get("Year")
        lab = (startup.get("Laboratory") or "").strip() or None
        epfl_id = startup.get("Id")

        sector = map_sector(epfl_sector, description, keywords)
        sectors[sector] = sectors.get(sector, 0) + 1

        metadata = json.dumps({
            "epfl_id": epfl_id,
            "epfl_sector": epfl_sector,
            "keywords": keywords,
            "laboratory": lab,
            "year": year,
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
                updates["city"] = "Lausanne"
            if updates:
                update_company(company_id, **updates)
            update_company_stage(company_id, "Pre-seed", "EPFL", date.today().isoformat())
            existing_count += 1
        else:
            company_id = insert_company(
                name=name,
                description=description,
                sector=sector,
                geography="Switzerland",
                city="Lausanne",
                website=website,
                stage="Pre-seed",
                heat_score=2,
                stage_source="EPFL",
                stage_detected_date=date.today().isoformat(),
            )
            new_count += 1

        insert_signal(
            company_id=company_id,
            source_type="program",
            source_name="EPFL",
            source_url=PAGE_URL,
            signal_layer="curated",
            title=f"{name} — EPFL spin-off" + (f" ({year})" if year else ""),
            metadata=metadata,
        )

        insert_program(
            company_id=company_id,
            program_name="EPFL",
            program_type="University Spin-off",
            program_country="Switzerland",
            cohort=str(year) if year else None,
        )

    log(f"\nEPFL: Found {len(active)} active spin-offs. "
        f"{new_count} new, {existing_count} already existed.")

    log(f"\n  Sector breakdown:")
    for s, c in sorted(sectors.items(), key=lambda x: -x[1]):
        log(f"    {s:20s} {c:>4}")


if __name__ == "__main__":
    main()
