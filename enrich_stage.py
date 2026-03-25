"""
Athena Stage Enrichment Pipeline — Funding history research via Parallel AI + Claude parsing.

For each high-thesis-fit company, this script:
  1. Sends a research task to Parallel AI (Core processor) to find funding history
  2. Stores the raw research in stage_research column
  3. Sends research to Claude Sonnet to extract structured funding data
  4. Updates stage, stage_source, stage_detected_date if confident

Usage:
    python enrich_stage.py                  # All thesis_fit >= 3, not yet verified
    python enrich_stage.py --limit 20       # First 20 (for testing)
    python enrich_stage.py --dry-run        # Show candidates, no API calls
    python enrich_stage.py --company-id 123 # Single company
"""

import json
import os
import sys
import time
import shutil
import argparse
import traceback
from datetime import datetime, date

import anthropic
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from database.database import get_connection, update_company, DB_PATH


# ── Config ────────────────────────────────────────────────────

PARALLEL_BASE = "https://api.parallel.ai"
PARALLEL_PROCESSOR = "core"

CLAUDE_MODEL = "claude-sonnet-4-20250514"
CLAUDE_MAX_TOKENS = 1024
CLAUDE_RATE_DELAY = 0.5  # seconds between Claude calls

# ── New DB columns ────────────────────────────────────────────

STAGE_COLUMNS = {
    "stage_research": "TEXT",
    "stage_confidence": "TEXT",
    "stage_verified_at": "TEXT",
    "stage_verification_source": "TEXT",
}


def ensure_stage_columns():
    """Add stage enrichment columns if they don't exist."""
    conn = get_connection()
    for col, coltype in STAGE_COLUMNS.items():
        try:
            conn.execute(f"SELECT {col} FROM companies LIMIT 1")
        except Exception:
            conn.execute(f"ALTER TABLE companies ADD COLUMN {col} {coltype}")
    conn.commit()
    conn.close()
    print("  Stage columns verified.")


# ── Company selection ─────────────────────────────────────────

def get_candidates(limit=None, company_id=None):
    """Get companies with thesis_fit >= 3 that haven't been stage-verified."""
    conn = get_connection()
    if company_id:
        rows = conn.execute(
            "SELECT id, name, website, description, stage, stage_source "
            "FROM companies WHERE id = ?",
            (company_id,),
        ).fetchall()
    else:
        query = """
            SELECT c.id, c.name, c.website, c.description, c.stage, c.stage_source
            FROM companies c
            WHERE c.thesis_fit_score >= 3
              AND c.stage_verified_at IS NULL
            ORDER BY c.athena_score DESC
        """
        if limit:
            query += f" LIMIT {int(limit)}"
        rows = conn.execute(query).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Parallel AI: Task Group batch API ─────────────────────────
# (copied from enrich_team.py for consistency)

PARALLEL_GROUP_BATCH_SIZE = 1000  # max runs per POST
PARALLEL_GROUP_POLL_INTERVAL = 30  # seconds between group status polls
PARALLEL_GROUP_TIMEOUT = 3600  # max seconds to wait for entire group
PARALLEL_RESULT_TIMEOUT = 30  # seconds per individual result fetch


def build_parallel_prompt(company):
    """Build the research prompt for Parallel AI."""
    name = company["name"]
    website = company.get("website") or "no website"
    description = (company.get("description") or "no description")[:300]

    return f"""Research the funding history of {name} ({website}).
Description: {description}

Find:
- What funding rounds has this company raised? (pre-seed, seed, Series A, etc.)
- How much was raised in each round, and when?
- Who were the investors in each round?
- Has the company received any grants or non-dilutive funding?
- Is there any Crunchbase, PitchBook, or Dealroom data available?
- Has the company been acquired, shut down, or IPO'd?

If you cannot find any funding information, say so explicitly. Do not guess or infer rounds from team size or company age."""


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
              f"({batch_start + 1}\u2013{batch_start + len(batch)} of {len(companies)})")
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


