"""
Athena — Geography data audit and fix.

Standardizes geography values, infers missing geography from programs and
cities, and prints before/after distribution.

Usage:
    python fix_geography.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.database import get_connection


# ── Standardization Map ──
# Maps non-standard geography values to their canonical form.

GEOGRAPHY_RENAME = {
    "UK": "United Kingdom",
    "Czech Republic": "Czechia",
    "United States of America": "United States",
}

# ── Program → Country ──
# For companies with Unknown/Europe geography, infer from their program.

PROGRAM_COUNTRY = {
    # Switzerland
    "Venture Kick": "Switzerland",
    "ETH AI Center": "Switzerland",
    "EPFL": "Switzerland",
    "University of Zurich": "Switzerland",

    # United Kingdom
    "Cambridge Enterprise": "United Kingdom",
    "Imperial Enterprise Lab": "United Kingdom",
    "University of Oxford": "United Kingdom",

    # Sweden
    "Chalmers Ventures": "Sweden",
    "Sting": "Sweden",
    "LEAD": "Sweden",
    "GU Ventures": "Sweden",
    "LU Innovation": "Sweden",
    "Hetch": "Sweden",
    "Minc": "Sweden",
    "Brewhouse": "Sweden",
    "Innovatum": "Sweden",
    "Arctic Ventures": "Sweden",
    "Inkubera": "Sweden",
    "Uminova": "Sweden",
    "Krinova": "Sweden",
    "KTH Innovation": "Sweden",

    # Denmark
    "DTU Science Park": "Denmark",
}

# ── City → Country ──
# For companies with a city but Unknown/Europe geography.

CITY_COUNTRY = {
    # United Kingdom
    "London": "United Kingdom",
    "Edinburgh": "United Kingdom",
    "Manchester": "United Kingdom",
    "Bristol": "United Kingdom",
    "Cambridge": "United Kingdom",
    "Oxford": "United Kingdom",
    "Birmingham": "United Kingdom",
    "Leeds": "United Kingdom",
    "Glasgow": "United Kingdom",
    "Liverpool": "United Kingdom",
    "Belfast": "United Kingdom",
    "Cardiff": "United Kingdom",
    "Brighton": "United Kingdom",
    "Nottingham": "United Kingdom",
    "Sheffield": "United Kingdom",
    "Newcastle": "United Kingdom",
    "Southampton": "United Kingdom",
    "Darlington": "United Kingdom",
    "Reading": "United Kingdom",
    "Bath": "United Kingdom",
    "Exeter": "United Kingdom",
    "York": "United Kingdom",
    "Coventry": "United Kingdom",
    "Leicester": "United Kingdom",
    "Aberdeen": "United Kingdom",
    "Dundee": "United Kingdom",
    "Swansea": "United Kingdom",
    "Norwich": "United Kingdom",
    "Plymouth": "United Kingdom",

    # France
    "Paris": "France",
    "Lyon": "France",
    "Marseille": "France",
    "Toulouse": "France",
    "Nice": "France",
    "Nantes": "France",
    "Strasbourg": "France",
    "Montpellier": "France",
    "Bordeaux": "France",
    "Lille": "France",
    "Rennes": "France",
    "Grenoble": "France",

    # Germany
    "Berlin": "Germany",
    "Munich": "Germany",
    "Hamburg": "Germany",
    "Frankfurt": "Germany",
    "Cologne": "Germany",
    "Stuttgart": "Germany",
    "Düsseldorf": "Germany",
    "Leipzig": "Germany",
    "Dortmund": "Germany",
    "Essen": "Germany",
    "Dresden": "Germany",
    "Hannover": "Germany",
    "Nuremberg": "Germany",
    "Heidelberg": "Germany",
    "Karlsruhe": "Germany",
    "Aachen": "Germany",
    "Bonn": "Germany",

    # Switzerland
    "Zurich": "Switzerland",
    "Zürich": "Switzerland",
    "Geneva": "Switzerland",
    "Genève": "Switzerland",
    "Basel": "Switzerland",
    "Bern": "Switzerland",
    "Lausanne": "Switzerland",
    "Lugano": "Switzerland",
    "Lucerne": "Switzerland",
    "St. Gallen": "Switzerland",
    "Winterthur": "Switzerland",

    # Sweden
    "Stockholm": "Sweden",
    "Gothenburg": "Sweden",
    "Malmö": "Sweden",
    "Uppsala": "Sweden",
    "Linköping": "Sweden",
    "Lund": "Sweden",
    "Umeå": "Sweden",
    "Västerås": "Sweden",
    "Örebro": "Sweden",
    "Helsingborg": "Sweden",
    "Norrköping": "Sweden",
    "Jönköping": "Sweden",

    # Denmark
    "Copenhagen": "Denmark",
    "Aarhus": "Denmark",
    "Odense": "Denmark",
    "Aalborg": "Denmark",

    # Netherlands
    "Amsterdam": "Netherlands",
    "Rotterdam": "Netherlands",
    "The Hague": "Netherlands",
    "Utrecht": "Netherlands",
    "Eindhoven": "Netherlands",
    "Delft": "Netherlands",
    "Groningen": "Netherlands",

    # Spain
    "Madrid": "Spain",
    "Barcelona": "Spain",
    "Valencia": "Spain",
    "Seville": "Spain",
    "Bilbao": "Spain",
    "Malaga": "Spain",
    "Zaragoza": "Spain",

    # Italy
    "Milan": "Italy",
    "Rome": "Italy",
    "Turin": "Italy",
    "Florence": "Italy",
    "Bologna": "Italy",
    "Naples": "Italy",

    # Ireland
    "Dublin": "Ireland",
    "Cork": "Ireland",
    "Galway": "Ireland",
    "Limerick": "Ireland",

    # Austria
    "Vienna": "Austria",
    "Graz": "Austria",
    "Linz": "Austria",
    "Salzburg": "Austria",
    "Innsbruck": "Austria",

    # Finland
    "Helsinki": "Finland",
    "Espoo": "Finland",
    "Tampere": "Finland",
    "Turku": "Finland",
    "Oulu": "Finland",

    # Norway
    "Oslo": "Norway",
    "Bergen": "Norway",
    "Trondheim": "Norway",
    "Stavanger": "Norway",

    # Portugal
    "Lisbon": "Portugal",
    "Porto": "Portugal",
    "Braga": "Portugal",

    # Belgium
    "Brussels": "Belgium",
    "Antwerp": "Belgium",
    "Ghent": "Belgium",
    "Leuven": "Belgium",

    # Poland
    "Warsaw": "Poland",
    "Krakow": "Poland",
    "Kraków": "Poland",
    "Wroclaw": "Poland",
    "Wrocław": "Poland",
    "Gdansk": "Poland",
    "Gdańsk": "Poland",
    "Poznan": "Poland",
    "Poznań": "Poland",

    # Estonia
    "Tallinn": "Estonia",
    "Tartu": "Estonia",

    # Czechia
    "Prague": "Czechia",
    "Brno": "Czechia",

    # Hungary
    "Budapest": "Hungary",

    # Romania
    "Bucharest": "Romania",
    "Cluj-Napoca": "Romania",
    "Cluj": "Romania",

    # Lithuania
    "Vilnius": "Lithuania",
    "Kaunas": "Lithuania",

    # Latvia
    "Riga": "Latvia",

    # Greece
    "Athens": "Greece",
    "Thessaloniki": "Greece",

    # Bulgaria
    "Sofia": "Bulgaria",

    # Croatia
    "Zagreb": "Croatia",

    # Slovenia
    "Ljubljana": "Slovenia",

    # Slovakia
    "Bratislava": "Slovakia",

    # Luxembourg
    "Luxembourg": "Luxembourg",
    "Luxembourg City": "Luxembourg",

    # Cyprus
    "Nicosia": "Cyprus",
    "Limassol": "Cyprus",

    # Malta
    "Valletta": "Malta",

    # Iceland
    "Reykjavik": "Iceland",
    "Reykjavík": "Iceland",
}


def get_distribution(conn):
    """Return geography distribution as {geo: count}."""
    rows = conn.execute(
        "SELECT geography, COUNT(*) FROM companies GROUP BY geography ORDER BY COUNT(*) DESC"
    ).fetchall()
    return {r[0] or "NULL": r[1] for r in rows}


def main():
    conn = get_connection()

    print()
    print("=" * 64)
    print("  ATHENA — Geography Audit & Fix")
    print("=" * 64)

    total = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    print(f"\n  Total companies: {total}")

    # Snapshot before
    before = get_distribution(conn)

    fixes = 0

    # ── Step 1: Standardize geography names ──
    print("\n  Step 1: Standardizing geography names...")
    for old, new in GEOGRAPHY_RENAME.items():
        cur = conn.execute(
            "UPDATE companies SET geography = ? WHERE geography = ?", (new, old)
        )
        if cur.rowcount:
            print(f"    {old:30s} → {new:20s} ({cur.rowcount} companies)")
            fixes += cur.rowcount
    conn.commit()

    # ── Step 2: Infer from programs ──
    print("\n  Step 2: Inferring geography from programs...")
    for program, country in PROGRAM_COUNTRY.items():
        cur = conn.execute("""
            UPDATE companies SET geography = ?
            WHERE id IN (
                SELECT c.id FROM companies c
                JOIN programs p ON p.company_id = c.id
                WHERE p.program_name = ?
                  AND (c.geography IS NULL OR c.geography IN ('Unknown', 'Europe'))
            )
        """, (country, program))
        if cur.rowcount:
            print(f"    {program:35s} → {country:20s} ({cur.rowcount} companies)")
            fixes += cur.rowcount
    conn.commit()

    # ── Step 3: Infer from city ──
    print("\n  Step 3: Inferring geography from city...")
    city_fixes = 0
    for city, country in CITY_COUNTRY.items():
        cur = conn.execute("""
            UPDATE companies SET geography = ?
            WHERE city = ?
              AND (geography IS NULL OR geography IN ('Unknown', 'Europe'))
        """, (country, city))
        if cur.rowcount:
            print(f"    {city:30s} → {country:20s} ({cur.rowcount} companies)")
            city_fixes += cur.rowcount
    fixes += city_fixes
    conn.commit()

    # ── Step 4: Check remaining Unknown/Europe ──
    remaining_unknown = conn.execute(
        "SELECT COUNT(*) FROM companies WHERE geography = 'Unknown'"
    ).fetchone()[0]
    remaining_europe = conn.execute(
        "SELECT COUNT(*) FROM companies WHERE geography = 'Europe'"
    ).fetchone()[0]

    if remaining_unknown or remaining_europe:
        print(f"\n  Remaining unfixable:")
        if remaining_unknown:
            print(f"    Unknown: {remaining_unknown} (mostly HackerNews/ProductHunt — no location data)")
        if remaining_europe:
            print(f"    Europe:  {remaining_europe} (pan-European programs without city)")

    # ── Summary ──
    after = get_distribution(conn)
    conn.close()

    print("\n" + "-" * 64)
    print(f"  Total fixes: {fixes}")
    print("-" * 64)

    # Before/after comparison
    all_geos = sorted(set(list(before.keys()) + list(after.keys())),
                      key=lambda g: after.get(g, 0), reverse=True)

    print(f"\n  {'Geography':30s} {'Before':>8s} {'After':>8s} {'Delta':>8s}")
    print(f"  {'─' * 30} {'─' * 8} {'─' * 8} {'─' * 8}")
    for geo in all_geos:
        b = before.get(geo, 0)
        a = after.get(geo, 0)
        delta = a - b
        if delta != 0:
            sign = "+" if delta > 0 else ""
            print(f"  {geo:30s} {b:>8} {a:>8} {sign}{delta:>7}")
        else:
            print(f"  {geo:30s} {b:>8} {a:>8}")

    print()


if __name__ == "__main__":
    main()
