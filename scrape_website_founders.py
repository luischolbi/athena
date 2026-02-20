"""
Extract founder data from company websites for T1/T2 companies without founders.

Visits company website, finds /about /team /people pages, extracts founder names,
titles, and LinkedIn URLs. Only captures explicitly stated founders/co-founders/CEOs.

Usage: python scrape_website_founders.py
"""

import json
import re
import sys
import os
import time
import urllib.parse

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.database import init_db, get_connection, insert_founder

# Suppress SSL warnings for sketchy certs
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

TIMEOUT = 5
DELAY = 2.0

# Patterns for team/about page links
TEAM_PATH_PATTERNS = re.compile(
    r'/(about|team|people|founders|our-team|about-us|who-we-are|leadership|management|company)',
    re.IGNORECASE,
)

# Title patterns that indicate a founder/CEO (must match exactly these roles)
FOUNDER_TITLE_RE = re.compile(
    r'\b((?:co[- ]?)?founder|CEO|chief executive officer)\b',
    re.IGNORECASE,
)

# Extended title patterns including CTO etc — only used when "founder" is also present
# to capture the full title
EXEC_TITLE_RE = re.compile(
    r'\b((?:co[- ]?)?founder(?:\s*[&/,]\s*(?:CEO|CTO|COO|CPO|CFO|chairman|president|managing director))?'
    r'|CEO(?:\s*[&/,]\s*(?:co[- ]?)?founder)?'
    r'|chief executive officer)\b',
    re.IGNORECASE,
)

LINKEDIN_RE = re.compile(r'https?://(?:www\.)?linkedin\.com/in/[A-Za-z0-9_-]+/?')


