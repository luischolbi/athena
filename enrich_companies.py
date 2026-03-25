"""
Athena LLM Enrichment Pipeline — Single-pass company evaluation.

For each curated company, this script:
  1. Fetches the company website (if available)
  2. Sends website content + existing DB data to Claude Sonnet
  3. Gets back: thesis evaluation, improved description, founder extraction
  4. Stores results in the database

Usage:
    python enrich_companies.py                  # All curated (Tier 1 + Tier 2)
    python enrich_companies.py --limit 50       # First 50 (for testing)
    python enrich_companies.py --company-id 123 # Single company (for debugging)
    python enrich_companies.py --dry-run        # Show what would be processed, no API calls
    python enrich_companies.py --skip-fetch     # Use existing descriptions only, no website visits

Cost estimate (Sonnet): ~$0.02-0.03 per company → ~$100-150 for full dataset
"""

import json
import os
import re
import sys
import time
import argparse
import traceback
from datetime import date, datetime

import anthropic
import requests
from urllib.parse import urlparse

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from database.database import get_connection, update_company, insert_founder


# ── Config ────────────────────────────────────────────────────

MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 2048
MAX_WEBSITE_CHARS = 30_000  # Truncate website text to control input tokens
REQUEST_TIMEOUT = 60  # seconds per API call
WEBSITE_FETCH_TIMEOUT = 15  # seconds per website fetch
RATE_LIMIT_DELAY = 0.5  # seconds between API calls to avoid rate limits

# ── Evaluation Prompt ─────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert venture capital analyst evaluating early-stage European startups for a pre-seed/seed fund focused on AI-first and deep-tech companies.

You return ONLY valid JSON — no markdown, no commentary, no explanation.
You are conservative and precise. When uncertain, you say so. You never hallucinate or guess."""

EVALUATION_PROMPT = """Evaluate this startup for a European pre-seed/seed VC fund focused on AI-first and deep-tech.

COMPANY DATA FROM OUR DATABASE:
- Name: {name}
- Current description: {description}
- Sector: {sector}
- Geography: {geography}
- City: {city}
- Stage: {stage}
- Programs: {programs}

{website_section}

INVESTMENT THESIS (Ellipsis Venture):
The fund invests in AI-first and deep-tech companies at pre-seed/seed stage in Europe.

WHAT COUNTS AS AI-FIRST / DEEP TECH:
- Training proprietary models, novel architectures, applied ML research
- Domain-specific fine-tuned models with proprietary data advantages
- Robotics, biotech/genomics, synthetic biology, photonics, semiconductors
- Companies where AI/ML is the core product, not a feature
- University spinouts with research-backed technology (PhD work, patents, publications)

WHAT DOES NOT COUNT (WRAPPERS):
- "AI-powered" tools that call OpenAI/Anthropic APIs without proprietary models or data
- SaaS products that added an AI chatbot or feature on top
- Companies that "leverage AI" or are "built with AI" but have no proprietary technology
- No-code/low-code tools with AI branding
- If the product could exist without AI and just uses it for marketing, it's a wrapper
- Pure quantum computing companies (quantum is not part of our investment thesis)

PLATFORM RISK CHECK:
- Could OpenAI, Google, or Anthropic ship this as a feature? If yes, high platform risk.
- Does the company have defensible advantages (proprietary data, domain expertise, workflow embedding) that big tech cannot easily replicate?

SCORING CALIBRATION (CRITICAL):
- Most pre-seed companies should score 2-3. This is normal and expected.
- A score of 4 means genuinely strong evidence visible from public data. Rare.
- A score of 5 means exceptional — best-in-class for the stage. Almost never from a website alone.
- Do NOT inflate scores. A 3 with high confidence is more useful than a generous 4.
- When data is limited, score conservatively and set confidence to "low".

EVALUATE AND RETURN A JSON OBJECT WITH EXACTLY THESE FIELDS:

