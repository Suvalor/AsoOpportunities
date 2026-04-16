# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ASO Keyword Engine — mines blue-ocean keywords from App Store via Apple Autocomplete API, combined with competition analysis and AI-driven seed evolution. Produces high-value keyword selection reports.

## Commands

```bash
# Run the FastAPI service locally
uvicorn app.main:app --host 0.0.0.0 --port 8000

# CLI one-off scan (full scan, writes CSV)
python main.py
python main.py --seeds 20          # only first 20 seeds
python main.py --country gb        # single country
python main.py --out results.csv   # custom output

# Docker
docker compose up -d --build

# Syntax check all source files
python3 -c "import ast, glob; [ast.parse(open(f).read()) for f in glob.glob('**/*.py', recursive=True) if '.venv' not in f]"
```

No test suite exists yet. There is no pytest configuration.

## Architecture

### Two-layer design

```
aso_core/    — Pure collection & scoring engine (no DB, no auth, no FastAPI)
app/         — FastAPI service layer (DB, auth, routes, evolution, reports)
```

`aso_core` is usable standalone via `main.py` CLI. `app/` wraps it as a service.

### Data flow

1. **Scan**: Seeds → Apple Autocomplete → keywords → iTunes competition → blue-ocean scoring → MySQL
2. **Enrichment**: Google Play autocomplete/competition, Google Trends rising, Reddit demand signals
3. **Evolution**: After full scan → evaluate seed performance → prune weak → Claude generates new pending seeds → validate via autocomplete → activate
4. **Reports**: AI generates markdown reports from keyword snapshots, injected with previous report as memory for self-iteration

### Key design decisions

- **No ORM** — hand-written SQL with pymysql + parameterized queries. All DB functions follow the pattern: `conn = _get_connection()` → `with conn.cursor() as cur` → `cur.execute()` → `conn.commit()` → `conn.close()` in finally block.
- **Time-series keywords** — `aso_keywords` table has no unique constraint on (keyword, country). Each scan inserts new rows. Queries use `ROW_NUMBER() OVER (PARTITION BY keyword, country ORDER BY blue_ocean_score DESC)` to deduplicate at read time.
- **Seed matrix** — `aso_core/config_data.py` defines 45 pain-point scenario seeds for first-time bootstrap. Seeds are then persisted in `aso_seeds` DB table and evolved via Claude API across generations.
- **Dual auth** — `X-API-Key` header (for n8n/API calls) and JWT cookie (for browser pages). `verify_api_key_or_cookie` accepts either.
- **Agent abstraction** — `app/agent_client.py` reads agent config (base_url, encrypted api_key, model) from DB via `aso_agent_assignments` table, replacing hardcoded Anthropic calls.
- **Fernet encryption** — Agent API keys encrypted with `AGENT_ENCRYPT_KEY` (64-hex / 32-byte) before storage.

### Frontend

Single-page HTML files in `app/static/`, no build step:
- `index.html` — login/register
- `dashboard.html` — main dashboard
- `seeds-dashboard.html` — seed evolution status (clickable cards → modal with seed list → keyword detail)
- `keyword-insights.html` — AI reports (clickable cards → keyword list modal)
- `agents.html` — agent management

All use dark theme, vanilla JS, fetch API with cookie auth.

### Scoring

`aso_core/scorer.py`: `blue_ocean_score()` returns integer score (max ~132) using non-linear continuous decay:
- Competition intensity (0-40): `40 * exp(-0.0004 * top_reviews)` — continuous, no threshold jumps
- Search authenticity (0-20): `20 * (1 - exp(-0.5 * coverage))` — marginal diminishing
- Market dispersion (0-15): `15 * (1 - concentration)` — continuous
- Competitor staleness (0-10), Trend signals (0-15), Cross-platform (0-12), Trends rising (0-8), Reddit (0-6), Android mod (-10 to +6), Synergy bonuses (0-8)

Labels: 💎 Gold Mine (≥75), 🟢 Blue Ocean (55-74), 🟡 Watch (35-54), 🔴 Skip (<35)

### API routes

All routes except `/health` and `/auth/*` require auth.

| Prefix | Router file | Key endpoints |
|--------|-------------|---------------|
| `/scan/` | `routers/scan.py` | POST /start (async background thread), GET /status/{batch_id} |
| `/analysis/` | `routers/analysis.py` | GET /top, GET /compare |
| `/seeds/` | `routers/seeds.py` | GET /status, GET /list, GET /{seed}/keywords |
| `/report/` | `routers/report.py` | POST /generate, GET /check, GET /latest, GET /history, GET /{id} |
| `/agents/` | `routers/agents.py` | CRUD + assignments (admin only) |
| `/auth/` | `routers/auth_router.py` | POST /register, /login, /logout, GET /me |

### Required environment variables

`API_KEY`, `JWT_SECRET`, `AGENT_ENCRYPT_KEY`, `MYSQL_HOST/PASSWORD/DB`, `ANTHROPIC_API_KEY` (if using direct Anthropic calls). See `.env.example` for full list.

## Code patterns

- **DB query deduplication**: All queries on `aso_keywords` must use `ROW_NUMBER() OVER (PARTITION BY keyword, country ...)` to avoid returning duplicate rows from different scan batches. Do NOT add a unique index to `aso_keywords` — it's a time-series table.
- **Datetime handling**: Use `datetime.now(timezone.utc).replace(tzinfo=None)` for UTC naive datetimes (MySQL compatibility). Never use `datetime.utcnow()` (deprecated in 3.12+).
- **Rate limiting sleep**: Place `time.sleep()` inside the try block after successful API response, NOT in `finally` — avoids unnecessary delay on request failure.
- **Falsy value check**: Use `is not None` instead of truthiness for float/int DB columns that can be `0` or `0.0` (e.g., `concentration`).
