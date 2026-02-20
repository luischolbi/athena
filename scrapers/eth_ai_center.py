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

from datetime import date

from database.database import (
    init_db,
    get_connection,
    insert_company,
    insert_signal,
    insert_program,
    insert_founder,
    update_company,
    update_company_stage,
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


def extract_founders_from_text(text):
    """Extract founder names from prose like 'co-founded by X, Y, and Z' or 'founded by X'."""
    founders = []
    # Match patterns like "founded by Name1, Name2, and Name3" or "co-founded by Name1 and Name2"
    pattern = r'(?:co-)?founded\s+by\s+(.+?)(?:\.|,\s*(?:the|who|it|a\b|in|at|this|they|with)|\s*$)'
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return founders

    names_str = match.group(1).strip()
    # Split on ", " and " and " to get individual names
    # Handle "A, B, and C" or "A and B" patterns
    names_str = re.sub(r',?\s+and\s+', ', ', names_str)
    parts = [n.strip() for n in names_str.split(',') if n.strip()]

    for name in parts:
        # A valid founder name should be 2+ words, start with uppercase, no weird chars
        name = name.strip(' .')
        words = name.split()
        if len(words) < 2 or len(words) > 5:
            continue
        # Each word should start with uppercase (proper name)
        if not all(w[0].isupper() for w in words if len(w) > 1):
            continue
        # Skip if it contains common non-name words
        skip_words = {'the', 'a', 'an', 'in', 'at', 'of', 'for', 'with', 'from'}
        if any(w.lower() in skip_words for w in words):
            continue
        founders.append(name)

    return founders


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
        "founders": [],
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

    # --- Founders from description prose ---
    if result["description"]:
        result["founders"] = extract_founders_from_text(result["description"])

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
            update_company_stage(company_id, "Pre-seed", "ETH AI Center", date.today().isoformat())
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
                stage_source="ETH AI Center",
                stage_detected_date=date.today().isoformat(),
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

        # Insert founders extracted from prose
        for fname in s["founders"]:
            insert_founder(
                company_id=company_id,
                name=fname,
                source="ETH AI Center",
            )

        log(f"  {'NEW' if not existing else 'UPD'}  {name[:40]:40s}  "
            f"year={s['affiliation_year'] or '?':4s}  "
            f"{(s['affiliation_connection'] or '')[:30]}"
            f"{'  founders=' + ','.join(s['founders']) if s['founders'] else ''}")

    log(f"\nETH AI Center: Found {len(startups)} startups. "
        f"{new_count} new, {existing_count} already existed.")


if __name__ == "__main__":
    main()
