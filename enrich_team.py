"""
Athena Team Enrichment Pipeline — Deep founder research via Parallel AI + Claude scoring.

For each high-thesis-fit company with founder data, this script:
  1. Sends a research task to Parallel AI (Core processor) for deep founder background
  2. Stores the raw research in team_research column
  3. Sends research to Claude Sonnet to score on 4 criteria → team_quality_score
  4. Stores scores and reasoning in the database

Modes:
  Default:       Enrich companies that already have founder names
  --no-founders: Discover founders for companies without any, then score

Usage:
    python enrich_team.py                  # All thesis_fit >= 3 with founders
    python enrich_team.py --no-founders    # Discover + score for companies without founders
    python enrich_team.py --limit 20       # First 20 (for testing)
    python enrich_team.py --dry-run        # Show what would be processed, no API calls
"""

import json
import os
import sys
import time
import shutil
import argparse
import traceback
from datetime import datetime

import anthropic
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from database.database import get_connection, update_company, DB_PATH


# ── Config ────────────────────────────────────────────────────

PARALLEL_BASE = "https://api.parallel.ai"
PARALLEL_PROCESSOR = "core"
PARALLEL_POLL_INTERVAL = 10  # seconds between polls
PARALLEL_TIMEOUT = 300  # max seconds to wait per task

CLAUDE_MODEL = "claude-sonnet-4-20250514"
CLAUDE_MAX_TOKENS = 1024
CLAUDE_RATE_DELAY = 0.5  # seconds between Claude calls

# ── New DB columns ────────────────────────────────────────────

TEAM_COLUMNS = {
    "team_research": "TEXT",
    "team_quality_score": "REAL",
    "team_technical_excellence": "REAL",
    "team_builder_track_record": "REAL",
    "team_domain_expertise": "REAL",
    "team_complementarity": "REAL",
    "team_reasoning": "TEXT",
    "team_enriched_at": "TEXT",
}


def ensure_team_columns():
    """Add team enrichment columns if they don't exist."""
    conn = get_connection()
    for col, coltype in TEAM_COLUMNS.items():
        try:
            conn.execute(f"SELECT {col} FROM companies LIMIT 1")
        except Exception:
            conn.execute(f"ALTER TABLE companies ADD COLUMN {col} {coltype}")
    conn.commit()
    conn.close()
    print("  Team columns verified.")


# ── Company selection ─────────────────────────────────────────

def get_candidates(limit=None):
    """Get companies with thesis_fit >= 3 that have founders and haven't been team-enriched."""
    conn = get_connection()
    query = """
        SELECT DISTINCT c.id, c.name, c.website, c.sector, c.description
        FROM companies c
        JOIN founders f ON f.company_id = c.id
        WHERE c.thesis_fit_score >= 3
          AND c.team_enriched_at IS NULL
        ORDER BY c.thesis_fit_score DESC, c.athena_score DESC
    """
    if limit:
        query += f" LIMIT {int(limit)}"
    rows = conn.execute(query).fetchall()
    companies = [dict(r) for r in rows]

    # Attach founders to each company
    for c in companies:
        founders = conn.execute(
            "SELECT name, title FROM founders WHERE company_id = ?",
            (c["id"],),
        ).fetchall()
        c["founders"] = [dict(f) for f in founders]

    conn.close()
    return companies


def get_no_founder_candidates(limit=None):
    """Get companies with thesis_fit >= 3 that have NO founders and haven't been team-enriched."""
    conn = get_connection()
    query = """
        SELECT c.id, c.name, c.website, c.sector, c.description
        FROM companies c
        WHERE c.thesis_fit_score >= 3
          AND c.team_enriched_at IS NULL
          AND c.id NOT IN (SELECT DISTINCT company_id FROM founders)
        ORDER BY c.thesis_fit_score DESC, c.athena_score DESC
    """
    if limit:
        query += f" LIMIT {int(limit)}"
    rows = conn.execute(query).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Parallel AI: Task Group batch API ─────────────────────────

PARALLEL_GROUP_BATCH_SIZE = 1000  # max runs per POST
PARALLEL_GROUP_POLL_INTERVAL = 30  # seconds between group status polls
PARALLEL_GROUP_TIMEOUT = 3600  # max seconds to wait for entire group
PARALLEL_RESULT_TIMEOUT = 30  # seconds per individual result fetch


