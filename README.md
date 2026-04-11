# Athena

Deal intelligence platform for European pre-seed AI and deep-tech startups. Automatically discovers, enriches, and scores companies from 20+ sources so scouts don't have to manually monitor dozens of accelerators, university programs, and tech communities.

**Stack:** FastAPI · React · Tailwind · SQLite · Claude Sonnet · Parallel AI  
**Deployed on:** Render (Docker, single service serving both API and frontend)

---

## How it works

```
Scrapers → Database → Matcher → Enrichment → Scorer → Dashboard
```

1. **Scrapers** pull companies from ~20 sources across two signal layers (curated + realtime)
2. **Matcher** deduplicates entries using fuzzy name matching and domain comparison
3. **Enrichment** runs in three passes: LLM thesis evaluation, deep founder research, and company age detection
4. **Scorer** combines enrichment results into a single 0–5 Athena Score
5. **Dashboard** surfaces everything with filters, rankings, a deal pipeline, and export

---

## Project structure

```
athena/
├── api/
│   └── main.py             # FastAPI backend — all endpoints (signals, filters, stats,
│                            #   export, founders, pipeline, quick-screen, /new)
│
├── database/
│   └── database.py         # SQLite schema, migrations, CRUD helpers
│
├── scrapers/               # ~20 scrapers, two signal layers
│   ├── __init__.py          #   Shared fetch() helper with retry logic
│   ├── hackernews.py        #   Realtime — HN Show/Launch posts
│   ├── producthunt.py       #   Realtime — daily product launches
│   ├── rss_feeds.py         #   Realtime — Sifted, TechCrunch RSS
│   ├── eu_startups.py       #   Realtime — EU-Startups directory (WordPress API)
│   ├── venturekick.py       #   Curated — Swiss accelerator (3 stages)
│   ├── entrepreneur_first.py #  Curated — EF portfolio
│   ├── seedcamp.py          #   Curated — Seedcamp portfolio
│   ├── ycombinator.py       #   Curated — YC companies (European filter)
│   ├── antler.py            #   Curated — Antler portfolio
│   ├── ewor.py              #   Curated — EWOR fellowship (2023–2025)
│   ├── techstars.py         #   Curated — Techstars portfolio
│   ├── fivehundred_global.py #  Curated — 500 Global portfolio
│   ├── epfl.py              #   Curated — EPFL spinouts
│   ├── kth.py               #   Curated — KTH Innovation portfolio
│   ├── eth_ai_center.py     #   Curated — ETH AI Center
│   ├── oxford.py            #   Curated — Oxford Innovation (CSV export)
│   ├── cambridge_enterprise.py # Curated — Cambridge Enterprise portfolio
│   ├── imperial_spinouts.py #   Curated — Imperial College spinouts
│   ├── dtu.py               #   Curated — DTU Science Park
│   ├── uzh.py               #   Curated — University of Zurich spinoffs
│   └── swedish_accelerators.py # Curated — Chalmers, Sting, LEAD, GU Ventures, etc.
│
├── scoring/
│   ├── scorer.py            # Athena Score calculation (weighted components, tiers, recency)
│   └── matcher.py           # Fuzzy deduplication + domain-based matching
│
├── enrich_companies.py      # LLM enrichment — thesis fit, technical depth, founders
├── enrich_team.py           # Team enrichment — Parallel AI research + Claude scoring
├── enrich_newness.py        # Company age detection — crt.sh SSL certificate dates
├── import_universities.py   # University reference data import (rankings, spinout counts)
├── run_scrapers.py          # Full pipeline: scrape → match → score (sequential)
│
├── Dockerfile               # Single Docker image: Python 3.11 + Node 20, builds frontend
├── athena.db                # SQLite database (baked into Docker image on deploy)
│
└── frontend/
    └── src/
        ├── pages/
        │   ├── Dashboard.js     # Main feed — all companies with filters + expandable cards
        │   ├── Top20.js         # Ranked leaderboard of highest-scoring companies
        │   ├── Pipeline.js      # Kanban board — drag-and-drop deal tracking
        │   ├── New.js           # Recently launched companies (SSL cert age)
        │   └── About.js         # Methodology explainer
        ├── components/
        │   ├── CompanyCard.js   # Expandable company detail (scores, founders, signals, buttons)
        │   ├── TopBar.js        # Navigation: Dashboard · Top 20 · Pipeline · New
        │   ├── FilterBar.js     # Filters: program, sector, geography, stage, score, tier
        │   ├── StatsOverview.js # Summary stats (total companies, scored, enriched)
        │   └── Footer.js
        └── api.js               # Axios API client
```

---

## Signal layers

Every company enters Athena through one of two layers:

