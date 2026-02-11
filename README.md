# Athena — European Pre-Seed Intelligence

Athena tracks early-stage European startups across university programs, accelerators, and tech communities. It combines curated signals from programs like Venture Kick, ETH AI Center, Cambridge Enterprise, and Entrepreneur First with real-time signals from HackerNews and European tech press.

## Live Demo

[Link will be added after deployment]

## How It Works

- **7 program sources**: Venture Kick, ETH AI Center, Entrepreneur First, Seedcamp, Cambridge Enterprise, Imperial College, Y Combinator (EU)
- **Real-time signals**: HackerNews, ProductHunt, RSS (Sifted, Tech.eu, EU-Startups, TechCrunch)
- **Smart scoring (1–10)**: Based on program pedigree, community buzz, cross-source appearances, and recency
- **Cross-layer matching**: Detects when program-vetted companies also generate real-time buzz

## Tech Stack

- **Frontend**: React + Tailwind CSS
- **Backend**: Python + FastAPI
- **Database**: SQLite
- **Scraping**: BeautifulSoup + requests

## Data Sources

| Source | Type | Companies |
|--------|------|-----------|
| Venture Kick | Grant program | Swiss university startups |
| ETH AI Center | University spin-offs | AI startups from ETH Zurich |
| Cambridge Enterprise | University spin-offs | Cambridge University ventures |
| Imperial College | University spin-offs | Imperial College London ventures |
| Entrepreneur First | Accelerator | Pan-European talent-first startups |
| Seedcamp | Seed fund | European seed-stage companies |
| HackerNews | Community | Trending tech launches |
| Sifted / Tech.eu | Press | European startup news |

## Setup

```bash
# Backend
pip install -r requirements.txt
python run_scrapers.py          # Full pipeline: scrape, match, score
python -m uvicorn api.main:app  # Start API on port 8000

# Frontend
cd frontend
npm install
npm start                       # Dev server on port 3000
npm run build                   # Production build
```

## Pipeline

```bash
python run_scrapers.py           # Run everything
python run_scrapers.py --quick   # Real-time scrapers only (HN, PH, RSS)
```

---

Built by **Luis** — Scout at **Ellipsis Ventures**
