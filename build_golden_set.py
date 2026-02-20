"""
Athena — Golden Set Verification Tool.

Selects 300 companies proportionally across sources, collects website evidence,
and generates CSV + HTML review page.

Usage:
    python build_golden_set.py
"""

import sys
import os
import csv
import time
import json
import re
from collections import Counter

import warnings
import requests
from bs4 import BeautifulSoup

warnings.filterwarnings("ignore", message="Unverified HTTPS request")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.database import get_connection

TIMEOUT = 5
DELAY = 2.0

# ── Countries & cities for location extraction ──

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

# ── Source groups ──

SOURCE_GROUPS = [
    {
        "label": "Venture Kick",
        "count": 50,
        "sources": [("Venture Kick", 50)],
    },
    {
        "label": "University Spinoffs",
        "count": 40,
        "sources": [
            ("University of Oxford", 7),
            ("EPFL", 7),
            ("ETH AI Center", 6),
            ("Imperial College", 7),
            ("University of Zurich", 7),
            ("DTU Science Park", 6),
        ],
    },
    {
        "label": "Y Combinator",
        "count": 40,
        "sources": [("Y Combinator", 40)],
    },
    {
        "label": "Techstars",
        "count": 40,
        "sources": [("Techstars", 40)],
    },
    {
        "label": "Antler",
        "count": 30,
        "sources": [("Antler", 30)],
    },
    {
        "label": "Entrepreneur First",
        "count": 20,
        "sources": [("Entrepreneur First", 20)],
    },
    {
        "label": "Swedish Accelerators",
        "count": 20,
        "sources": [
            ("Sting", 4),
            ("Chalmers Ventures", 3),
            ("Uminova", 3),
            ("LEAD", 3),
            ("GU Ventures", 2),
            ("KTH Innovation", 2),
            ("Hetch", 1),
            ("Minc", 1),
            ("LU Innovation", 1),
        ],
    },
    {
        "label": "500 Global / Seedcamp / EWOR",
        "count": 20,
        "sources": [
            ("Seedcamp", 8),
            ("500 Global", 7),
            ("EWOR", 5),
        ],
    },
    {
        "label": "EU-Startups (T3)",
        "count": 40,
        "sources": [("EU-Startups", 40)],
        "tier_filter": 3,
    },
]

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
})


# ── Website evidence collector ──

def extract_locations(text):
    """Find European country and city mentions in page text."""
    lower = text.lower()
    found_countries = set()
    found_cities = set()

    for country in EUROPEAN_COUNTRIES:
        # Word-boundary match to avoid partial matches
        if re.search(r'\b' + re.escape(country) + r'\b', lower):
            found_countries.add(country.title())

    for city in EUROPEAN_CITIES:
        if re.search(r'\b' + re.escape(city) + r'\b', lower):
            found_cities.add(city.title())

    # Also look for structured location patterns
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