{{
    "is_ai_core": true/false — Is this company building core AI/ML technology or deep tech? Be strict. Using AI tools ≠ building AI. null if impossible to determine.
    
    "technical_depth": 1-5 — How deep is the technical innovation?
        5 = Novel research, proprietary models, first-author publications, patents, PhD spinout
        4 = Significant technical moat, custom ML pipelines, domain-specific models, proprietary data flywheel
        3 = Applied ML with meaningful customization, fine-tuned models, some differentiation
        2 = Light ML usage, mostly off-the-shelf with minor adaptation, thin layer over foundation models
        1 = No real tech depth, API wrapper, or pure SaaS with AI buzzwords
        null if impossible to assess
    
    "thesis_fit": 1-5 — Overall fit with Ellipsis's investment thesis.
        5 = Exceptional fit: core AI/deep-tech, European, early-stage, proprietary technology, clear moat, low platform risk
        4 = Strong fit: most criteria met, defensible technology, minor gaps
        3 = Partial fit: some AI/deep-tech elements but unclear depth or differentiation
        2 = Weak fit: mostly doesn't align, or high platform risk, or likely a wrapper
        1 = No fit: not AI/deep-tech, confirmed wrapper, wrong stage, or non-European
        null if impossible to assess
    
    "platform_risk": "low" / "medium" / "high" — Could a foundation model provider (OpenAI, Google, Anthropic) replicate this as a feature? "low" = requires deep domain expertise, proprietary data, or complex workflow integration. "high" = generic AI application that big tech could easily ship.
    
    "proprietary_data": true/false/null — Does the company appear to have a proprietary data advantage that compounds over time? Look for signals like unique datasets, user-generated data flywheels, domain-specific training data, or exclusive data partnerships.
    
    "thesis_reasoning": One sentence explaining your thesis fit assessment. Be specific about what you observed, not generic.
    
    "confidence": "high" / "medium" / "low" — How confident are you in this evaluation given the available data? "high" = website clearly explains what they do and the technology. "medium" = partial information, some inference needed. "low" = very little to go on.
    
    "improved_description": A clean, factual one-sentence description of what the company actually does (not marketing language). Only provide this if the current description is missing, outdated, vague, or just marketing copy. null if the current description is accurate and clear.
    
    "founders": An array of founders/co-founders you can identify with HIGH confidence. Each entry: {{"name": "...", "title": "...", "linkedin_url": "..."}}
        CRITICAL RULES FOR FOUNDERS:
        - Only include people who are clearly founders, co-founders, or CEOs of THIS company
        - Do NOT include investors, advisors, board members, employees, or journalists
        - Do NOT include anyone you're not highly confident about
        - If a LinkedIn URL is not explicitly on the page, set it to null — do NOT guess or construct URLs
        - If you cannot identify any founders with high confidence, return an empty array []
        - An empty array is ALWAYS better than a wrong name
        - A name without a title is acceptable if the person is clearly a founder
    
    "funding_stage_hint": If the website or any available data explicitly mentions a specific funding round (e.g., "raised €2M seed round", "pre-seed backed by X", "grant from Y"), extract it here as a short string like "Seed", "Pre-seed", or "Grant". null if no funding info found. Do NOT infer stage from company age or team size.
}}

