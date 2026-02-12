"""
ETH AI Center affiliated startups scraper for Athena.

Scrapes https://ai.ethz.ch/entrepreneurship/affiliated-startups.html
and stores each startup in the database.
"""

import json
import re
import sys
import os

import requests
from bs4 import BeautifulSoup
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

PAGE_URL = "https://ai.ethz.ch/entrepreneurship/affiliated-startups.html"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
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


def parse_startup(wrapper):
    """Parse a single textimage__wrapper div into startup data.

    Returns dict with name, website, description, affiliation_year,
    affiliation_connection — or None if this isn't a startup entry.
    """
    full_text = wrapper.get_text(separator="\n")

    # Only process entries that have affiliation info (startups, not sponsors)
    if "affiliation" not in full_text.lower():
        return None

    result = {
        "name": None,
        "website": None,
        "description": None,
        "affiliation_year": None,
        "affiliation_connection": None,
    }

    # --- Company name and website ---
    # Primary: from eth-link anchor (text after "external page" prefix)
    eth_links = wrapper.select("a.eth-link")
    for link in eth_links:
        text = link.get_text(strip=True).replace("external page", "").strip()
        if text:
            result["name"] = text
            result["website"] = link.get("href", "").rstrip("/") or None
            break

    # Fallback: name from img alt, website from figure link
    if not result["name"]:
        img = wrapper.select_one("figure img")
        if img and img.get("alt"):
            result["name"] = img["alt"].strip().title()
        fig_link = wrapper.select_one("figure a")
        if fig_link and fig_link.get("href"):
            result["website"] = fig_link["href"].rstrip("/") or None

    if not result["name"]:
        return None

    # Skip non-startup entries (e.g. "this Form." application link)
    if result["name"].lower() in ("this form.", "this form"):
        return None

    # --- Description ---
    # Get the first paragraph's text, stripping the company name prefix
    paragraphs = wrapper.find_all("p")
    for p in paragraphs:
        text = p.get_text(separator=" ", strip=True)
        # Skip affiliation-only paragraphs
        if text.lower().startswith("affiliation"):
            continue
        # Skip very short paragraphs (empty or just whitespace)
        if len(text) < 20:
            continue
        # Strip "external page CompanyName" prefix
        desc = re.sub(r'^external page\s+', '', text)
        # Strip company name from start if present
        if result["name"] and desc.startswith(result["name"]):
            desc = desc[len(result["name"]):].lstrip(" .,;:-–—")
        desc = desc.strip()
        if desc:
            result["description"] = desc
            break

    # --- Affiliation Year (case-insensitive) ---
    match = re.search(r'affiliation\s+year:\s*(\d{4})', full_text, re.IGNORECASE)
    if match:
        result["affiliation_year"] = match.group(1)

    # --- Affiliation Connection (case-insensitive) ---
    match = re.search(
        r'affiliation\s+connection:\s*(.+?)(?:\n|$)',
        full_text, re.IGNORECASE,
    )
    if match:
        result["affiliation_connection"] = match.group(1).strip()

    return result


def main():
    init_db()

    log("ETH AI Center Scraper")
    log("=" * 50)

    # Fetch the page
    log(f"\nFetching {PAGE_URL}...")
    try:
        resp = fetch(PAGE_URL, headers=HEADERS)
    except requests.RequestException as e:
        log(f"ERROR: Failed to fetch page: {e}")
        return

    soup = BeautifulSoup(resp.text, "html.parser")
    wrappers = soup.find_all("div", class_="textimage__wrapper")
    log(f"  Found {len(wrappers)} content blocks")

    # Parse startups
    startups = []
    for w in wrappers:
        data = parse_startup(w)
        if data:
            startups.append(data)

    log(f"  Parsed {len(startups)} startups (filtered sponsors/partners)\n")

    new_count = 0
    existing_count = 0

    for s in startups:
        name = s["name"]
        existing = find_existing(name)

        metadata = json.dumps({
            "affiliation_year": s["affiliation_year"],
            "affiliation_connection": s["affiliation_connection"],
        })

        if existing:
            company_id = existing["id"]
            updates = {}
            if s["website"] and not existing.get("website"):
                updates["website"] = s["website"]
            if s["description"] and not existing.get("description"):
                updates["description"] = s["description"]
            if existing.get("geography") in (None, "Unknown"):
                updates["geography"] = "Switzerland"
            if existing.get("sector") in (None, "Other"):
                updates["sector"] = "AI / ML"
            update_company(company_id, **updates)
            existing_count += 1
        else:
            company_id = insert_company(
                name=name,
                description=s["description"],
                sector="AI / ML",
                geography="Switzerland",
                city="Zurich",
                website=s["website"],
                stage="Pre-seed",
                heat_score=2,
            )
            new_count += 1

        insert_signal(
            company_id=company_id,
            source_type="program",
            source_name="ETH AI Center",
            source_url=PAGE_URL,
            signal_layer="curated",
            title=f"{name} — ETH AI Center affiliated startup",
            metadata=metadata,
        )

        insert_program(
            company_id=company_id,
            program_name="ETH AI Center",
            program_type="University Spin-off",
            program_country="Switzerland",
            cohort=s["affiliation_year"],
        )

        log(f"  {'NEW' if not existing else 'UPD'}  {name[:40]:40s}  "
            f"year={s['affiliation_year'] or '?':4s}  "
            f"{(s['affiliation_connection'] or '')[:30]}")

    log(f"\nETH AI Center: Found {len(startups)} startups. "
        f"{new_count} new, {existing_count} already existed.")


if __name__ == "__main__":
    main()