def collect_evidence(url):
    """
    Visit a URL and extract all available evidence.
    Returns dict with: http_status, title, meta_description, og_description,
                       found_countries, found_cities, error
    """
    empty = {
        "http_status": None,
        "title": "",
        "meta_description": "",
        "og_description": "",
        "found_countries": [],
        "found_cities": [],
        "error": "",
    }

    if not url or not url.strip():
        empty["error"] = "No website URL"
        return empty

    if not url.startswith("http"):
        url = "https://" + url
    elif url.startswith("http://"):
        url = "https://" + url[7:]

    try:
        resp = SESSION.get(url, timeout=TIMEOUT, verify=False, allow_redirects=True)
        result = dict(empty)
        result["http_status"] = resp.status_code

        if resp.status_code >= 400:
            result["error"] = f"HTTP {resp.status_code}"
            return result

        # Parse HTML
        ct = resp.headers.get("Content-Type", "")
        if "text/html" not in ct and "application/xhtml" not in ct:
            result["error"] = f"Not HTML ({ct[:40]})"
            return result

        soup = BeautifulSoup(resp.text, "html.parser")

        # Title
        title_tag = soup.find("title")
        if title_tag and title_tag.string:
            result["title"] = title_tag.string.strip()[:300]
        elif title_tag:
            result["title"] = title_tag.get_text(strip=True)[:300]

        # Meta description
        meta_desc = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
        if meta_desc and meta_desc.get("content"):
            result["meta_description"] = meta_desc["content"].strip()[:500]

        # OG description (fallback)
        og_desc = soup.find("meta", attrs={"property": re.compile(r"^og:description$", re.I)})
        if og_desc and og_desc.get("content"):
            result["og_description"] = og_desc["content"].strip()[:500]

        # If meta_description empty, try og:description
        if not result["meta_description"] and result["og_description"]:
            result["meta_description"] = result["og_description"]

        # Extract visible text for location search
        # Remove script and style elements
        for tag in soup(["script", "style", "noscript", "svg", "path"]):
            tag.decompose()

        page_text = soup.get_text(separator=" ", strip=True)
        # Limit to first 10K chars of text to keep it fast
        page_text = page_text[:10000]

        countries, cities = extract_locations(page_text)
        result["found_countries"] = countries
        result["found_cities"] = cities

        return result

    except requests.exceptions.Timeout:
        result = dict(empty)
        result["error"] = "Timeout (5s)"
        return result
    except requests.exceptions.ConnectionError as e:
        result = dict(empty)
        err_str = str(e)[:80]
        if "NameResolutionError" in err_str or "Name or service not known" in err_str:
            result["error"] = "DNS failure"
        elif "Connection refused" in err_str:
            result["error"] = "Connection refused"
        else:
            result["error"] = f"Connection error"
        return result
    except requests.exceptions.TooManyRedirects:
        result = dict(empty)
        result["error"] = "Too many redirects"
        return result
    except Exception as e:
        result = dict(empty)
        result["error"] = str(e)[:60]
        return result


# ── Selection ──

def select_companies(conn):
    """Select 300 companies proportionally across source groups."""
    selected = []
    seen_ids = set()

    for group in SOURCE_GROUPS:
        tier_filter = group.get("tier_filter")
        for source_name, count in group["sources"]:
            if source_name == "EU-Startups" and tier_filter == 3:
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


# ── Profile URL builder ──

