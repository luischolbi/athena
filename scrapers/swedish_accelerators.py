"""
Swedish accelerator portfolio scraper for Athena.

Scrapes company portfolios from 13 Swedish incubators/accelerators.
Each accelerator has its own scrape function tailored to its page structure.

Included:
  Chalmers Ventures (Gothenburg, ~180 companies)
  LEAD (Linköping, ~145 companies)
  Sting (Stockholm, ~100+ companies, paginated Webflow)
  Hetch (Helsingborg, ~51 companies)
  Minc (Malmö, ~40-50 companies, Webflow CMS)
  Brewhouse (Gothenburg, ~35 companies)
  LU Innovation (Lund, ~36 companies, Drupal accordion)
  Innovatum (Gothenburg, ~31 companies, paginated WP)
  Arctic Ventures (Luleå, ~28 companies, Squarespace)
  GU Ventures (Gothenburg, ~21 companies, Squarespace gallery)
  Inkubera (Örebro, ~18 companies)
  Uminova (Umeå, ~17 companies)
  Krinova (Kristianstad, ~12 companies)

Skipped:
  BBI (4 companies, JS-rendered)
  Ideon Innovation (HTTP 555, blocked by WAF)
  Founders Loft (Wix, fully JS-rendered)
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

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

SECTOR_RULES = [
    ("AI / ML", ["artificial intelligence", "machine learning", "deep learning",
                 "nlp", "computer vision", "llm", "generative ai", " ai ",
                 "robotik", "robotics"]),
    ("Health / Bio", ["health", "biotech", "medical", "pharma", "clinical",
                      "diagnostics", "therapeutics", "drug", "surgical",
                      "neuro", "brain", "hälsa", "medicinsk"]),
    ("Fintech", ["fintech", "banking", "payments", "insurance", "financial",
                 "lending", "neobank", "betalning"]),
    ("Climate", ["climate", "carbon", "renewable", "solar", "energy",
                 "sustainability", "cleantech", "green", "sustainable",
                 "energi", "hållbar", "klimat"]),
    ("SaaS", ["saas", "software", "platform", "b2b", "enterprise", "cloud",
              "api", "automation"]),
    ("Deep Tech", ["quantum", "semiconductor", "hardware",
                   "materials", "space", "satellite", "drone", "3d",
                   "nano", "halvledare", "photonics", "laser"]),
    ("Consumer", ["consumer", "marketplace", "e-commerce", "retail", "food",
                  "delivery", "livsmedel"]),
]

ACCELERATORS = [
    {"name": "Chalmers Ventures", "city": "Gothenburg",
     "url": "https://chalmersventures.com/startups/"},
    {"name": "LEAD", "city": "Linköping",
     "url": "https://lead.se/vara-startups/"},
    {"name": "Sting", "city": "Stockholm",
     "url": "https://www.sting.co/companies"},
    {"name": "Hetch", "city": "Helsingborg",
     "url": "https://hetch.se/hetch-companies/"},
    {"name": "Minc", "city": "Malmö",
     "url": "https://www.minc.se/community/startup-alumnis"},
    {"name": "Brewhouse", "city": "Gothenburg",
     "url": "https://brewhouseinkubator.se/en/our-startups/"},
    {"name": "LU Innovation", "city": "Lund",
     "url": "https://www.innovation.lu.se/lu-ventures/luv-teknik"},
    {"name": "Innovatum", "city": "Gothenburg",
     "url": "https://innovatumsciencepark.se/en/startup/"},
    {"name": "Arctic Ventures", "city": "Luleå",
     "url": "https://arctic-ventures.se/portfolio"},
    {"name": "GU Ventures", "city": "Gothenburg",
     "url": "https://www.guventures.com/portfolio"},
    {"name": "Inkubera", "city": "Örebro",
     "url": "https://inkubera.se/aktiva-bolag/"},
    {"name": "Uminova", "city": "Umeå",
     "url": "https://www.uminovainnovation.se/bolag/"},
    {"name": "Krinova", "city": "Kristianstad",
     "url": "https://krinova.se/en/business-development/krinova-incubator/"},
]

SKIPPED = [
    ("BBI", "JS-rendered, only 4 companies"),
    ("Ideon Innovation", "HTTP 555, blocked by WAF"),
    ("Founders Loft", "Wix, fully JS-rendered"),
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
    if not text:
        return "Other"
    lower = f" {text.lower()} "
    for sector, keywords in SECTOR_RULES:
        if any(kw in lower for kw in keywords):
            return sector
    return "Other"


# --- Individual scrape functions ---


def scrape_chalmers():
    """Chalmers Ventures: static HTML, <a> wrapping <h3>, ~180 companies."""
    url = "https://chalmersventures.com/startups/"
    resp = fetch(url, headers=HEADERS)
    soup = BeautifulSoup(resp.text, "html.parser")

    companies = []
    for h3 in soup.find_all("h3"):
        a = h3.find_parent("a")
        if not a or "/startups/" not in a.get("href", ""):
            continue
        name = h3.get_text(strip=True)
        if not name:
            continue
        href = a["href"]
        if not href.startswith("http"):
            href = "https://chalmersventures.com" + href
        companies.append({
            "name": name,
            "description": None,
            "website": None,
            "source_url": href,
        })
    return companies


def scrape_lead():
    """LEAD: static WP, div.filter-card with data-title, descriptions, websites."""
    url = "https://lead.se/vara-startups/"
    resp = fetch(url, headers=HEADERS)
    soup = BeautifulSoup(resp.text, "html.parser")

    companies = []
    for card in soup.select("div.filter-card"):
        name = card.get("data-title")
        if not name:
            continue

        desc_span = card.select_one("span.data-text")
        description = None
        website = None
        if desc_span:
            link = desc_span.select_one("a[href]")
            if link:
                website = link["href"]
            # Clone span to extract text without nested tags
            clone = BeautifulSoup(str(desc_span), "html.parser")
            for tag in clone.find_all(["p", "a"]):
                tag.decompose()
            description = clone.get_text(strip=True) or None

        companies.append({
            "name": name,
            "description": description,
            "website": website,
            "source_url": url,
        })
    return companies


def scrape_sting():
    """Sting: JS-rendered listing, so use sitemap to get company URLs, then
    extract names from slugs. ~497 companies."""
    sitemap_url = "https://www.sting.co/sitemap.xml"
    resp = fetch(sitemap_url, headers=HEADERS)
    soup = BeautifulSoup(resp.text, "xml")

    companies = []
    for loc in soup.find_all("loc"):
        url = loc.get_text(strip=True)
        if "/companies/" not in url:
            continue
        slug = url.rstrip("/").split("/")[-1]
        if not slug or slug == "companies":
            continue

        name = slug.replace("-", " ").title()

        companies.append({
            "name": name,
            "description": None,
            "website": None,
            "source_url": url,
        })
    return companies


def scrape_hetch():
    """Hetch: static HTML, external links with img[alt] for names, ~51 companies."""
    url = "https://hetch.se/hetch-companies/"
    resp = fetch(url, headers=HEADERS)
    soup = BeautifulSoup(resp.text, "html.parser")

    companies = []
    seen = set()
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if "hetch.se" in href or not href.startswith("http"):
            continue

        img = a.find("img")
        if not img:
            continue

        name = (img.get("alt") or "").strip()
        if not name or name.lower() in seen:
            continue
        if name.lower() in ("logo", "icon", "image"):
            continue

        seen.add(name.lower())
        companies.append({
            "name": name,
            "description": None,
            "website": href,
            "source_url": url,
        })
    return companies


def scrape_minc():
    """Minc: Webflow CMS with w-dyn-item cards, h4 names, ~40-50 companies."""
    url = "https://www.minc.se/community/startup-alumnis"
    resp = fetch(url, headers=HEADERS)
    soup = BeautifulSoup(resp.text, "html.parser")

    companies = []
    seen = set()

    for item in soup.select(".w-dyn-item"):
        h4 = item.find("h4")
        if not h4:
            continue
        name = h4.get_text(strip=True)
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())

        website = None
        industry = None
        card_text = item.get_text(" ", strip=True)

        # Extract website link
        for a in item.find_all("a", href=True):
            href = a["href"]
            if href.startswith("http") and "minc.se" not in href:
                website = href
                break

        # Extract industry if labeled
        match = re.search(r"Industry:\s*([\w\s/&-]+)", card_text)
        if match:
            industry = match.group(1).strip()

        companies.append({
            "name": name,
            "description": industry,
            "website": website,
            "source_url": url,
        })

    # Fallback: if w-dyn-item didn't work, try raw h4 tags
    if not companies:
        for h4 in soup.find_all("h4"):
            name = h4.get_text(strip=True)
            if not name or len(name) > 80 or name.lower() in seen:
                continue
            seen.add(name.lower())
            companies.append({
                "name": name,
                "description": None,
                "website": None,
                "source_url": url,
            })

    return companies


def scrape_brewhouse():
    """Brewhouse: static HTML, <a> wrapping <h2>, descriptions in <p>."""
    url = "https://brewhouseinkubator.se/en/our-startups/"
    resp = fetch(url, headers=HEADERS)
    soup = BeautifulSoup(resp.text, "html.parser")

    skip_headings = {"our startups", "alumni", "incubator startups",
                     "music tech", "about", "contact", "apply now",
                     "previous startups", "current startups", "our alumni"}

    companies = []
    for h2 in soup.find_all("h2"):
        name = h2.get_text(strip=True)
        if not name or name.lower() in skip_headings:
            continue

        source_url = url
        a = h2.find_parent("a")
        if a and a.get("href"):
            href = a["href"]
            if not href.startswith("http"):
                href = "https://brewhouseinkubator.se" + href
            source_url = href

        # Description from next <p>
        description = None
        next_el = h2.find_next_sibling("p")
        if not next_el and h2.parent:
            next_el = h2.parent.find_next_sibling("p")
        if next_el:
            desc = next_el.get_text(strip=True)
            if desc and len(desc) > 10:
                description = desc

        companies.append({
            "name": name,
            "description": description,
            "website": None,
            "source_url": source_url,
        })
    return companies


def scrape_lu_innovation():
    """LU Innovation: Drupal accordion lists, <li> items with name - description."""
    url = "https://www.innovation.lu.se/lu-ventures/luv-teknik"
    resp = fetch(url, headers=HEADERS)
    soup = BeautifulSoup(resp.text, "html.parser")

    companies = []
    seen = set()

    for accordion in soup.select("div.accordion-body"):
        for li in accordion.find_all("li"):
            text = li.get_text(strip=True)
            if not text:
                continue

            link = li.find("a")
            if link:
                name = link.get_text(strip=True)
            else:
                parts = text.split(" - ", 1)
                name = parts[0].strip()

            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())

            # Description after " - "
            desc_match = re.search(r" [-–] (.+)", text)
            description = desc_match.group(1).strip() if desc_match else None

            website = None
            if link and link.get("href"):
                href = link["href"]
                if href.startswith("http") and "innovation.lu.se" not in href:
                    website = href

            companies.append({
                "name": name,
                "description": description,
                "website": website,
                "source_url": url,
            })
    return companies


def scrape_innovatum():
    """Innovatum: static WP with URL-based pagination, <h4> names near logos."""
    companies = []
    seen = set()

    # Navigation headings to skip
    skip = {"startup", "next page", "previous page", "about", "contact",
            "news", "events", "english", "svenska", "what we do", "who we are",
            "latest news", "current project", "our startups", "contact us",
            "social", "the incubator", "esa-bic", "dual use accelerator",
            "business development support - our programs",
            "business development support – our programs"}

    for page_num in range(1, 5):
        if page_num == 1:
            page_url = "https://innovatumsciencepark.se/en/startup/"
        else:
            page_url = f"https://innovatumsciencepark.se/en/startup/page/{page_num}/"

        try:
            resp = fetch(page_url, headers=HEADERS)
        except requests.RequestException:
            break

        soup = BeautifulSoup(resp.text, "html.parser")

        page_count = 0
        for h4 in soup.find_all("h4"):
            name = h4.get_text(strip=True).strip("#").strip()
            if not name or name.lower() in skip or len(name) > 80:
                continue
            if name.lower() in seen:
                continue

            # Company h4s should be near an image (logo)
            parent = h4.find_parent("div")
            if parent and not parent.find("img"):
                continue

            seen.add(name.lower())

            website = None
            if parent:
                a = parent.find("a", href=True)
                if a:
                    href = a["href"]
                    if href.startswith("http") and "innovatumsciencepark" not in href:
                        website = href

            companies.append({
                "name": name,
                "description": None,
                "website": website,
                "source_url": page_url,
            })
            page_count += 1

        if page_count == 0:
            break

        # Check for next page
        next_link = soup.find("a", href=re.compile(rf"/page/{page_num + 1}/"))
        if not next_link:
            break

        time.sleep(1.5)

    return companies


def scrape_arctic_ventures():
    """Arctic Ventures: Squarespace, h3 headings + p descriptions."""
    url = "https://arctic-ventures.se/portfolio"
    resp = fetch(url, headers=HEADERS)
    soup = BeautifulSoup(resp.text, "html.parser")

    companies = []
    for h3 in soup.find_all("h3"):
        text = h3.get_text(strip=True)
        if not text or "arctic venture" in text.lower():
            continue

        link = h3.find("a")
        if link:
            name = link.get_text(strip=True)
            website = link.get("href")
        else:
            name = text
            website = None

        if not name:
            continue

        # Convert ALL CAPS to Title Case
        if name.isupper():
            name = name.title()

        # Description from next <p>
        description = None
        p = h3.find_next("p")
        if p:
            desc = p.get_text(strip=True)
            if desc and len(desc) > 5:
                description = desc

        companies.append({
            "name": name,
            "description": description,
            "website": website,
            "source_url": url,
        })
    return companies


def scrape_gu_ventures():
    """GU Ventures: Squarespace gallery, names derived from internal link slugs.
    Scrapes the main /portfolio page which contains all categories."""
    url = "https://www.guventures.com/portfolio"
    resp = fetch(url, headers=HEADERS)
    soup = BeautifulSoup(resp.text, "html.parser")

    # Known non-company paths on the Squarespace site
    skip_paths = {"portfolio-tech", "portfolio-life-science", "portfolio",
                  "about", "contact", "news", "blog", "team", ""}

    companies = []
    seen = set()

    # Find all internal links that are single-segment paths (company pages)
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("http") or href.startswith("#") or href.startswith("mailto"):
            continue
        slug = href.strip("/")
        if not slug or "/" in slug or slug in skip_paths:
            continue
        if slug.lower() in seen:
            continue

        # Must be inside a gallery or content area (skip nav links)
        parent_gallery = a.find_parent(class_=re.compile(r"sqs-gallery|sqs-block"))
        if not parent_gallery:
            continue

        seen.add(slug.lower())
        name = slug.replace("-", " ").title()

        companies.append({
            "name": name,
            "description": None,
            "website": None,
            "source_url": f"https://www.guventures.com/{slug}",
        })
    return companies


def scrape_inkubera():
    """Inkubera: WordPress, sequential h3 headings with h4 taglines."""
    url = "https://inkubera.se/aktiva-bolag/"
    resp = fetch(url, headers=HEADERS)
    soup = BeautifulSoup(resp.text, "html.parser")

    skip = {"aktiva bolag", "alumner", "våra bolag", "about", "kontakt"}

    companies = []
    for h3 in soup.find_all("h3"):
        name = h3.get_text(strip=True)
        if not name or name.lower() in skip or len(name) > 80:
            continue

        # Tagline in next h4
        description = None
        h4 = h3.find_next_sibling("h4")
        if h4:
            description = h4.get_text(strip=True)
        if not description:
            p = h3.find_next_sibling("p")
            if p:
                desc = p.get_text(strip=True)
                if desc and len(desc) > 10:
                    description = desc

        # Website from nearby <a>
        website = None
        for sibling in h3.find_next_siblings(limit=5):
            links = sibling.find_all("a", href=True) if sibling.name != "a" else [sibling]
            for a in links:
                href = a.get("href", "")
                if href.startswith("http") and "inkubera.se" not in href:
                    website = href
                    break
            if website:
                break

        companies.append({
            "name": name,
            "description": description,
            "website": website,
            "source_url": url,
        })
    return companies


def scrape_uminova():
    """Uminova: WordPress, active companies use /bolag/ links with <h5>,
    alumni use <a class='preview-single-company' title='Name' href='website'>."""
    url = "https://www.uminovainnovation.se/bolag/"
    resp = fetch(url, headers=HEADERS)
    soup = BeautifulSoup(resp.text, "html.parser")

    companies = []
    seen = set()

    # Active companies: <a href="/bolag/slug/"><h5>Name</h5><p>Desc</p></a>
    for a in soup.find_all("a", href=re.compile(r"/bolag/.+")):
        href = a.get("href", "")
        slug = href.rstrip("/").split("/")[-1]
        if not slug or slug == "bolag":
            continue

        h5 = a.find("h5")
        if not h5:
            continue
        name = h5.get_text(strip=True)
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())

        p = a.find("p")
        description = p.get_text(strip=True) if p else None

        source_url = href
        if not source_url.startswith("http"):
            source_url = "https://www.uminovainnovation.se" + source_url

        companies.append({
            "name": name,
            "description": description,
            "website": None,
            "source_url": source_url,
        })

    # Alumni: <a class="preview-single-company" title="Name" href="website">
    for a in soup.find_all("a", class_="preview-single-company"):
        title = (a.get("title") or "").strip()
        if not title or title.lower() in seen:
            continue
        seen.add(title.lower())

        href = a.get("href", "")
        website = href if href.startswith("http") else None

        companies.append({
            "name": title,
            "description": None,
            "website": website,
            "source_url": url,
        })

    return companies


def scrape_krinova():
    """Krinova: WordPress, puff cards with h3 'Read more about X'."""
    url = "https://krinova.se/en/business-development/krinova-incubator/"
    resp = fetch(url, headers=HEADERS)
    soup = BeautifulSoup(resp.text, "html.parser")

    companies = []
    for card in soup.select("div.puff"):
        h3 = card.find("h3")
        if not h3:
            continue
        text = h3.get_text(strip=True)
        name = re.sub(r"^Read more about\s+", "", text, flags=re.I).strip()
        if not name:
            continue

        a = card.find("a", href=True)
        source_url = url
        if a:
            href = a["href"]
            if not href.startswith("http"):
                href = "https://krinova.se" + href
            source_url = href

        companies.append({
            "name": name,
            "description": None,
            "website": None,
            "source_url": source_url,
        })
    return companies


# Map names -> functions
SCRAPE_FNS = {
    "Chalmers Ventures": scrape_chalmers,
    "LEAD": scrape_lead,
    "Sting": scrape_sting,
    "Hetch": scrape_hetch,
    "Minc": scrape_minc,
    "Brewhouse": scrape_brewhouse,
    "LU Innovation": scrape_lu_innovation,
    "Innovatum": scrape_innovatum,
    "Arctic Ventures": scrape_arctic_ventures,
    "GU Ventures": scrape_gu_ventures,
    "Inkubera": scrape_inkubera,
    "Uminova": scrape_uminova,
    "Krinova": scrape_krinova,
}


def store_companies(companies, acc_name, acc_city):
    """Store scraped companies in the database. Returns (new_count, existing_count)."""
    new_count = 0
    existing_count = 0

    for data in companies:
        name = data["name"]
        description = data.get("description")
        website = data.get("website")
        sector = detect_sector(description or name)

        metadata = json.dumps({
            "website": website,
            "source_url": data.get("source_url"),
        })

        existing = find_existing(name)

        if existing:
            company_id = existing["id"]
            updates = {}
            if description and not existing.get("description"):
                updates["description"] = description
            if website and not existing.get("website"):
                updates["website"] = website
            if existing.get("sector") in (None, "Other") and sector != "Other":
                updates["sector"] = sector
            if not existing.get("city") and acc_city:
                updates["city"] = acc_city
            if updates:
                update_company(company_id, **updates)
            update_company_stage(company_id, "Seed", acc_name, date.today().isoformat())
            existing_count += 1
        else:
            company_id = insert_company(
                name=name,
                description=description,
                sector=sector,
                geography="Sweden",
                city=acc_city,
                website=website,
                stage="Seed",
                heat_score=1,
                stage_source=acc_name,
                stage_detected_date=date.today().isoformat(),
            )
            new_count += 1

        insert_signal(
            company_id=company_id,
            source_type="swedish_accelerator",
            source_name=acc_name,
            source_url=data.get("source_url", ""),
            signal_layer="curated",
            title=f"{name} — {acc_name}",
            metadata=metadata,
        )

        insert_program(
            company_id=company_id,
            program_name=acc_name,
            program_type="Accelerator",
            program_country="Sweden",
        )

    return new_count, existing_count


def main():
    init_db()

    log("Swedish Accelerator Portfolio Scraper")
    log("=" * 50)

    total_new = 0
    total_existing = 0
    results = []
    failed = []

    for acc in ACCELERATORS:
        name = acc["name"]
        city = acc["city"]
        scrape_fn = SCRAPE_FNS[name]

        log(f"\n  [{name}] ({city})")
        log(f"  URL: {acc['url']}")

        try:
            companies = scrape_fn()
            log(f"  Found {len(companies)} companies")

            if companies:
                new, existing = store_companies(companies, name, city)
                total_new += new
                total_existing += existing
                log(f"  Stored: {new} new, {existing} existing")
                results.append((name, len(companies), new, existing))
            else:
                log(f"  WARNING: no companies found")
                results.append((name, 0, 0, 0))

        except Exception as e:
            log(f"  ERROR: {e}")
            failed.append((name, str(e)))

        time.sleep(2)

    # Summary
    log("\n" + "=" * 50)
    log("  SUMMARY — Swedish Accelerators")
    log("=" * 50)

    log(f"\n  {'Accelerator':<22s} {'Found':>6s} {'New':>6s} {'Exist':>6s}")
    log(f"  {'-' * 22} {'-' * 6} {'-' * 6} {'-' * 6}")
    for name, found, new, existing in results:
        log(f"  {name:<22s} {found:>6d} {new:>6d} {existing:>6d}")

    log(f"\n  TOTAL: {total_new} new + {total_existing} existing "
        f"= {total_new + total_existing} companies")

    if failed:
        log(f"\n  FAILED ({len(failed)}):")
        for name, reason in failed:
            log(f"    - {name}: {reason}")

    if SKIPPED:
        log(f"\n  SKIPPED ({len(SKIPPED)}):")
        for name, reason in SKIPPED:
            log(f"    - {name}: {reason}")


if __name__ == "__main__":
    main()
