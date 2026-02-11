"""
Imperial College spinout portfolio scraper for Athena.

Scrapes https://www.imperial.ac.uk/admin-services/enterprise/about/data-and-reporting/spinout-portfolio/
— a single static page with ~88 spinout companies listed alphabetically.

Each company is a <p><strong>Name</strong></p> followed by a <ul> with
description in the first <li> and website link in the second <li>.
"""

import json
import re
import sys
import os

import requests
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

PAGE_URL = (
    "https://www.imperial.ac.uk/admin-services/enterprise/"
    "about/data-and-reporting/spinout-portfolio/"
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# Keyword-based sector detection from description
SECTOR_RULES = [
    ("AI / ML",      [r"\bAI\b", r"\bML\b", r"machine learning", r"\bLLM\b",
                      r"\bGPT\b", r"neural net", r"deep learning",
                      r"artificial intelligence"]),
    ("Fintech",      [r"fintech", r"banking", r"payments?\b", r"neobank",
                      r"insurance", r"lending"]),
    ("Climate",      [r"climate", r"carbon", r"\benergy\b", r"\bsolar\b",
                      r"clean\s*tech", r"sustainability", r"sustainable",
                      r"battery", r"green", r"vanadium", r"renewable"]),
    ("Health / Bio", [r"health", r"medical", r"biotech", r"pharma",
                      r"genomic", r"diagnostic", r"therapeutics",
                      r"drug discovery", r"clinical", r"cancer",
                      r"vaccine", r"antibod", r"disease", r"surgical",
                      r"wearable medical", r"respiratory", r"kidney",
                      r"gene therapy", r"cell therap"]),
    ("SaaS",         [r"\bSaaS\b", r"\bB2B\b", r"\bplatform\b",
                      r"developer", r"infrastructure", r"software"]),
    ("Deep Tech",    [r"quantum", r"photonic", r"semiconductor", r"nano",
                      r"robotic", r"sensor", r"material", r"ceramic",
                      r"optic"]),
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


def detect_sector(description):
    """Keyword-based sector detection from description text."""
    if not description:
        return "Other"
    for sector, patterns in SECTOR_RULES:
        for pat in patterns:
            if re.search(pat, description, re.IGNORECASE):
                return sector
    return "Other"


def parse_companies(soup):
    """Parse all spinout companies from the page.

    Structure: inside div.row.wysiwyg sections, each company is:
      <p><strong>Company Name</strong></p>
      <ul>
        <li><span>Description</span></li>
        <li><a href="...">website</a></li>   (or "No website available.")
      </ul>
    """
    content = soup.find("div", id="primary-content")
    if not content:
        return []

    companies = []
    wysiwygs = content.find_all("div", class_="wysiwyg")

    for section in wysiwygs:
        paragraphs = section.find_all("p")
        for p in paragraphs:
            strong = p.find("strong")
            if not strong:
                continue
            # Skip anchor-only strongs (letter jump links) and non-company text
            if strong.find("a") and not strong.find("a").get_text(strip=True):
                continue
            name = strong.get_text(strip=True)
            if not name or name == "Jump to:" or len(name) < 2:
                continue

            # Get description and website from the following <ul>
            next_ul = p.find_next_sibling("ul")
            description = None
            website = None

            if next_ul:
                for li in next_ul.find_all("li"):
                    link = li.find("a", href=True)
                    if link and link["href"].startswith("http"):
                        website = link["href"].strip()
                    else:
                        text = li.get_text(strip=True)
                        # Skip placeholder text
                        if text and "no website available" not in text.lower():
                            description = text

            companies.append({
                "name": name,
                "description": description,
                "website": website,
            })

    return companies


def main():
    init_db()

    log("Imperial College Spinouts Scraper")
    log("=" * 50)

    # Fetch the page
    log(f"\nFetching {PAGE_URL}...")
    try:
        resp = requests.get(PAGE_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        log(f"ERROR: Failed to fetch page: {e}")
        return

    soup = BeautifulSoup(resp.text, "html.parser")
    companies = parse_companies(soup)
    log(f"  Found {len(companies)} spinout companies\n")

    new_count = 0
    existing_count = 0

    for data in companies:
        name = data["name"]
        sector = detect_sector(data["description"])
        existing = find_existing(name)

        metadata = json.dumps({
            "source_page": PAGE_URL,
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
                updates["city"] = "London"
            update_company(company_id, **updates)
            existing_count += 1
        else:
            company_id = insert_company(
                name=name,
                description=data["description"],
                sector=sector,
                geography="UK",
                city="London",
                website=data["website"],
                stage="Pre-seed",
                heat_score=2,
            )
            new_count += 1

        insert_signal(
            company_id=company_id,
            source_type="program",
            source_name="Imperial College",
            source_url=data["website"] or PAGE_URL,
            signal_layer="curated",
            title=f"{name} — Imperial College spinout",
            metadata=metadata,
        )

        insert_program(
            company_id=company_id,
            program_name="Imperial Enterprise Lab",
            program_type="University Spin-off",
            program_country="UK",
        )

        log(f"  {'NEW' if not existing else 'UPD'}  {name[:40]:40s}  {sector}")

    log(f"\nImperial College: Found {len(companies)} spinouts. "
        f"{new_count} new, {existing_count} already existed.")


if __name__ == "__main__":
    main()