def build_parallel_prompt(company):
    """Build the research prompt for Parallel AI."""
    name = company["name"]
    website = company.get("website") or "no website"

    founder_lines = []
    for f in company["founders"]:
        title = f.get("title") or ""
        if title:
            founder_lines.append(f"- {f['name']} — {title}")
        else:
            founder_lines.append(f"- {f['name']}")
    founders_str = "\n".join(founder_lines)

    return f"""Research the founding team of {name} ({website}). Founders:
{founders_str}

For each founder, find:
- Educational background (university, degree, field of study, PhD if applicable)
- Previous companies and roles with approximate dates
- Top AI/tech company experience (DeepMind, Meta AI/FAIR, OpenAI, Google Brain, Microsoft Research, Apple ML, Amazon Science, etc.)
- Publications, citations, h-index, conference papers (NeurIPS, ICML, ICLR, CVPR, etc.)
- Patents filed or granted
- Prior startups founded or co-founded, outcomes (exit, acquisition, failed, ongoing)
- C-level or senior roles (CTO, VP Eng, etc.) at startups, especially YC/top accelerator companies
- Products built and shipped at scale
- Years of experience in the domain relevant to what their current company does

Also assess the team as a whole:
- Do the founders have complementary skills (technical + commercial + domain)?
- Are there obvious gaps in the team composition?"""


def build_parallel_prompt_no_founders(company):
    """Build the research prompt for companies without known founders."""
    name = company["name"]
    website = company.get("website") or "no website"
    sector = company.get("sector") or "unknown sector"
    desc = (company.get("description") or "")[:300]

    return f"""Research the founding team of {name} ({website}). We don't have founder names yet.
Sector: {sector}
Description: {desc}

Find:
- Who founded or co-founded this company? Names and roles.
- For each founder found: educational background, previous companies and roles, top AI/tech company experience, publications, patents, prior startups or exits, domain expertise.
- Team composition: do they have complementary skills?

If you cannot identify any founders with high confidence, say so explicitly."""


