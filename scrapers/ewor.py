"""
EWOR fellowship portfolio scraper for Athena.

Scrapes all fellows from ewor.com/year/{year} pages (2023, 2024, 2025),
then visits each /fellows/{slug} profile for company details.

Each year page lists fellows as cards linking to /fellows/{slug}.
Each fellow profile has: company name, description, website, LinkedIn.
"""

import json
import re
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

BASE_URL = "https://www.ewor.com"
YEAR_PAGES = [2025, 2024, 2023]
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# Keyword-based sector detection
SECTOR_RULES = [
    ("AI / ML", ["artificial intelligence", "machine learning", "deep learning",
                 "nlp", "computer vision", "llm", "generative ai", " ai "]),
    ("Health / Bio", ["health", "biotech", "medical", "pharma", "clinical",
                      "diagnostics", "therapeutics", "drug", "surgical",
                      "neuro", "brain"]),
    ("Fintech", ["fintech", "banking", "payments", "insurance", "financial",
                 "lending", "neobank"]),
    ("Climate", ["climate", "carbon", "renewable", "solar", "energy",
                 "sustainability", "cleantech", "green", "sustainable"]),
    ("SaaS", ["saas", "software", "platform", "b2b", "enterprise", "cloud",
              "api", "automation"]),
    ("Deep Tech", ["robotics", "quantum", "semiconductor", "hardware",
                   "materials", "space", "satellite", "drone", "3d"]),
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


def detect_sector(text):
    """Detect Athena sector from description text using keyword rules."""
    if not text:
        return "Other"
    lower = f" {text.lower()} "
    for sector, keywords in SECTOR_RULES:
        if any(kw in lower for kw in keywords):
            return sector
    return "Other"


def fetch_year_fellows(year):
    """Fetch fellow profile URLs from a /year/{year} page."""
    url = f"{BASE_URL}/year/{year}"
    log(f"  Fetching {url}...")
    try:
        resp = fetch(url, headers=HEADERS)
    except requests.RequestException as e:
        log(f"    ERROR: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    fellows = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/fellows/" in href:
            full_url = href if href.startswith("http") else f"{BASE_URL}{href}"
            if full_url not in [f[0] for f in fellows]:
                fellows.append((full_url, year))
    log(f"    Found {len(fellows)} fellows for {year}")
    return fellows


def parse_fellow_page(url):
    """Parse a /fellows/{slug} page for company data."""
    try:
        resp = fetch(url, headers=HEADERS)
    except requests.RequestException as e:
        log(f"    ERROR fetching {url}: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # Company name — look for it in various possible elements
    # The fellow page has a company showcase card
    company_name = None
    description = None

    # Try to find company name from page text/structure
    # Look for h-tags and text blocks
    for h in soup.find_all(["h1", "h2", "h3"]):
        text = h.get_text(strip=True)
        # Skip navigation items and common headers
        if text.lower() in ("fellow", "more from ewor", "ewor", ""):
            continue
        # Fellow name is usually the first prominent heading
        # Company name often appears in a card or secondary heading
        if not company_name and len(text) > 1 and len(text) < 60:
            # Check if this looks like a person name (contains space, no special chars)
            if " " in text and not any(c in text for c in ".,;:!?"):
                continue  # likely a person name, skip
            company_name = text

    # Get description from meta tags
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc and meta_desc.get("content"):
        description = meta_desc["content"].strip()

    # Try og:description
    if not description:
        og_desc = soup.find("meta", attrs={"property": "og:description"})
        if og_desc and og_desc.get("content"):
            description = og_desc["content"].strip()

    # Website and LinkedIn
    website = None
    linkedin = None
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "linkedin.com/in/" in href or "linkedin.com/company/" in href:
            linkedin = href
        elif (href.startswith("http") and "ewor.com" not in href
              and "linkedin.com" not in href and "webflow" not in href
              and "google" not in href and "facebook" not in href
              and "twitter" not in href and "instagram" not in href
              and "youtube" not in href and "tiktok" not in href
              and "cookie" not in href):
            if not website:
                website = href

    # Get fellow name from the slug
    slug = url.rstrip("/").split("/")[-1]
    fellow_name = slug.replace("-", " ").title()

    return {
        "company_name": company_name,
        "fellow_name": fellow_name,
        "description": description,
        "website": website,
        "linkedin": linkedin,
        "source_url": url,
    }


def main():
    init_db()

    log("EWOR Portfolio Scraper")
    log("=" * 50)

    # 1. Collect fellow URLs from all year pages
    log("\n  Phase 1: Collecting fellows from year pages...")
    all_fellows = []
    for year in YEAR_PAGES:
        fellows = fetch_year_fellows(year)
        all_fellows.extend(fellows)
        time.sleep(1.5)

    # Deduplicate by URL
    seen = set()
    unique_fellows = []
    for url, year in all_fellows:
        if url not in seen:
            seen.add(url)
            unique_fellows.append((url, year))

    log(f"\n  Total unique fellows: {len(unique_fellows)}")

    # 2. Scrape each fellow profile
    log("\n  Phase 2: Scraping fellow profiles...")
    new_count = 0
    existing_count = 0
    errors = 0
    sectors = {}

    for i, (url, year) in enumerate(unique_fellows, 1):
        slug = url.rstrip("/").split("/")[-1]
        log(f"  [{i}/{len(unique_fellows)}] {slug} ({year})")

        data = parse_fellow_page(url)
        if not data or not data.get("company_name"):
            # Use slug-derived name as fallback
            if data:
                name = data["fellow_name"] + " (EWOR)"
            else:
                errors += 1
                continue
        else:
            name = data["company_name"]

        description = data.get("description")
        sector = detect_sector(description)
        sectors[sector] = sectors.get(sector, 0) + 1

        metadata = json.dumps({
            "fellow": data.get("fellow_name"),
            "website": data.get("website"),
            "linkedin": data.get("linkedin"),
            "year": year,
        })

        existing = find_existing(name)

        if existing:
            company_id = existing["id"]
            updates = {}
            if description and not existing.get("description"):
                updates["description"] = description
            if data.get("website") and not existing.get("website"):
                updates["website"] = data["website"]
            if existing.get("sector") in (None, "Other") and sector != "Other":
                updates["sector"] = sector
            if updates:
                update_company(company_id, **updates)
            update_company_stage(company_id, "Pre-seed", "EWOR", date.today().isoformat())
            existing_count += 1
        else:
            company_id = insert_company(
                name=name,
                description=description,
                sector=sector,
                geography="Europe",
                website=data.get("website"),
                stage="Pre-seed",
                heat_score=2,
                stage_source="EWOR",
                stage_detected_date=date.today().isoformat(),
            )
            new_count += 1

        insert_signal(
            company_id=company_id,
            source_type="program",
            source_name="EWOR",
            source_url=data["source_url"],
            signal_layer="curated",
            title=f"{name} — EWOR Fellowship {year}",
            metadata=metadata,
        )

        insert_program(
            company_id=company_id,
            program_name="EWOR",
            program_type="Accelerator",
            program_country="Germany",
            cohort=str(year),
        )

        if i < len(unique_fellows):
            time.sleep(1.5)

    log(f"\nEWOR: {len(unique_fellows)} fellows. "
        f"{new_count} new, {existing_count} already existed."
        + (f" ({errors} errors)" if errors else ""))

    log(f"\n  Sector breakdown:")
    for s, c in sorted(sectors.items(), key=lambda x: -x[1]):
        log(f"    {s:20s} {c:>4}")


if __name__ == "__main__":
    main()
