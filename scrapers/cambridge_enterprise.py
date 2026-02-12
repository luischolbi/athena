"""
Cambridge Enterprise portfolio scraper for Athena.

Scrapes https://www.enterprise.cam.ac.uk/venture-building-investment/portfolio-companies/
— a single static page with ~126 company cards in div.logo-grid-item elements.
Also checks the equity portfolio page to tag equity holdings.
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

PORTFOLIO_URL = (
    "https://www.enterprise.cam.ac.uk"
    "/venture-building-investment/portfolio-companies/"
)
EQUITY_URL = (
    "https://www.enterprise.cam.ac.uk"
    "/venture-building-investment/equity-portfolio/"
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# Map Cambridge Enterprise sector tags to Athena sectors
CE_TAG_TO_SECTOR = {
    "life sciences": "Health / Bio",
    "sustainability": "Climate",
    "social ventures": "Other",
    # "Deep Tech" handled separately with keyword detection
}

# Keyword-based sector detection from description
SECTOR_RULES = [
    ("AI / ML",      [r"\bAI\b", r"\bML\b", r"machine learning", r"\bLLM\b",
                      r"\bGPT\b", r"neural net", r"deep learning",
                      r"artificial intelligence"]),
    ("Fintech",      [r"fintech", r"banking", r"payments?\b", r"neobank",
                      r"insurance", r"lending"]),
    ("Climate",      [r"climate", r"carbon", r"\benergy\b", r"\bsolar\b",
                      r"clean\s*tech", r"sustainability"]),
    ("Health / Bio", [r"health", r"medical", r"biotech", r"pharma",
                      r"genomic", r"diagnostic", r"therapeutics",
                      r"drug discovery", r"clinical"]),
    ("SaaS",         [r"\bSaaS\b", r"\bB2B\b", r"\bplatform\b",
                      r"developer", r"infrastructure", r"software"]),
]


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


def detect_sector(ce_tag, description):
    """Map CE sector tag to Athena sector, with keyword fallback."""
    tag_lower = (ce_tag or "").strip().lower()

    # Direct mapping for non-Deep-Tech tags
    if tag_lower in CE_TAG_TO_SECTOR:
        return CE_TAG_TO_SECTOR[tag_lower]

    # For "Deep Tech" or unknown tags, try keyword detection on description
    if description:
        for sector, patterns in SECTOR_RULES:
            for pat in patterns:
                if re.search(pat, description, re.IGNORECASE):
                    return sector

    # Default for Deep Tech with no keyword match
    if tag_lower == "deep tech":
        return "Deep Tech"

    return "Other"


def fetch_page(url, label):
    """Fetch a page and return BeautifulSoup, or None on failure."""
    log(f"  Fetching {label}...")
    try:
        resp = fetch(url, headers=HEADERS)
        return BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException as e:
        log(f"  ERROR: Failed to fetch {label}: {e}")
        return None


def get_equity_names(soup):
    """Extract company names from the equity portfolio page."""
    names = set()
    if soup is None:
        return names
    items = soup.find_all("div", class_="logo-grid-item")
    for item in items:
        name_el = item.select_one("span.block.text-20bm.mb-2")
        if name_el:
            names.add(name_el.get_text(strip=True).lower())
    return names


def parse_item(item):
    """Parse a single div.logo-grid-item. Returns dict or None."""
    result = {
        "name": None,
        "description": None,
        "sector_tag": None,
        "website": None,
        "academic_founders": None,
        "contact": None,
        "related_articles": [],
    }

    # --- Card data ---
    name_el = item.select_one("span.block.text-20bm.mb-2")
    if name_el:
        result["name"] = name_el.get_text(strip=True)
    if not result["name"]:
        return None

    sector_el = item.select_one("span.block.text-18sn")
    if sector_el:
        result["sector_tag"] = sector_el.get_text(strip=True)

    # --- Popup data (div.mfp-hide) ---
    popup = item.select_one("div.mfp-hide")
    if popup:
        # Description: first <p> in popup
        first_p = popup.find("p")
        if first_p:
            result["description"] = first_p.get_text(strip=True)

        # Founders and contact from span.block elements
        spans = popup.find_all("span", class_="block")
        for span in spans:
            text = span.get_text(strip=True)
            if text.lower().startswith("academic founder"):
                # Get the value after the label — could be in same or next element
                value = text.split(":", 1)[-1].strip() if ":" in text else ""
                if not value:
                    next_sib = span.find_next_sibling()
                    if next_sib:
                        value = next_sib.get_text(strip=True)
                result["academic_founders"] = value or None
            elif text.lower().startswith("point of contact"):
                value = text.split(":", 1)[-1].strip() if ":" in text else ""
                if not value:
                    next_sib = span.find_next_sibling()
                    if next_sib:
                        value = next_sib.get_text(strip=True)
                result["contact"] = value or None

        # Website: a.button link
        website_el = popup.select_one("a.button")
        if website_el and website_el.get("href"):
            result["website"] = website_el["href"].strip()

        # Related articles
        related = popup.select_one("div.related-articles")
        if related:
            for a in related.find_all("a", href=True):
                result["related_articles"].append({
                    "title": a.get_text(strip=True),
                    "url": a["href"].strip(),
                })

    return result


def main():
    init_db()

    log("Cambridge Enterprise Scraper")
    log("=" * 50)

    # Fetch both pages
    portfolio_soup = fetch_page(PORTFOLIO_URL, "full portfolio")
    if portfolio_soup is None:
        return

    equity_soup = fetch_page(EQUITY_URL, "equity portfolio")
    equity_names = get_equity_names(equity_soup)
    if equity_names:
        log(f"  Found {len(equity_names)} equity portfolio companies")

    # Parse all portfolio companies
    items = portfolio_soup.find_all("div", class_="logo-grid-item")
    log(f"  Found {len(items)} company cards\n")

    new_count = 0
    existing_count = 0

    for item in items:
        data = parse_item(item)
        if data is None:
            continue

        name = data["name"]
        sector = detect_sector(data["sector_tag"], data["description"])
        is_equity = name.lower() in equity_names
        existing = find_existing(name)

        metadata = json.dumps({
            "academic_founders": data["academic_founders"],
            "contact": data["contact"],
            "sector_tag": data["sector_tag"],
            "is_equity_portfolio": is_equity,
            "related_articles": data["related_articles"],
        })

        if existing:
            company_id = existing["id"]
            updates = {}
            if data["website"] and not existing.get("website"):
                updates["website"] = data["website"]
            if data["description"] and not existing.get("description"):
                updates["description"] = data["description"]
            if existing.get("sector") in (None, "Other") and sector != "Other":
                updates["sector"] = sector
            if existing.get("geography") in (None, "Unknown"):
                updates["geography"] = "UK"
            if not existing.get("city"):
                updates["city"] = "Cambridge"
            update_company(company_id, **updates)
            existing_count += 1
        else:
            company_id = insert_company(
                name=name,
                description=data["description"],
                sector=sector,
                geography="UK",
                city="Cambridge",
                website=data["website"],
                stage="Pre-seed",
                heat_score=2,
            )
            new_count += 1

        insert_signal(
            company_id=company_id,
            source_type="program",
            source_name="Cambridge Enterprise",
            source_url=data["website"] or PORTFOLIO_URL,
            signal_layer="curated",
            title=f"{name} — Cambridge Enterprise portfolio",
            metadata=metadata,
        )

        insert_program(
            company_id=company_id,
            program_name="Cambridge Enterprise",
            program_type="University Spin-off",
            program_country="UK",
        )

        equity_tag = " [EQUITY]" if is_equity else ""
        log(f"  {'NEW' if not existing else 'UPD'}  {name[:40]:40s}  "
            f"{sector:15s}{equity_tag}")

    log(f"\nCambridge Enterprise: Found {new_count + existing_count} companies. "
        f"{new_count} new, {existing_count} already existed.")


if __name__ == "__main__":
    main()