def create_task_group(api_key):
    """Create a Parallel AI task group. Returns taskgroup_id."""
    resp = requests.post(
        f"{PARALLEL_BASE}/v1beta/tasks/groups",
        headers={"x-api-key": api_key, "Content-Type": "application/json"},
        json={},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["taskgroup_id"]


def submit_group_runs(taskgroup_id, companies, api_key):
    """Submit runs to a task group in batches. Returns {run_id: company} mapping."""
    run_to_company = {}
    for batch_start in range(0, len(companies), PARALLEL_GROUP_BATCH_SIZE):
        batch = companies[batch_start:batch_start + PARALLEL_GROUP_BATCH_SIZE]
        inputs = []
        for c in batch:
            prompt = build_parallel_prompt(c)
            inputs.append({"input": prompt, "processor": PARALLEL_PROCESSOR})

        resp = requests.post(
            f"{PARALLEL_BASE}/v1beta/tasks/groups/{taskgroup_id}/runs",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json={"inputs": inputs},
            timeout=60,
        )
        resp.raise_for_status()
        run_ids = resp.json()["run_ids"]

        for run_id, company in zip(run_ids, batch):
            run_to_company[run_id] = company

        print(f"    Submitted batch: {len(run_ids)} runs "
              f"({batch_start + 1}–{batch_start + len(batch)} of {len(companies)})")
    return run_to_company


def poll_group_status(taskgroup_id, api_key, total):
    """Poll group status until all tasks complete or timeout. Returns status counts."""
    start = time.time()
    while time.time() - start < PARALLEL_GROUP_TIMEOUT:
        try:
            resp = requests.get(
                f"{PARALLEL_BASE}/v1beta/tasks/groups/{taskgroup_id}",
                headers={"x-api-key": api_key},
                timeout=30,
            )
            resp.raise_for_status()
            status = resp.json().get("status", {})
            counts = status.get("task_run_status_counts", {})
            completed = counts.get("completed", 0)
            failed = counts.get("failed", 0)
            running = counts.get("running", 0)
            queued = counts.get("queued", 0)
            done = completed + failed
            elapsed = (time.time() - start) / 60
            print(f"    [{elapsed:.1f}m] Parallel AI: {completed} completed, "
                  f"{failed} failed, {running} running, {queued} queued "
                  f"({done}/{total})", flush=True)
            if done >= total:
                return counts
        except requests.RequestException as e:
            print(f"    Poll error: {e}")
        time.sleep(PARALLEL_GROUP_POLL_INTERVAL)

    print(f"    WARNING: Group poll timed out after {PARALLEL_GROUP_TIMEOUT}s")
    return None


def fetch_run_result(run_id, api_key):
    """Fetch individual run result. Returns output dict or None."""
    try:
        resp = requests.get(
            f"{PARALLEL_BASE}/v1/tasks/runs/{run_id}/result",
            headers={"x-api-key": api_key},
            timeout=PARALLEL_RESULT_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            run = data.get("run", {})
            if run.get("status") == "completed":
                return data.get("output", {})
    except requests.RequestException:
        pass
    return None


def extract_research_text(output):
    """Extract the text content from Parallel AI output."""
    if not output:
        return None
    content = output.get("content", {})
    if isinstance(content, dict):
        return content.get("output", "")
    if isinstance(content, str):
        return content
    return json.dumps(content)


# ── Claude: team scoring ──────────────────────────────────────

SCORING_SYSTEM = """You are an expert venture capital analyst scoring startup founding teams.

Given research about a startup's founding team, score them on these 4 criteria. Be precise and calibrated.

Technical Excellence (weight 60%):
5 = Top AI lab alumni (DeepMind/FAIR/OpenAI/Google Brain) OR first-author at NeurIPS/ICML/ICLR OR PhD from top-5 CS program with strong citations
4 = Strong ML background, good publications, senior ML role at top tech company
3 = Solid technical background, some publications or relevant engineering at known companies
2 = General engineering background, no ML-specific depth
1 = No technical signal found

Builder Track Record (weight 20%):
5 = Founded company with meaningful exit OR C-level at company that scaled significantly (Series B+, YC alumni, major traction)
4 = Founded company with real traction OR CTO/VP Eng at strong startup OR shipped major product at top company
3 = Founded a company (any stage) OR senior engineering at a startup OR built products at a known company
2 = Early employee at a startup OR shipped features
1 = No startup or product-building experience found

Domain Expertise (weight 10%):
5 = 7+ years in exact target domain, senior roles directly relevant to current company
4 = 5+ years in target or closely adjacent domain
3 = Some relevant domain experience, partially related background
2 = Mostly unrelated background, weak connection to current company's domain
1 = No domain connection at all

Team Complementarity (weight 10%):
5 = Clear technical + commercial + domain coverage, 2+ founders with distinct complementary backgrounds
4 = Good coverage, minor overlap or one small gap
3 = Some overlap, one notable gap
2 = Significant overlap, all same profile
1 = Solo founder or all identical backgrounds

Return ONLY valid JSON, no markdown:
{
  "technical_excellence": <1-5>,
  "builder_track_record": <1-5>,
  "domain_expertise": <1-5>,
  "team_complementarity": <1-5>,
  "team_quality_score": <weighted score 1.0-5.0, one decimal>,
  "team_reasoning": "<one sentence explaining the score>"
}"""


def score_team_with_claude(company_name, research_text, claude_client):
    """Send research to Claude for scoring. Returns parsed dict or None."""
    user_msg = f"Company: {company_name}\n\nTeam Research:\n{research_text}"

    try:
        response = claude_client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=CLAUDE_MAX_TOKENS,
            system=SCORING_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = response.content[0].text.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text.rsplit("```", 1)[0]
        return json.loads(text)
    except (json.JSONDecodeError, Exception) as e:
        print(f"    Claude scoring error: {e}")
        return None


FOUNDER_EXTRACTION_SYSTEM = """Extract founder names from team research. Return ONLY valid JSON, no markdown:
{
  "founders": [
    {"name": "Full Name", "title": "Role if known, e.g. CEO, CTO, Co-founder"},
    ...
  ],
  "confidence": "high" | "medium" | "low",
  "note": "optional note if uncertain"
}

Rules:
- Only include people explicitly identified as founders, co-founders, or founding team members.
- Do NOT include employees, advisors, board members, or investors.
- If no founders can be identified with reasonable confidence, return {"founders": [], "confidence": "low", "note": "..."}
- Use the person's full name as it appears in the research."""


def extract_founders_with_claude(company_name, research_text, claude_client):
    """Ask Claude to extract founder names from research. Returns list of {name, title} dicts."""
    user_msg = f"Company: {company_name}\n\nResearch:\n{research_text}"
    try:
        response = claude_client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=512,
            system=FOUNDER_EXTRACTION_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text.rsplit("```", 1)[0]
        data = json.loads(text)
        confidence = data.get("confidence", "low")
        founders = data.get("founders", [])
        if confidence == "low" and not founders:
            return []
        return founders
    except (json.JSONDecodeError, Exception) as e:
        print(f"    Founder extraction error: {e}")
        return []


def insert_founders(company_id, founders):
    """Insert extracted founders into the founders table. Returns count inserted."""
    if not founders:
        return 0
    conn = get_connection()
    count = 0
    for f in founders:
        name = (f.get("name") or "").strip()
        if not name:
            continue
        # Skip if already exists
        existing = conn.execute(
            "SELECT 1 FROM founders WHERE company_id = ? AND name = ?",
            (company_id, name),
        ).fetchone()
        if existing:
            continue
        title = (f.get("title") or "").strip() or None
        conn.execute(
            "INSERT INTO founders (company_id, name, title, source) VALUES (?, ?, ?, ?)",
            (company_id, name, title, "parallel_ai"),
        )
        count += 1
    conn.commit()
    conn.close()
    return count


# ── Store results ─────────────────────────────────────────────

def store_team_data(company_id, research_json, scores):
    """Store team research and scores in the database."""
    fields = {
        "team_research": json.dumps(research_json) if research_json else None,
        "team_enriched_at": datetime.utcnow().isoformat(),
    }
    if scores:
        fields["team_quality_score"] = scores.get("team_quality_score")
        fields["team_technical_excellence"] = scores.get("technical_excellence")
        fields["team_builder_track_record"] = scores.get("builder_track_record")
        fields["team_domain_expertise"] = scores.get("domain_expertise")
        fields["team_complementarity"] = scores.get("team_complementarity")
        fields["team_reasoning"] = scores.get("team_reasoning")
    update_company(company_id, **fields)


# ── Main ──────────────────────────────────────────────────────

def run_team_enrichment(limit=None, dry_run=False):
    """Run the team enrichment pipeline using Parallel AI Task Groups."""

    # Auto-backup
    backup_path = DB_PATH + '.backup'
    shutil.copy2(DB_PATH, backup_path)
    print(f"  Backup: {backup_path}")

    print()
    print("=" * 64)
    print("  ATHENA TEAM ENRICHMENT PIPELINE")
    print("=" * 64)

    # Ensure columns exist
    ensure_team_columns()

    # Get candidates
    companies = get_candidates(limit)
    total = len(companies)
    print(f"  Found {total} companies to process")

    if dry_run:
        print("\n  DRY RUN — no API calls\n")
        for i, c in enumerate(companies, 1):
            founders = ", ".join(f["name"] for f in c["founders"])
            print(f"  [{i}/{total}] {c['name']} (id={c['id']})")
            print(f"    Founders: {founders}")
        print(f"\n  Would process {total} companies")
        return

    if total == 0:
        print("  Nothing to process.")
        return

    # API keys
    parallel_key = os.environ.get("PARALLEL_API_KEY")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if not parallel_key:
        print("  ERROR: PARALLEL_API_KEY not set")
        sys.exit(1)
    if not anthropic_key:
        print("  ERROR: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    claude_client = anthropic.Anthropic(api_key=anthropic_key)
    start_time = time.time()

    # ── Phase 1: Batch submit to Parallel AI ──

    print(f"\n  Phase 1: Submitting {total} tasks to Parallel AI...")
    taskgroup_id = create_task_group(parallel_key)
    print(f"    Task group: {taskgroup_id}")

    run_to_company = submit_group_runs(taskgroup_id, companies, parallel_key)
    print(f"    Total runs submitted: {len(run_to_company)}")

    # ── Phase 2: Wait for all Parallel AI tasks to complete ──

    print(f"\n  Phase 2: Waiting for Parallel AI to complete all tasks...")
    final_counts = poll_group_status(taskgroup_id, parallel_key, total)

    parallel_elapsed = (time.time() - start_time) / 60
    if final_counts:
        p_ok = final_counts.get("completed", 0)
        p_fail = final_counts.get("failed", 0)
        print(f"\n    Parallel AI done in {parallel_elapsed:.1f}m: "
              f"{p_ok} completed, {p_fail} failed")
    else:
        print(f"\n    Parallel AI phase ended (timeout or error)")

    # ── Phase 3: Fetch results + score with Claude ──

    print(f"\n  Phase 3: Fetching results and scoring with Claude...")
    ok = 0
    failed = 0

    for i, (run_id, company) in enumerate(run_to_company.items(), 1):
        cid = company["id"]
        name = company["name"]
        founders_str = ", ".join(f["name"] for f in company["founders"])

        try:
            # Fetch Parallel AI result
            output = fetch_run_result(run_id, parallel_key)
            research_text = extract_research_text(output)

            if not research_text:
                print(f"  [{i}/{total}] {name} — no research (failed/timeout)")
                store_team_data(cid, output, None)
                failed += 1
                continue

            # Score with Claude
            time.sleep(CLAUDE_RATE_DELAY)
            scores = score_team_with_claude(name, research_text, claude_client)

            if scores:
                tq = scores.get("team_quality_score", "?")
                te = scores.get("technical_excellence", "?")
                bt = scores.get("builder_track_record", "?")
                de = scores.get("domain_expertise", "?")
                tc = scores.get("team_complementarity", "?")
                reasoning = scores.get("team_reasoning", "")
                print(f"  [{i}/{total}] {name} — {tq}/5 "
                      f"(T:{te} B:{bt} D:{de} C:{tc})")
            else:
                print(f"  [{i}/{total}] {name} — Claude scoring failed")

            store_team_data(cid, output, scores)
            ok += 1

        except Exception as e:
            print(f"  [{i}/{total}] {name} — ERROR: {e}")
            traceback.print_exc()
            failed += 1

        # Progress milestones
        if i % 100 == 0:
            elapsed = (time.time() - start_time) / 60
            print(f"\n  --- Progress: {i}/{total} scored | {ok} OK, {failed} failed | "
                  f"{elapsed:.1f}m elapsed ---\n")

    # Summary
    elapsed = (time.time() - start_time) / 60
    print()
    print("=" * 64)
    print("  TEAM ENRICHMENT COMPLETE")
    print("=" * 64)
    print(f"  Processed:         {ok + failed}")
    print(f"  Successful:        {ok}")
    print(f"  Failed:            {failed}")
    print(f"  Parallel AI phase: {parallel_elapsed:.1f} minutes")
    print(f"  Total time:        {elapsed:.1f} minutes")
    if elapsed > 0:
        print(f"  Rate:              {(ok + failed) / elapsed:.0f} companies/min")


def run_no_founders_enrichment(limit=None, dry_run=False):
    """Run team enrichment for companies WITHOUT founder data.

    Phase 1: Submit to Parallel AI asking to discover founders
    Phase 2: Wait for Parallel AI
    Phase 3: Extract founder names (Claude) → insert into founders table
    Phase 4: Score team quality (Claude) → store scores
    """

    # Auto-backup
    backup_path = DB_PATH + '.backup'
    shutil.copy2(DB_PATH, backup_path)
    print(f"  Backup: {backup_path}")

    print()
    print("=" * 64)
    print("  ATHENA TEAM ENRICHMENT — NO-FOUNDERS MODE")
    print("=" * 64)

    ensure_team_columns()

    companies = get_no_founder_candidates(limit)
    total = len(companies)
    print(f"  Found {total} companies without founders to process")

    if dry_run:
        print("\n  DRY RUN — no API calls\n")
        for i, c in enumerate(companies, 1):
            print(f"  [{i}/{total}] {c['name']} (id={c['id']}) — {c.get('website', 'no website')}")
        print(f"\n  Would process {total} companies")
        return

    if total == 0:
        print("  Nothing to process.")
        return

    # API keys
    parallel_key = os.environ.get("PARALLEL_API_KEY")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if not parallel_key:
        print("  ERROR: PARALLEL_API_KEY not set")
        sys.exit(1)
    if not anthropic_key:
        print("  ERROR: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    claude_client = anthropic.Anthropic(api_key=anthropic_key)
    start_time = time.time()

    # ── Phase 1: Batch submit to Parallel AI ──

    print(f"\n  Phase 1: Submitting {total} tasks to Parallel AI...")
    taskgroup_id = create_task_group(parallel_key)
    print(f"    Task group: {taskgroup_id}")

    # Build run mapping using no-founders prompt
    run_to_company = {}
    for batch_start in range(0, len(companies), PARALLEL_GROUP_BATCH_SIZE):
        batch = companies[batch_start:batch_start + PARALLEL_GROUP_BATCH_SIZE]
        inputs = []
        for c in batch:
            prompt = build_parallel_prompt_no_founders(c)
            inputs.append({"input": prompt, "processor": PARALLEL_PROCESSOR})

        resp = requests.post(
            f"{PARALLEL_BASE}/v1beta/tasks/groups/{taskgroup_id}/runs",
            headers={"x-api-key": parallel_key, "Content-Type": "application/json"},
            json={"inputs": inputs},
            timeout=60,
        )
        resp.raise_for_status()
        run_ids = resp.json()["run_ids"]

        for run_id, company in zip(run_ids, batch):
            run_to_company[run_id] = company

        print(f"    Submitted batch: {len(run_ids)} runs "
              f"({batch_start + 1}–{batch_start + len(batch)} of {total})")

    print(f"    Total runs submitted: {len(run_to_company)}")

    # ── Phase 2: Wait for Parallel AI ──

    print(f"\n  Phase 2: Waiting for Parallel AI to complete all tasks...")
    final_counts = poll_group_status(taskgroup_id, parallel_key, total)

    parallel_elapsed = (time.time() - start_time) / 60
    if final_counts:
        p_ok = final_counts.get("completed", 0)
        p_fail = final_counts.get("failed", 0)
        print(f"\n    Parallel AI done in {parallel_elapsed:.1f}m: "
              f"{p_ok} completed, {p_fail} failed")

    # ── Phase 3+4: Extract founders + score ──

    print(f"\n  Phase 3: Extracting founders and scoring with Claude...")
    ok = 0
    failed = 0
    founders_found = 0
    founders_inserted = 0

    for i, (run_id, company) in enumerate(run_to_company.items(), 1):
        cid = company["id"]
        name = company["name"]

        try:
            # Fetch Parallel AI result
            output = fetch_run_result(run_id, parallel_key)
            research_text = extract_research_text(output)

            if not research_text:
                print(f"  [{i}/{total}] {name} — no research (failed/timeout)")
                store_team_data(cid, output, None)
                failed += 1
                continue

            # Extract founder names
            time.sleep(CLAUDE_RATE_DELAY)
            extracted = extract_founders_with_claude(name, research_text, claude_client)
            n_extracted = len(extracted)

            if n_extracted > 0:
                n_inserted = insert_founders(cid, extracted)
                founders_found += n_extracted
                founders_inserted += n_inserted
                founder_names = ", ".join(f.get("name", "?") for f in extracted)
            else:
                founder_names = "none found"
                n_inserted = 0

            # Score with Claude
            time.sleep(CLAUDE_RATE_DELAY)
            scores = score_team_with_claude(name, research_text, claude_client)

            if scores:
                tq = scores.get("team_quality_score", "?")
                te = scores.get("technical_excellence", "?")
                bt = scores.get("builder_track_record", "?")
                de = scores.get("domain_expertise", "?")
                tc = scores.get("team_complementarity", "?")
                print(f"  [{i}/{total}] {name} — {tq}/5 "
                      f"(T:{te} B:{bt} D:{de} C:{tc}) "
                      f"[+{n_inserted} founders: {founder_names}]")
            else:
                print(f"  [{i}/{total}] {name} — scored:N/A "
                      f"[+{n_inserted} founders: {founder_names}]")

            store_team_data(cid, output, scores)
            ok += 1

        except Exception as e:
            print(f"  [{i}/{total}] {name} — ERROR: {e}")
            traceback.print_exc()
            failed += 1

        if i % 100 == 0:
            elapsed = (time.time() - start_time) / 60
            print(f"\n  --- Progress: {i}/{total} | {ok} OK, {failed} failed | "
                  f"{founders_inserted} founders inserted | {elapsed:.1f}m elapsed ---\n")

    # Summary
    elapsed = (time.time() - start_time) / 60
    print()
    print("=" * 64)
    print("  NO-FOUNDERS ENRICHMENT COMPLETE")
    print("=" * 64)
    print(f"  Processed:           {ok + failed}")
    print(f"  Successful:          {ok}")
    print(f"  Failed:              {failed}")
    print(f"  Founders discovered: {founders_found}")
    print(f"  Founders inserted:   {founders_inserted}")
    print(f"  Parallel AI phase:   {parallel_elapsed:.1f} minutes")
    print(f"  Total time:          {elapsed:.1f} minutes")
    if elapsed > 0:
        print(f"  Rate:                {(ok + failed) / elapsed:.0f} companies/min")


# ── CLI ───────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Athena Team Enrichment Pipeline")
    parser.add_argument("--limit", type=int, help="Max companies to process")
    parser.add_argument("--dry-run", action="store_true", help="Show candidates, no API calls")
    parser.add_argument("--no-founders", action="store_true",
                        help="Discover founders for companies without any, then score")
    args = parser.parse_args()

    if args.no_founders:
        run_no_founders_enrichment(limit=args.limit, dry_run=args.dry_run)
    else:
        run_team_enrichment(limit=args.limit, dry_run=args.dry_run)
