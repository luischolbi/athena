"""
Athena — Golden Set V3: Per-Field Verification System.

Selects 100 companies stratified across tiers, takes screenshots,
runs fuzzy auto-checks on 6 fields, and produces an interactive
HTML review page with side-by-side evidence.

Artifacts produced in eval/golden_set_v3/:
  - screenshots/{company_id}_website.png and {company_id}_source.png
  - evidence/{company_id}_website.txt and {company_id}_source.txt
  - golden_set_results.csv  (per-field CSV: 100 companies × 6 fields)
  - review.html             (interactive HTML review page)

Usage:
    python build_golden_set_v3.py
"""

import sys
import os
import csv
import time
import json
import re
import asyncio
from collections import Counter
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from fuzzywuzzy import fuzz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from database.database import get_connection

# ── Constants ──

TIMEOUT_MS = 10000   # 10 seconds per page
DELAY = 3.0          # seconds between requests
CONCURRENCY = 5      # parallel browser pages

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "eval", "golden_set_v3")
SCREENSHOTS_DIR = os.path.join(OUTPUT_DIR, "screenshots")
EVIDENCE_DIR = os.path.join(OUTPUT_DIR, "evidence")

FIELDS = ["name", "description", "geography", "city", "sector", "founders"]

# ── European locations (from V2) ──

EUROPEAN_COUNTRIES = {
    "switzerland", "germany", "france", "united kingdom", "uk", "england",
    "scotland", "wales", "ireland", "netherlands", "belgium", "austria",
    "sweden", "norway", "denmark", "finland", "iceland", "spain", "portugal",
    "italy", "greece", "poland", "czech republic", "czechia", "hungary",
    "romania", "bulgaria", "croatia", "serbia", "slovenia", "slovakia",
    "estonia", "latvia", "lithuania", "luxembourg", "malta", "cyprus",
}

EUROPEAN_CITIES = {
    "zurich", "zürich", "geneva", "genève", "lausanne", "bern", "basel",
    "berlin", "munich", "münchen", "hamburg", "frankfurt", "cologne", "köln",
    "stuttgart", "düsseldorf", "dresden", "leipzig",
    "london", "cambridge", "oxford", "edinburgh", "manchester", "bristol",
    "birmingham", "leeds", "glasgow", "cardiff", "belfast",
    "paris", "lyon", "marseille", "toulouse", "bordeaux", "nice", "nantes",
    "amsterdam", "rotterdam", "utrecht", "eindhoven", "the hague", "delft",
    "brussels", "antwerp", "ghent", "leuven",
    "vienna", "graz", "salzburg", "linz", "innsbruck",
    "stockholm", "gothenburg", "malmö", "lund", "uppsala",
    "oslo", "bergen", "trondheim", "stavanger",
    "copenhagen", "aarhus",
    "helsinki", "espoo", "tampere", "oulu",
    "madrid", "barcelona", "valencia", "seville", "bilbao",
    "lisbon", "porto",
    "rome", "milan", "turin", "florence", "bologna", "naples",
    "dublin", "cork", "galway",
    "warsaw", "krakow", "wroclaw", "gdansk", "poznan",
    "prague", "brno",
    "budapest",
    "bucharest", "cluj",
    "tallinn", "riga", "vilnius",
    "luxembourg",
}

PROFILE_FALLBACKS = {
    "Venture Kick": "https://www.venturekick.ch/portfolio",
    "Y Combinator": "https://www.ycombinator.com/companies",
    "Techstars": "https://www.techstars.com/portfolio",
    "Antler": "https://www.antler.co/portfolio",
    "Entrepreneur First": "https://www.joinef.com/portfolio/",
    "Seedcamp": "https://seedcamp.com/our-companies/",
    "500 Global": "https://500.co/companies",
    "EWOR": "https://www.ewor.com/portfolio",
    "EPFL": "https://vpi-startup-compass.pages.dev/startup-list/",
    "ETH AI Center": "https://ai.ethz.ch/entrepreneurship/affiliated-startups.html",
    "University of Oxford": "https://innovation.ox.ac.uk/portfolio/companies-formed/",
    "Imperial College": "https://www.imperial.ac.uk/admin-services/enterprise/about/data-and-reporting/spinout-portfolio/",
    "University of Zurich": "https://www.innovation.uzh.ch/en/spin-offs.html",
    "DTU Science Park": "https://dtusciencepark.com/startups/",
    "EU-Startups": "https://www.eu-startups.com/",
    "Sting": "https://sting.co/companies/",
    "Chalmers Ventures": "https://www.chalmersventures.com/startups",
    "Uminova": "https://www.uminovainnovation.se/en/our-companies/",
    "LEAD": "https://lead.se/en/companies/",
    "GU Ventures": "https://www.guventures.com/portfolio",
    "KTH Innovation": "https://www.kth.se/en/innovation",
    "Hetch": "https://hetch.se/startups/",
    "Minc": "https://minc.se/startups",
    "LU Innovation": "https://www.lu.se/innovation",
    "Cambridge Enterprise": "https://www.enterprise.cam.ac.uk/",
}

# ── Sector keywords ──

SECTOR_KEYWORDS = {
    "AI / Machine Learning": ["artificial intelligence", "machine learning", "deep learning", "neural network", "nlp", "natural language", "computer vision", "generative ai", "llm", "large language model"],
    "FinTech": ["fintech", "financial technology", "banking", "payment", "lending", "insurance", "insurtech", "neobank", "cryptocurrency", "blockchain"],
    "HealthTech / BioTech": ["healthtech", "health tech", "biotech", "biotechnology", "medical", "healthcare", "pharmaceutical", "drug discovery", "clinical", "diagnostics", "genomics", "telemedicine"],
    "CleanTech / Energy": ["cleantech", "clean tech", "renewable", "solar", "wind energy", "sustainability", "carbon", "climate", "energy storage", "battery", "hydrogen", "electric vehicle", "ev charging"],
    "SaaS / Enterprise": ["saas", "software as a service", "enterprise software", "b2b", "crm", "erp", "cloud platform", "workflow", "automation", "productivity"],
    "E-commerce / Marketplace": ["ecommerce", "e-commerce", "marketplace", "online shop", "retail tech", "d2c", "direct to consumer"],
    "EdTech": ["edtech", "education", "learning platform", "online learning", "e-learning", "tutoring", "skill development"],
    "PropTech / Construction": ["proptech", "real estate", "property", "construction tech", "building", "smart home", "smart building"],
    "FoodTech / AgriTech": ["foodtech", "food tech", "agritech", "agri-tech", "agriculture", "farming", "food production", "alternative protein", "vertical farming"],
    "Cybersecurity": ["cybersecurity", "cyber security", "information security", "infosec", "threat detection", "encryption", "identity", "authentication"],
    "Robotics / Hardware": ["robotics", "robot", "drone", "autonomous", "hardware", "sensor", "iot", "internet of things", "embedded", "semiconductor", "chip"],
    "Mobility / Transport": ["mobility", "transport", "logistics", "delivery", "autonomous driving", "fleet", "shipping", "supply chain"],
    "LegalTech": ["legaltech", "legal tech", "legal technology", "contract", "compliance"],
    "HRTech": ["hrtech", "hr tech", "human resources", "recruitment", "hiring", "talent", "workforce"],
    "SpaceTech": ["space", "satellite", "aerospace", "rocket", "orbit"],
    "Gaming / Entertainment": ["gaming", "game", "esports", "entertainment", "streaming", "media"],
    "DeepTech / Quantum": ["quantum", "deep tech", "deeptech", "photonics", "nanotechnology", "advanced materials"],
}

