"""
Venture Kick portfolio scraper for Athena.

Scrapes all companies from https://www.venturekick.ch/portfolio,
extracts profile details (website, city, sector, VK stage), and
stores them in the database.
"""

import json
import re
import sys
import os
import time

import requests
from scrapers import fetch
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.database import (
    init_db,
    get_connection,
    insert_company,
    insert_signal,
    insert_program,
    update_company,
)

BASE_URL = "https://www.venturekick.ch"
PORTFOLIO_URL = f"{BASE_URL}/portfolio?profilesEntry=1"
AJAX_URL = f"{BASE_URL}/index.cfm?page=135343"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

REQUEST_DELAY = 1.5  # seconds between requests

# Map VK primary tag categories to Athena sectors
VK_PRIMARY_SECTOR = {
    "biotech": "Health / Bio",
    "medtech": "Health / Bio",
    "cleantech": "Climate",
    "electronics, mechanics": "Deep Tech",
    "micro-, nano technology": "Deep Tech",
    "materials, chemicals": "Deep Tech",
    "others": "Other",
}

# Sub-tag overrides — if any sub-tag matches, use this sector instead
VK_SUBTAG_OVERRIDE = {
    "machine learning / ai": "AI / ML",
    "fintech": "Fintech",
    "finance": "Fintech",
    "blockchain": "Fintech",
}


def map_vk_sector(tags):
    """Map Venture Kick sector tags to a normalized Athena sector.

    Tags are like ["Biotech", "Cancer", "Diagnostics"] or
    ["ICT", "Machine Learning / AI", "SaaS"].
    """
    if not tags:
        return "Other"

    # Check sub-tags first for specific overrides
    for tag in tags[1:]:
        override = VK_SUBTAG_OVERRIDE.get(tag.lower())
        if override:
            return override

    # Map by primary category
    primary = tags[0].lower()
    sector = VK_PRIMARY_SECTOR.get(primary)
    if sector:
        return sector

    # ICT is broad — default to SaaS unless sub-tags suggest otherwise
    if primary == "ict":
        return "SaaS"

    return "Other"


# VK stage comment indices → (stage label, funding amount, company stage)
VK_STAGES = {
    19: ("Stage 3", "CHF 150,000", "Pre-seed"),
    18: ("Stage 2", "CHF 40,000", "Pre-seed"),
    17: ("Stage 1", "CHF 10,000", "Grant only"),
}


def log(msg):
    print(msg, flush=True)


