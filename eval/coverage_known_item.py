"""
Athena — Known-Item Coverage Test.

For each of the top 10 sources, visits the portfolio page, samples 25
companies spread across the page, and checks our DB for matches.

Outputs:
  - eval/coverage_known_item.csv
  - Summary table to stdout

Usage:
    python eval/coverage_known_item.py
"""

import sys
import os
import csv
import re
import random
import asyncio
import time

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database.database import get_connection

TIMEOUT_MS = 12000


# ── Fuzzy matching ──

LEGAL_SUFFIXES = (" ag", " sa", " gmbh", " ltd", " limited", " inc", " inc.",
                  " co.", " llc", " corp", " corp.", " sàrl", " sarl", " se",
                  " ab", " aps", " as", " oy", " bv", " nv", " ltd.")


def normalize(name):
    n = name.lower().strip()
    for suffix in LEGAL_SUFFIXES:
        if n.endswith(suffix):
            n = n[:-len(suffix)].rstrip()
    return re.sub(r'[^a-z0-9 ]', '', n).strip()


def bigrams(s):
    s = s.lower().replace(" ", "")
    if len(s) < 2:
        return set()
    return {s[i:i+2] for i in range(len(s) - 1)}


def dice_similarity(a, b):
    ba, bb = bigrams(a), bigrams(b)
    if not ba or not bb:
        return 0.0
    return 2 * len(ba & bb) / (len(ba) + len(bb))


def find_in_db(name, db_index):
    """Match a company name against the DB.
    Returns (found: bool, matched_name: str)."""
    norm = normalize(name)
    if not norm or len(norm) < 2:
        return False, ""

    # Exact normalized match
    if norm in db_index:
        return True, db_index[norm]

    # Containment match — only for names >= 6 chars to avoid false positives
    if len(norm) >= 6:
        for db_norm, db_orig in db_index.items():
            if len(db_norm) >= 6:
                if norm == db_norm:
                    return True, db_orig
                if len(norm) >= 8 and len(db_norm) >= 8:
                    if norm in db_norm or db_norm in norm:
                        return True, db_orig

    # Dice coefficient — require higher threshold for short names
    threshold = 0.85 if len(norm) < 8 else 0.80
    best_score = 0
    best_match = ""
    for db_norm, db_orig in db_index.items():
        # Skip if length difference is too large
        if abs(len(norm) - len(db_norm)) > max(len(norm), len(db_norm)) * 0.35:
            continue
        score = dice_similarity(norm, db_norm)
        if score > best_score:
            best_score = score
            best_match = db_orig

    if best_score >= threshold:
        return True, best_match

    return False, ""


# ── Source extractors ──

async def extract_venture_kick(page):
    """venturekick.ch/portfolio — h2 tags: 'CompanyName\\nDescription'"""
    await page.goto("https://www.venturekick.ch/portfolio", timeout=20000,
                    wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)
    # Scroll to load more
    for _ in range(3):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1000)

    names = await page.evaluate("""() => {
        const h2s = document.querySelectorAll('h2');
        const result = [];
        for (const h2 of h2s) {
            const text = h2.textContent.trim();
            const name = text.split('\\n')[0].trim();
            if (name.length > 2 && name.length < 80 && name !== 'Login' && !name.includes('Venture Kick')) {
                result.push(name);
            }
        }
        return result;
    }""")
    return names