# ── Claude: stage scoring ─────────────────────────────────────

SCORING_SYSTEM = """You are a venture capital data analyst. Given research about a startup's funding history, extract structured funding data.

Return ONLY valid JSON, no markdown:
{
    "current_stage": "Pre-seed" | "Seed" | "Series A" | "Series B" | "Series C" | "Growth" | "IPO" | "Acquired" | "Shut down" | "Grant only" | "Bootstrapped" | "Unknown",
    "funding_rounds": [
        {
            "stage": "Seed",
            "amount": "\u20ac2M",
            "date": "2024-03",
            "investors": ["Investor A", "Investor B"],
            "source_url": "https://..."
        }
    ],
    "total_raised": "\u20ac2M" or null,
    "last_round_date": "2024-03" or null,
    "confidence": "high" | "medium" | "low",
    "reasoning": "One sentence explaining what was found and confidence level",
    "source_url": "Primary source URL for the most recent round" or null
}

Rules:
- "current_stage" = the MOST RECENT round raised, not the earliest.
- If research explicitly says no funding found, return current_stage "Unknown" with confidence "high" \u2014 that's useful data.
- If the company only has accelerator/program participation and no separate round, return "Pre-seed" with confidence "medium".
- Do NOT confuse accelerator prize money (e.g., Venture Kick CHF 10k) with a funding round. That's "Grant only".
- A company in an accelerator that hasn't announced a round is "Pre-seed" (assumed), not "Seed".
- If conflicting info exists, use the most recent credible source."""


def parse_stage_with_claude(company, research_text, claude_client):
    """Send research to Claude for structured extraction. Returns parsed dict or None."""
    user_msg = (
        f"Company: {company['name']}\n"
        f"Current stage in our database: {company.get('stage', 'Unknown')} "
        f"(source: {company.get('stage_source', 'unknown')})\n\n"
        f"Research:\n{research_text}"
    )

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
        print(f"    Claude parsing error: {e}")
        return None


# ── Store results ─────────────────────────────────────────────

def store_stage_data(company_id, research_output, parsed, company):
    """Store stage research and optionally update the stage column."""
    fields = {
        "stage_research": json.dumps(research_output) if research_output else None,
        "stage_verified_at": datetime.utcnow().isoformat(),
    }

    if parsed:
        confidence = parsed.get("confidence", "low")
        fields["stage_confidence"] = confidence
        fields["stage_verification_source"] = parsed.get("source_url")

        new_stage = parsed.get("current_stage")
        current_stage = company.get("stage")

        # Only update stage if confident and different
        if (
            confidence in ("high", "medium")
            and new_stage
            and new_stage != "Unknown"
            and new_stage != current_stage
        ):
            fields["stage"] = new_stage
            fields["stage_source"] = parsed.get("source_url") or "parallel_ai_verified"
            fields["stage_detected_date"] = parsed.get("last_round_date") or date.today().isoformat()

        # Store full funding rounds in stage_research (overwrite with enriched version)
        enriched_research = {
            "raw_output": research_output,
            "parsed": parsed,
        }
        fields["stage_research"] = json.dumps(enriched_research)
    else:
        fields["stage_confidence"] = "low"

    update_company(company_id, **fields)


# ── Main ──────────────────────────────────────────────────────