def find_existing(name):
    """Check if company already exists by name (case-insensitive)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM companies WHERE LOWER(name) = LOWER(?)", (name,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# --- Portfolio listing ---

def parse_company_cards(html):
    """Parse company cards from HTML, returning list of (name, description, profile_url)."""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for holder in soup.find_all("div", class_="company-holder"):
        link = holder.select_one(".txt-holder h2 span a")
        if not link:
            continue
        name = link.get_text(strip=True)
        profile_url = link["href"]

        # Description is the text node after the <span> inside <h2>
        h2 = holder.select_one(".txt-holder h2")
        desc = ""
        if h2:
            full_text = h2.get_text(strip=True)
            # Remove the company name prefix
            if full_text.startswith(name):
                desc = full_text[len(name):].strip()

        results.append((name, desc, profile_url))
    return results


def fetch_all_portfolio_cards():
    """Fetch all company cards from the portfolio via initial page + AJAX pagination."""
    all_cards = []

    # Initial page
    log("  Fetching portfolio page...")
    try:
        resp = fetch(PORTFOLIO_URL, headers=HEADERS)
    except requests.RequestException as e:
        log(f"  ERROR fetching portfolio page: {e}")
        return all_cards

    soup = BeautifulSoup(resp.text, "html.parser")
    all_rows = soup.find(id="all_rows")
    if all_rows:
        cards = parse_company_cards(str(all_rows))
        all_cards.extend(cards)
    log(f"    Initial page: {len(all_cards)} companies")

    # AJAX pagination
    row_count = 20
    batch = 0
    while True:
        batch += 1
        time.sleep(REQUEST_DELAY)
        try:
            resp = fetch(AJAX_URL, method="POST", data={"RowCount": row_count},
                         headers=HEADERS)
        except requests.RequestException as e:
            log(f"  ERROR on AJAX batch {batch}: {e}")
            break

        cards = parse_company_cards(resp.text)
        if not cards:
            break
        all_cards.extend(cards)

        # Extract next RowCount and check if there are more
        match = re.search(r'RowCount=(\d+)', resp.text)
        has_more = '.show-more").show()' in resp.text

        if not has_more or not match:
            break
        row_count = int(match.group(1))

        if batch % 10 == 0:
            log(f"    Batch {batch}: {len(all_cards)} companies so far")

    log(f"    Total: {len(all_cards)} companies found")
    return all_cards


# --- Company profile parsing ---

def fetch_profile(profile_url):
    """Fetch and parse a company's Venture Kick profile page.

    Returns dict with: website, city, sector_tags, vk_stage, funding_amount,
    company_stage, description.  Returns None on failure.
    """
    try:
        resp = fetch(profile_url, headers=HEADERS)
    except requests.RequestException as e:
        log(f"    WARNING: Failed to fetch {profile_url}: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    result = {
        "website": None,
        "city": None,
        "sector_tags": [],
        "vk_stage": None,
        "funding_amount": None,
        "company_stage": "Grant only",
        "description": None,
    }

    # --- Extract structured data from HTML comments in main-col ---
    main_col = soup.select_one("article.startup-detail div.main-col")
    if main_col:
        comments = re.findall(r'<!--\s*(.*?)\s*-->', str(main_col))
        # Comment layout (0-indexed):
        #  [6]  = city
        #  [9]  = website
        #  [17] = "1" if Stage 1 passed
        #  [18] = "2" if Stage 2 passed
        #  [19] = "4" if Stage 3 passed
        #  [22] = one-liner description
        #  [23] = full description

        if len(comments) > 9 and comments[9].strip():
            url = comments[9].strip()
            if not url.startswith("http"):
                url = "http://" + url
            result["website"] = url

        if len(comments) > 6 and comments[6].strip():
            result["city"] = comments[6].strip()

        if len(comments) > 23 and comments[23].strip():
            # Clean HTML tags from description
            desc_html = comments[23].strip()
            desc_soup = BeautifulSoup(desc_html, "html.parser")
            result["description"] = desc_soup.get_text(separator=" ", strip=True)
        elif len(comments) > 22 and comments[22].strip():
            result["description"] = comments[22].strip()

        # Determine highest VK stage reached
        for idx, (stage_label, funding, company_stage) in VK_STAGES.items():
            if len(comments) > idx and comments[idx].strip():
                result["vk_stage"] = stage_label
                result["funding_amount"] = funding
                result["company_stage"] = company_stage
                break  # VK_STAGES is ordered highest-first

    # --- Sector tags from sidebar ---
    tags_ul = soup.select_one("aside.sub-col ul.tags")
    if tags_ul:
        for li in tags_ul.find_all("li"):
            tag_text = li.get_text(strip=True)
            if tag_text:
                result["sector_tags"].append(tag_text)

    # --- Fallback: city from sidebar text ---
    if not result["city"]:
        sidebar = soup.select_one("aside.sub-col div.sub-col-box")
        if sidebar:
            text = sidebar.get_text()
            match = re.search(r'Headquarter:\s*(.+?)(?:\n|$)', text)
            if match:
                result["city"] = match.group(1).strip()

    return result


# --- Main ---

def get_existing_vk_names():
    """Return set of company names that already have a Venture Kick signal."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT DISTINCT LOWER(c.name) FROM companies c
        JOIN signals s ON s.company_id = c.id
        WHERE s.source_name = 'Venture Kick'
    """).fetchall()
    conn.close()
    return {r[0] for r in rows}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Scrape Venture Kick portfolio")
    parser.add_argument("--resume", action="store_true",
                        help="Skip companies already in the DB with a VK signal")
    args = parser.parse_args()

    init_db()

    log("Venture Kick Scraper")
    log("=" * 50)

    # Phase 1: Fetch all portfolio cards
    log("\nPhase 1: Fetching portfolio listing...")
    cards = fetch_all_portfolio_cards()
    if not cards:
        log("No companies found. Exiting.")
        return

    # Filter out already-processed companies when resuming
    skipped = 0
    if args.resume:
        already_done = get_existing_vk_names()
        original = len(cards)
        cards = [(n, d, u) for n, d, u in cards if n.lower() not in already_done]
        skipped = original - len(cards)
        log(f"  --resume: skipping {skipped} already-processed companies")

    # Phase 2: Visit each profile page and store data
    log(f"\nPhase 2: Fetching {len(cards)} company profiles...")
    new_count = 0
    existing_count = 0
    errors = 0

    for i, (name, card_desc, profile_url) in enumerate(cards, 1):
        time.sleep(REQUEST_DELAY)

        profile = fetch_profile(profile_url)
        if profile is None:
            errors += 1
            # Still store with card-level data
            profile = {
                "website": None, "city": None, "sector_tags": [],
                "vk_stage": None, "funding_amount": None,
                "company_stage": "Grant only", "description": None,
            }

        description = profile["description"] or card_desc
        sector = map_vk_sector(profile["sector_tags"])

        # Check for existing company
        existing = find_existing(name)

        if existing:
            company_id = existing["id"]
            # Update with richer data if available
            updates = {}
            if profile["website"] and not existing.get("website"):
                updates["website"] = profile["website"]
            if profile["city"] and not existing.get("city"):
                updates["city"] = profile["city"]
            if sector != "Other" and existing.get("sector") not in (
                "AI / ML", "Fintech", "Climate", "Health / Bio", "SaaS", "Deep Tech",
            ):
                updates["sector"] = sector
            if description and not existing.get("description"):
                updates["description"] = description
            if existing.get("geography") in (None, "Unknown"):
                updates["geography"] = "Switzerland"
            update_company(company_id, **updates)
            existing_count += 1
        else:
            company_id = insert_company(
                name=name,
                description=description,
                sector=sector,
                geography="Switzerland",
                city=profile["city"],
                website=profile["website"],
                stage=profile["company_stage"],
                heat_score=2,
            )
            new_count += 1

        # Always add signal
        insert_signal(
            company_id=company_id,
            source_type="program",
            source_name="Venture Kick",
            source_url=profile_url,
            signal_layer="curated",
            title=f"{name} — Venture Kick portfolio",
            metadata=json.dumps({
                "vk_stage": profile["vk_stage"],
                "funding_amount": profile["funding_amount"],
                "sector_tags": profile["sector_tags"],
            }),
        )

        # Add program entry
        if profile["vk_stage"]:
            insert_program(
                company_id=company_id,
                program_name="Venture Kick",
                program_type="Grant",
                program_country="Switzerland",
                cohort=profile["vk_stage"],
                funding_amount=profile["funding_amount"],
            )

        if i % 50 == 0:
            log(f"  [{i}/{len(cards)}] processed "
                f"({new_count} new, {existing_count} existing, {errors} errors)")

    log(f"\nVenture Kick: Found {len(cards) + skipped} companies total. "
        f"{new_count} new, {existing_count} already existed, {skipped} skipped (resume)."
        + (f" ({errors} profile fetch errors)" if errors else ""))


if __name__ == "__main__":
    main()