| Layer | Sources | What it means |
|-------|---------|---------------|
| **Curated** | Accelerators (YC, EF, Seedcamp, Antler, Venture Kick, EWOR, Techstars, 500 Global), university spinouts (EPFL, KTH, ETH, Oxford, Cambridge, Imperial, DTU, UZH), Swedish accelerators | Company passed an external selection process — accepted into a competitive program or spun out of a research lab |
| **Realtime** | HackerNews, ProductHunt, press (Sifted, TechCrunch via RSS), EU-Startups directory | Company is generating public buzz — launched a product, got press coverage, or appeared on HN |

Each scraper stores the company record + one or more **signals** (the specific detection event with source, URL, metadata like HN points or PH upvotes).

---

## Enrichment pipeline

Enrichment runs in three independent passes. Each has its own script, is idempotent (interrupted runs resume cleanly via NULL timestamp filters), and backs up the database automatically before running.

### 1. Thesis evaluation (`enrich_companies.py`)

For each company, fetches the website (including /about and /team pages), then sends everything to **Claude Sonnet** for structured evaluation.

Returns:
- `thesis_fit` (1–5) — Does it match Ellipsis's thesis? AI/deep-tech, European, early-stage
- `technical_depth` (1–5) — Is it building real tech or wrapping existing APIs?
- `is_ai_core` (boolean) — True AI company vs. "AI-powered" marketing
- `platform_risk` — Dependency on a single platform (e.g., OpenAI API wrappers)
- `proprietary_data` — Whether the company has a data moat
- `confidence` — How certain Claude is about the evaluation
- `funding_stage_hint` — Stage inference when our data is missing
- `improved_description` — Better description when ours is weak/missing
- `founders` — Extracted founder names and titles

Cost: ~$0.02–0.03 per company via Sonnet.

### 2. Team scoring (`enrich_team.py`)

For companies with `thesis_fit >= 3` (~1,400 companies):

**Phase 1:** Sends each company to **Parallel AI** (Core processor, $0.025/task) for deep founder research — LinkedIn profiles, publications, prior companies, technical background.

**Phase 2:** Sends the research output to **Claude Sonnet** which scores the team on 4 criteria:
- Technical Excellence (60% weight) — AI lab alumni, publications, PhD from top programs
- Builder Track Record (20%) — Prior exits, companies founded, products shipped
- Domain Expertise (10%) — Years in the target domain
- Team Complementarity (10%) — Technical + commercial + domain coverage

Result: `team_quality_score` (1.0–5.0)

Two modes:
- Default: scores companies that already have founder names in the database
- `--no-founders`: discovers founders first (for companies with no founder data), then scores

### 3. Newness detection (`enrich_newness.py`)

Queries **crt.sh** (Certificate Transparency logs) for each company's domain to find when the website first got an SSL certificate — a reliable proxy for when the site went live.

Classification:
- `new` = SSL cert under 6 months old
- `recent` = 6–18 months
- `old` = over 18 months

Rate-limited to 3 concurrent requests with 3-second delays (crt.sh overloads easily). Results feed the `/new` page.

---

## Athena Score

Weighted composite score (0–5.0) calculated from enrichment results:

| Component | Weight | Source |
|-----------|--------|--------|
| Team Signal | 35% | Parallel AI research + Claude scoring |
| Thesis Fit | 30% | LLM thesis evaluation |
| Program Pedigree | 25% | Accelerator/program quality tier |
| Data Completeness | 10% | How many fields are populated (description, website, geography, city, sector, founders, stage) |
| Traction Signal | 0% | Scored but not weighted (signal count + diversity: press, HN, PH, multiple programs) |

When team data is unavailable, the 35% weight redistributes proportionally across the other components.

### Program tiers

Programs are ranked by pedigree for the Program Pedigree score:

- **Tier 5:** Y Combinator, Entrepreneur First, Techstars, EPFL, ETH AI Center, Oxford, Cambridge, Imperial
- **Tier 4:** Antler, Seedcamp, Venture Kick (Stage 2/3 bumps to Tier 5)
- **Tier 3:** 500 Global, EWOR, KTH Innovation, DTU, UZH, Swedish accelerators (Chalmers, Sting, LEAD, GU Ventures, LU Innovation)

Companies in 3+ distinct sources get a multi-source bump (+1 tier, capped at 5).

### Cohort recency adjustment

The scorer applies a recency bonus/penalty based on program cohort year. Recent cohorts (2025–2026) get a small boost. Old cohorts (pre-2020) get penalized — a company from a 2018 cohort is likely past pre-seed stage even if our data still says "Pre-seed."

### Decision thresholds

- **≥4.0** — High Priority
- **3.5–3.9** — Worth Investigating
- **3.0–3.4** — On Radar
- **<3.0** — Low Priority

### Quantum override

33 quantum computing companies have been manually excluded from scoring via `thesis_override`. Their original LLM evaluations are preserved (shown with "(overridden)" in the UI) but they don't appear in rankings.

---

## Matcher (`scoring/matcher.py`)

Deduplicates the database using:

