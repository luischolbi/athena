"""
EU-Startups directory scraper for Athena.

Fetches all startup listings from the EU-Startups WordPress Business Directory
via the WP REST API, then optionally enriches with detail page data.

API: GET https://www.eu-startups.com/wp-json/wp/v2/wpbdp_listing
     ?per_page=100&page={1..N}&orderby=date&order=desc

Categories (countries): GET /wp-json/wp/v2/wpbdp_category?per_page=100

Phase 1: Bulk API fetch → name, description, country, listing date
Phase 2 (--enrich N): Detail page scrape → website, city, tags, founded year

Total: ~33,885 listings across 30 European countries.
"""

import json
import sys
import os
import time
import argparse
from html import unescape

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
from database.database import (
    init_db,
    get_connection,
    insert_company,
    insert_signal,
    update_company,
    update_company_stage,
)

API_BASE = "https://www.eu-startups.com/wp-json/wp/v2"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

COUNTRY_MAP = {
    "United Kingdom": "UK",
}

# Sector detection keywords (applied to tags + description text)
SECTOR_RULES = [
    ("AI / ML", [
        "artificial intelligence", "machine learning", "deep learning",
        "nlp", "computer vision", "ai-powered", "generative ai", "llm",
        "neural network", "ai platform", "ai tool",
    ]),
    ("Fintech", [
        "fintech", "banking", "payments", "lending", "insurtech",
        "neobank", "defi", "cryptocurrency", "blockchain", "regtech",
    ]),
    ("Health / Bio", [
        "health", "biotech", "medtech", "pharma", "medical", "clinical",
        "genomic", "diagnostic", "therapeutics", "femtech", "mental health",
    ]),
    ("Climate", [
        "climate", "cleantech", "renewable", "sustainability", "carbon",
        "solar", "wind energy", "green energy", "circular economy",
    ]),
    ("Deep Tech", [
        "robotics", "quantum", "semiconductor", "space tech", "hardware",
        "iot", "3d printing", "nanotechnology", "autonomous", "drone",
    ]),
    ("SaaS", [
        "saas", "cloud platform", "devops", "cybersecurity", "crm",
        "erp", "hr tech", "martech", "analytics platform", "api",
    ]),
    ("Consumer", [
        "e-commerce", "ecommerce", "marketplace", "food delivery",
        "gaming", "social media", "fashion tech", "direct-to-consumer",
    ]),
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


def has_signal(company_id):
    """Check if a signal already exists for this company from EU-Startups."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM signals WHERE company_id = ? AND source_name = ?",
        (company_id, "EU-Startups"),
    ).fetchone()
    conn.close()
    return row is not None


def detect_sector(text):
    """Detect Athena sector from combined tags + description text."""
    if not text:
        return "Other"
    text_lower = text.lower()
    for sector, keywords in SECTOR_RULES:
        if any(kw in text_lower for kw in keywords):
            return sector
    return "Other"


def clean_html(html_str):
    """Strip HTML tags and decode entities."""
    if not html_str:
        return ""
    if "<" not in html_str:
        return unescape(html_str).strip()
    soup = BeautifulSoup(html_str, "html.parser")
    text = soup.get_text(separator=" ", strip=True)
    return unescape(text).strip()


def fetch_categories():
    """Fetch all WPBDP categories (countries) and return {id: name} map."""
    categories = {}
    page = 1
    while True:
        url = f"{API_BASE}/wpbdp_category?per_page=100&page={page}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code != 200:
                break
            data = r.json()
            if not data:
                break
            for cat in data:
                categories[cat["id"]] = cat["name"]
            total_pages = int(r.headers.get("X-WP-TotalPages", 1))
            if page >= total_pages:
                break
            page += 1
            time.sleep(0.5)
        except Exception as e:
            log(f"  Error fetching categories page {page}: {e}")
            break
    return categories


def fetch_listings_page(page_num, per_page=100, after=None):
    """Fetch a page of listings from the WP REST API."""
    params = {
        "per_page": per_page,
        "page": page_num,
        "orderby": "date",
        "order": "desc",
    }
    if after:
        params["after"] = f"{after}T00:00:00"
    r = requests.get(
        f"{API_BASE}/wpbdp_listing",
        headers=HEADERS, params=params, timeout=30,
    )
    r.raise_for_status()
    total = int(r.headers.get("X-WP-Total", 0))
    total_pages = int(r.headers.get("X-WP-TotalPages", 0))
    return r.json(), total, total_pages


def parse_detail_page(html):
    """Parse a WPBDP listing detail page for rich fields."""
    soup = BeautifulSoup(html, "html.parser")
    fields = {}

    for field_div in soup.find_all("div", class_="wpbdp-field-display"):
        label_el = field_div.find("span", class_="field-label")
        value_el = field_div.find("div", class_="value")
        if not label_el or not value_el:
            continue
        label = label_el.get_text(strip=True).rstrip(":")
        value = value_el.get_text(strip=True)

        if label == "Website":
            link = value_el.find("a", href=True)
            fields["website"] = link["href"] if link else value
        elif label == "Based in":
            fields["city"] = value
        elif label == "Tags":
            fields["tags"] = value
        elif label == "Founded":
            fields["founded"] = value
        elif label == "Total Funding":
            fields["funding"] = value
        elif label == "Company Status":
            fields["status"] = value
        elif label in ("Business Description", "Long Business Description"):
            if "description" not in fields or len(value) > len(fields["description"]):
                fields["description"] = value

    return fields


def main():
    parser = argparse.ArgumentParser(description="EU-Startups directory scraper")
    parser.add_argument(
        "--enrich", type=int, default=0, metavar="N",
        help="Fetch detail pages for up to N new listings to get website/city/tags",
    )
    parser.add_argument(
        "--since", type=str, default=None,
        help="Only fetch listings created after this date (YYYY-MM-DD)",
    )
    args = parser.parse_args()

    init_db()

    log("EU-Startups Directory Scraper")
    log("=" * 50)

    # 1. Fetch categories (countries)
    log("\n  Fetching categories...")
    categories = fetch_categories()
    log(f"  Found {len(categories)} categories")

    # 2. Paginate through listings via REST API
    log("\n  Fetching listings via REST API...")
    all_listings = []
    page = 1

    try:
        data, total, total_pages = fetch_listings_page(1, after=args.since)
    except requests.RequestException as e:
        log(f"  ERROR: Could not fetch first page: {e}")
        return

    all_listings.extend(data)
    log(f"  Total listings: {total} across {total_pages} pages")
    if args.since:
        log(f"  Filtering: created after {args.since}")

    while page < total_pages:
        page += 1
        if page % 25 == 0:
            log(f"    Page {page}/{total_pages} ({len(all_listings)} fetched)...")
        try:
            data, _, _ = fetch_listings_page(page, after=args.since)
            if not data:
                break
            all_listings.extend(data)
            time.sleep(1)
        except requests.RequestException as e:
            log(f"    Error on page {page}: {e}, retrying...")
            time.sleep(5)
            try:
                data, _, _ = fetch_listings_page(page, after=args.since)
                all_listings.extend(data)
            except Exception:
                log(f"    Skipping page {page}")
            continue

    log(f"  Fetched {len(all_listings)} listings total")

    # 3. Process listings
    new_count = 0
    existing_count = 0
    enriched_count = 0
    skipped = 0
    countries = {}
    sectors = {}

    for i, listing in enumerate(all_listings):
        name = clean_html(listing.get("title", {}).get("rendered", ""))
        if not name or len(name) < 2:
            skipped += 1
            continue

        # Country from category IDs
        cat_ids = listing.get("wpbdp_category", [])
        country = None
        for cid in cat_ids:
            c = categories.get(cid)
            if c:
                country = COUNTRY_MAP.get(c, c)
                break

        # Description from excerpt or content
        description = clean_html(listing.get("excerpt", {}).get("rendered", ""))
        if not description:
            description = clean_html(listing.get("content", {}).get("rendered", ""))
        if description and len(description) > 500:
            description = description[:497] + "..."

        listing_url = listing.get("link", "")
        listing_date = listing.get("date", "")[:10]

        if country:
            countries[country] = countries.get(country, 0) + 1

        existing = find_existing(name)

        # Detail page enrichment (optional)
        website = None
        city = None
        tags = None
        founded = None
        funding = None

        needs_enrich = (
            args.enrich > 0
            and enriched_count < args.enrich
            and listing_url
            and (not existing or not existing.get("website"))
        )
        if needs_enrich:
            try:
                r = requests.get(listing_url, headers=HEADERS, timeout=20)
                if r.status_code == 200:
                    fields = parse_detail_page(r.text)
                    website = fields.get("website")
                    city = fields.get("city")
                    tags = fields.get("tags", "")
                    founded = fields.get("founded")
                    funding = fields.get("funding")
                    if fields.get("description") and (
                        not description or len(fields["description"]) > len(description)
                    ):
                        description = fields["description"]
                    enriched_count += 1
                time.sleep(1.5)
            except Exception:
                pass

        sector = detect_sector(f"{tags or ''} {description}")
        sectors[sector] = sectors.get(sector, 0) + 1

        metadata = json.dumps({
            "eu_startups_id": listing.get("id"),
            "listing_date": listing_date,
            "tags": tags,
            "founded": founded,
            "funding": funding,
        })

        if existing:
            company_id = existing["id"]
            updates = {}
            if description and not existing.get("description"):
                updates["description"] = description
            if website and not existing.get("website"):
                updates["website"] = website
            if city and not existing.get("city"):
                updates["city"] = city
            if existing.get("sector") in (None, "Other") and sector != "Other":
                updates["sector"] = sector
            if updates:
                update_company(company_id, **updates)
            update_company_stage(company_id, "Seed", "EU-Startups", date.today().isoformat())
            existing_count += 1
        else:
            company_id = insert_company(
                name=name,
                description=description,
                sector=sector,
                geography=country,
                city=city,
                website=website,
                stage="Seed",
                heat_score=1,
                stage_source="EU-Startups",
                stage_detected_date=date.today().isoformat(),
            )
            new_count += 1

        if not has_signal(company_id):
            insert_signal(
                company_id=company_id,
                source_type="database",
                source_name="EU-Startups",
                source_url=listing_url,
                signal_layer="realtime",
                title=f"{name} — EU-Startups Directory",
                metadata=metadata,
            )

        if (i + 1) % 2000 == 0:
            log(f"    Processed {i + 1}/{len(all_listings)}...")

    log(f"\nEU-Startups Directory: {len(all_listings)} listings processed.")
    log(f"  {new_count} new, {existing_count} already existed, {skipped} skipped")
    if args.enrich:
        log(f"  {enriched_count} detail pages enriched")

    log(f"\n  Country breakdown (top 15):")
    for c, n in sorted(countries.items(), key=lambda x: -x[1])[:15]:
        log(f"    {c:25s} {n:>5}")

    log(f"\n  Sector breakdown:")
    for s, n in sorted(sectors.items(), key=lambda x: -x[1]):
        log(f"    {s:20s} {n:>5}")


if __name__ == "__main__":
    main()
