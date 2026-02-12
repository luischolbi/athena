"""
RSS feed scraper for Athena.

Parses RSS feeds from European startup news sources (Sifted, Tech.eu,
EU-Startups, TechCrunch) to extract company signals from articles
published in the last 30 days.
"""

import json
import re
import sys
import os
from datetime import datetime, timedelta, timezone
from html import unescape

import feedparser
import requests
from scrapers import fetch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.database import (
    init_db,
    get_connection,
    insert_company,
    insert_signal,
    update_company,
)

FEEDS = [
    {
        "name": "Sifted",
        "url": "https://sifted.eu/feed",
        "source_name": "Sifted",
        "default_european": True,
    },
    {
        "name": "Tech.eu",
        "url": "https://tech.eu/feed",
        "source_name": "Tech.eu",
        "default_european": True,
    },
    {
        "name": "EU-Startups",
        "url": "https://www.eu-startups.com/feed/",
        "source_name": "EU-Startups",
        "default_european": True,
    },
    {
        "name": "TechCrunch",
        "url": "https://techcrunch.com/tag/startups/feed/",
        "source_name": "TechCrunch",
        "default_european": False,
    },
]

CUTOFF_DAYS = 30

# --- Company name extraction patterns ---

# Note: These patterns are intentionally NOT re.IGNORECASE so that
# [A-Z] in the company name capture group only matches uppercase letters.
# This prevents the multi-word group from greedily capturing lowercase
# words like "and", "is", "acquires" as part of the company name.

# Optional prefix that appears before a company name in article titles
_PREFIX = (
    r'^(?:[\w-]+-based\s+)?'                       # "London-based"
    r'(?:\w+\s+)?'                                  # "DefenceTech" / "Irish"
    r'(?:[Ss]tartup\s+|[Cc]ompany\s+)?'             # "startup" / "company"
)

# Company name capture: one or more Capitalized words
_COMPANY = (
    r'([A-Z][A-Za-z0-9\.]+'
    r'(?:[\s\.\-][A-Z][A-Za-z0-9\.]*)*)'
)

# "CompanyName raises/secures/closes/lands/bags/gets €Xm"
FUNDING_RE = re.compile(
    _PREFIX + _COMPANY + r'\s+'
    r'(?:[Rr]aises?|[Ss]ecures?|[Cc]loses?|[Ll]ands?|[Bb]ags?|[Gg]ets?|[Nn]abs?|[Ss]nags?|[Ll]ocks?\s+in|[Pp]ulls?\s+in)',
)

# "CompanyName launches/announces/unveils/reveals/introduces"
LAUNCH_RE = re.compile(
    _PREFIX + _COMPANY + r'\s+'
    r'(?:[Ll]aunches?|[Aa]nnounces?|[Uu]nveils?|[Rr]eveals?|[Ii]ntroduces?|[Rr]olls?\s+out|[Ee]xpands?|[Oo]pens?)',
)

# "CompanyName, the/a ... startup"
DESCRIPTION_RE = re.compile(
    r'^([A-Z][A-Za-z0-9]+'
    r'(?:[\s\.\-][A-Z][A-Za-z0-9]*)*)'
    r',?\s+(?:the|a|an)\s+',
)

# "CompanyName is expanding/is building/has raised"
VERB_RE = re.compile(
    r'^(?:[\w-]+-based\s+)?'
    r'([A-Z][A-Za-z0-9]+'
    r'(?:[\s\.\-][A-Z][A-Za-z0-9]*)*)'
    r'\s+'
    r'(?:is\s+|has\s+|to\s+)',
)

NAME_PATTERNS = [FUNDING_RE, LAUNCH_RE, VERB_RE, DESCRIPTION_RE]

# Words that are NOT company names (false positive filter)
NOT_COMPANY = {
    "the", "a", "an", "this", "these", "here", "how", "why", "what",
    "who", "where", "when", "which", "are", "is", "was", "were", "will",
    "with", "from", "only", "just", "some", "more", "most", "all",
    "exclusive", "breaking", "update", "report", "analysis", "opinion",
    "review", "interview", "watch", "meet", "inside", "podcast",
    "newsletter", "introducing", "european", "europe",
    "startup", "startups", "founder", "founders", "investors", "vc", "vcs",
    "french", "german", "british", "dutch", "spanish", "italian", "swedish",
    "finnish", "danish", "norwegian", "swiss", "polish", "irish", "uk",
    "london", "berlin", "paris", "top", "best", "new", "big", "daily",
    "chip", "mining", "uber", "apple", "google", "meta", "amazon",
    "microsoft", "ai", "digitising", "polarsteps",
}


# --- European geography data ---