async def extract_epfl(page):
    """EPFL VPI startup-list — table with name every ~7 lines"""
    await page.goto("https://vpi-startup-compass.pages.dev/startup-list/",
                    timeout=15000, wait_until="domcontentloaded")
    await page.wait_for_timeout(5000)

    names = await page.evaluate("""() => {
        // Try table rows first
        const rows = document.querySelectorAll('table tbody tr, [class*="row"]');
        if (rows.length > 5) {
            return [...rows].map(r => {
                const cells = r.querySelectorAll('td, [class*="cell"]');
                if (cells.length > 0) return cells[0].textContent.trim();
                return r.textContent.trim().split('\\n')[0].trim();
            }).filter(n => n.length > 1 && n.length < 80);
        }
        // Fallback: parse visible text
        const text = document.body.innerText;
        const lines = text.split('\\n').map(l => l.trim()).filter(l => l.length > 0);
        // Find "Startup name" header, then extract every Nth line
        let start = -1;
        for (let i = 0; i < lines.length; i++) {
            if (lines[i] === 'Startup name' || lines[i].includes('Startup Info')) {
                start = i + 1;
                break;
            }
        }
        if (start < 0) return [];
        // Each startup entry is: name, desc, lab, sector, year, status, "N/A"
        const names = [];
        for (let i = start; i < lines.length; i += 7) {
            const name = lines[i];
            if (name && name.length > 1 && name.length < 80 &&
                !['Active', 'Closed', 'N/A', 'Public'].includes(name) &&
                !/^\\d{4}$/.test(name)) {
                names.push(name);
            }
        }
        return names;
    }""")
    return names


async def extract_eth_ai(page):
    """ETH AI Center — extract names from 'external page' + description pattern"""
    await page.goto("https://ai.ethz.ch/entrepreneurship/affiliated-startups.html",
                    timeout=15000, wait_until="domcontentloaded")
    await page.wait_for_timeout(5000)
    for _ in range(5):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(800)

    names = await page.evaluate("""() => {
        const text = document.body.innerText;
        const lines = text.split('\\n').map(l => l.trim()).filter(l => l.length > 0);
        const names = [];
        for (let i = 0; i < lines.length; i++) {
            if (lines[i] === 'external page' && i + 1 < lines.length) {
                const next = lines[i + 1];
                const match = next.match(/^([A-Z][A-Za-z0-9._\\- ]+?)\\s+(?:is|are|builds?|provides?|developed|enables?|offers?|automates?|uses?|creates?|helps?|has|was|specializes|delivers|combines|leverages|makes?|focuses|empowers|collects|revolutionizes)/);
                if (match) {
                    const name = match[1].trim();
                    if (name.length > 1 && name.length < 50) {
                        names.push(name);
                    }
                }
            }
        }
        return names;
    }""")
    return names


async def extract_oxford(page):
    """Oxford — h3 tags have company names, grouped under year h2s"""
    await page.goto("https://innovation.ox.ac.uk/portfolio/companies-formed/",
                    timeout=15000, wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)

    names = await page.evaluate("""() => {
        const h3s = document.querySelectorAll('h3');
        return [...h3s].map(h => h.textContent.trim())
            .filter(n => n.length > 1 && n.length < 80);
    }""")
    return names


async def extract_yc(page):
    """YC — company cards with Name+Location+Description, European filter"""
    await page.goto("https://www.ycombinator.com/companies?regions=Europe",
                    timeout=15000, wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)
    for _ in range(8):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(800)

    names = await page.evaluate("""() => {
        const cards = document.querySelectorAll('a[href*="/companies/"]');
        const names = new Set();
        for (const card of cards) {
            const href = card.getAttribute('href') || '';
            // Skip filter/nav links
            if (!href.match(/\\/companies\\/[a-z0-9-]+$/i)) continue;
            // Company name is in the first span or strong element
            const nameEl = card.querySelector('span, strong');
            if (nameEl) {
                const name = nameEl.textContent.trim();
                if (name.length > 1 && name.length < 60) names.add(name);
            }
        }
        return [...names];
    }""")
    return names