def safe_get(url, timeout=TIMEOUT):
    """GET with timeout, SSL fallback, redirect following."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True, verify=True)
        return resp
    except requests.exceptions.SSLError:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True, verify=False)
            return resp
        except Exception:
            return None
    except Exception:
        return None


def find_team_page_url(soup, base_url):
    """Find a link to /about, /team, /people etc. on the homepage."""
    candidates = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or href.startswith("mailto:"):
            continue

        # Resolve relative URLs
        full_url = urllib.parse.urljoin(base_url, href)

        # Must be same domain
        try:
            if urllib.parse.urlparse(full_url).netloc != urllib.parse.urlparse(base_url).netloc:
                continue
        except Exception:
            continue

        path = urllib.parse.urlparse(full_url).path.lower()
        link_text = a.get_text(strip=True).lower()

        # Check URL path
        if TEAM_PATH_PATTERNS.search(path):
            # Prefer /team over /about
            priority = 0 if any(w in path for w in ("/team", "/people", "/founders")) else 1
            candidates.append((priority, full_url))

        # Check link text
        elif any(w in link_text for w in ("team", "about us", "people", "founders", "who we are")):
            priority = 0 if any(w in link_text for w in ("team", "people", "founder")) else 1
            candidates.append((priority, full_url))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def extract_json_ld_founders(soup):
    """Extract founders from JSON-LD structured data if available."""
    founders = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
        except (json.JSONDecodeError, TypeError):
            continue

        # Handle both single objects and arrays
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            # Check for founder/founders field
            for key in ("founder", "founders", "member", "employee"):
                people = item.get(key, [])
                if isinstance(people, dict):
                    people = [people]
                if not isinstance(people, list):
                    continue
                for p in people:
                    if not isinstance(p, dict):
                        continue
                    name = p.get("name", "").strip()
                    title = p.get("jobTitle", "").strip()
                    if name and (key == "founder" or key == "founders" or FOUNDER_TITLE_RE.search(title)):
                        founders.append({
                            "name": name,
                            "title": title or None,
                            "linkedin_url": None,
                        })
    return founders


def is_valid_person_name(text):
    """Check if text looks like a real person name (not a product/feature/heading)."""
    if not text or len(text) > 60 or len(text) < 3:
        return False
    words = text.split()
    if len(words) < 2 or len(words) > 5:
        return False
    # Each word should start with uppercase
    if not all(w[0].isupper() for w in words if len(w) > 1):
        return False
    # Should not contain common non-name words
    bad_words = {
        'the', 'a', 'an', 'and', 'or', 'for', 'with', 'from', 'our', 'your',
        'new', 'how', 'why', 'get', 'all', 'top', 'best', 'more', 'we',
        'powered', 'based', 'driven', 'smart', 'next', 'data', 'cloud',
        'solutions', 'platform', 'technology', 'services', 'products',
        'about', 'team', 'careers', 'blog', 'contact', 'home', 'login',
    }
    if any(w.lower() in bad_words for w in words):
        return False
    # Should not be all caps (acronym/brand)
    if all(w.isupper() for w in words):
        return False
    # At least one word should be >= 3 chars (avoid "Mr. X" type)
    if not any(len(w) >= 3 for w in words):
        return False
    # Should not contain digits
    if any(c.isdigit() for c in text):
        return False
    # Should not match title patterns
    if FOUNDER_TITLE_RE.search(text):
        return False
    return True


def find_name_near_title(title_element):
    """Given an element containing a founder title, find the person's name nearby.

    Searches: sibling elements, parent's children, parent's siblings,
    and combined text patterns like 'Name, Title'.
    """
    candidates = []

    parent = title_element.parent
    if not parent:
        return None, None

    # Strategy 1: Name is in a sibling div/span of the title's parent
    # Common: <div><div>Name</div><div>Title</div></div>
    container = parent.parent if parent.parent else parent
    if container.name in ("span", "em", "i", "small"):
        container = container.parent or container

    # Go up one more level for deeply nested structures
    # <div><div><div><span>Name</span></div><div><span>Title</span></div></div></div>
    if container and len(list(container.children)) <= 2:
        up = container.parent
        if up and up.name not in ("body", "html", "section", "article", "main"):
            container = up

    # Look at all text-bearing elements in this container
    for el in container.find_all(["h1", "h2", "h3", "h4", "h5", "h6",
                                   "strong", "b", "span", "p", "div", "a"]):
        # Skip if this is the title element itself
        if el == parent or el == title_element:
            continue
        # Skip if it contains the title element
        if parent in el.descendants:
            continue
        text = el.get_text(strip=True)
        if is_valid_person_name(text):
            # Prefer headings/strong
            priority = 0 if el.name in ("h1","h2","h3","h4","h5","h6","strong","b") else 1
            candidates.append((priority, text, el))

    # Strategy 2: Name and title combined in one element
    # "John Smith, Co-founder & CEO" or "John Smith - CEO"
    parent_text = parent.get_text(strip=True)
    combined = re.match(
        r'^([A-Z][a-zA-Zéèêëàâäöüïîôç\'-]+(?:\s+(?:de|van|von|di|del|le|la|el|al|bin|den|der|dos))?'
        r'(?:\s+[A-Z][a-zA-Zéèêëàâäöüïîôç\'-]+){1,3})\s*[,\-–—|:]\s*',
        parent_text,
    )
    if combined and is_valid_person_name(combined.group(1)):
        candidates.insert(0, (0, combined.group(1).strip(), parent))

    # Strategy 3: Check previous sibling of the title's parent
    prev = parent.find_previous_sibling()
    if prev:
        prev_text = prev.get_text(strip=True)
        if is_valid_person_name(prev_text):
            candidates.append((1, prev_text, prev))

    if not candidates:
        return None, None

    # Sort by priority and pick first
    candidates.sort(key=lambda x: x[0])
    best_name = candidates[0][1]
    best_el = candidates[0][2]

    # Find LinkedIn URL near the name
    linkedin = None
    # Check container for LinkedIn links
    for a in container.find_all("a", href=True):
        li_match = LINKEDIN_RE.search(a["href"])
        if li_match:
            linkedin = li_match.group(0).rstrip("/")
            break

    return best_name, linkedin


def extract_founders_from_html(soup):
    """Extract founders from HTML by finding title patterns near names."""
    founders = []
    seen_names = set()

    # Find all text nodes containing founder/CEO titles
    all_elements = soup.find_all(string=FOUNDER_TITLE_RE)

    for text_node in all_elements:
        parent = text_node.parent
        if not parent:
            continue

        # Skip navigation, footer, header, scripts
        skip = False
        for ancestor in parent.parents:
            if ancestor.name in ("nav", "footer", "script", "style", "noscript", "head"):
                skip = True
                break
            cls = " ".join(ancestor.get("class", []))
            if any(w in cls.lower() for w in ("nav", "footer", "cookie", "banner", "menu")):
                skip = True
                break
        if skip:
            continue

        title_text = text_node.strip()

        # Skip if title text is too long (it's a paragraph, not a title label)
        if len(title_text) > 100:
            continue

        # Extract the best title match
        title_match = EXEC_TITLE_RE.search(title_text)
        if not title_match:
            title_match = FOUNDER_TITLE_RE.search(title_text)
        title = title_match.group(0).strip() if title_match else None

        name, linkedin = find_name_near_title(text_node)

        if name and name.lower() not in seen_names:
            seen_names.add(name.lower())
            founders.append({
                "name": name,
                "title": title,
                "linkedin_url": linkedin,
            })

    return founders


def process_company(company_id, name, website):
    """Visit website, find team page, extract founders. Returns list of founder dicts."""
    if not website:
        return []

    # Normalize URL
    url = website.strip().rstrip("/")
    if not url.startswith("http"):
        url = "https://" + url

    # Step 1: Fetch homepage
    resp = safe_get(url)
    if not resp or resp.status_code != 200:
        return []

    soup = BeautifulSoup(resp.content, "html.parser")
    base_url = resp.url  # After redirects

    # Step 2: Try to extract from homepage first (some have founder info right there)
    founders = extract_json_ld_founders(soup)
    if not founders:
        founders = extract_founders_from_html(soup)

    if founders:
        return founders

    # Step 3: Find and fetch team/about page
    team_url = find_team_page_url(soup, base_url)
    if not team_url:
        return []

    time.sleep(1)  # Brief pause before second request
    resp2 = safe_get(team_url)
    if not resp2 or resp2.status_code != 200:
        return []

    soup2 = BeautifulSoup(resp2.content, "html.parser")

    # Try JSON-LD first, then HTML parsing
    founders = extract_json_ld_founders(soup2)
    if not founders:
        founders = extract_founders_from_html(soup2)

    return founders


def main():
    init_db()

    print("Website Founder Extraction")
    print("=" * 60)

    conn = get_connection()

    # Get T1 companies without founders, with websites
    t1_companies = conn.execute("""
        SELECT c.id, c.name, c.website FROM companies c
        WHERE c.data_tier = 1
        AND c.website IS NOT NULL AND c.website != ''
        AND c.id NOT IN (SELECT DISTINCT company_id FROM founders)
        AND c.company_status != 'inactive'
        ORDER BY c.athena_score DESC
    """).fetchall()

    # Get T2 companies without founders, with websites
    t2_companies = conn.execute("""
        SELECT c.id, c.name, c.website FROM companies c
        WHERE c.data_tier = 2
        AND c.website IS NOT NULL AND c.website != ''
        AND c.id NOT IN (SELECT DISTINCT company_id FROM founders)
        AND c.company_status != 'inactive'
        ORDER BY c.athena_score DESC
    """).fetchall()

    conn.close()

    print(f"\nTier 1: {len(t1_companies)} companies to check")
    print(f"Tier 2: {len(t2_companies)} companies to check")
    print(f"Total: {len(t1_companies) + len(t2_companies)} websites\n")

    total_checked = 0
    total_founders = 0
    companies_with_founders = 0
    companies_skipped = 0

    for tier, companies in [(1, t1_companies), (2, t2_companies)]:
        tier_checked = 0
        tier_founders = 0
        tier_with = 0

        print(f"\n{'='*60}")
        print(f"TIER {tier}: Checking {len(companies)} companies...")
        print(f"{'='*60}")

        for i, row in enumerate(companies):
            cid = row["id"]
            name = row["name"]
            website = row["website"]

            if i > 0 and i % 100 == 0:
                print(f"\n  [{tier}] Progress: {i}/{len(companies)} checked, "
                      f"{tier_founders} founders from {tier_with} companies\n")

            founders = process_company(cid, name, website)

            if founders:
                companies_with_founders += 1
                tier_with += 1
                for f in founders:
                    insert_founder(
                        company_id=cid,
                        name=f["name"],
                        title=f["title"],
                        linkedin_url=f.get("linkedin_url"),
                        source="Website",
                    )
                    total_founders += 1
                    tier_founders += 1
                print(f"  T{tier}  {name[:35]:35s}  "
                      f"{', '.join(f['name'] + (' — ' + f['title'] if f['title'] else '') for f in founders)}")

            total_checked += 1
            tier_checked += 1
            time.sleep(DELAY)

        print(f"\n  Tier {tier} done: {tier_checked} checked, "
              f"{tier_founders} founders from {tier_with} companies")

    # Final summary
    conn = get_connection()
    total_all_founders = conn.execute("SELECT COUNT(*) FROM founders").fetchone()[0]
    total_companies_with = conn.execute("SELECT COUNT(DISTINCT company_id) FROM founders").fetchone()[0]

    t1_total = conn.execute("SELECT COUNT(*) FROM companies WHERE data_tier = 1").fetchone()[0]
    t1_with = conn.execute("""
        SELECT COUNT(DISTINCT c.id) FROM companies c
        JOIN founders f ON f.company_id = c.id WHERE c.data_tier = 1
    """).fetchone()[0]

    t2_total = conn.execute("SELECT COUNT(*) FROM companies WHERE data_tier = 2").fetchone()[0]
    t2_with = conn.execute("""
        SELECT COUNT(DISTINCT c.id) FROM companies c
        JOIN founders f ON f.company_id = c.id WHERE c.data_tier = 2
    """).fetchone()[0]

    by_source = conn.execute("""
        SELECT source, COUNT(*) as cnt, COUNT(DISTINCT company_id) as companies
        FROM founders GROUP BY source ORDER BY cnt DESC
    """).fetchall()
    conn.close()

    print(f"\n{'='*60}")
    print("FINAL SUMMARY")
    print(f"{'='*60}")
    print(f"\nThis run:")
    print(f"  Companies checked: {total_checked}")
    print(f"  Founders found: {total_founders}")
    print(f"  Companies with founders: {companies_with_founders}")
    print(f"\nOverall founder coverage:")
    print(f"  Total founders in DB: {total_all_founders}")
    print(f"  Tier 1: {t1_with}/{t1_total} companies have founder data ({100*t1_with/max(t1_total,1):.1f}%)")
    print(f"  Tier 2: {t2_with}/{t2_total} companies have founder data ({100*t2_with/max(t2_total,1):.1f}%)")
    print(f"  Tier 1 still missing: {t1_total - t1_with}")
    print(f"  Tier 2 still missing: {t2_total - t2_with}")
    print(f"\nBy source:")
    for row in by_source:
        print(f"  {row['source']:<25s} {row['cnt']:>6d} founders, {row['companies']:>5d} companies")


if __name__ == "__main__":
    main()