Return ONLY the JSON object. No other text."""


# ── Website Fetching ──────────────────────────────────────────

# Simple user agent to avoid bot blocks
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def _clean_html_to_text(html):
    """Extract visible text from HTML, removing scripts, styles, and tags."""
    # Remove script and style blocks
    html = re.sub(r'<script[^>]*>.*?</script>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<nav[^>]*>.*?</nav>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<footer[^>]*>.*?</footer>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', ' ', html)
    # Decode common HTML entities
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&nbsp;', ' ').replace('&quot;', '"').replace('&#39;', "'")
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def fetch_website_text(url, timeout=WEBSITE_FETCH_TIMEOUT):
    """Fetch a website and return cleaned visible text.
    
    Returns (text, error_message). On failure, text is None.
    """
    if not url:
        return None, "No URL"
    
    # Normalize URL
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        
        # Only process HTML
        content_type = resp.headers.get('content-type', '')
        if 'html' not in content_type.lower() and 'text' not in content_type.lower():
            return None, f"Non-HTML content: {content_type}"
        
        text = _clean_html_to_text(resp.text)
        
        if len(text) < 50:
            return None, "Page too short (< 50 chars)"
        
        # Truncate to limit
        if len(text) > MAX_WEBSITE_CHARS:
            text = text[:MAX_WEBSITE_CHARS] + f"\n[... truncated, {len(text) - MAX_WEBSITE_CHARS} chars omitted ...]"
        
        return text, None
        
    except requests.exceptions.Timeout:
        return None, "Timeout"
    except requests.exceptions.ConnectionError:
        return None, "Connection error"
    except requests.exceptions.HTTPError as e:
        return None, f"HTTP {e.response.status_code}"
    except Exception as e:
        return None, str(e)


def fetch_additional_pages(base_url, timeout=WEBSITE_FETCH_TIMEOUT):
    """Try to fetch /about and /team pages for additional context.
    
    Returns combined text from successful fetches.
    """
    additional_text = ""
    parsed = urlparse(base_url if base_url.startswith('http') else f'https://{base_url}')
    base = f"{parsed.scheme}://{parsed.netloc}"
    
    for path in ['/about', '/about-us', '/team', '/our-team']:
        try:
            url = base + path
            resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
            if resp.status_code == 200 and 'html' in resp.headers.get('content-type', '').lower():
                text = _clean_html_to_text(resp.text)
                if len(text) > 100:  # Only include if there's real content
                    additional_text += f"\n\n--- {path} page ---\n{text[:10_000]}"
        except Exception:
            continue
    
    return additional_text


# ── Database Helpers ──────────────────────────────────────────

def get_curated_companies():
    """Get all Tier 1 and Tier 2 companies for enrichment."""
    conn = get_connection()
    rows = conn.execute("""
        SELECT id, name, description, sector, geography, city, website, stage,
               data_tier, athena_score, llm_evaluation
        FROM companies 
        WHERE data_tier IN (1, 2)
        ORDER BY RANDOM()
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_company_programs(company_id):
    """Get program info for a company."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT program_name, cohort, funding_amount FROM programs WHERE company_id = ?",
        (company_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_existing_founders(company_id):
    """Check if company already has founders from other sources."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT name, source FROM founders WHERE company_id = ?",
        (company_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def ensure_llm_columns():
    """Add llm_evaluation and ai_core columns if they don't exist."""
    conn = get_connection()
    for col, coltype in [
        ("llm_evaluation", "TEXT"),
        ("ai_core", "INTEGER"),  # 0/1 boolean
        ("thesis_fit_score", "REAL"),
        ("technical_depth_score", "REAL"),
        ("llm_confidence", "TEXT"),
        ("llm_enriched_at", "TEXT"),
        ("platform_risk", "TEXT"),  # low/medium/high
        ("proprietary_data", "INTEGER"),  # 0/1 boolean
    ]:
        try:
            conn.execute(f"SELECT {col} FROM companies LIMIT 1")
        except Exception:
            conn.execute(f"ALTER TABLE companies ADD COLUMN {col} {coltype}")
    conn.commit()
    conn.close()
    print("  Database columns verified.")


# ── LLM Evaluation ────────────────────────────────────────────

def build_evaluation_prompt(company, programs, website_text=None):
    """Build the evaluation prompt for a single company."""
    # Format programs
    if programs:
        prog_str = ", ".join(
            f"{p['program_name']}" + (f" ({p['cohort']})" if p.get('cohort') else "")
            for p in programs
        )
    else:
        prog_str = "None known"
    
    # Website section
    if website_text:
        website_section = f"WEBSITE CONTENT (from {company.get('website', 'N/A')}):\n{website_text}"
    else:
        website_section = "WEBSITE CONTENT: Not available (could not fetch or no URL on record)."
    
    return EVALUATION_PROMPT.format(
        name=company.get("name", "Unknown"),
        description=company.get("description") or "None available",
        sector=company.get("sector") or "Unknown",
        geography=company.get("geography") or "Unknown",
        city=company.get("city") or "Unknown",
        stage=company.get("stage") or "Unknown",
        programs=prog_str,
        website_section=website_section,
    )


def evaluate_company(client, company, programs, website_text=None):
    """Run LLM evaluation for a single company.
    
    Returns parsed evaluation dict or None on failure.
    """
    prompt = build_evaluation_prompt(company, programs, website_text)
    
    for attempt in range(2):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            
            response_text = response.content[0].text.strip()
            
            # Strip markdown fences if present
            if response_text.startswith("```"):
                response_text = re.sub(r'^```(?:json)?\s*\n?', '', response_text)
                response_text = re.sub(r'\n?```\s*$', '', response_text)
                response_text = response_text.strip()
            
            evaluation = json.loads(response_text)
            
            # Validate required fields
            required = ["is_ai_core", "technical_depth", "thesis_fit", 
                       "thesis_reasoning", "confidence", "founders"]
            for field in required:
                if field not in evaluation:
                    if attempt == 0:
                        print(f"    Missing field '{field}', retrying...")
                        continue
                    evaluation[field] = None
            
            return evaluation
            
        except json.JSONDecodeError:
            if attempt == 0:
                print(f"    Invalid JSON, retrying...")
                continue
            print(f"    Invalid JSON on retry. Response: {response_text[:200]}")
            return None
        except anthropic.RateLimitError:
            wait = 30
            print(f"    Rate limited, waiting {wait}s...")
            time.sleep(wait)
            continue
        except anthropic.APIError as e:
            print(f"    API error (attempt {attempt+1}): {e}")
            if attempt == 0:
                time.sleep(5)
                continue
            return None
        except Exception as e:
            print(f"    Unexpected error (attempt {attempt+1}): {e}")
            if attempt == 0:
                time.sleep(2)
                continue
            return None
    
    return None


def store_evaluation(company_id, evaluation, company):
    """Store LLM evaluation results in the database."""
    update_fields = {
        "llm_evaluation": json.dumps(evaluation),
        "llm_enriched_at": datetime.now().isoformat(),
    }
    
    # Store ai_core as boolean (0/1)
    if evaluation.get("is_ai_core") is not None:
        update_fields["ai_core"] = 1 if evaluation["is_ai_core"] else 0
    
    # Store thesis fit score
    if evaluation.get("thesis_fit") is not None:
        update_fields["thesis_fit_score"] = float(evaluation["thesis_fit"])
    
    # Store technical depth
    if evaluation.get("technical_depth") is not None:
        update_fields["technical_depth_score"] = float(evaluation["technical_depth"])
    
    # Store confidence
    if evaluation.get("confidence"):
        update_fields["llm_confidence"] = evaluation["confidence"]
    
    # Store platform risk
    if evaluation.get("platform_risk"):
        update_fields["platform_risk"] = evaluation["platform_risk"]
    
    # Store proprietary data flag
    if evaluation.get("proprietary_data") is not None:
        update_fields["proprietary_data"] = 1 if evaluation["proprietary_data"] else 0
    
    # Update description if improved version provided and current is missing/weak
    current_desc = company.get("description") or ""
    if evaluation.get("improved_description") and (
        not current_desc or len(current_desc) < 20
    ):
        update_fields["description"] = evaluation["improved_description"]
    
    # Update stage if hint provided and current stage is empty
    if evaluation.get("funding_stage_hint") and not company.get("stage"):
        update_fields["stage"] = evaluation["funding_stage_hint"]
        update_fields["stage_source"] = "llm_enrichment"
        update_fields["stage_detected_date"] = date.today().isoformat()
    
    update_company(company_id, **update_fields)
    
    # Store founders (only if company doesn't already have founders)
    founders = evaluation.get("founders") or []
    if founders:
        existing = get_existing_founders(company_id)
        existing_names = {f["name"].lower() for f in existing}
        
        new_count = 0
        for f in founders:
            name = (f.get("name") or "").strip()
            if not name or len(name) < 2:
                continue
            if name.lower() in existing_names:
                continue
            
            insert_founder(
                company_id=company_id,
                name=name,
                title=(f.get("title") or "").strip() or None,
                linkedin_url=(f.get("linkedin_url") or "").strip() or None,
                source="llm_enrichment",
            )
            new_count += 1
        
        return new_count
    
    return 0


# ── Main Pipeline ─────────────────────────────────────────────

def run_enrichment(limit=None, company_id=None, dry_run=False, skip_fetch=False):
    """Run the LLM enrichment pipeline."""
    import shutil
    from database.database import DB_PATH
    backup_path = DB_PATH + '.backup'
    shutil.copy2(DB_PATH, backup_path)
    print(f"  Backup: {backup_path}")

    print()
    print("=" * 64)
    print("  ATHENA LLM ENRICHMENT PIPELINE")
    print("=" * 64)

    # Ensure DB columns exist
    ensure_llm_columns()
    
    # Get companies to process
    if company_id:
        conn = get_connection()
        row = conn.execute("SELECT * FROM companies WHERE id = ?", (company_id,)).fetchone()
        conn.close()
        if not row:
            print(f"  Company {company_id} not found.")
            return
        companies = [dict(row)]
        print(f"  Single company mode: {companies[0]['name']}")
    else:
        companies = get_curated_companies()
        print(f"  Found {len(companies)} curated companies")
    
    # Filter out already-enriched companies (unless single company mode)
    if not company_id:
        unenriched = [c for c in companies if not c.get("llm_evaluation")]
        already_done = len(companies) - len(unenriched)
        if already_done > 0:
            print(f"  Skipping {already_done} already enriched")
        companies = unenriched
    
    if limit:
        companies = companies[:limit]
        print(f"  Limited to {limit} companies")
    
    print(f"  Processing {len(companies)} companies")
    
    if dry_run:
        print("\n  DRY RUN — no API calls will be made\n")
        for c in companies[:20]:
            print(f"    [{c['id']}] {c['name']} — {c.get('website', 'no website')}")
        if len(companies) > 20:
            print(f"    ... and {len(companies) - 20} more")
        return
    
    # Initialize API client
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("  ERROR: ANTHROPIC_API_KEY not set")
        print("  Run: export ANTHROPIC_API_KEY=sk-ant-...")
        return
    
    client = anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT)
    
    # Stats
    stats = {
        "processed": 0,
        "success": 0,
        "failed": 0,
        "websites_fetched": 0,
        "websites_failed": 0,
        "founders_added": 0,
        "descriptions_improved": 0,
        "ai_core_count": 0,
        "wrapper_count": 0,
    }
    
    start_time = time.time()
    
    print(f"\n  Starting enrichment...\n")
    
    for i, company in enumerate(companies):
        cid = company["id"]
        name = company["name"]
        
        print(f"  [{i+1}/{len(companies)}] {name} (id={cid})")
        
        # Step 1: Fetch website
        website_text = None
        if not skip_fetch and company.get("website"):
            print(f"    Fetching {company['website']}...")
            website_text, err = fetch_website_text(company["website"])
            
            if website_text:
                stats["websites_fetched"] += 1
                # Also try /about and /team pages
                additional = fetch_additional_pages(company["website"])
                if additional:
                    website_text += additional
                    # Re-truncate if needed
                    if len(website_text) > MAX_WEBSITE_CHARS:
                        website_text = website_text[:MAX_WEBSITE_CHARS]
                print(f"    Website: {len(website_text)} chars")
            else:
                stats["websites_failed"] += 1
                print(f"    Website failed: {err}")
        elif skip_fetch:
            print(f"    Skipping website fetch (--skip-fetch)")
        else:
            print(f"    No website URL")
        
        # Step 2: Get programs
        programs = get_company_programs(cid)
        
        # Step 3: LLM evaluation
        print(f"    Evaluating...")
        evaluation = evaluate_company(client, company, programs, website_text)
        
        if evaluation:
            # Step 4: Store results
            new_founders = store_evaluation(cid, evaluation, company)
            
            stats["success"] += 1
            stats["founders_added"] += new_founders
            
            if evaluation.get("improved_description"):
                current_desc = company.get("description") or ""
                if not current_desc or len(current_desc) < 20:
                    stats["descriptions_improved"] += 1
            
            if evaluation.get("is_ai_core"):
                stats["ai_core_count"] += 1
            elif evaluation.get("is_ai_core") is False:
                stats["wrapper_count"] += 1
            
            # Print summary
            thesis = evaluation.get("thesis_fit", "?")
            depth = evaluation.get("technical_depth", "?")
            conf = evaluation.get("confidence", "?")
            ai = "AI-core" if evaluation.get("is_ai_core") else "Not AI-core" if evaluation.get("is_ai_core") is False else "Unknown"
            founders = evaluation.get("founders", [])
            
            print(f"    → Thesis: {thesis}/5 | Depth: {depth}/5 | {ai} | Conf: {conf}")
            plat_risk = evaluation.get("platform_risk", "?")
            prop_data = "Yes" if evaluation.get("proprietary_data") else "No" if evaluation.get("proprietary_data") is False else "?"
            print(f"    → Platform risk: {plat_risk} | Proprietary data: {prop_data}")
            if founders:
                print(f"    → Founders: {', '.join(f['name'] for f in founders)}")
            if evaluation.get("thesis_reasoning"):
                print(f"    → {evaluation['thesis_reasoning']}")
        else:
            stats["failed"] += 1
            print(f"    → FAILED")
        
        stats["processed"] += 1
        
        # Rate limit delay
        time.sleep(RATE_LIMIT_DELAY)
        
        # Progress update every 50 companies
        if (i + 1) % 50 == 0:
            elapsed = time.time() - start_time
            rate = stats["processed"] / elapsed * 60
            remaining = (len(companies) - i - 1) / rate if rate > 0 else 0
            print(f"\n  --- Progress: {i+1}/{len(companies)} | "
                  f"{stats['success']} OK, {stats['failed']} failed | "
                  f"{rate:.0f}/min | ~{remaining:.0f} min remaining ---\n")
    
    # Final stats
    elapsed = time.time() - start_time
    
    print()
    print("=" * 64)
    print("  ENRICHMENT COMPLETE")
    print("=" * 64)
    print(f"  Processed:            {stats['processed']}")
    print(f"  Successful:           {stats['success']}")
    print(f"  Failed:               {stats['failed']}")
    print(f"  Websites fetched:     {stats['websites_fetched']}")
    print(f"  Websites failed:      {stats['websites_failed']}")
    print(f"  Founders added:       {stats['founders_added']}")
    print(f"  Descriptions updated: {stats['descriptions_improved']}")
    print(f"  AI-core companies:    {stats['ai_core_count']}")
    print(f"  Non AI-core:          {stats['wrapper_count']}")
    print(f"  Time:                 {elapsed/60:.1f} minutes")
    print(f"  Rate:                 {stats['processed']/elapsed*60:.0f} companies/min")
    print()


# ── CLI ───────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Athena LLM Enrichment Pipeline")
    parser.add_argument("--limit", type=int, help="Max companies to process")
    parser.add_argument("--company-id", type=int, help="Process single company by ID")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be processed")
    parser.add_argument("--skip-fetch", action="store_true", help="Skip website fetching, use DB data only")
    
    args = parser.parse_args()
    
    run_enrichment(
        limit=args.limit,
        company_id=args.company_id,
        dry_run=args.dry_run,
        skip_fetch=args.skip_fetch,
    )
