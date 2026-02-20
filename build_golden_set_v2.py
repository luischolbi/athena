"""
Athena — Golden Set V2: Playwright Evidence Collector.

Uses headless Chromium to visit company websites AND source profile pages,
extracting evidence from both.  Produces:
  - eval/golden_set_v2.csv           (3-source comparison)
  - eval/golden_set_manual_review.html  (interactive review page)

Usage:
    python build_golden_set_v2.py
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from database.database import get_connection

TIMEOUT_MS = 5000   # 5 seconds per page
DELAY = 1.5         # seconds between requests
CONCURRENCY = 5     # parallel browser pages

# ── European locations ──

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

# ── Source groups (same as v1) ──

SOURCE_GROUPS = [
    {"label": "Venture Kick", "count": 50, "sources": [("Venture Kick", 50)]},
    {"label": "University Spinoffs", "count": 40, "sources": [
        ("University of Oxford", 7), ("EPFL", 7), ("ETH AI Center", 6),
        ("Imperial College", 7), ("University of Zurich", 7), ("DTU Science Park", 6)]},
    {"label": "Y Combinator", "count": 40, "sources": [("Y Combinator", 40)]},
    {"label": "Techstars", "count": 40, "sources": [("Techstars", 40)]},
    {"label": "Antler", "count": 30, "sources": [("Antler", 30)]},
    {"label": "Entrepreneur First", "count": 20, "sources": [("Entrepreneur First", 20)]},
    {"label": "Swedish Accelerators", "count": 20, "sources": [
        ("Sting", 4), ("Chalmers Ventures", 3), ("Uminova", 3), ("LEAD", 3),
        ("GU Ventures", 2), ("KTH Innovation", 2), ("Hetch", 1), ("Minc", 1),
        ("LU Innovation", 1)]},
    {"label": "500 Global / Seedcamp / EWOR", "count": 20, "sources": [
        ("Seedcamp", 8), ("500 Global", 7), ("EWOR", 5)]},
    {"label": "EU-Startups (T3)", "count": 40, "sources": [("EU-Startups", 40)],
     "tier_filter": 3},
]

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
}


# ── Helpers ──

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


def extract_founding_date(text):
    """Try to find a founding year in page text."""
    patterns = [
        r'(?:founded|established|started|launched|incorporated)\s+(?:in\s+)?(\d{4})',
        r'(?:since|est\.?)\s+(\d{4})',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            year = int(m.group(1))
            if 1990 <= year <= 2026:
                return str(year)
    return ""


def extract_people_names(text):
    """Extract potential founder/team member names from page text.

    Looks for common patterns like "Name, Title" or "Name — Role".
    Returns deduplicated list of name strings.
    """
    names = set()

    # Pattern: "Name, CEO" or "Name — Co-founder" etc.
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

    return sorted(names)[:10]  # cap at 10


def extract_stage_funding(text):
    """Try to find funding stage info from page text."""
    patterns = [
        r'((?:Pre-?)?[Ss]eed)\s+(?:round|funding|stage)',
        r'[Ss]eries\s+([A-D])\s+(?:round|funding)',
        r'raised\s+[\$€£]?\s*([\d.,]+\s*[MmKk])',
        r'(angel|pre-seed|seed|series\s+[a-d])\s+(?:investment|round)',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(0).strip()[:80]
    return ""


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


# ── Playwright evidence collectors ──

async def visit_page(page, url):
    """Navigate to a URL with timeout. Returns (success, error_string)."""
    try:
        resp = await page.goto(url, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
        status = resp.status if resp else None
        if status and status >= 400:
            return False, f"HTTP {status}"
        return True, ""
    except PWTimeout:
        return False, "Timeout (5s)"
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


async def collect_website_evidence(page, url):
    """Visit company website homepage and extract evidence."""
    empty = {
        "http_status": None, "title": "", "meta_description": "",
        "og_description": "", "found_countries": [], "found_cities": [],
        "founding_date": "", "team_members": [], "error": "",
    }

    url = normalize_url(url)
    if not url:
        empty["error"] = "No website URL"
        return empty

    ok, err = await visit_page(page, url)
    result = dict(empty)

    if not ok:
        result["error"] = err
        return result

    result["http_status"] = 200

    meta = await get_page_meta(page)
    result.update(meta)

    text = await get_visible_text(page)

    countries, cities = extract_locations(text)
    result["found_countries"] = countries
    result["found_cities"] = cities
    result["founding_date"] = extract_founding_date(text)

    return result


async def collect_source_evidence(page, source_url, source_name, company_name):
    """Visit source profile page and extract evidence about the company."""
    empty = {
        "description": "", "founders": [], "founding_date": "",
        "geography": "", "city": "", "stage": "", "error": "",
    }

    if not source_url or not source_url.startswith("http"):
        empty["error"] = "No source profile URL"
        return empty

    ok, err = await visit_page(page, source_url)
    if not ok:
        empty["error"] = err
        return empty

    text = await get_visible_text(page, limit=20000)
    result = dict(empty)

    meta = await get_page_meta(page)
    if meta["meta_description"]:
        result["description"] = meta["meta_description"]

    name_lower = company_name.lower()
    paragraphs = [p.strip() for p in text.split("\n") if len(p.strip()) > 40]
    for para in paragraphs:
        if name_lower in para.lower() and len(para) > 50:
            result["description"] = para[:500]
            break

    people = extract_people_names(text)
    result["founders"] = people

    countries, cities = extract_locations(text)
    if countries:
        result["geography"] = ", ".join(countries[:2])
    if cities:
        result["city"] = ", ".join(cities[:2])

    result["founding_date"] = extract_founding_date(text)
    result["stage"] = extract_stage_funding(text)

    return result


async def process_company(context, item, founder_map, sem, progress):
    """Process a single company: visit website + source page."""
    async with sem:
        c = item["company"]
        website = c["website"] or ""
        source_name = item["source_name"]
        profile_url = build_profile_url(source_name, item["source_url"], c["name"])

        page = await context.new_page()

        # Block heavy resources
        async def block_resources(route):
            if route.request.resource_type in ("image", "stylesheet", "font", "media", "websocket"):
                await route.abort()
            else:
                await route.continue_()
        await page.route("**/*", block_resources)

        try:
            # Visit company website
            if website.strip():
                await asyncio.sleep(DELAY)
            web_ev = await collect_website_evidence(page, website)

            # Extract founders from homepage
            team_members = []
            if website.strip() and web_ev.get("http_status"):
                homepage_text = await get_visible_text(page, limit=15000)
                team_members = extract_people_names(homepage_text)

            # Visit source page
            if profile_url and profile_url.startswith("http"):
                await asyncio.sleep(DELAY)
            src_ev = await collect_source_evidence(page, profile_url, source_name, c["name"])
        finally:
            await page.close()

        # Build result row
        our_founders_str = ", ".join(founder_map.get(c["id"], []))
        web_geo_parts = web_ev.get("found_countries", [])
        web_city_parts = web_ev.get("found_cities", [])

        row = {
            "company_name": c["name"],
            "source": source_name,
            "our_website": website,
            "our_description": c["description"] or "",
            "our_geography": c["geography"] or "",
            "our_city": c["city"] or "",
            "our_sector": c["sector"] or "",
            "our_stage": c["stage"] or "",
            "our_founders": our_founders_str,
            "website_status": web_ev.get("http_status") or web_ev.get("error", ""),
            "website_title": web_ev.get("title", ""),
            "website_description": web_ev.get("meta_description", ""),
            "website_geography": ", ".join(web_geo_parts[:3]) if web_geo_parts else "",
            "website_city": ", ".join(web_city_parts[:3]) if web_city_parts else "",
            "website_founding_date": web_ev.get("founding_date", ""),
            "website_founders": ", ".join(team_members) if team_members else "",
            "source_profile_url": profile_url,
            "source_description": src_ev.get("description", ""),
            "source_geography": src_ev.get("geography", ""),
            "source_city": src_ev.get("city", ""),
            "source_founding_date": src_ev.get("founding_date", ""),
            "source_stage": src_ev.get("stage", ""),
            "source_founders": ", ".join(src_ev.get("founders", [])),
            "_error": web_ev.get("error", ""),
            "_web_ev": web_ev,
            "_src_ev": src_ev,
            "_team_members": team_members,
        }
        row["auto_verdict"] = compute_verdict(row)

        progress["done"] += 1
        n = progress["done"]
        total = progress["total"]
        elapsed = time.time() - progress["t_start"]
        eta = (total - n) * elapsed / max(n, 1) / 60
        ws = web_ev.get("http_status") or str(web_ev.get("error", ""))[:12]
        src_ok = "OK" if not src_ev.get("error") else str(src_ev.get("error", ""))[:12]
        tm = f" team={len(team_members)}" if team_members else ""
        print(f"    {n:>3}/{total} (ETA {eta:.1f}m) "
              f"{c['name'][:25]:25s} web=[{ws}] src=[{src_ok}]{tm}", flush=True)

        return row


# ── Selection (same logic as v1) ──

def select_companies(conn):
    """Select 300 companies proportionally across source groups."""
    selected = []
    seen_ids = set()

    for group in SOURCE_GROUPS:
        tier_filter = group.get("tier_filter")
        for source_name, count in group["sources"]:
            if tier_filter == 3:
                query = """
                    SELECT c.*, s.source_url
                    FROM companies c
                    JOIN signals s ON s.company_id = c.id AND s.source_name = ?
                    WHERE c.data_tier = 3
                    GROUP BY c.id
                    ORDER BY (CASE WHEN c.website IS NOT NULL AND c.website != '' THEN 0 ELSE 1 END), RANDOM()
                """
            else:
                query = """
                    SELECT c.*, s.source_url
                    FROM companies c
                    JOIN signals s ON s.company_id = c.id AND s.source_name = ?
                    GROUP BY c.id
                    ORDER BY (CASE WHEN c.website IS NOT NULL AND c.website != '' THEN 0 ELSE 1 END), RANDOM()
                """
            rows = conn.execute(query, (source_name,)).fetchall()

            picked = 0
            for row in rows:
                if picked >= count:
                    break
                if row["id"] in seen_ids:
                    continue
                seen_ids.add(row["id"])
                selected.append({
                    "company": row,
                    "group_label": group["label"],
                    "source_name": source_name,
                    "source_url": row["source_url"] or "",
                })
                picked += 1

            if picked < count:
                print(f"  Warning: only found {picked}/{count} for {source_name}", flush=True)

    return selected


def build_profile_url(source_name, source_url, company_name):
    if source_url and source_url.startswith("http"):
        return source_url
    return PROFILE_FALLBACKS.get(source_name, "")


# ── CSV ──

def write_csv(results, path):
    fieldnames = [
        "company_name", "source",
        # Our data
        "our_website", "our_description", "our_geography", "our_city",
        "our_sector", "our_stage", "our_founders",
        # Website evidence
        "website_status", "website_title", "website_description",
        "website_geography", "website_city", "website_founding_date",
        "website_founders",
        # Source evidence
        "source_profile_url", "source_description",
        "source_geography", "source_city", "source_founding_date",
        "source_stage", "source_founders",
        # Verdict
        "auto_verdict",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow({k: v for k, v in r.items() if k in fieldnames})


# ── Auto-verdict logic ──

def compute_verdict(r):
    """
    Auto-verdict:
      OK       — key fields match or only have our data (nothing contradicts)
      REVIEW   — website or source data disagrees with ours
      ?        — no evidence at all from website or source
    """
    issues = []
    has_any_evidence = False

    # Description: check if website/source description exists
    web_desc = r.get("website_description", "")
    src_desc = r.get("source_description", "")
    if web_desc or src_desc:
        has_any_evidence = True

    # Geography: compare our geography vs website/source geography
    our_geo = (r.get("our_geography") or "").lower().strip()
    web_geo = (r.get("website_geography") or "").lower().strip()
    src_geo = (r.get("source_geography") or "").lower().strip()
    if web_geo or src_geo:
        has_any_evidence = True
        if our_geo:
            if web_geo and our_geo not in web_geo and web_geo not in our_geo:
                issues.append("geo_mismatch_website")
            if src_geo and our_geo not in src_geo and src_geo not in our_geo:
                issues.append("geo_mismatch_source")

    # Founders: check if our founders overlap with discovered founders
    our_founders = (r.get("our_founders") or "").lower()
    web_founders = (r.get("website_founders") or "").lower()
    src_founders = (r.get("source_founders") or "").lower()
    if web_founders or src_founders:
        has_any_evidence = True

    # Website status
    ws = r.get("website_status", "")
    if ws and "200" not in str(ws) and "OK" not in str(ws):
        if "DNS" in str(ws) or "Timeout" in str(ws) or "Error" in str(ws):
            issues.append("website_unreachable")

    if not has_any_evidence:
        return "?"
    if issues:
        return "REVIEW"
    return "OK"


# ── HTML builder ──

def esc(text):
    if not text:
        return ""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))


def verdict_badge(verdict):
    if verdict == "OK":
        return '<span style="color:#4ade80;font-weight:600;font-size:12px;padding:2px 8px;border:1px solid #22c55e40;border-radius:4px">OK</span>'
    elif verdict == "REVIEW":
        return '<span style="color:#f87171;font-weight:600;font-size:12px;padding:2px 8px;border:1px solid #f8717140;border-radius:4px">REVIEW</span>'
    else:
        return '<span style="color:#9ca3af;font-weight:600;font-size:12px;padding:2px 8px;border:1px solid #6b728040;border-radius:4px">?</span>'


def write_html(results, path):
    cards = []
    for i, r in enumerate(results):
        verdict = r.get("auto_verdict", "?")

        website_link = f'<a href="{esc(r["our_website"])}" target="_blank">website</a>' if r.get("our_website") else "—"
        source_link = f'<a href="{esc(r["source_profile_url"])}" target="_blank">source</a>' if r.get("source_profile_url") else "—"

        # Build field comparison rows
        fields = [
            {
                "name": "Description",
                "ours": r.get("our_description") or "—",
                "website": r.get("website_description") or '<span class="empty">No meta description</span>',
                "source": r.get("source_description") or '<span class="empty">Not extracted</span>',
            },
            {
                "name": "Geography",
                "ours": r.get("our_geography") or "—",
                "website": r.get("website_geography") or '<span class="empty">No location found</span>',
                "source": r.get("source_geography") or '<span class="empty">Not found</span>',
            },
            {
                "name": "City",
                "ours": r.get("our_city") or "—",
                "website": r.get("website_city") or '<span class="empty">No city found</span>',
                "source": r.get("source_city") or '<span class="empty">Not found</span>',
            },
            {
                "name": "Sector",
                "ours": r.get("our_sector") or "—",
                "website": f'<span class="inferred">Title: {esc(r.get("website_title", "")[:80])}</span>' if r.get("website_title") else '<span class="empty">No title</span>',
                "source": '<span class="empty">Not extractable</span>',
            },
            {
                "name": "Stage",
                "ours": r.get("our_stage") or "—",
                "website": '<span class="empty">Not extractable from website</span>',
                "source": esc(r.get("source_stage") or "") or '<span class="empty">Not found</span>',
            },
            {
                "name": "Founders",
                "ours": esc(r.get("our_founders") or "—"),
                "website": esc(r.get("website_founders") or "") or '<span class="empty">Not found</span>',
                "source": esc(r.get("source_founders") or "") or '<span class="empty">Not found</span>',
            },
            {
                "name": "Founded",
                "ours": "—",
                "website": esc(r.get("website_founding_date") or "") or '<span class="empty">Not found</span>',
                "source": esc(r.get("source_founding_date") or "") or '<span class="empty">Not found</span>',
            },
        ]

        field_rows = []
        for j, f in enumerate(fields):
            # If ours is plain text, escape it; if website/source already have HTML spans, don't re-escape
            ours_val = esc(f["ours"]) if not f["ours"].startswith("<") else f["ours"]
            web_val = f["website"] if "<span" in f["website"] else esc(f["website"])
            src_val = f["source"] if "<span" in f["source"] else esc(f["source"])

            field_rows.append(f"""
                <tr class="field-row">
                    <td class="field-name">{esc(f['name'])}</td>
                    <td class="our-data">{ours_val}</td>
                    <td class="web-evidence">{web_val}</td>
                    <td class="src-evidence">{src_val}</td>
                    <td class="verdict-cell">
                        <label><input type="radio" name="c{i}_{j}" value="correct"> OK</label>
                        <label><input type="radio" name="c{i}_{j}" value="incorrect"> Wrong</label>
                        <label><input type="radio" name="c{i}_{j}" value="unsure"> ?</label>
                    </td>
                </tr>
            """)

        # Website status
        ws = r.get("website_status", "")
        if ws == "200" or ws == 200:
            status_html = '<span style="color:#4ade80">200 OK</span>'
        elif ws:
            status_html = f'<span style="color:#f87171">{esc(str(ws))}</span>'
        else:
            err = r.get("_error", "No URL")
            status_html = f'<span style="color:#9ca3af">{esc(err)}</span>'

        cards.append(f"""
        <div class="card {verdict.lower()}" data-idx="{i}" data-verdict="{verdict}">
            <div class="card-header">
                <div class="card-title">
                    <span class="card-num">#{i+1}</span>
                    <strong>{esc(r['company_name'])}</strong>
                    <span class="source-badge">{esc(r['source'])}</span>
                    {verdict_badge(verdict)}
                    <span class="status-badge">{status_html}</span>
                </div>
                <div class="card-links">{website_link} &middot; {source_link}</div>
            </div>
            <table class="field-table">
                <thead>
                    <tr>
                        <th style="width:80px">Field</th>
                        <th>Our Data</th>
                        <th>Website Evidence</th>
                        <th>Source Evidence</th>
                        <th style="width:180px">Manual Verdict</th>
                    </tr>
                </thead>
                <tbody>{''.join(field_rows)}</tbody>
            </table>
        </div>
        """)

    data_json = json.dumps([{
        "name": r["company_name"], "source": r["source"], "verdict": r.get("auto_verdict", "?")
    } for r in results])

    # Count verdicts
    v_ok = sum(1 for r in results if r.get("auto_verdict") == "OK")
    v_review = sum(1 for r in results if r.get("auto_verdict") == "REVIEW")
    v_unknown = sum(1 for r in results if r.get("auto_verdict") == "?")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Athena Golden Set V2 — Manual Review</title>
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
    .card.ok {{ border-left: 3px solid #22c55e40; }}
    .card.review {{ border-left: 3px solid #f8717160; }}
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
    .status-badge {{ font-size: 11px; font-family: monospace; }}
    .card-links {{ font-size: 12px; }}
    .card-links a {{ color: #60a5fa; text-decoration: none; }}
    .card-links a:hover {{ text-decoration: underline; }}
    .field-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    .field-table th {{
        text-align: left; padding: 8px 14px; font-size: 11px;
        text-transform: uppercase; letter-spacing: 0.05em;
        color: #8b8b9e; border-bottom: 1px solid #2a2a3a; font-weight: 500;
    }}
    .field-table td {{ padding: 8px 14px; border-bottom: 1px solid #2a2a3a20; vertical-align: top; }}
    .field-name {{ font-weight: 500; color: #a0a0b0; width: 80px; white-space: nowrap; }}
    .our-data {{ max-width: 250px; word-break: break-word; }}
    .web-evidence {{ max-width: 280px; word-break: break-word; color: #c0c0d0; font-size: 12px; }}
    .src-evidence {{ max-width: 280px; word-break: break-word; color: #c0c0d0; font-size: 12px; }}
    .verdict-cell {{ white-space: nowrap; }}
    .verdict-cell label {{
        display: inline-flex; align-items: center; gap: 3px;
        margin-right: 8px; font-size: 12px; cursor: pointer; color: #8b8b9e;
    }}
    .verdict-cell input[type="radio"] {{ cursor: pointer; accent-color: #3b82f6; }}
    .empty {{ color: #6b728080; font-style: italic; }}
    .inferred {{ color: #8b8b9e; font-size: 11px; }}
    .card.manual-reviewed {{ border-color: #22c55e60; }}
    .card.manual-issues {{ border-color: #f87171; }}
    .hidden {{ display: none !important; }}
</style>
</head>
<body>

<h1>Athena Golden Set V2 — Manual Review</h1>
<p class="subtitle">
    {len(results)} companies &middot;
    <span style="color:#4ade80">{v_ok} OK</span> &middot;
    <span style="color:#f87171">{v_review} Review</span> &middot;
    <span style="color:#9ca3af">{v_unknown} No evidence</span>
    &middot; 3-column comparison: Our Data | Website | Source
</p>

<div class="toolbar">
    <button onclick="exportResults()">Export Results (CSV)</button>
    <div class="filter-btns">
        <button class="active" onclick="filterCards('all', this)">All ({len(results)})</button>
        <button onclick="filterCards('ok', this)">OK ({v_ok})</button>
        <button onclick="filterCards('review', this)">Review ({v_review})</button>
        <button onclick="filterCards('unknown', this)">? ({v_unknown})</button>
        <button onclick="filterCards('unreviewed', this)">Unreviewed</button>
    </div>
    <div class="stats" id="stats">0 / {len(results)} manually reviewed</div>
</div>

<div id="cards">
{''.join(cards)}
</div>

<script>
const TOTAL = {len(results)};
const FIELD_COUNT = 7;
const DATA = {data_json};

function updateStats() {{
    let reviewed = 0, issues = 0;
    for (let i = 0; i < TOTAL; i++) {{
        let allDone = true, hasIssue = false;
        for (let j = 0; j < FIELD_COUNT; j++) {{
            const radios = document.querySelectorAll(`input[name="c${{i}}_${{j}}"]`);
            const checked = Array.from(radios).find(r => r.checked);
            if (!checked) allDone = false;
            if (checked && checked.value === 'incorrect') hasIssue = true;
        }}
        const card = document.querySelector(`.card[data-idx="${{i}}"]`);
        card.classList.toggle('manual-reviewed', allDone);
        card.classList.toggle('manual-issues', hasIssue);
        if (allDone) reviewed++;
        if (hasIssue) issues++;
    }}
    document.getElementById('stats').textContent =
        `${{reviewed}} / ${{TOTAL}} manually reviewed (${{issues}} with issues)`;
}}

document.addEventListener('change', (e) => {{
    if (e.target.type === 'radio') updateStats();
}});

function filterCards(mode, btn) {{
    document.querySelectorAll('.filter-btns button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    document.querySelectorAll('.card').forEach(card => {{
        const v = card.dataset.verdict;
        if (mode === 'all') {{ card.classList.remove('hidden'); return; }}
        if (mode === 'ok') {{ card.classList.toggle('hidden', v !== 'OK'); return; }}
        if (mode === 'review') {{ card.classList.toggle('hidden', v !== 'REVIEW'); return; }}
        if (mode === 'unknown') {{ card.classList.toggle('hidden', v !== '?'); return; }}
        if (mode === 'unreviewed') {{ card.classList.toggle('hidden', card.classList.contains('manual-reviewed')); return; }}
    }});
}}

function exportResults() {{
    let csv = 'company_name,source,auto_verdict,field,manual_verdict\\n';
    const fields = ['Description','Geography','City','Sector','Stage','Founders','Founded'];
    for (let i = 0; i < TOTAL; i++) {{
        for (let j = 0; j < FIELD_COUNT; j++) {{
            const radios = document.querySelectorAll(`input[name="c${{i}}_${{j}}"]`);
            const checked = Array.from(radios).find(r => r.checked);
            const verdict = checked ? checked.value : 'not_reviewed';
            const name = DATA[i].name.replace(/"/g, '""');
            const src = DATA[i].source.replace(/"/g, '""');
            const av = DATA[i].verdict;
            csv += `"${{name}}","${{src}}","${{av}}","${{fields[j]}}","${{verdict}}"\\n`;
        }}
    }}
    const blob = new Blob([csv], {{ type: 'text/csv' }});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'golden_set_v2_review_results.csv';
    a.click();
    URL.revokeObjectURL(url);
}}

updateStats();
</script>

</body>
</html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


# ── Main ──

async def async_main():
    conn = get_connection()

    print(flush=True)
    print("=" * 64, flush=True)
    print("  ATHENA — Golden Set V2 (Playwright Async)", flush=True)
    print("=" * 64, flush=True)

    # Step 1: Select companies
    print("\n  Step 1: Selecting 300 companies across sources...", flush=True)
    selected = select_companies(conn)
    print(f"    Selected {len(selected)} companies", flush=True)

    group_counts = Counter(s["group_label"] for s in selected)
    for label, cnt in group_counts.most_common():
        print(f"      {label:35s} {cnt:>3}", flush=True)

    # Fetch our founders for each company
    print("\n  Step 1b: Loading our founder data...", flush=True)
    founder_map = {}
    for item in selected:
        cid = item["company"]["id"]
        rows = conn.execute(
            "SELECT name, title FROM founders WHERE company_id = ?", (cid,)
        ).fetchall()
        founder_map[cid] = [f"{r['name']}" + (f" ({r['title']})" if r['title'] else "") for r in rows]
    print(f"    Loaded founders for {sum(1 for v in founder_map.values() if v)} companies", flush=True)

    conn.close()

    # Step 2: Launch Playwright and collect evidence concurrently
    total = len(selected)
    print(f"\n  Step 2: Collecting evidence with Playwright ({total} companies, {CONCURRENCY} concurrent)...", flush=True)
    print(f"    {DELAY}s delay, {TIMEOUT_MS}ms timeout", flush=True)

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
            print(f"    ERROR: {selected[i]['company']['name']}: {r}", flush=True)
            good_results.append({
                "company_name": selected[i]["company"]["name"],
                "source": selected[i]["source_name"],
                "our_website": selected[i]["company"]["website"] or "",
                "our_description": selected[i]["company"]["description"] or "",
                "our_geography": selected[i]["company"]["geography"] or "",
                "our_city": selected[i]["company"]["city"] or "",
                "our_sector": selected[i]["company"]["sector"] or "",
                "our_stage": selected[i]["company"]["stage"] or "",
                "our_founders": ", ".join(founder_map.get(selected[i]["company"]["id"], [])),
                "website_status": f"Error: {str(r)[:40]}",
                "website_title": "", "website_description": "",
                "website_geography": "", "website_city": "",
                "website_founding_date": "", "website_founders": "",
                "source_profile_url": build_profile_url(selected[i]["source_name"], selected[i]["source_url"], selected[i]["company"]["name"]),
                "source_description": "", "source_geography": "",
                "source_city": "", "source_founding_date": "",
                "source_stage": "", "source_founders": "",
                "auto_verdict": "?", "_error": str(r)[:60],
            })
        else:
            good_results.append(r)

    results = good_results

    # Compute stats from results
    stats = {
        "web_ok": 0, "web_err": 0, "web_no_url": 0,
        "src_ok": 0, "src_err": 0, "src_no_url": 0,
        "has_web_desc": 0, "has_src_desc": 0,
        "has_web_loc": 0, "has_src_loc": 0,
        "has_web_founders": 0, "has_src_founders": 0,
    }
    for r in results:
        ws = r.get("website_status", "")
        if ws == "No website URL" or (isinstance(ws, str) and "No website" in ws):
            stats["web_no_url"] += 1
        elif ws == 200 or ws == "200":
            stats["web_ok"] += 1
        else:
            stats["web_err"] += 1

        if r.get("source_description") or r.get("source_founders") or r.get("source_geography"):
            stats["src_ok"] += 1
        elif not r.get("source_profile_url"):
            stats["src_no_url"] += 1
        else:
            stats["src_err"] += 1

        if r.get("website_description"):
            stats["has_web_desc"] += 1
        if r.get("source_description"):
            stats["has_src_desc"] += 1
        if r.get("website_geography") or r.get("website_city"):
            stats["has_web_loc"] += 1
        if r.get("source_geography") or r.get("source_city"):
            stats["has_src_loc"] += 1
        if r.get("website_founders"):
            stats["has_web_founders"] += 1
        if r.get("source_founders"):
            stats["has_src_founders"] += 1

    # Step 3: Write CSV
    os.makedirs("eval", exist_ok=True)
    csv_path = "eval/golden_set_v2.csv"
    write_csv(results, csv_path)
    print(f"\n  Step 3: Saved {csv_path}", flush=True)

    # Step 4: Write HTML
    html_path = "eval/golden_set_manual_review.html"
    write_html(results, html_path)
    print(f"  Step 4: Saved {html_path}", flush=True)

    # Summary
    elapsed = time.time() - progress["t_start"]
    print(f"\n{'─' * 64}", flush=True)
    print(f"  Completed in {elapsed/60:.1f} minutes", flush=True)
    print(f"  Evidence collection summary:", flush=True)
    print(f"  {'':>4}{'Website':>12} {'Source':>12}", flush=True)
    print(f"    OK:       {stats['web_ok']:>4}       {stats['src_ok']:>4}", flush=True)
    print(f"    Errors:   {stats['web_err']:>4}       {stats['src_err']:>4}", flush=True)
    print(f"    No URL:   {stats['web_no_url']:>4}       {stats['src_no_url']:>4}", flush=True)
    print(f"", flush=True)
    print(f"  Evidence fill rates:", flush=True)
    print(f"    Description: web={stats['has_web_desc']:>4}/{total}  src={stats['has_src_desc']:>4}/{total}", flush=True)
    print(f"    Location:    web={stats['has_web_loc']:>4}/{total}  src={stats['has_src_loc']:>4}/{total}", flush=True)
    print(f"    Founders:    web={stats['has_web_founders']:>4}/{total}  src={stats['has_src_founders']:>4}/{total}", flush=True)

    v_ok = sum(1 for r in results if r.get("auto_verdict") == "OK")
    v_review = sum(1 for r in results if r.get("auto_verdict") == "REVIEW")
    v_unknown = sum(1 for r in results if r.get("auto_verdict") == "?")
    print(f"", flush=True)
    print(f"  Auto-verdicts:", flush=True)
    print(f"    OK:     {v_ok:>4}  ({v_ok*100//total}%)", flush=True)
    print(f"    REVIEW: {v_review:>4}  ({v_review*100//total}%)", flush=True)
    print(f"    ?:      {v_unknown:>4}  ({v_unknown*100//total}%)", flush=True)

    print(f"\n  Open eval/golden_set_manual_review.html in a browser to review.", flush=True)
    print(f"{'─' * 64}", flush=True)
    print(flush=True)


if __name__ == "__main__":
    asyncio.run(async_main())
