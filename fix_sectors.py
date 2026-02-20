"""
Athena — Sector classification audit and fix.

Reclassifies companies with sector "Other" or empty using enhanced keyword
matching on descriptions, signal titles, and program inference.

Usage:
    python fix_sectors.py
"""

import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.database import get_connection


# ── Sector keyword rules ──
# Order matters: checked top-to-bottom, first match wins.
# Specific sectors first, broad ones last.

SECTOR_RULES = [
    ("Cybersecurity", [
        r"cyber\s*security", r"infosec", r"information security",
        r"threat detection", r"threat intelligence", r"endpoint security",
        r"penetration test", r"vulnerabilit", r"zero.?trust",
        r"malware", r"ransomware", r"identity management",
        r"access management", r"\bSIEM\b", r"\bSOC\b", r"encryption",
        r"security platform", r"fraud detection", r"fraud prevention",
        r"anti.?fraud",
    ]),
    ("LegalTech", [
        r"legaltech", r"legal tech", r"legal\s+automation",
        r"contract management", r"contract analysis",
        r"law\s+firm", r"legal\s+document", r"legal\s+service",
        r"legal\s+practice", r"legal\s+workflow",
        r"\bgdpr\b", r"regulatory\s+compliance",
        r"compliance\s+(?:platform|solution|tool|software)",
    ]),
    ("EdTech", [
        r"\bedtech\b", r"education\s+tech", r"e-learning", r"elearning",
        r"online\s+(?:learning|course|education|classroom)",
        r"learning\s+platform", r"learning\s+management",
        r"tutor(?:ing|ial)", r"skill\s+development", r"upskilling",
        r"training\s+platform", r"student\s+(?:engagement|success)",
        r"curriculum", r"classroom\s+(?:tool|management)",
        r"language\s+learning",
    ]),
    ("FoodTech", [
        r"\bfoodtech\b", r"food\s+tech", r"food\s+(?:delivery|waste|safety)",
        r"alternative\s+protein", r"plant.based\s+(?:food|meat|protein)",
        r"cultured\s+meat", r"cell.based\s+meat", r"lab.grown",
        r"meal\s+kit", r"meal\s+prep", r"recipe\s+(?:platform|app)",
        r"agri\s*tech", r"agri-tech", r"precision\s+agriculture",
        r"vertical\s+farm", r"smart\s+farm", r"aquaculture",
        r"crop\s+(?:monitoring|management)", r"agriculture",
        r"food\s+(?:production|processing|supply)",
        r"restaurant\s+tech",
    ]),
    ("PropTech", [
        r"\bproptech\b", r"property\s+tech",
        r"real\s+estate\s+(?:tech|platform|software)",
        r"construction\s+tech", r"contech\b",
        r"building\s+management", r"smart\s+building",
        r"facility\s+management", r"facilities\s+management",
        r"mortgage\s+(?:tech|platform)",
        r"home\s+(?:buying|selling|rental)\s+(?:platform|marketplace)",
        r"housing\s+(?:platform|marketplace)",
    ]),
    ("Gaming", [
        r"\bgaming\b", r"game\s+(?:dev|studio|engine|design)",
        r"video\s+game", r"\besports?\b", r"e-sports?",
        r"mobile\s+game", r"casual\s+game", r"game\s+platform",
        r"interactive\s+entertainment", r"metaverse",
    ]),
    ("E-commerce", [
        r"\be-?commerce\b", r"online\s+(?:shop|store|retail|marketplace)",
        r"retail\s+tech", r"shopping\s+(?:platform|experience)",
        r"direct.to.consumer", r"\bD2C\b", r"\bDTC\b",
        r"dropshipping", r"online\s+marketplace",
        r"social\s+commerce",
    ]),
    ("Logistics", [
        r"\blogistics\b", r"supply\s+chain\s+(?:tech|management|platform)",
        r"freight\s+(?:tech|platform|management)",
        r"warehouse\s+(?:management|automation)",
        r"last.mile\s+deliver", r"fleet\s+management",
        r"shipping\s+(?:platform|management|software)",
        r"fulfillment\s+(?:platform|center|service)",
        r"cargo\s+(?:tracking|management)",
        r"route\s+optimi",
    ]),
    ("AI / ML", [
        r"\bAI\b", r"\bML\b", r"machine\s+learning",
        r"\bLLM\b", r"\bGPT\b", r"neural\s+net",
        r"deep\s+learning", r"artificial\s+intelligence",
        r"computer\s+vision", r"natural\s+language\s+processing",
        r"\bNLP\b", r"generative\s+ai", r"large\s+language\s+model",
        r"transformer\s+model", r"predictive\s+(?:analytics|model)",
        r"recommendation\s+(?:engine|system)",
        r"speech\s+recognition", r"image\s+recognition",
        r"intelligent\s+automation",
    ]),
    ("Health / Bio", [
        r"health\s*(?:care|tech)?", r"\bmedical\b", r"\bbiotech\b",
        r"\bpharma(?:ceutical)?", r"\bgenomic", r"\bdiagnostic",
        r"\btherapeutic", r"\bclinical\b", r"\bpatient\b",
        r"drug\s+discover", r"\bmedtech\b", r"\bsurgical\b",
        r"\bhospital\b", r"mental\s+health", r"\bwellness\b",
        r"telemedicine", r"telehealth", r"\boncology\b",
        r"bioscience", r"life\s+science", r"\bneuroscience\b",
        r"\bfemtech\b", r"fertility", r"wearable.*health",
        r"digital\s+health", r"personalized\s+medicine",
        r"medical\s+device",
    ]),
    ("Fintech", [
        r"\bfintech\b", r"\bbanking\b", r"\bpayments?\b",
        r"\bneobank\b", r"\binsurance\b", r"\blending\b",
        r"financial\s+(?:service|tech|platform|management)",
        r"\binsurtech\b", r"wealth\s+management",
        r"\binvestment\s+(?:platform|management)",
        r"trading\s+(?:platform|tool)", r"\bblockchain\b",
        r"crypto(?:currency)?", r"\bdefi\b", r"\bweb3\b",
        r"payment\s+(?:processing|gateway|infrastructure)",
        r"open\s+banking", r"credit\s+(?:scoring|platform)",
        r"\bregtech\b",
    ]),
    ("Climate", [
        r"\bclimate\b", r"\bcarbon\b", r"\benergy\b",
        r"\bsolar\b", r"clean\s*tech", r"\bsustainabilit",
        r"\brenewable\b", r"green\s*tech", r"circular\s+economy",
        r"\brecycling\b", r"waste\s+(?:management|reduction)",
        r"electric\s+vehicle", r"\bEV\s", r"\bEVs\b",
        r"battery\s+tech", r"\bhydrogen\b", r"decarboni",
        r"emission", r"energy\s+(?:storage|efficiency|transition)",
        r"wind\s+(?:energy|power|turbine)",
        r"green\s+(?:energy|hydrogen)",
    ]),
    ("Deep Tech", [
        r"\bquantum\b", r"\bsemiconductor\b", r"\bhardware\b",
        r"\bmaterials?\s+science", r"space\s+tech", r"\bsatellite\b",
        r"\bdrone\b", r"3d\s+print", r"\bnano\s*tech",
        r"\bphotonics?\b", r"\blaser\b", r"\brobotics?\b",
        r"\brobot\b", r"\bsensor\b", r"\bIoT\b",
        r"internet\s+of\s+things", r"\bautonomous\b",
        r"\boptics?\b", r"embedded\s+system",
        r"advanced\s+materials", r"additive\s+manufactur",
    ]),
    ("SaaS", [
        r"\bSaaS\b", r"\bB2B\b", r"enterprise\s+software",
        r"developer\s+tool", r"cloud\s+(?:platform|service|native)",
        r"\bAPI\b", r"workflow\s+automation",
        r"\bCRM\b", r"\bERP\b", r"project\s+management",
        r"collaboration\s+(?:tool|platform|software)",
        r"HR\s+tech", r"human\s+resource", r"recruitment\s+(?:platform|software)",
        r"data\s+analytics", r"business\s+intelligence",
        r"no.code", r"low.code", r"marketing\s+(?:automation|platform)",
        r"customer\s+(?:success|engagement|support)\s+(?:platform|software)",
        r"productivity\s+(?:tool|software)",
    ]),
]