async def extract_techstars(page):
    """Techstars — company cards, filter to European locations"""
    await page.goto("https://www.techstars.com/portfolio", timeout=15000,
                    wait_until="domcontentloaded")
    await page.wait_for_timeout(4000)
    for _ in range(5):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1000)

    eu_countries = [
        "United Kingdom", "Germany", "France", "Netherlands", "Sweden",
        "Denmark", "Norway", "Finland", "Ireland", "Switzerland", "Austria",
        "Belgium", "Spain", "Portugal", "Italy", "Poland", "Czech",
        "Hungary", "Romania", "Bulgaria", "Croatia", "Estonia", "Latvia",
        "Lithuania", "Luxembourg", "Greece", "Iceland", "Slovenia", "Slovakia",
    ]
    eu_str = "|".join(eu_countries)

    names = await page.evaluate("""(euPattern) => {
        const re = new RegExp(euPattern);
        const cards = document.querySelectorAll('[class*=Company]');
        const names = [];
        for (const c of cards) {
            const text = c.textContent.trim();
            if (!re.test(text)) continue;  // Skip non-European
            const match = text.match(/^([^(]+)/);
            if (match) {
                const name = match[1].trim();
                if (name.length > 1 && name.length < 60) names.push(name);
            }
        }
        return names;
    }""", eu_str)
    return names


async def extract_antler(page):
    """Antler — use location filter in URL for European companies"""
    eu_countries_url = [
        "Denmark", "Finland", "France", "Germany", "Italy", "Netherlands",
        "Norway", "Portugal", "Spain", "Sweden", "United+Kingdom",
    ]
    all_names = []
    for country in eu_countries_url[:5]:  # Sample a few European countries
        url = f"https://www.antler.co/portfolio?location={country}"
        await page.goto(url, timeout=15000, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        for _ in range(5):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(500)

        names = await page.evaluate("""() => {
            const text = document.body.innerText;
            const lines = text.split('\\n').map(l => l.trim()).filter(l => l.length > 0);
            let start = -1;
            for (let i = 0; i < lines.length; i++) {
                if (/^Showing \\d+ of \\d+$/.test(lines[i])) { start = i + 1; break; }
            }
            if (start < 0) return [];
            const result = [];
            // Company blocks: name is a short line NOT matching country/sector/year
            const skipPatterns = /^(\\d{4}|B2B|ConsumerTech|FinTech|HealthTech|FoodTech|Energy|Real Estate|DeepTech|EdTech|Logistics|LegalTech|InsurTech|HRTech|MarketPlace|UK|Germany|France|Netherlands|Sweden|Denmark|Norway|Finland|Ireland|Switzerland|Austria|Belgium|Spain|Portugal|Italy|Poland|Czech|Hungary|Romania|Bulgaria|Croatia|Estonia|Latvia|Lithuania|Luxembourg|Greece|Iceland|Slovenia|Slovakia|Show|Load|ANTLER|FOUNDER|Cohort|Featured)/;
            for (let i = start; i < lines.length; i++) {
                const line = lines[i];
                if (line.length > 2 && line.length < 50 && !skipPatterns.test(line) &&
                    lines[i-1] !== line) {
                    // Check: next line should be longer (description)
                    const next = i + 1 < lines.length ? lines[i + 1] : '';
                    if (next.length > 20) result.push(line);
                }
            }
            return result;
        }""")
        all_names.extend(names)

    return list(dict.fromkeys(all_names))


async def extract_seedcamp(page):
    """Seedcamp — h4 tags; scroll aggressively and try clicking 'load more'"""
    await page.goto("https://seedcamp.com/our-companies/", timeout=15000,
                    wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)

    # Try clicking load more / show all buttons
    for _ in range(10):
        try:
            btn = page.locator('button:has-text("Load"), button:has-text("Show"), button:has-text("More"), a:has-text("Load more")')
            if await btn.count() > 0:
                await btn.first.click()
                await page.wait_for_timeout(2000)
            else:
                break
        except Exception:
            break
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1000)

    # Scroll extensively
    for _ in range(10):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(800)

    names = await page.evaluate("""() => {
        const skip = new Set(['news & views', 'pitch us', 'press', 'faqs', 'esg policy',
            'privacy policy', 'complaints', 'contact', 'about', 'team', 'blog', 'careers',
            'home', 'portfolio', 'our companies', 'cookie policy', 'terms', 'seedcamp',
            'apply', 'events', 'newsletter', 'twitter', 'linkedin', 'instagram']);

        // Try h4 tags first
        const h4s = document.querySelectorAll('h4');
        let result = [...h4s].map(h => h.textContent.trim())
            .filter(n => n.length > 1 && n.length < 80 && !skip.has(n.toLowerCase()));
        if (result.length > 15) return result;

        // Parse "Link to X's website" patterns from aria-labels or link text
        const links = document.querySelectorAll('a');
        const fromLinks = [];
        for (const a of links) {
            const text = a.textContent.trim();
            const aria = a.getAttribute('aria-label') || '';
            // "Link to Volter's website" → extract "Volter"
            let match = (text || aria).match(/Link to ([^']+)'s website/i);
            if (match) {
                fromLinks.push(match[1].trim());
                continue;
            }
            // Also try aria-label directly for company names
            match = aria.match(/^Visit ([^']+)/i);
            if (match) {
                fromLinks.push(match[1].trim());
            }
        }
        if (fromLinks.length > result.length) {
            // Dedupe and merge
            result = [...new Set([...result, ...fromLinks])];
        }

        return result.filter(n => !skip.has(n.toLowerCase()));
    }""")
    return names