EUROPEAN_COUNTRIES = {
    "albania", "andorra", "armenia", "austria", "azerbaijan", "belarus",
    "belgium", "bosnia", "bulgaria", "croatia", "cyprus", "czech republic",
    "czechia", "denmark", "estonia", "finland", "france", "georgia", "germany",
    "greece", "hungary", "iceland", "ireland", "italy", "kazakhstan", "kosovo",
    "latvia", "liechtenstein", "lithuania", "luxembourg", "malta", "moldova",
    "monaco", "montenegro", "netherlands", "north macedonia", "norway",
    "poland", "portugal", "romania", "san marino", "serbia",
    "slovakia", "slovenia", "spain", "sweden", "switzerland", "turkey",
    "uk", "united kingdom", "ukraine",
}

EUROPEAN_CITIES = {
    "zurich": "Switzerland", "zürich": "Switzerland", "geneva": "Switzerland",
    "basel": "Switzerland", "bern": "Switzerland", "lausanne": "Switzerland",
    "london": "UK", "edinburgh": "UK", "manchester": "UK",
    "cambridge": "UK", "oxford": "UK", "bristol": "UK",
    "berlin": "Germany", "munich": "Germany", "hamburg": "Germany",
    "frankfurt": "Germany", "cologne": "Germany", "stuttgart": "Germany",
    "paris": "France", "lyon": "France", "marseille": "France",
    "toulouse": "France", "bordeaux": "France",
    "amsterdam": "Netherlands", "rotterdam": "Netherlands",
    "eindhoven": "Netherlands",
    "madrid": "Spain", "barcelona": "Spain", "valencia": "Spain",
    "rome": "Italy", "milan": "Italy", "turin": "Italy",
    "stockholm": "Sweden", "copenhagen": "Denmark",
    "oslo": "Norway", "helsinki": "Finland",
    "dublin": "Ireland", "lisbon": "Portugal", "porto": "Portugal",
    "warsaw": "Poland", "krakow": "Poland", "prague": "Czech Republic",
    "budapest": "Hungary", "bucharest": "Romania", "vienna": "Austria",
    "brussels": "Belgium", "tallinn": "Estonia", "riga": "Latvia",
    "vilnius": "Lithuania", "zagreb": "Croatia", "ljubljana": "Slovenia",
}

AMBIGUOUS_CITIES = {"nice", "bath", "reading", "hull", "cork", "essen", "split"}

# --- Sector detection ---

SECTOR_RULES = [
    ("AI / ML",      [r"\bAI\b", r"\bML\b", r"machine learning", r"\bLLM\b",
                      r"\bGPT\b", r"neural net", r"deep learning",
                      r"artificial intelligence"]),
    ("Fintech",      [r"fintech", r"banking", r"payments?\b", r"neobank",
                      r"insurance", r"lending", r"insurtech"]),
    ("Climate",      [r"climate", r"carbon", r"\benergy\b", r"\bsolar\b",
                      r"clean\s*tech", r"sustainability", r"greentech"]),
    ("Health / Bio", [r"health", r"medical", r"biotech", r"pharma",
                      r"genomic", r"diagnostic", r"therapeutics",
                      r"medtech", r"healthtech"]),
    ("SaaS",         [r"\bSaaS\b", r"\bB2B\b", r"\bplatform\b",
                      r"developer tool", r"infrastructure", r"\bAPI\b"]),
]


def log(msg):
    print(msg, flush=True)


def detect_sector(text):
    if not text:
        return "Other"
    for sector, patterns in SECTOR_RULES:
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                return sector
    return "Other"


def detect_europe(text):
    """Check text for European country/city references."""
    if not text:
        return None, None
    text_lower = text.lower()

    for city, country in EUROPEAN_CITIES.items():
        if city in AMBIGUOUS_CITIES:
            if re.search(r'\b' + re.escape(city.title()) + r'\b', text):
                return country, city.title()
        else:
            if re.search(r'\b' + re.escape(city) + r'\b', text_lower):
                return country, city.title()

    for country in EUROPEAN_COUNTRIES:
        if re.search(r'\b' + re.escape(country) + r'\b', text_lower):
            normalized = country.title()
            if country in ("uk", "united kingdom"):
                normalized = "UK"
            elif country == "czechia":
                normalized = "Czech Republic"
            return normalized, None

    return None, None


def extract_company_name(title):
    """Try to extract a company name from an article title.

    Returns the extracted name or None.
    """
    if not title:
        return None

    # Clean HTML entities
    title = unescape(title).strip()

    for pattern in NAME_PATTERNS:
        m = pattern.match(title)
        if m:
            name = m.group(1).strip()

            # Strip "X startup" prefix: "Mining startup Hades" → "Hades"
            startup_strip = re.sub(
                r'^(?:\w+\s+)?(?:startup|company|firm|venture)\s+',
                '', name, flags=re.IGNORECASE,
            ).strip()
            if startup_strip and startup_strip[0].isupper():
                name = startup_strip

            # Reject names that are too long (likely sentence fragments)
            if len(name.split()) > 3:
                continue

            # Validate: not a common word, at least 2 chars, starts with capital
            first_word = name.split()[0].lower()
            if (name.lower() not in NOT_COMPANY
                    and first_word not in NOT_COMPANY
                    and len(name) >= 2
                    and name[0].isupper()):
                return name

    return None