def build_profile_url(source_name, source_url, company_name):
    """Build a clickable profile URL for the source."""
    if source_url and source_url.startswith("http"):
        return source_url
    fallbacks = {
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
    return fallbacks.get(source_name, "")


# ── CSV writer ──

def write_csv(results, path):
    """Write results to CSV."""
    fieldnames = [
        "company_name", "source",
        "our_website", "website_http_status", "website_title", "website_meta_description",
        "our_geography", "our_city", "our_sector", "our_description", "our_stage",
        "website_locations_found",
        "source_profile_url",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow(r)


# ── HTML builder ──

def esc(text):
    """Escape HTML entities."""
    if not text:
        return ""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;"))


def write_html(results, path):
    """Write HTML review page."""
    cards = []
    for i, r in enumerate(results):
        is_ok = r["website_http_status"] and 200 <= r["website_http_status"] < 300
        status_code = r["website_http_status"]
        error = r.get("_error", "")

        if is_ok:
            status_html = f'<span style="color:#4ade80;font-weight:600">{status_code}</span>'
        elif status_code:
            status_html = f'<span style="color:#f87171;font-weight:600">{status_code}</span>'
        else:
            status_html = f'<span style="color:#9ca3af">Website unreachable</span>'
            if error:
                status_html += f' <span style="color:#6b7280;font-size:11px">({esc(error)})</span>'

        # Build evidence for each field
        site_unreachable = not is_ok and not status_code

        # Website evidence
        web_evidence = status_html
        if r["website_title"]:
            web_evidence += f'<br><span style="color:#8b8b9e;font-size:11px">Title: {esc(r["website_title"])}</span>'

        # Description evidence
        if site_unreachable:
            desc_evidence = '<span style="color:#6b7280">Website unreachable</span>'
        elif r["website_meta_description"]:
            desc_evidence = esc(r["website_meta_description"])
        else:
            desc_evidence = '<span style="color:#6b7280">No meta description found on page</span>'

        # Geography evidence
        locations = r.get("website_locations_found", "")
        if site_unreachable:
            geo_evidence = '<span style="color:#6b7280">Website unreachable</span>'
        elif locations:
            geo_evidence = f'<span style="color:#a0a0b0">Found on page: {esc(locations)}</span>'
        else:
            geo_evidence = '<span style="color:#6b7280">No location found on page</span>'

        # City evidence — same as geo but more specific
        city_evidence = geo_evidence

        # Sector evidence — from title + meta
        if site_unreachable:
            sector_evidence = '<span style="color:#6b7280">Website unreachable</span>'
        elif r["website_title"] or r["website_meta_description"]:
            parts = []
            if r["website_title"]:
                parts.append(f'Title: {esc(r["website_title"][:100])}')
            if r["website_meta_description"]:
                parts.append(f'Meta: {esc(r["website_meta_description"][:150])}')
            sector_evidence = f'<span style="color:#8b8b9e;font-size:11px">{"<br>".join(parts)}</span>'
        else:
            sector_evidence = '<span style="color:#6b7280">No title/description to infer sector</span>'

        # Stage evidence — not extractable
        if site_unreachable:
            stage_evidence = '<span style="color:#6b7280">Website unreachable</span>'
        else:
            stage_evidence = '<span style="color:#6b7280">Not extractable from website</span>'

        fields = [
            ("website", r["our_website"], web_evidence),
            ("geography", r["our_geography"], geo_evidence),
            ("city", r["our_city"], city_evidence),
            ("sector", r["our_sector"], sector_evidence),
            ("description", r["our_description"], desc_evidence),
            ("stage", r["our_stage"], stage_evidence),
        ]

        field_rows = []
        for fname, our_val, evidence in fields:
            field_rows.append(f"""
                <tr class="field-row">
                    <td class="field-name">{esc(fname)}</td>
                    <td class="our-data">{esc(our_val or '—')}</td>
                    <td class="evidence">{evidence}</td>
                    <td class="verdict">
                        <label><input type="radio" name="c{i}_{fname}" value="correct"> OK</label>
                        <label><input type="radio" name="c{i}_{fname}" value="incorrect"> Wrong</label>
                        <label><input type="radio" name="c{i}_{fname}" value="unsure"> ?</label>
                    </td>
                </tr>
            """)

        source_link = f'<a href="{esc(r["source_profile_url"])}" target="_blank">source</a>' if r["source_profile_url"] else "—"
        website_link = f'<a href="{esc(r["our_website"])}" target="_blank">visit</a>' if r["our_website"] else "—"

        cards.append(f"""
        <div class="card" data-idx="{i}" data-name="{esc(r['company_name'])}">
            <div class="card-header">
                <div class="card-title">
                    <span class="card-num">#{i+1}</span>
                    <strong>{esc(r['company_name'])}</strong>
                    <span class="source-badge">{esc(r['source'])}</span>
                </div>
                <div class="card-links">
                    {website_link} &middot; {source_link}
                </div>
            </div>
            <table class="field-table">
                <thead>
                    <tr>
                        <th>Field</th>
                        <th>Our Data</th>
                        <th>Evidence from Website</th>
                        <th>Verdict</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(field_rows)}
                </tbody>
            </table>
        </div>
        """)

    data_json = json.dumps([{"name": r["company_name"], "source": r["source"]} for r in results])

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Athena Golden Set — Manual Review</title>
<style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        background: #0f1117; color: #e2e2e9; padding: 20px 40px;
        line-height: 1.5;
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
    .card-links {{ font-size: 12px; }}
    .card-links a {{ color: #60a5fa; text-decoration: none; }}
    .card-links a:hover {{ text-decoration: underline; }}
    .field-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    .field-table th {{
        text-align: left; padding: 8px 14px; font-size: 11px;
        text-transform: uppercase; letter-spacing: 0.05em;
        color: #8b8b9e; border-bottom: 1px solid #2a2a3a;
        font-weight: 500;
    }}
    .field-table td {{ padding: 8px 14px; border-bottom: 1px solid #2a2a3a20; vertical-align: top; }}
    .field-name {{ font-weight: 500; color: #a0a0b0; width: 90px; white-space: nowrap; }}
    .our-data {{ max-width: 300px; word-break: break-word; }}
    .evidence {{ max-width: 380px; word-break: break-word; color: #c0c0d0; font-size: 12px; }}
    .verdict {{ white-space: nowrap; }}
    .verdict label {{
        display: inline-flex; align-items: center; gap: 3px;
        margin-right: 10px; font-size: 12px; cursor: pointer; color: #8b8b9e;
    }}
    .verdict input[type="radio"] {{ cursor: pointer; accent-color: #3b82f6; }}
    .card.reviewed {{ border-color: #22c55e40; }}
    .card.has-issues {{ border-color: #f8717140; }}
    .hidden {{ display: none !important; }}
</style>
</head>
<body>

<h1>Athena Golden Set — Manual Review</h1>
<p class="subtitle">{len(results)} companies &middot; Review each field and mark as correct, incorrect, or unsure</p>

<div class="toolbar">
    <button onclick="exportResults()">Export Results (CSV)</button>
    <div class="filter-btns">
        <button class="active" onclick="filterCards('all', this)">All ({len(results)})</button>
        <button onclick="filterCards('unreviewed', this)">Unreviewed</button>
        <button onclick="filterCards('issues', this)">Has Issues</button>
    </div>
    <div class="stats" id="stats">0 / {len(results)} reviewed</div>
</div>

<div id="cards">
{''.join(cards)}
</div>

<script>
const TOTAL = {len(results)};
const FIELDS = ['website', 'geography', 'city', 'sector', 'description', 'stage'];
const DATA = {data_json};

function updateStats() {{
    let reviewed = 0, issues = 0;
    for (let i = 0; i < TOTAL; i++) {{
        let allDone = true, hasIssue = false;
        for (const f of FIELDS) {{
            const radios = document.querySelectorAll(`input[name="c${{i}}_${{f}}"]`);
            const checked = Array.from(radios).find(r => r.checked);
            if (!checked) allDone = false;
            if (checked && checked.value === 'incorrect') hasIssue = true;
        }}
        const card = document.querySelector(`.card[data-idx="${{i}}"]`);
        card.classList.toggle('reviewed', allDone);
        card.classList.toggle('has-issues', hasIssue);
        if (allDone) reviewed++;
        if (hasIssue) issues++;
    }}
    document.getElementById('stats').textContent = `${{reviewed}} / ${{TOTAL}} reviewed (${{issues}} with issues)`;
}}

document.addEventListener('change', (e) => {{
    if (e.target.type === 'radio') updateStats();
}});

function filterCards(mode, btn) {{
    document.querySelectorAll('.filter-btns button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    document.querySelectorAll('.card').forEach(card => {{
        if (mode === 'all') {{ card.classList.remove('hidden'); return; }}
        if (mode === 'unreviewed') {{ card.classList.toggle('hidden', card.classList.contains('reviewed')); return; }}
        if (mode === 'issues') {{ card.classList.toggle('hidden', !card.classList.contains('has-issues')); return; }}
    }});
}}

function exportResults() {{
    let csv = 'company_name,source,field,verdict\\n';
    for (let i = 0; i < TOTAL; i++) {{
        for (const f of FIELDS) {{
            const radios = document.querySelectorAll(`input[name="c${{i}}_${{f}}"]`);
            const checked = Array.from(radios).find(r => r.checked);
            const verdict = checked ? checked.value : 'not_reviewed';
            const name = DATA[i].name.replace(/"/g, '""');
            const src = DATA[i].source.replace(/"/g, '""');
            csv += `"${{name}}","${{src}}","${{f}}","${{verdict}}"\\n`;
        }}
    }}
    const blob = new Blob([csv], {{ type: 'text/csv' }});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'golden_set_review_results.csv';
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

def main():
    conn = get_connection()

    print(flush=True)
    print("=" * 64, flush=True)
    print("  ATHENA — Golden Set Verification Tool", flush=True)
    print("=" * 64, flush=True)

    # Step 1: Select companies
    print("\n  Step 1: Selecting 300 companies across sources...", flush=True)
    selected = select_companies(conn)
    print(f"    Selected {len(selected)} companies", flush=True)

    group_counts = Counter(s["group_label"] for s in selected)
    for label, cnt in group_counts.most_common():
        print(f"      {label:35s} {cnt:>3}", flush=True)

    # Step 2: Collect evidence from websites
    print(f"\n  Step 2: Collecting evidence from {len(selected)} websites...", flush=True)
    print(f"    ({DELAY}s delay, {TIMEOUT}s timeout — ~{len(selected) * DELAY / 60:.0f} min)", flush=True)

    results = []
    evidence_stats = {"ok": 0, "error": 0, "no_url": 0,
                      "has_title": 0, "has_meta": 0, "has_location": 0}

    for i, item in enumerate(selected):
        c = item["company"]
        website = c["website"]

        # Delay between requests
        if i > 0 and website and website.strip():
            time.sleep(DELAY)

        ev = collect_evidence(website)

        # Stats
        if ev["error"] == "No website URL":
            evidence_stats["no_url"] += 1
        elif ev["http_status"] and 200 <= ev["http_status"] < 300:
            evidence_stats["ok"] += 1
        else:
            evidence_stats["error"] += 1
        if ev["title"]:
            evidence_stats["has_title"] += 1
        if ev["meta_description"]:
            evidence_stats["has_meta"] += 1
        if ev["found_countries"] or ev["found_cities"]:
            evidence_stats["has_location"] += 1

        # Combine location findings
        loc_parts = []
        if ev["found_countries"]:
            loc_parts.extend(ev["found_countries"])
        if ev["found_cities"]:
            loc_parts.extend(ev["found_cities"])
        locations_str = ", ".join(loc_parts) if loc_parts else ""

        profile_url = build_profile_url(
            item["source_name"], item["source_url"], c["name"])

        results.append({
            "company_name": c["name"],
            "source": item["source_name"],
            "our_website": website or "",
            "website_http_status": ev["http_status"],
            "website_title": ev["title"],
            "website_meta_description": ev["meta_description"],
            "our_geography": c["geography"],
            "our_city": c["city"],
            "our_sector": c["sector"],
            "our_description": c["description"],
            "our_stage": c["stage"],
            "website_locations_found": locations_str,
            "source_profile_url": profile_url,
            "_error": ev["error"],  # internal, not in CSV
        })

        if (i + 1) % 20 == 0:
            eta = (len(selected) - i - 1) * DELAY / 60
            name = c["name"][:28]
            st = ev["http_status"] or ev["error"][:15]
            loc = f" loc={','.join(ev['found_cities'][:2])}" if ev["found_cities"] else ""
            ttl = f" title=yes" if ev["title"] else ""
            print(f"    {i+1:>3}/{len(selected)} "
                  f"(ETA {eta:.1f}m) "
                  f"{name:28s} [{st}]{ttl}{loc}", flush=True)

    conn.close()

    # Step 3: Write CSV
    os.makedirs("eval", exist_ok=True)
    csv_path = "eval/golden_set_verification.csv"
    # Strip internal fields for CSV
    csv_results = [{k: v for k, v in r.items() if not k.startswith("_")} for r in results]
    write_csv(csv_results, csv_path)
    print(f"\n  Step 3: Saved {csv_path}", flush=True)

    # Step 4: Write HTML
    html_path = "eval/golden_set_manual_review.html"
    write_html(results, html_path)
    print(f"  Step 4: Saved {html_path}", flush=True)

    # Summary
    print(f"\n{'─' * 64}", flush=True)
    print(f"  Evidence collection summary:", flush=True)
    print(f"    HTTP 200:          {evidence_stats['ok']:>4}", flush=True)
    print(f"    Errors/timeouts:   {evidence_stats['error']:>4}", flush=True)
    print(f"    No website URL:    {evidence_stats['no_url']:>4}", flush=True)
    print(f"    Has title:         {evidence_stats['has_title']:>4} / {len(results)}", flush=True)
    print(f"    Has meta desc:     {evidence_stats['has_meta']:>4} / {len(results)}", flush=True)
    print(f"    Has location:      {evidence_stats['has_location']:>4} / {len(results)}", flush=True)

    # Evidence coverage per field in HTML
    total = len(results)
    filled = {
        "website": sum(1 for r in results if r["website_http_status"]),
        "description": sum(1 for r in results if r["website_meta_description"]),
        "geography": sum(1 for r in results if r["website_locations_found"]),
    }
    print(f"\n  Evidence fill rates:", flush=True)
    print(f"    Website status:    {filled['website']:>4} / {total} ({filled['website']*100//total}%)", flush=True)
    print(f"    Description:       {filled['description']:>4} / {total} ({filled['description']*100//total}%)", flush=True)
    print(f"    Location:          {filled['geography']:>4} / {total} ({filled['geography']*100//total}%)", flush=True)
    print(f"    Sector:            inferred from title+meta (no direct extraction)", flush=True)
    print(f"    Stage:             not extractable from website", flush=True)

    print(f"\n  Open eval/golden_set_manual_review.html in a browser to review.", flush=True)
    print(f"{'─' * 64}", flush=True)
    print(flush=True)


if __name__ == "__main__":
    main()