# Compiled patterns per sector for performance
COMPILED_RULES = [
    (sector, [re.compile(p, re.IGNORECASE) for p in patterns])
    for sector, patterns in SECTOR_RULES
]


# ── Program → Sector (for companies without descriptions) ──
PROGRAM_SECTOR = {
    "ETH AI Center": "AI / ML",
}


def classify_text(text):
    """Classify text into a sector using keyword rules. Returns sector or None."""
    if not text or len(text) < 10:
        return None
    for sector, patterns in COMPILED_RULES:
        for pat in patterns:
            if pat.search(text):
                return sector
    return None


def main():
    conn = get_connection()

    print()
    print("=" * 64)
    print("  ATHENA — Sector Classification Audit & Fix")
    print("=" * 64)

    total = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    print(f"\n  Total companies: {total}")

    # Snapshot before
    before = {}
    for row in conn.execute(
        "SELECT sector, COUNT(*) FROM companies GROUP BY sector ORDER BY COUNT(*) DESC"
    ).fetchall():
        before[row[0] or "NULL"] = row[1]

    print(f"\n  Before:")
    for sector, cnt in sorted(before.items(), key=lambda x: -x[1]):
        print(f"    {sector:25s} {cnt:>6}")

    fixes_by_sector = {}
    fix_methods = {"description": 0, "signal_title": 0, "program": 0}

    # ── Step 1: Reclassify "Other" companies WITH descriptions ──
    print(f"\n  Step 1: Reclassifying from descriptions...")
    rows = conn.execute("""
        SELECT id, name, description FROM companies
        WHERE (sector IS NULL OR sector = 'Other' OR sector = '')
          AND description IS NOT NULL AND description != ''
    """).fetchall()

    step1_count = 0
    for r in rows:
        new_sector = classify_text(r["description"])
        if new_sector:
            conn.execute(
                "UPDATE companies SET sector = ? WHERE id = ?",
                (new_sector, r["id"])
            )
            fixes_by_sector[new_sector] = fixes_by_sector.get(new_sector, 0) + 1
            step1_count += 1
    conn.commit()
    print(f"    Reclassified {step1_count} / {len(rows)} companies with descriptions")

    # ── Step 2: Reclassify from signal titles ──
    print(f"\n  Step 2: Reclassifying from signal titles...")
    rows = conn.execute("""
        SELECT c.id, c.name, GROUP_CONCAT(s.title, ' | ') AS titles
        FROM companies c
        JOIN signals s ON s.company_id = c.id
        WHERE (c.sector IS NULL OR c.sector = 'Other' OR c.sector = '')
        GROUP BY c.id
    """).fetchall()

    step2_count = 0
    for r in rows:
        titles = r["titles"] or ""
        new_sector = classify_text(titles)
        if new_sector:
            conn.execute(
                "UPDATE companies SET sector = ? WHERE id = ?",
                (new_sector, r["id"])
            )
            fixes_by_sector[new_sector] = fixes_by_sector.get(new_sector, 0) + 1
            step2_count += 1
    conn.commit()
    print(f"    Reclassified {step2_count} / {len(rows)} companies from signal titles")

    # ── Step 3: Infer from program ──
    print(f"\n  Step 3: Inferring from programs...")
    step3_count = 0
    for program, sector in PROGRAM_SECTOR.items():
        cur = conn.execute("""
            UPDATE companies SET sector = ?
            WHERE id IN (
                SELECT c.id FROM companies c
                JOIN programs p ON p.company_id = c.id
                WHERE p.program_name = ?
                  AND (c.sector IS NULL OR c.sector = 'Other' OR c.sector = '')
            )
        """, (sector, program))
        if cur.rowcount:
            print(f"    {program:35s} → {sector:20s} ({cur.rowcount} companies)")
            fixes_by_sector[sector] = fixes_by_sector.get(sector, 0) + cur.rowcount
            step3_count += cur.rowcount
    conn.commit()
    print(f"    Reclassified {step3_count} companies from programs")

    # ── Step 4: Reclassify from company name as last resort ──
    print(f"\n  Step 4: Reclassifying from company names...")
    rows = conn.execute("""
        SELECT id, name FROM companies
        WHERE (sector IS NULL OR sector = 'Other' OR sector = '')
    """).fetchall()

    step4_count = 0
    for r in rows:
        new_sector = classify_text(r["name"])
        if new_sector:
            conn.execute(
                "UPDATE companies SET sector = ? WHERE id = ?",
                (new_sector, r["id"])
            )
            fixes_by_sector[new_sector] = fixes_by_sector.get(new_sector, 0) + 1
            step4_count += 1
    conn.commit()
    print(f"    Reclassified {step4_count} / {len(rows)} companies from names")

    # ── Summary ──
    total_fixed = step1_count + step2_count + step3_count + step4_count
    fix_methods = {
        "description": step1_count,
        "signal_title": step2_count,
        "program": step3_count,
        "company_name": step4_count,
    }

    after = {}
    for row in conn.execute(
        "SELECT sector, COUNT(*) FROM companies GROUP BY sector ORDER BY COUNT(*) DESC"
    ).fetchall():
        after[row[0] or "NULL"] = row[1]

    remaining = after.get("Other", 0)
    conn.close()

    print(f"\n" + "-" * 64)
    print(f"  Total reclassified: {total_fixed}")
    for method, cnt in fix_methods.items():
        print(f"    from {method:20s}: {cnt}")
    print(f"\n  Reclassified into:")
    for sector, cnt in sorted(fixes_by_sector.items(), key=lambda x: -x[1]):
        print(f"    {sector:25s} +{cnt}")
    print(f"\n  Remaining 'Other': {remaining}")
    print("-" * 64)

    # Before/after comparison
    all_sectors = sorted(
        set(list(before.keys()) + list(after.keys())),
        key=lambda s: after.get(s, 0), reverse=True
    )

    print(f"\n  {'Sector':25s} {'Before':>8s} {'After':>8s} {'Delta':>8s}")
    print(f"  {'─' * 25} {'─' * 8} {'─' * 8} {'─' * 8}")
    for sector in all_sectors:
        b = before.get(sector, 0)
        a = after.get(sector, 0)
        delta = a - b
        if delta != 0:
            sign = "+" if delta > 0 else ""
            print(f"  {sector:25s} {b:>8} {a:>8} {sign}{delta:>7}")
        else:
            print(f"  {sector:25s} {b:>8} {a:>8}")

    print()


if __name__ == "__main__":
    main()