# ── Tier source configs ──
# T1: 10 sources × 5 = 50 companies
TIER1_SOURCES = [
    ("Venture Kick", 5),
    ("Y Combinator", 5),
    ("Techstars", 5),
    ("EPFL", 5),
    ("University of Oxford", 5),
    ("Imperial College", 5),
    ("University of Zurich", 5),
    ("ETH AI Center", 5),
    ("Cambridge Enterprise", 5),
    ("Seedcamp", 5),
]

# T2: ~12 sources totaling 35 companies
TIER2_SOURCES = [
    ("Antler", 5),
    ("Entrepreneur First", 5),
    ("DTU Science Park", 4),
    ("500 Global", 3),
    ("EWOR", 3),
    ("Sting", 3),
    ("Chalmers Ventures", 2),
    ("Uminova", 2),
    ("LEAD", 2),
    ("KTH Innovation", 2),
    ("Hetch", 2),
    ("Minc", 2),
]

# T3: EU-Startups × 15
TIER3_SOURCES = [
    ("EU-Startups", 15),
]


# ── Helpers (from V2) ──

def normalize_url(url):
    """Ensure URL has scheme."""
    if not url or not url.strip():
        return ""
    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url
    elif url.startswith("http://"):
        url = "https://" + url[7:]
    return url


def esc(text):
    if not text:
        return ""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))


def extract_locations(text):
    """Find European country and city mentions in page text."""
    lower = text.lower()
    found_countries = set()
    found_cities = set()

    for country in EUROPEAN_COUNTRIES:
        if re.search(r'\b' + re.escape(country) + r'\b', lower):
            found_countries.add(country.title())

    for city in EUROPEAN_CITIES:
        if re.search(r'\b' + re.escape(city) + r'\b', lower):
            found_cities.add(city.title())

    patterns = [
        r'(?:based|located|headquartered|offices?)\s+in\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)',
        r'(?:address|location)[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})',
    ]
    for pat in patterns:
        for m in re.finditer(pat, text):
            loc = m.group(1).strip()
            if loc.lower() in EUROPEAN_CITIES:
                found_cities.add(loc)
            if loc.lower() in EUROPEAN_COUNTRIES:
                found_countries.add(loc)

    return sorted(found_countries), sorted(found_cities)


def extract_people_names(text):
    """Extract potential founder/team member names from page text."""
    names = set()
    title_keywords = (
        r'(?:CEO|CTO|COO|CFO|CMO|CPO|'
        r'[Cc]o-?[Ff]ounder|[Ff]ounder|'
        r'[Cc]hief\s+\w+\s+[Oo]fficer|'
        r'[Mm]anaging\s+[Dd]irector|'
        r'[Hh]ead\s+of|[Dd]irector|[Pp]resident|'
        r'[Pp]artner|[Vv]ice\s+[Pp]resident|VP)'
    )
    pat = re.compile(
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})'
        r'\s*[,\-–—|]\s*'
        + title_keywords
    )
    for m in pat.finditer(text):
        name = m.group(1).strip()
        if 4 <= len(name) <= 60:
            names.add(name)
    return sorted(names)[:10]


def build_profile_url(source_name, source_url, company_name):
    if source_url and source_url.startswith("http"):
        return source_url
    return PROFILE_FALLBACKS.get(source_name, "")


# ── Playwright helpers (from V2) ──