async def extract_ef(page):
    """EF — h4 tags (skip nav items)"""
    await page.goto("https://www.joinef.com/portfolio/", timeout=15000,
                    wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)
    for _ in range(5):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(800)

    skip_items = {"featured", "all companies", "load more", "view all",
                  "entrepreneur first", "portfolio", "about", "contact"}

    names = await page.evaluate("""(skipItems) => {
        const skip = new Set(skipItems);
        const h4s = document.querySelectorAll('h4');
        return [...h4s].map(h => h.textContent.trim())
            .filter(n => n.length > 1 && n.length < 80 && !skip.has(n.toLowerCase()));
    }""", list(skip_items))
    return names


async def extract_imperial(page):
    """Imperial — strong/b tags between A-Z h2 sections, deduped"""
    await page.goto("https://www.imperial.ac.uk/admin-services/enterprise/about/data-and-reporting/spinout-portfolio/",
                    timeout=15000, wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)

    names = await page.evaluate("""() => {
        const h2s = document.querySelectorAll('h2');
        const seen = new Set();
        const results = [];
        for (const h2 of h2s) {
            const letter = h2.textContent.trim();
            if (letter.length > 2 || !/[A-Z#]/.test(letter)) continue;
            let sib = h2.nextElementSibling;
            while (sib && sib.tagName !== 'H2') {
                const strongs = sib.querySelectorAll('strong, b');
                strongs.forEach(s => {
                    const t = s.textContent.trim();
                    if (t.length > 1 && t.length < 80 && !seen.has(t)) {
                        seen.add(t);
                        results.push(t);
                    }
                });
                sib = sib.nextElementSibling;
            }
        }
        return results;
    }""")
    return names


def sample_spread(items, n=25):
    """Sample n items spread evenly across the list."""
    items = list(dict.fromkeys(items))
    if len(items) <= n:
        return items
    step = len(items) / n
    indices = set()
    for i in range(n):
        center = int(i * step)
        jitter = random.randint(-max(1, int(step * 0.2)), max(1, int(step * 0.2)))
        idx = max(0, min(len(items) - 1, center + jitter))
        indices.add(idx)
    while len(indices) < n and len(indices) < len(items):
        indices.add(random.randint(0, len(items) - 1))
    return [items[i] for i in sorted(indices)]


SOURCES = [
    ("Venture Kick", extract_venture_kick),
    ("EPFL", extract_epfl),
    ("ETH AI Center", extract_eth_ai),
    ("Oxford", extract_oxford),
    ("Y Combinator", extract_yc),
    ("Techstars", extract_techstars),
    ("Antler", extract_antler),
    ("Seedcamp", extract_seedcamp),
    ("Entrepreneur First", extract_ef),
    ("Imperial", extract_imperial),
]