def strip_html(text):
    """Remove HTML tags from text."""
    if not text:
        return ""
    clean = re.sub(r'<[^>]+>', ' ', text)
    clean = unescape(clean)
    return re.sub(r'\s+', ' ', clean).strip()


def parse_date(entry):
    """Parse the published date from a feedparser entry."""
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        try:
            return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass
    if hasattr(entry, 'updated_parsed') and entry.updated_parsed:
        try:
            return datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass
    return None


def find_existing(name):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM companies WHERE LOWER(name) = LOWER(?)", (name,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def has_signal(company_id, source_url):
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM signals WHERE company_id = ? AND source_url = ?",
        (company_id, source_url),
    ).fetchone()
    conn.close()
    return row is not None


def process_feed(feed_config):
    """Parse a single RSS feed and extract company signals.

    Returns (articles_parsed, signals_created, new_companies, errors).
    """
    name = feed_config["name"]
    url = feed_config["url"]
    source_name = feed_config["source_name"]
    default_european = feed_config["default_european"]

    log(f"\n  Fetching {name}: {url}")

    try:
        resp = fetch(url)
        feed = feedparser.parse(resp.content)
    except (requests.RequestException, Exception) as e:
        log(f"    ERROR: Failed to fetch/parse feed: {e}")
        return 0, 0, 0, 1

    if feed.bozo and not feed.entries:
        log(f"    ERROR: Feed parsing failed: {feed.bozo_exception}")
        return 0, 0, 0, 1

    cutoff = datetime.now(timezone.utc) - timedelta(days=CUTOFF_DAYS)
    articles_parsed = 0
    signals_created = 0
    new_companies = 0

    for entry in feed.entries:
        # Check date
        pub_date = parse_date(entry)
        if pub_date and pub_date < cutoff:
            continue

        articles_parsed += 1

        title = getattr(entry, 'title', '') or ''
        link = getattr(entry, 'link', '') or ''
        summary = strip_html(getattr(entry, 'summary', '') or '')
        content = ''
        if hasattr(entry, 'content') and entry.content:
            content = strip_html(entry.content[0].get('value', ''))

        # Combine text for analysis
        full_text = f"{title} {summary} {content}"

        # Extract company name from title
        company_name = extract_company_name(title)
        if not company_name:
            continue

        # Detect geography
        geo, city = detect_europe(full_text)
        if not geo and default_european:
            # Sifted, Tech.eu, EU-Startups are European by default
            geo = "Europe"

        # For TechCrunch (non-default European), skip if no European signal
        if not geo and not default_european:
            continue

        # Detect sector
        sector = detect_sector(full_text)

        # Published date string
        pub_str = pub_date.strftime("%Y-%m-%d") if pub_date else None

        # Check for existing company
        existing = find_existing(company_name)

        metadata = json.dumps({
            "article_title": title,
            "published": pub_str,
            "source_feed": name,
        })

        if existing:
            company_id = existing["id"]

            # Skip if signal already exists for this URL
            if has_signal(company_id, link):
                continue

            updates = {}
            if summary and not existing.get("description"):
                updates["description"] = summary[:500]
            if existing.get("sector") in (None, "Other") and sector != "Other":
                updates["sector"] = sector
            if existing.get("geography") in (None, "Unknown") and geo not in (None, "Unknown"):
                updates["geography"] = geo
                if city:
                    updates["city"] = city
            update_company(company_id, **updates)
        else:
            company_id = insert_company(
                name=company_name,
                description=summary[:500] if summary else None,
                sector=sector,
                geography=geo,
                city=city,
                stage="Unknown",
                heat_score=1,
            )
            new_companies += 1

        insert_signal(
            company_id=company_id,
            source_type="rss",
            source_name=source_name,
            source_url=link,
            signal_layer="realtime",
            title=f"{company_name} — {source_name} mention",
            metadata=metadata,
        )
        signals_created += 1

    return articles_parsed, signals_created, new_companies, 0


def main():
    init_db()

    log("RSS Feed Scraper")
    log("=" * 50)

    total_articles = 0
    total_signals = 0
    total_new = 0
    feed_errors = 0

    for feed_config in FEEDS:
        articles, signals, new, errors = process_feed(feed_config)
        total_articles += articles
        total_signals += signals
        total_new += new
        feed_errors += errors

        status = "OK" if errors == 0 else "ERROR"
        log(f"    {feed_config['name']}: {articles} articles parsed, "
            f"{signals} company signals extracted, "
            f"{new} new companies [{status}]")

    log(f"\n{'=' * 50}")
    log(f"RSS Feeds: {total_articles} articles total, "
        f"{total_signals} company signals, "
        f"{total_new} new companies")
    if feed_errors:
        log(f"  {feed_errors} feed(s) had errors")
    log("")


if __name__ == "__main__":
    main()