async def visit_page(page, url):
    """Navigate to a URL with timeout. Returns (success, error_string)."""
    try:
        resp = await page.goto(url, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
        status = resp.status if resp else None
        if status and status >= 400:
            return False, f"HTTP {status}"
        return True, ""
    except PWTimeout:
        return False, "Timeout"
    except Exception as e:
        err = str(e)[:80]
        if "ERR_NAME_NOT_RESOLVED" in err or "DNS" in err:
            return False, "DNS failure"
        if "ERR_CONNECTION_REFUSED" in err:
            return False, "Connection refused"
        if "ERR_CONNECTION_RESET" in err:
            return False, "Connection reset"
        return False, f"Error: {err[:50]}"


async def get_page_meta(page):
    """Extract title, meta description, og:description from current page."""
    result = {"title": "", "meta_description": "", "og_description": ""}
    try:
        result["title"] = (await page.title() or "")[:300]
    except Exception:
        pass
    try:
        result["meta_description"] = (await page.locator('meta[name="description"]')
                                       .first.get_attribute("content") or "")[:500]
    except Exception:
        pass
    try:
        result["og_description"] = (await page.locator('meta[property="og:description"]')
                                     .first.get_attribute("content") or "")[:500]
    except Exception:
        pass
    if not result["meta_description"] and result["og_description"]:
        result["meta_description"] = result["og_description"]
    return result


async def get_visible_text(page, limit=15000):
    """Get visible text content from page, removing scripts/styles."""
    try:
        text = await page.evaluate("""() => {
            const clone = document.body.cloneNode(true);
            clone.querySelectorAll('script, style, noscript, svg, path, link').forEach(el => el.remove());
            return clone.innerText || clone.textContent || '';
        }""")
        return (text or "")[:limit]
    except Exception:
        return ""


async def get_h1s(page):
    """Extract all h1 text from the page."""
    try:
        h1s = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('h1')).map(h => h.innerText || h.textContent || '').filter(t => t.trim());
        }""")
        return h1s or []
    except Exception:
        return []


# ── B. Auto-check functions ──

def check_name(db_value, title, h1s, body_text):
    """Check company name against website title, h1s, and body text."""
    if not db_value or not db_value.strip():
        return "NEEDS_REVIEW", "Empty DB value"

    name = db_value.strip()

    # Check against title
    if title:
        ratio = fuzz.ratio(name.lower(), title.lower())
        if ratio > 80:
            return "AUTO_PASS", f"Title match (ratio={ratio}): {title[:80]}"
        # Substring check
        if name.lower() in title.lower():
            return "AUTO_PASS", f"Name found in title: {title[:80]}"

    # Check against h1s
    for h1 in h1s:
        if not h1:
            continue
        ratio = fuzz.ratio(name.lower(), h1.strip().lower())
        if ratio > 80:
            return "AUTO_PASS", f"H1 match (ratio={ratio}): {h1[:80]}"
        if name.lower() in h1.lower():
            return "AUTO_PASS", f"Name found in H1: {h1[:80]}"

    # Substring in body
    if body_text and name.lower() in body_text.lower():
        return "AUTO_PASS", f"Name found in body text"

    # Fuzzy partial against title
    if title:
        ratio = fuzz.ratio(name.lower(), title.lower())
        if ratio > 50:
            return "NEEDS_REVIEW", f"Partial title match (ratio={ratio}): {title[:80]}"

    return "AUTO_FAIL", f"Name not found. Title: {(title or 'N/A')[:80]}"


def check_description(db_value, body_text):
    """Check description against website body text using fuzzy matching."""
    if not db_value or not db_value.strip():
        return "NEEDS_REVIEW", "Empty DB value"
    if not body_text or not body_text.strip():
        return "NEEDS_REVIEW", "No body text available"

    desc = db_value.strip()[:200]  # Limit for fuzzy matching performance
    body_lower = body_text.lower()

    partial = fuzz.partial_ratio(desc.lower(), body_lower[:5000])
    token = fuzz.token_set_ratio(desc.lower(), body_lower[:5000])
    best = max(partial, token)

    if best > 70:
        return "AUTO_PASS", f"Description match (partial={partial}, token_set={token})"
    if best > 40:
        return "NEEDS_REVIEW", f"Weak match (partial={partial}, token_set={token})"

    return "AUTO_FAIL", f"No match (partial={partial}, token_set={token})"


def check_geography(db_value, combined_text):
    """Check geography (country) against combined website+source text."""
    if not db_value or not db_value.strip():
        return "NEEDS_REVIEW", "Empty DB value"
    if not combined_text or not combined_text.strip():
        return "NEEDS_REVIEW", "No text available"

    country = db_value.strip().lower()
    text_lower = combined_text.lower()

    # Exact word-boundary regex match
    if re.search(r'\b' + re.escape(country) + r'\b', text_lower):
        return "AUTO_PASS", f"Country '{db_value.strip()}' found in text"

    # Try common aliases
    aliases = {
        "united kingdom": ["uk", "england", "britain", "great britain"],
        "uk": ["united kingdom", "england", "britain"],
        "switzerland": ["swiss", "suisse", "schweiz"],
        "germany": ["german", "deutschland"],
        "france": ["french"],
        "netherlands": ["dutch", "holland"],
        "sweden": ["swedish", "sverige"],
        "norway": ["norwegian", "norge"],
        "denmark": ["danish", "danmark"],
        "finland": ["finnish", "suomi"],
        "austria": ["austrian", "österreich"],
        "spain": ["spanish", "españa"],
        "portugal": ["portuguese"],
        "italy": ["italian", "italia"],
        "ireland": ["irish"],
        "poland": ["polish", "polska"],
        "czech republic": ["czechia", "czech"],
        "czechia": ["czech republic", "czech"],
    }
    for alias in aliases.get(country, []):
        if re.search(r'\b' + re.escape(alias) + r'\b', text_lower):
            return "AUTO_PASS", f"Country alias '{alias}' found for '{db_value.strip()}'"

    return "AUTO_FAIL", f"Country '{db_value.strip()}' not found in text"


def check_city(db_value, combined_text):
    """Check city against combined website+source text."""
    if not db_value or not db_value.strip():
        return "NEEDS_REVIEW", "Empty DB value"
    if not combined_text or not combined_text.strip():
        return "NEEDS_REVIEW", "No text available"

    city = db_value.strip().lower()
    text_lower = combined_text.lower()

    if re.search(r'\b' + re.escape(city) + r'\b', text_lower):
        return "AUTO_PASS", f"City '{db_value.strip()}' found in text"

    return "AUTO_FAIL", f"City '{db_value.strip()}' not found in text"


def check_sector(db_value, body_text):
    """Check sector via keyword list lookup from SECTOR_KEYWORDS."""
    if not db_value or not db_value.strip():
        return "NEEDS_REVIEW", "Empty DB value"
    if not body_text or not body_text.strip():
        return "NEEDS_REVIEW", "No body text available"

    sector = db_value.strip()
    body_lower = body_text.lower()

    # Find matching sector keyword list
    keywords = SECTOR_KEYWORDS.get(sector)
    if not keywords:
        # Try partial match on sector name
        sector_lower = sector.lower()
        for sname, kws in SECTOR_KEYWORDS.items():
            if sector_lower in sname.lower() or sname.lower() in sector_lower:
                keywords = kws
                break

    if not keywords:
        # No keyword list for this sector — try the sector name itself as keyword
        if sector.lower() in body_lower:
            return "AUTO_PASS", f"Sector name '{sector}' found in body"
        return "NEEDS_REVIEW", f"No keyword list for sector '{sector}'"

    found = []
    for kw in keywords:
        if kw.lower() in body_lower:
            found.append(kw)

    if found:
        return "AUTO_PASS", f"Sector keywords found: {', '.join(found[:5])}"

    return "AUTO_FAIL", f"No sector keywords found for '{sector}'"


def check_founders(db_founders, body_text):
    """Check founder names against website body text.
    db_founders is a list of founder name strings.
    """
    if not db_founders:
        return "NEEDS_REVIEW", "No founders in DB"
    if not body_text or not body_text.strip():
        return "NEEDS_REVIEW", "No body text available"

    body_lower = body_text.lower()
    found = []
    not_found = []

    for name in db_founders:
        name_clean = name.strip()
        if not name_clean:
            continue

        # Exact substring match
        if name_clean.lower() in body_lower:
            found.append(name_clean)
            continue

        # Last-name fallback
        parts = name_clean.split()
        if len(parts) >= 2:
            last_name = parts[-1]
            if len(last_name) > 2 and last_name.lower() in body_lower:
                found.append(f"{name_clean} (last name)")
                continue

        not_found.append(name_clean)

    total = len(found) + len(not_found)
    if total == 0:
        return "NEEDS_REVIEW", "No valid founder names"

    if not not_found:
        return "AUTO_PASS", f"All founders found: {', '.join(found[:5])}"
    if found:
        return "NEEDS_REVIEW", f"Partial: found {', '.join(found[:3])}; missing {', '.join(not_found[:3])}"

    return "AUTO_FAIL", f"No founders found. Looking for: {', '.join(not_found[:5])}"


# ── C. Stratified sampling ──

def select_companies(conn):
    """Select 100 companies stratified across tiers with score mixing."""
    selected = []
    seen_ids = set()

    tier_configs = [
        (1, TIER1_SOURCES),
        (2, TIER2_SOURCES),
        (3, TIER3_SOURCES),
    ]

    for tier, sources in tier_configs:
        for source_name, count in sources:
            # Extra filter for T3
            if tier == 3:
                extra = "AND c.website IS NOT NULL AND c.website != '' AND c.description IS NOT NULL AND c.description != ''"
            else:
                extra = ""

            # Half from top scores, half from bottom scores
            half_top = (count + 1) // 2
            half_bottom = count - half_top

            # Top scores
            query_top = f"""
                SELECT c.*, s.source_url, s.source_name
                FROM companies c
                JOIN signals s ON s.company_id = c.id AND s.source_name = ?
                WHERE c.data_tier = ? {extra}
                GROUP BY c.id
                ORDER BY c.athena_score DESC
            """
            rows_top = conn.execute(query_top, (source_name, tier)).fetchall()

            # Bottom scores (randomized among low scorers)
            query_bottom = f"""
                SELECT c.*, s.source_url, s.source_name
                FROM companies c
                JOIN signals s ON s.company_id = c.id AND s.source_name = ?
                WHERE c.data_tier = ? {extra}
                GROUP BY c.id
                ORDER BY c.athena_score ASC, RANDOM()
            """
            rows_bottom = conn.execute(query_bottom, (source_name, tier)).fetchall()

            picked = 0
            # Pick from top
            for row in rows_top:
                if picked >= half_top:
                    break
                if row["id"] in seen_ids:
                    continue
                seen_ids.add(row["id"])
                selected.append({
                    "company": row,
                    "source_name": source_name,
                    "source_url": row["source_url"] or "",
                    "tier": tier,
                })
                picked += 1

            top_picked = picked

            # Pick from bottom (skip already picked)
            picked_bottom = 0
            for row in rows_bottom:
                if picked_bottom >= half_bottom:
                    break
                if row["id"] in seen_ids:
                    continue
                seen_ids.add(row["id"])
                selected.append({
                    "company": row,
                    "source_name": source_name,
                    "source_url": row["source_url"] or "",
                    "tier": tier,
                })
                picked_bottom += 1

            total_picked = top_picked + picked_bottom
            if total_picked < count:
                print(f"  Warning: only found {total_picked}/{count} for T{tier} {source_name}", flush=True)

    return selected


# ── D. Process company ──

async def process_company(context, item, founder_map, sem, progress):
    """Process a single company: screenshot + evidence + auto-checks."""
    async with sem:
        c = item["company"]
        cid = c["id"]
        website = normalize_url(c["website"] or "")
        source_name = item["source_name"]
        profile_url = build_profile_url(source_name, item["source_url"], c["name"])
        tier = item["tier"]
        athena_score = c["athena_score"] or 0

        page = await context.new_page()

        # Block heavy resources initially
        async def block_resources(route):
            if route.request.resource_type in ("image", "stylesheet", "font", "media", "websocket"):
                await route.abort()
            else:
                await route.continue_()
        await page.route("**/*", block_resources)

        website_title = ""
        website_meta = ""
        website_body = ""
        website_h1s = []
        website_ok = False
        website_error = ""
        source_text = ""
        source_ok = False
        source_error = ""

        try:
            # ── Visit website ──
            if website:
                await asyncio.sleep(DELAY)
                ok, err = await visit_page(page, website)
                if ok:
                    website_ok = True
                    meta = await get_page_meta(page)
                    website_title = meta.get("title", "")
                    website_meta = meta.get("meta_description", "")
                    website_body = await get_visible_text(page, limit=15000)
                    website_h1s = await get_h1s(page)

                    # Save evidence text
                    evidence_web = f"URL: {website}\nTitle: {website_title}\nMeta: {website_meta}\nH1s: {json.dumps(website_h1s)}\n\n--- Body ---\n{website_body[:10000]}"
                    with open(os.path.join(EVIDENCE_DIR, f"{cid}_website.txt"), "w", encoding="utf-8") as f:
                        f.write(evidence_web)

                    # Screenshot: unroute blocking, take screenshot, re-route
                    await page.unroute("**/*")
                    try:
                        await page.screenshot(
                            path=os.path.join(SCREENSHOTS_DIR, f"{cid}_website.png"),
                            full_page=False, timeout=8000
                        )
                    except Exception:
                        pass
                    await page.route("**/*", block_resources)
                else:
                    website_error = err
                    # Save error evidence
                    with open(os.path.join(EVIDENCE_DIR, f"{cid}_website.txt"), "w", encoding="utf-8") as f:
                        f.write(f"URL: {website}\nError: {err}\n")

            # ── Visit source page ──
            if profile_url and profile_url.startswith("http"):
                await asyncio.sleep(DELAY)
                ok, err = await visit_page(page, profile_url)
                if ok:
                    source_ok = True
                    source_text = await get_visible_text(page, limit=20000)

                    # Save evidence text
                    with open(os.path.join(EVIDENCE_DIR, f"{cid}_source.txt"), "w", encoding="utf-8") as f:
                        f.write(f"URL: {profile_url}\nSource: {source_name}\n\n--- Body ---\n{source_text[:10000]}")

                    # Screenshot
                    await page.unroute("**/*")
                    try:
                        await page.screenshot(
                            path=os.path.join(SCREENSHOTS_DIR, f"{cid}_source.png"),
                            full_page=False, timeout=8000
                        )
                    except Exception:
                        pass
                    await page.route("**/*", block_resources)
                else:
                    source_error = err
                    with open(os.path.join(EVIDENCE_DIR, f"{cid}_source.txt"), "w", encoding="utf-8") as f:
                        f.write(f"URL: {profile_url}\nSource: {source_name}\nError: {err}\n")

        finally:
            await page.close()

        # ── Combined text for geography/city checks ──
        combined_text = (website_body or "") + "\n" + (source_text or "")

        # ── Run 6 auto-checks ──
        db_founders = founder_map.get(cid, [])
        # Strip title info from founder names for matching
        founder_names = []
        for fn in db_founders:
            # founders stored as "Name (Title)" — extract just name
            name_part = fn.split("(")[0].strip() if "(" in fn else fn.strip()
            if name_part:
                founder_names.append(name_part)

        # Website unreachable → NEEDS_REVIEW for website-dependent checks
        if not website:
            name_verdict, name_evidence = "NEEDS_REVIEW", "No website URL"
            desc_verdict, desc_evidence = "NEEDS_REVIEW", "No website URL"
            sector_verdict, sector_evidence = "NEEDS_REVIEW", "No website URL"
            founders_verdict, founders_evidence = "NEEDS_REVIEW", "No website URL"
        elif not website_ok:
            name_verdict, name_evidence = "NEEDS_REVIEW", f"Website unreachable: {website_error}"
            desc_verdict, desc_evidence = "NEEDS_REVIEW", f"Website unreachable: {website_error}"
            sector_verdict, sector_evidence = "NEEDS_REVIEW", f"Website unreachable: {website_error}"
            founders_verdict, founders_evidence = "NEEDS_REVIEW", f"Website unreachable: {website_error}"
        else:
            name_verdict, name_evidence = check_name(c["name"], website_title, website_h1s, website_body)
            desc_verdict, desc_evidence = check_description(c["description"], website_body)
            sector_verdict, sector_evidence = check_sector(c["sector"], website_body)
            # For founders, check combined text (website + source)
            founders_verdict, founders_evidence = check_founders(founder_names, combined_text)

        # Geography & city use combined text regardless
        geo_verdict, geo_evidence = check_geography(c["geography"], combined_text)
        city_verdict, city_evidence = check_city(c["city"], combined_text)

        field_results = [
            {
                "field": "name",
                "db_value": c["name"] or "",
                "evidence_found": name_evidence,
                "auto_verdict": name_verdict,
            },
            {
                "field": "description",
                "db_value": (c["description"] or "")[:200],
                "evidence_found": desc_evidence,
                "auto_verdict": desc_verdict,
            },
            {
                "field": "geography",
                "db_value": c["geography"] or "",
                "evidence_found": geo_evidence,
                "auto_verdict": geo_verdict,
            },
            {
                "field": "city",
                "db_value": c["city"] or "",
                "evidence_found": city_evidence,
                "auto_verdict": city_verdict,
            },
            {
                "field": "sector",
                "db_value": c["sector"] or "",
                "evidence_found": sector_evidence,
                "auto_verdict": sector_verdict,
            },
            {
                "field": "founders",
                "db_value": ", ".join(founder_names) if founder_names else "",
                "evidence_found": founders_evidence,
                "auto_verdict": founders_verdict,
            },
        ]

        result = {
            "company_id": cid,
            "company_name": c["name"],
            "source": source_name,
            "tier": tier,
            "athena_score": athena_score,
            "website": website,
            "profile_url": profile_url,
            "website_ok": website_ok,
            "website_error": website_error,
            "source_ok": source_ok,
            "source_error": source_error,
            "website_title": website_title,
            "website_meta": website_meta,
            "field_results": field_results,
        }

        progress["done"] += 1
        n = progress["done"]
        total = progress["total"]
        elapsed = time.time() - progress["t_start"]
        eta = (total - n) * elapsed / max(n, 1) / 60
        verdicts_str = " ".join(f"{fr['field'][0].upper()}={'P' if fr['auto_verdict']=='AUTO_PASS' else 'F' if fr['auto_verdict']=='AUTO_FAIL' else 'R'}" for fr in field_results)
        print(f"    {n:>3}/{total} (ETA {eta:.1f}m) "
              f"T{tier} {c['name'][:25]:25s} [{verdicts_str}]", flush=True)

        return result


# ── E. CSV writer ──

def write_csv(results, path):
    """Write 600-row CSV (100 companies × 6 fields)."""
    fieldnames = [
        "company_id", "company_name", "source", "tier", "athena_score",
        "field", "db_value", "evidence_found", "auto_verdict",
        "human_verdict", "notes",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            for fr in r["field_results"]:
                writer.writerow({
                    "company_id": r["company_id"],
                    "company_name": r["company_name"],
                    "source": r["source"],
                    "tier": r["tier"],
                    "athena_score": r["athena_score"],
                    "field": fr["field"],
                    "db_value": fr["db_value"],
                    "evidence_found": fr["evidence_found"],
                    "auto_verdict": fr["auto_verdict"],
                    "human_verdict": "",
                    "notes": "",
                })


# ── F. HTML writer ──

def verdict_color(verdict):
    if verdict == "AUTO_PASS":
        return "#4ade80"  # green
    elif verdict == "AUTO_FAIL":
        return "#f87171"  # red
    else:
        return "#fbbf24"  # amber


def verdict_badge_html(verdict):
    color = verdict_color(verdict)
    label = verdict.replace("AUTO_", "").replace("NEEDS_", "")
    return f'<span style="color:{color};font-weight:600;font-size:11px;padding:2px 6px;border:1px solid {color}40;border-radius:4px">{label}</span>'


def tier_badge_html(tier):
    colors = {1: "#60a5fa", 2: "#a78bfa", 3: "#f97316"}
    c = colors.get(tier, "#9ca3af")
    return f'<span style="color:{c};font-size:11px;padding:2px 6px;border:1px solid {c}40;border-radius:4px">T{tier}</span>'


def write_html(results, path):
    """Write interactive HTML review page."""
    total_fields = sum(len(r["field_results"]) for r in results)
    pass_count = sum(1 for r in results for fr in r["field_results"] if fr["auto_verdict"] == "AUTO_PASS")
    fail_count = sum(1 for r in results for fr in r["field_results"] if fr["auto_verdict"] == "AUTO_FAIL")
    review_count = sum(1 for r in results for fr in r["field_results"] if fr["auto_verdict"] == "NEEDS_REVIEW")

    cards = []
    for i, r in enumerate(results):
        cid = r["company_id"]
        website_link = f'<a href="{esc(r["website"])}" target="_blank">website</a>' if r.get("website") else "—"
        source_link = f'<a href="{esc(r["profile_url"])}" target="_blank">source</a>' if r.get("profile_url") else "—"

        # Screenshots
        web_screenshot = f"screenshots/{cid}_website.png"
        src_screenshot = f"screenshots/{cid}_source.png"

        # Field rows
        field_rows = []
        for j, fr in enumerate(r["field_results"]):
            v_color = verdict_color(fr["auto_verdict"])
            v_label = fr["auto_verdict"].replace("AUTO_", "").replace("NEEDS_", "")
            field_rows.append(f"""
                <tr class="field-row" data-verdict="{fr['auto_verdict']}">
                    <td class="field-name">{esc(fr['field'])}</td>
                    <td class="db-value">{esc(fr['db_value'][:200])}</td>
                    <td class="evidence">{esc(fr['evidence_found'][:300])}</td>
                    <td class="auto-verdict" style="color:{v_color}">{v_label}</td>
                    <td class="override-cell">
                        <label><input type="radio" name="f{i}_{j}" value="PASS"> Pass</label>
                        <label><input type="radio" name="f{i}_{j}" value="FAIL"> Fail</label>
                        <label><input type="radio" name="f{i}_{j}" value="SKIP"> Skip</label>
                        <input type="text" class="note-input" placeholder="notes" data-field="f{i}_{j}_note">
                    </td>
                </tr>
            """)

        # Compute per-card verdict summary
        card_verdicts = [fr["auto_verdict"] for fr in r["field_results"]]
        card_fails = sum(1 for v in card_verdicts if v == "AUTO_FAIL")
        card_reviews = sum(1 for v in card_verdicts if v == "NEEDS_REVIEW")
        card_class = "card"
        if card_fails > 0:
            card_class += " has-fail"
        if card_reviews > 0:
            card_class += " has-review"

        cards.append(f"""
        <div class="{card_class}" data-idx="{i}" data-company-id="{cid}">
            <div class="card-header">
                <div class="card-title">
                    <span class="card-num">#{i+1}</span>
                    <strong>{esc(r['company_name'])}</strong>
                    <span class="source-badge">{esc(r['source'])}</span>
                    {tier_badge_html(r['tier'])}
                    <span class="score-badge">Score: {r['athena_score']:.1f}</span>
                </div>
                <div class="card-links">{website_link} &middot; {source_link}</div>
            </div>
            <div class="screenshots">
                <div class="screenshot-col">
                    <div class="screenshot-label">Website</div>
                    <img src="{web_screenshot}" alt="Website screenshot" onerror="this.style.display='none';this.nextElementSibling.style.display='block'">
                    <div class="screenshot-placeholder" style="display:none">No screenshot</div>
                </div>
                <div class="screenshot-col">
                    <div class="screenshot-label">Source</div>
                    <img src="{src_screenshot}" alt="Source screenshot" onerror="this.style.display='none';this.nextElementSibling.style.display='block'">
                    <div class="screenshot-placeholder" style="display:none">No screenshot</div>
                </div>
            </div>
            <table class="field-table">
                <thead>
                    <tr>
                        <th style="width:90px">Field</th>
                        <th>DB Value</th>
                        <th>Evidence</th>
                        <th style="width:70px">Auto</th>
                        <th style="width:250px">Override</th>
                    </tr>
                </thead>
                <tbody>{''.join(field_rows)}</tbody>
            </table>
        </div>
        """)

    data_json = json.dumps([{
        "id": r["company_id"],
        "name": r["company_name"],
        "source": r["source"],
        "tier": r["tier"],
        "fields": [{
            "field": fr["field"],
            "db_value": fr["db_value"],
            "auto_verdict": fr["auto_verdict"],
            "evidence": fr["evidence_found"],
        } for fr in r["field_results"]]
    } for r in results])

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Athena Golden Set V3 — Per-Field Review</title>
<style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        background: #0f1117; color: #e2e2e9; padding: 20px 40px; line-height: 1.5;
    }}
    h1 {{ font-size: 22px; margin-bottom: 6px; color: #fff; }}
    .subtitle {{ color: #8b8b9e; font-size: 13px; margin-bottom: 24px; }}
    .toolbar {{
        position: sticky; top: 0; z-index: 100;
        background: #0f1117ee; backdrop-filter: blur(10px);
        padding: 12px 0; margin-bottom: 16px;
        border-bottom: 1px solid #2a2a3a;
        display: flex; gap: 12px; align-items: center; flex-wrap: wrap;
    }}
    .toolbar button {{
        padding: 8px 16px; border-radius: 8px; border: 1px solid #3b82f6;
        background: #3b82f620; color: #60a5fa; font-size: 13px;
        cursor: pointer; font-weight: 500;
    }}
    .toolbar button:hover {{ background: #3b82f640; }}
    .toolbar .stats {{ color: #8b8b9e; font-family: monospace; font-size: 12px; margin-left: auto; }}
    .filter-btns {{ display: flex; gap: 6px; }}
    .filter-btns button {{
        padding: 5px 12px; border-radius: 6px; border: 1px solid #2a2a3a;
        background: #1a1a2e; color: #8b8b9e; font-size: 12px; cursor: pointer;
    }}
    .filter-btns button.active {{ border-color: #3b82f6; color: #60a5fa; background: #3b82f615; }}
    .card {{
        background: #1a1a2e; border: 1px solid #2a2a3a; border-radius: 12px;
        margin-bottom: 12px; overflow: hidden;
    }}
    .card.has-fail {{ border-left: 3px solid #f87171; }}
    .card.has-review:not(.has-fail) {{ border-left: 3px solid #fbbf24; }}
    .card:not(.has-fail):not(.has-review) {{ border-left: 3px solid #22c55e40; }}
    .card-header {{
        padding: 14px 18px; display: flex; justify-content: space-between;
        align-items: center; border-bottom: 1px solid #2a2a3a;
    }}
    .card-title {{ display: flex; align-items: center; gap: 10px; }}
    .card-num {{ color: #8b8b9e; font-family: monospace; font-size: 12px; }}
    .card-title strong {{ font-size: 15px; color: #fff; }}
    .source-badge {{
        font-size: 11px; padding: 2px 8px; border-radius: 4px;
        background: #3b82f615; color: #60a5fa; border: 1px solid #3b82f630;
    }}
    .score-badge {{
        font-size: 11px; padding: 2px 8px; border-radius: 4px;
        background: #6b728015; color: #9ca3af; border: 1px solid #6b728030;
        font-family: monospace;
    }}
    .card-links {{ font-size: 12px; }}
    .card-links a {{ color: #60a5fa; text-decoration: none; }}
    .card-links a:hover {{ text-decoration: underline; }}
    .screenshots {{
        display: flex; gap: 12px; padding: 12px 18px;
        border-bottom: 1px solid #2a2a3a;
    }}
    .screenshot-col {{ flex: 1; text-align: center; }}
    .screenshot-label {{
        font-size: 11px; color: #8b8b9e; margin-bottom: 6px;
        text-transform: uppercase; letter-spacing: 0.05em;
    }}
    .screenshot-col img {{
        max-width: 100%; border-radius: 6px; border: 1px solid #2a2a3a;
        max-height: 250px; object-fit: contain;
    }}
    .screenshot-placeholder {{
        color: #6b728080; font-style: italic; font-size: 12px;
        padding: 40px; border: 1px dashed #2a2a3a; border-radius: 6px;
    }}
    .field-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    .field-table th {{
        text-align: left; padding: 8px 14px; font-size: 11px;
        text-transform: uppercase; letter-spacing: 0.05em;
        color: #8b8b9e; border-bottom: 1px solid #2a2a3a; font-weight: 500;
    }}
    .field-table td {{ padding: 8px 14px; border-bottom: 1px solid #2a2a3a20; vertical-align: top; }}
    .field-name {{ font-weight: 500; color: #a0a0b0; width: 90px; white-space: nowrap; text-transform: capitalize; }}
    .db-value {{ max-width: 250px; word-break: break-word; }}
    .evidence {{ max-width: 300px; word-break: break-word; color: #c0c0d0; font-size: 12px; }}
    .auto-verdict {{ font-weight: 600; font-size: 12px; white-space: nowrap; }}
    .override-cell {{ white-space: nowrap; }}
    .override-cell label {{
        display: inline-flex; align-items: center; gap: 3px;
        margin-right: 6px; font-size: 12px; cursor: pointer; color: #8b8b9e;
    }}
    .override-cell input[type="radio"] {{ cursor: pointer; accent-color: #3b82f6; }}
    .note-input {{
        width: 80px; padding: 2px 6px; margin-left: 4px;
        background: #0f1117; border: 1px solid #2a2a3a; border-radius: 4px;
        color: #e2e2e9; font-size: 11px;
    }}
    .hidden {{ display: none !important; }}
    .card.manual-reviewed {{ opacity: 0.7; }}
</style>
</head>
<body>

<h1>Athena Golden Set V3 — Per-Field Review</h1>
<p class="subtitle">
    {len(results)} companies &middot; {total_fields} fields &middot;
    <span style="color:#4ade80">{pass_count} AUTO_PASS</span> &middot;
    <span style="color:#f87171">{fail_count} AUTO_FAIL</span> &middot;
    <span style="color:#fbbf24">{review_count} NEEDS_REVIEW</span>
</p>

<div class="toolbar">
    <button onclick="exportResults()">Export CSV</button>
    <div class="filter-btns">
        <button class="active" onclick="filterCards('all', this)">All ({len(results)})</button>
        <button onclick="filterCards('needs_review', this)">NEEDS_REVIEW</button>
        <button onclick="filterCards('auto_fail', this)">AUTO_FAIL</button>
        <button onclick="filterCards('unreviewed', this)">Unreviewed</button>
    </div>
    <div class="stats" id="stats">0 of {total_fields} fields reviewed, 0 overrides</div>
</div>

<div id="cards">
{''.join(cards)}
</div>

<script>
const TOTAL_COMPANIES = {len(results)};
const FIELD_COUNT = {len(FIELDS)};
const DATA = {data_json};

function updateStats() {{
    let reviewed = 0, overrides = 0;
    for (let i = 0; i < TOTAL_COMPANIES; i++) {{
        let allDone = true;
        for (let j = 0; j < FIELD_COUNT; j++) {{
            const radios = document.querySelectorAll('input[name="f' + i + '_' + j + '"]');
            const checked = Array.from(radios).find(r => r.checked);
            if (!checked) allDone = false;
            else {{ reviewed++; overrides++; }}
        }}
        const card = document.querySelector('.card[data-idx="' + i + '"]');
        if (card) card.classList.toggle('manual-reviewed', allDone);
    }}
    document.getElementById('stats').textContent =
        reviewed + ' of ' + (TOTAL_COMPANIES * FIELD_COUNT) + ' fields reviewed, ' + overrides + ' overrides';
}}

document.addEventListener('change', (e) => {{
    if (e.target.type === 'radio') updateStats();
}});

function filterCards(mode, btn) {{
    document.querySelectorAll('.filter-btns button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    document.querySelectorAll('.card').forEach(card => {{
        if (mode === 'all') {{ card.classList.remove('hidden'); return; }}
        if (mode === 'needs_review') {{
            const hasReview = card.querySelector('tr[data-verdict="NEEDS_REVIEW"]');
            card.classList.toggle('hidden', !hasReview);
            return;
        }}
        if (mode === 'auto_fail') {{
            const hasFail = card.querySelector('tr[data-verdict="AUTO_FAIL"]');
            card.classList.toggle('hidden', !hasFail);
            return;
        }}
        if (mode === 'unreviewed') {{
            card.classList.toggle('hidden', card.classList.contains('manual-reviewed'));
            return;
        }}
    }});
}}

function exportResults() {{
    let csv = 'company_id,company_name,source,tier,field,db_value,auto_verdict,human_verdict,notes\\n';
    for (let i = 0; i < TOTAL_COMPANIES; i++) {{
        const d = DATA[i];
        for (let j = 0; j < FIELD_COUNT; j++) {{
            const f = d.fields[j];
            const radios = document.querySelectorAll('input[name="f' + i + '_' + j + '"]');
            const checked = Array.from(radios).find(r => r.checked);
            const human = checked ? checked.value : '';
            const noteEl = document.querySelector('input[data-field="f' + i + '_' + j + '_note"]');
            const note = noteEl ? noteEl.value.replace(/"/g, '""') : '';
            const name = d.name.replace(/"/g, '""');
            const src = d.source.replace(/"/g, '""');
            const dbv = f.db_value.replace(/"/g, '""');
            csv += '"' + d.id + '","' + name + '","' + src + '",' + d.tier + ',"' + f.field + '","' + dbv + '","' + f.auto_verdict + '","' + human + '","' + note + '"\\n';
        }}
    }}
    const blob = new Blob([csv], {{ type: 'text/csv' }});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'golden_set_v3_review.csv';
    a.click();
    URL.revokeObjectURL(url);
}}

updateStats();
</script>

</body>
</html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


# ── G. Print summary ──

def print_summary(results):
    """Print console summary of auto-check results."""
    all_fields = []
    for r in results:
        for fr in r["field_results"]:
            all_fields.append({**fr, "tier": r["tier"]})

    total = len(all_fields)
    pass_count = sum(1 for f in all_fields if f["auto_verdict"] == "AUTO_PASS")
    fail_count = sum(1 for f in all_fields if f["auto_verdict"] == "AUTO_FAIL")
    review_count = sum(1 for f in all_fields if f["auto_verdict"] == "NEEDS_REVIEW")

    print(f"\n{'─' * 64}", flush=True)
    print(f"  Auto-check Summary", flush=True)
    print(f"{'─' * 64}", flush=True)
    print(f"  Total fields:    {total}", flush=True)
    print(f"  AUTO_PASS:       {pass_count:>4}  ({pass_count*100//max(total,1)}%)", flush=True)
    print(f"  AUTO_FAIL:       {fail_count:>4}  ({fail_count*100//max(total,1)}%)", flush=True)
    print(f"  NEEDS_REVIEW:    {review_count:>4}  ({review_count*100//max(total,1)}%)", flush=True)

    # Per-field breakdown
    print(f"\n  Per-field breakdown:", flush=True)
    print(f"  {'Field':<14} {'PASS':>6} {'FAIL':>6} {'REVIEW':>8} {'Total':>6}", flush=True)
    print(f"  {'─'*44}", flush=True)
    for field_name in FIELDS:
        field_items = [f for f in all_fields if f["field"] == field_name]
        fp = sum(1 for f in field_items if f["auto_verdict"] == "AUTO_PASS")
        ff = sum(1 for f in field_items if f["auto_verdict"] == "AUTO_FAIL")
        fr = sum(1 for f in field_items if f["auto_verdict"] == "NEEDS_REVIEW")
        ft = len(field_items)
        print(f"  {field_name:<14} {fp:>6} {ff:>6} {fr:>8} {ft:>6}", flush=True)

    # Per-tier pass rates
    print(f"\n  Per-tier pass rates:", flush=True)
    for tier in [1, 2, 3]:
        tier_items = [f for f in all_fields if f["tier"] == tier]
        if not tier_items:
            continue
        tp = sum(1 for f in tier_items if f["auto_verdict"] == "AUTO_PASS")
        tt = len(tier_items)
        print(f"  Tier {tier}: {tp}/{tt} ({tp*100//max(tt,1)}% pass)", flush=True)

    print(f"{'─' * 64}", flush=True)


# ── H. Orchestrator ──

async def async_main():
    print(flush=True)
    print("=" * 64, flush=True)
    print("  ATHENA — Golden Set V3 (Per-Field Verification)", flush=True)
    print("=" * 64, flush=True)

    # Create output directories
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    os.makedirs(EVIDENCE_DIR, exist_ok=True)

    conn = get_connection()

    # Step 1: Select 100 companies
    print("\n  Step 1: Selecting 100 companies (stratified by tier)...", flush=True)
    selected = select_companies(conn)
    print(f"    Selected {len(selected)} companies", flush=True)

    tier_counts = Counter(s["tier"] for s in selected)
    for tier in sorted(tier_counts):
        print(f"      Tier {tier}: {tier_counts[tier]}", flush=True)

    source_counts = Counter(s["source_name"] for s in selected)
    for src, cnt in source_counts.most_common():
        print(f"        {src:30s} {cnt:>3}", flush=True)

    # Step 2: Load founders from DB
    print("\n  Step 2: Loading founders...", flush=True)
    founder_map = {}
    for item in selected:
        cid = item["company"]["id"]
        rows = conn.execute(
            "SELECT name, title FROM founders WHERE company_id = ?", (cid,)
        ).fetchall()
        founder_map[cid] = [
            f"{r['name']}" + (f" ({r['title']})" if r['title'] else "")
            for r in rows
        ]
    has_founders = sum(1 for v in founder_map.values() if v)
    print(f"    Loaded founders for {has_founders}/{len(selected)} companies", flush=True)

    conn.close()

    # Step 3: Launch Playwright
    total = len(selected)
    print(f"\n  Step 3: Processing {total} companies ({CONCURRENCY} concurrent, {DELAY}s delay, {TIMEOUT_MS}ms timeout)...", flush=True)

    progress = {"done": 0, "total": total, "t_start": time.time()}
    sem = asyncio.Semaphore(CONCURRENCY)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 720},
        )

        tasks = [
            process_company(context, item, founder_map, sem, progress)
            for item in selected
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        await browser.close()

    # Filter out exceptions
    good_results = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            c = selected[i]["company"]
            cid = c["id"]
            print(f"    ERROR: {c['name']}: {r}", flush=True)
            # Create a placeholder result
            good_results.append({
                "company_id": cid,
                "company_name": c["name"],
                "source": selected[i]["source_name"],
                "tier": selected[i]["tier"],
                "athena_score": c["athena_score"] or 0,
                "website": normalize_url(c["website"] or ""),
                "profile_url": build_profile_url(selected[i]["source_name"], selected[i]["source_url"], c["name"]),
                "website_ok": False,
                "website_error": str(r)[:60],
                "source_ok": False,
                "source_error": "",
                "website_title": "",
                "website_meta": "",
                "field_results": [
                    {"field": f, "db_value": "", "evidence_found": f"Error: {str(r)[:50]}", "auto_verdict": "NEEDS_REVIEW"}
                    for f in FIELDS
                ],
            })
        else:
            good_results.append(r)

    results = good_results
    elapsed = time.time() - progress["t_start"]
    print(f"\n    Completed in {elapsed/60:.1f} minutes", flush=True)

    # Step 4: Write CSV
    csv_path = os.path.join(OUTPUT_DIR, "golden_set_results.csv")
    write_csv(results, csv_path)
    print(f"\n  Step 4: Saved {csv_path}", flush=True)

    # Step 5: Write HTML
    html_path = os.path.join(OUTPUT_DIR, "review.html")
    write_html(results, html_path)
    print(f"  Step 5: Saved {html_path}", flush=True)

    # Step 6: Summary
    print_summary(results)

    print(f"\n  Open eval/golden_set_v3/review.html in a browser to review.", flush=True)
    print(f"{'─' * 64}\n", flush=True)


if __name__ == "__main__":
    asyncio.run(async_main())