- **Fuzzy name matching** — normalized names (stripped of legal suffixes like GmbH, Ltd, AG), bigram similarity (Dice coefficient), containment checks for short names
- **Domain matching** — companies sharing the same website domain (excluding generic hosting like github.io, vercel.app, linkedin.com)
- **Conservative merging** — prefers false negatives over false positives. When merging, keeps the richer record and moves all signals/programs to the survivor

Runs automatically as part of `run_scrapers.py`.

---

## Pipeline & Quick Screen

### Pipeline (`/pipeline`)

Kanban board for manual deal tracking. No companies auto-populate — scouts browse the Dashboard, Top 20, or /new, find something interesting, and click **"Add to Pipeline."** The company appears in the "New" column.

**Columns:** New → Reviewing → Shortlisted → Submitted → Passed

Features:
- Drag-and-drop between columns
- Notes and comments per company
- "Add to Pipeline" button on company cards across all pages
- Toast notification on add

### Quick Screen

Button on company cards that sends the company's website URL + name to Ellipsis's automated screening tool via an **n8n webhook** (`POST /api/quick-screen` → proxied to the n8n endpoint to avoid CORS). Available on Dashboard, Top 20, and Pipeline.

The screening tool (built by Celina via Parallel AI) runs a relevancy check and returns whether the company fits Ellipsis's criteria.

---

## Dashboard features

- **Filters** — program, sector, geography, stage, score threshold, cohort year, data tier
- **Search** — full-text search across company names and descriptions
- **Expandable cards** — click to see score breakdown, founders (editable), signals, website status, thesis reasoning, team reasoning
- **Website status** — each company shows active/inactive/unverified status. Toggle filters to hide inactive or unverified companies
- **CSV export** — exports all currently filtered companies with scores and metadata
- **Stage display** — shows stage with source and age-based confidence (current / stale / outdated)

---

## Database

SQLite with 6 tables:

- **companies** — core record: name, description, sector, geography, city, website, stage, stage_source, stage_detected_date. Enrichment fields: thesis_fit_score, technical_depth_score, ai_core, platform_risk, proprietary_data, llm_evaluation, llm_confidence, llm_enriched_at. Team fields: team_quality_score, team_technical_excellence, team_builder_track_record, team_domain_expertise, team_complementarity, team_reasoning, team_research, team_enriched_at. Score fields: athena_score, athena_score_breakdown. Newness fields: ssl_first_seen, newness_status, newness_checked_at. Pipeline fields: pipeline_status, pipeline_notes. Status: company_status, thesis_override.
- **signals** — one row per source detection (company_id, source_name, source_type, source_url, signal_layer, title, metadata, detected_at)
- **programs** — accelerator/program memberships (company_id, program_name, program_type, program_country, cohort, funding_amount)
- **founders** — founder profiles (company_id, name, title, linkedin_url, email, source)
- **universities** — reference data for university programs (name, country, city, rank, score, num_spinouts, total_funding)
- **scrape_snapshots** — baseline snapshots for future scraper diffing

---

## Running it

```bash
# Full pipeline (scrape all sources → deduplicate → score)
python run_scrapers.py

# Quick mode (realtime scrapers only — HN, PH, RSS, EU-Startups)
python run_scrapers.py --quick

# LLM thesis evaluation
python enrich_companies.py                    # all curated companies
python enrich_companies.py --limit 50         # first 50 (testing)
python enrich_companies.py --company-id 123   # single company (debugging)
python enrich_companies.py --dry-run          # show candidates, no API calls
python enrich_companies.py --skip-fetch       # use DB data only, no website visits

# Team enrichment (Parallel AI + Claude)
python enrich_team.py                         # companies with thesis_fit >= 3 + existing founders
python enrich_team.py --no-founders           # discover founders first, then score
python enrich_team.py --limit 20              # first 20 (testing)
python enrich_team.py --dry-run               # show candidates, no API calls

# Company age detection (crt.sh)
python enrich_newness.py --limit 20           # first 20 (testing)
python enrich_newness.py --min-score 3.5      # high-scoring companies only
python enrich_newness.py --force              # re-check all companies

# Start locally
uvicorn api.main:app --reload --port 8000     # API
cd frontend && npm start                      # Frontend (dev server)

# Deploy to Render
git push --force origin main:deploy           # deploys from "deploy" branch, not "main"
```

**Important:** Scrapers must run sequentially (SQLite can't handle concurrent writes). The `run_scrapers.py` script handles this automatically. Enrichment scripts also back up `athena.db` before running.

---

## Deployment

Single Render service running Docker. The Dockerfile installs Python 3.11 + Node 20, builds the React frontend (`npm run build`), and serves everything via FastAPI's StaticFiles mount on port 8000.

The database (`athena.db`) is baked into the Docker image — it's committed to git and copied into the container at build time. This means local enrichment runs need to be committed and pushed to update the deployed data.

Deploys from the `deploy` branch (not `main`). Use `git push --force origin main:deploy` to trigger a deploy.