async def main():
    print(flush=True)
    print("=" * 64, flush=True)
    print("  ATHENA — Known-Item Coverage Test", flush=True)
    print("=" * 64, flush=True)

    # Load all DB company names
    print("\n  Loading database...", flush=True)
    conn = get_connection()
    rows = conn.execute("SELECT name FROM companies").fetchall()
    conn.close()

    db_index = {}
    for r in rows:
        norm = normalize(r["name"])
        if norm:
            db_index[norm] = r["name"]
    print(f"    {len(db_index)} unique normalized names in DB", flush=True)

    # Process each source
    all_results = []
    summary = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
        )

        for source_name, extractor in SOURCES:
            print(f"\n  [{source_name}]", flush=True)
            page = await context.new_page()

            async def block_resources(route):
                if route.request.resource_type in ("image", "font", "media", "websocket"):
                    await route.abort()
                else:
                    await route.continue_()
            await page.route("**/*", block_resources)

            try:
                raw_names = await extractor(page)
                # Clean up
                clean = [n.strip() for n in raw_names
                         if n.strip() and len(n.strip()) >= 2 and len(n.strip()) < 80]
                clean = list(dict.fromkeys(clean))
                print(f"    Extracted {len(clean)} companies from page", flush=True)

                if len(clean) < 3:
                    print(f"    WARNING: Too few companies extracted!", flush=True)
                    await page.close()
                    summary.append({"source": source_name, "sampled": 0, "found": 0, "recall": 0})
                    continue

                sampled = sample_spread(clean, 25)
                print(f"    Sampled {len(sampled)} companies", flush=True)

                found = 0
                for name in sampled:
                    is_found, matched = find_in_db(name, db_index)
                    all_results.append({
                        "source": source_name,
                        "company_name_on_website": name,
                        "found_in_db": "yes" if is_found else "no",
                        "matched_db_name": matched if is_found else "",
                    })
                    if is_found:
                        found += 1
                    status = "Y" if is_found else "N"
                    match_info = f" -> {matched}" if is_found and normalize(matched) != normalize(name) else ""
                    print(f"      {status}  {name}{match_info}", flush=True)

                recall = found / len(sampled) * 100 if sampled else 0
                summary.append({
                    "source": source_name,
                    "sampled": len(sampled),
                    "found": found,
                    "recall": recall,
                })
                print(f"    => {found}/{len(sampled)} found ({recall:.0f}%)", flush=True)

            except Exception as e:
                print(f"    ERROR: {str(e)[:120]}", flush=True)
                summary.append({"source": source_name, "sampled": 0, "found": 0, "recall": 0})

            await page.close()

        await browser.close()

    # Save CSV
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "coverage_known_item.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "source", "company_name_on_website", "found_in_db", "matched_db_name"])
        writer.writeheader()
        for r in all_results:
            writer.writerow(r)
    print(f"\n  Saved: {csv_path}", flush=True)

    # Summary table
    print(f"\n{'=' * 64}", flush=True)
    print(f"  {'Source':<25s} {'Sampled':>8s} {'Found':>7s} {'Recall %':>10s}", flush=True)
    print(f"  {'-'*25} {'-'*8} {'-'*7} {'-'*10}", flush=True)
    total_sampled = 0
    total_found = 0
    for s in summary:
        print(f"  {s['source']:<25s} {s['sampled']:>8d} {s['found']:>7d} {s['recall']:>9.0f}%", flush=True)
        total_sampled += s["sampled"]
        total_found += s["found"]
    print(f"  {'-'*25} {'-'*8} {'-'*7} {'-'*10}", flush=True)
    overall = total_found / total_sampled * 100 if total_sampled else 0
    print(f"  {'TOTAL':<25s} {total_sampled:>8d} {total_found:>7d} {overall:>9.1f}%", flush=True)
    print(f"{'=' * 64}", flush=True)
    print(flush=True)


if __name__ == "__main__":
    asyncio.run(main())