def run_stage_enrichment(limit=None, company_id=None, dry_run=False):
    """Run the stage enrichment pipeline using Parallel AI Task Groups."""

    # Auto-backup
    backup_path = DB_PATH + '.pre_stage_enrich'
    shutil.copy2(DB_PATH, backup_path)
    print(f"  Backup: {backup_path}")

    print()
    print("=" * 64)
    print("  ATHENA STAGE ENRICHMENT PIPELINE")
    print("=" * 64)

    # Ensure columns exist
    ensure_stage_columns()

    # Get candidates
    companies = get_candidates(limit, company_id)
    total = len(companies)
    print(f"  Found {total} companies to process")

    if dry_run:
        print("\n  DRY RUN \u2014 no API calls\n")
        for i, c in enumerate(companies, 1):
            stage = c.get("stage") or "None"
            source = c.get("stage_source") or "unknown"
            print(f"  [{i}/{total}] {c['name']} (id={c['id']}) "
                  f"\u2014 current: {stage} (from {source})")
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

    # ── Phase 3: Fetch results + parse with Claude ──

    print(f"\n  Phase 3: Fetching results and parsing with Claude...")
    ok = 0
    failed = 0
    stages_updated = 0
    stage_changes = {}  # track old -> new for summary

    for i, (run_id, company) in enumerate(run_to_company.items(), 1):
        cid = company["id"]
        name = company["name"]
        old_stage = company.get("stage") or "None"

        try:
            # Fetch Parallel AI result
            output = fetch_run_result(run_id, parallel_key)
            research_text = extract_research_text(output)

            if not research_text:
                print(f"  [{i}/{total}] {name} \u2014 no research (failed/timeout)")
                store_stage_data(cid, output, None, company)
                failed += 1
                continue

            # Parse with Claude
            time.sleep(CLAUDE_RATE_DELAY)
            parsed = parse_stage_with_claude(company, research_text, claude_client)

            if parsed:
                new_stage = parsed.get("current_stage", "?")
                confidence = parsed.get("confidence", "?")
                total_raised = parsed.get("total_raised") or "unknown"
                reasoning = parsed.get("reasoning", "")
                n_rounds = len(parsed.get("funding_rounds", []))

                # Check if stage will actually change
                changed = (
                    confidence in ("high", "medium")
                    and new_stage != "Unknown"
                    and new_stage != old_stage
                )
                change_str = f" \u2192 {new_stage}" if changed else ""
                if changed:
                    stages_updated += 1
                    key = f"{old_stage} \u2192 {new_stage}"
                    stage_changes[key] = stage_changes.get(key, 0) + 1

                print(f"  [{i}/{total}] {name} \u2014 {old_stage}{change_str} "
                      f"| conf={confidence} | raised={total_raised} "
                      f"| {n_rounds} rounds")
            else:
                print(f"  [{i}/{total}] {name} \u2014 Claude parsing failed")

            store_stage_data(cid, output, parsed, company)
            ok += 1

        except Exception as e:
            print(f"  [{i}/{total}] {name} \u2014 ERROR: {e}")
            traceback.print_exc()
            failed += 1

        # Progress milestones
        if i % 100 == 0:
            elapsed = (time.time() - start_time) / 60
            print(f"\n  --- Progress: {i}/{total} | {ok} OK, {failed} failed | "
                  f"{stages_updated} stages updated | {elapsed:.1f}m elapsed ---\n")

    # Summary
    elapsed = (time.time() - start_time) / 60
    print()
    print("=" * 64)
    print("  STAGE ENRICHMENT COMPLETE")
    print("=" * 64)
    print(f"  Processed:         {ok + failed}")
    print(f"  Successful:        {ok}")
    print(f"  Failed:            {failed}")
    print(f"  Stages updated:    {stages_updated}")
    print(f"  Parallel AI phase: {parallel_elapsed:.1f} minutes")
    print(f"  Total time:        {elapsed:.1f} minutes")
    if elapsed > 0:
        print(f"  Rate:              {(ok + failed) / elapsed:.0f} companies/min")

    if stage_changes:
        print(f"\n  Stage Changes:")
        for change, count in sorted(stage_changes.items(), key=lambda x: -x[1]):
            print(f"    {change}: {count}")

    print()


# ── CLI ───────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Athena Stage Enrichment Pipeline")
    parser.add_argument("--limit", type=int, help="Max companies to process")
    parser.add_argument("--company-id", type=int, help="Process single company by ID")
    parser.add_argument("--dry-run", action="store_true", help="Show candidates, no API calls")

    args = parser.parse_args()

    run_stage_enrichment(
        limit=args.limit,
        company_id=args.company_id,
        dry_run=args.dry_run,
    )
