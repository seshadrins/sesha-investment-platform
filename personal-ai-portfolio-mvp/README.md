# Personal AI Portfolio Manager — MVP

A self-hosted, Windows-first portfolio ledger and decision-support application.

## What this MVP includes

- Opening portfolio import using CSV
- Manual Buy, Sell, Dividend and Adjustment transactions
- Lot-aware holdings calculation using FIFO
- Current price entry and CSV price import
- Portfolio dashboard
- Realised and unrealised profit/loss
- Holding-period and allocation analysis
- Transparent rule-based `BUY_MORE`, `HOLD`, `TRIM`, `SELL`, `STRONG_SELL` and `REVIEW` assessments for holdings
- Investment-thesis and decision-journal fields
- Portfolio reconciliation report
- PostgreSQL database in Docker
- FastAPI backend and Streamlit user interface
- Database backup and restore scripts
- Automated tests for core portfolio calculations
- Vendor-neutral market-data and company-research provider interfaces
- CSV adapters for end-of-day prices and company metadata

This software is for personal research and decision support. It does not guarantee returns and does not place orders.

The main dashboard is stock-centric: **Owned stocks** and **Prospective · Strong Buy** are the two
top-level tabs. Each descriptive analysis tab contains the complete, consistently ordered stock
list—no stock selection is required—and scrolls vertically when the list exceeds the table viewport. The
**Portfolio & Thesis**, **Data & Freshness**, **Financial Analysis**, **Investor Style Fit**, and
**Followed Investors** tabs, plus a final **Summary & Recommendation** tab. Task pages remain available for imports, data
sync, thesis editing, screening, configuration, backtesting, and disclosure management.

Dashboard analysis is materialized by a separate scheduler service. One morning orchestrator runs
Tuesday through Saturday at `06:00 Asia/Kolkata` by default and invokes each activity only when its
configured evidence cycle is due. It retrieves the latest Upstox close on or before the preceding
calendar day, refreshes due company statements in batches, advances a resumable NIFTY 500 screening
cycle, checks semi-annual constituent freshness, ingests due followed-investor exchange filings,
and finally rebuilds the cached dashboard snapshot. The default 10-company NIFTY batch
completes the 500-stock universe in approximately 10 weeks.

The dashboard exposes every activity's last status and a **Forced run scope** control. Configure the
orchestration time with `ANALYSIS_SCHEDULE_DAYS`, `ANALYSIS_SCHEDULE_HOUR`,
`ANALYSIS_SCHEDULE_MINUTE`, and `ANALYSIS_SCHEDULE_TIMEZONE`; configure API budgets with
`FUNDAMENTALS_BATCH_SIZE` and `SCREENING_BATCH_SIZE`. Company evidence is stored only when the
provider returns a change and is not marked complete until it covers the newly due reporting period.
Investor disclosures are discovered and parsed from official exchange sources in restart-safe
batches; source-linked CSV remains the recovery path when an exchange blocks unattended access or
a format is unsupported. See
[Scheduled automation](docs/SCHEDULED_AUTOMATION.md) for the complete cadence, configuration,
forced-run scopes, and failure behavior.

Scheduler reliability is persisted rather than inferred only from container logs. A heartbeat,
15-minute start grace, 90-minute stall threshold, bounded 30/60-minute activity retries, and
48-hour restart catch-up are enabled by default. The dashboard shows overdue/degraded status and a
20-run history while continuing to serve the last successful snapshot.

## Architecture

```text
Browser
  |
  v
Streamlit UI :8501
  |
  v
FastAPI :8000
  |
  v
PostgreSQL + pgvector
```

Recommendation rules remain deterministic. Phase 5 uses an LLM only as a schema-validated parser
fallback after deterministic XBRL extraction; model output never changes recommendation rules.

## Prerequisites on Windows

1. Windows 10/11 with WSL 2 enabled.
2. Rancher Desktop, Podman Desktop, or Docker Desktop.
3. Docker-compatible CLI and Docker Compose.
4. PowerShell 7 is recommended, but Windows PowerShell also works.

## First-time setup

Open PowerShell in the repository folder:

```powershell
Copy-Item .env.example .env
.\start.ps1
```

Open:

- User interface: http://localhost:8501
- API documentation: http://localhost:8000/docs
- Health endpoint: http://localhost:8000/health

The first startup builds the images and creates database tables automatically.

## Suggested first workflow

1. Open **Portfolio Setup**.
2. Download or use `imports/opening_portfolio_template.csv`.
3. Import your opening holdings.
4. Open **Prices** and enter current prices.
5. Open **Dashboard** to verify holdings and returns.
6. Use **Transactions** for subsequent buys, sells and dividends.
7. Use **Thesis & Review** to capture the reason for holding a stock and review recommendations.
8. Periodically use **Reconcile** to compare calculated holdings with a broker-export CSV.

Company metadata can be refreshed through `POST /imports/company-research` using
`imports/company_research_template.csv`. Blank optional fields preserve existing values.

### Upstox Analytics integration

Set `UPSTOX_ANALYTICS_TOKEN` in `.env` to a read-only Upstox Analytics Token, then restart the
Compose stack. The Prices page can sync daily historical closes and company profiles for NSE/BSE
instruments that have an ISIN. No trading endpoints are implemented or called. The status and sync
endpoints are `GET /providers/upstox` and `POST /providers/upstox/sync`.

### Financial analysis

The **Financial Analysis** page works for held and prospective NSE/BSE equities. Search Upstox to
add an unowned company without creating a transaction, then refresh its annual and quarterly
statements, ratios, ownership data, corporate actions, and competitors. Scores are transparent
review aids; bank/NBFC/insurance companies are excluded from industrial leverage and cash-quality
rules. Valuation bands build from dated observations and disclose when history is insufficient.

### Investor styles and backtesting

The **Investor Styles** page evaluates version-controlled YAML rule sets in
`services/api/app/investor_styles/`. Historical decisions use only annual fundamentals assumed
available 120 days after period end, then measure forward returns from observable Upstox closes.
Every rule exposes its observed value, threshold, weight, and pass/fail result. Small samples,
reporting-lag assumptions, and omitted costs/dividends are displayed with every backtest.

### NIFTY 500 prospective-stock screening

The **NIFTY 500 screening** tab refreshes official NIFTY 100, NIFTY Midcap 150, and NIFTY
Smallcap 250 constituent files, preserving Large/Mid/Small labels. Screening is resumable in small
batches because each non-owned company requires multiple Upstox Analytics fundamentals requests.
Every result is retained in a dated audit, but only companies currently satisfying the deterministic
`STRONG_BUY` gate enter **Prospective Stocks**. Index membership is a candidate filter, not a
recommendation, and the application never places an order.

### Followed investor signals (Phase 5)

Phase 5 maintains a versioned list of followed public-market investors and a dated disclosure
ledger. The scheduled workflow discovers official NSE/BSE shareholding filings, caches the source
document, extracts inline-XBRL deterministically, validates attributable holdings, and produces new,
increased, reduced, unchanged, and explicitly reported exit signals in a stock-by-investor matrix.
Every observation retains its source URL and stale-data status. These signals are corroborating
evidence only and never override Phase 4's deterministic recommendation rules.
The versioned default configuration now contains 15 enabled profiles: Vijay Kedia, Ashish Kacholia,
Mukul Agrawal, Dolly Khanna, Rekha Jhunjhunwala, Akash Bhanshali, Ashish Dhawan, Nemish Shah,
Madhusudan Kela, Sunil Singhania, Anil Kumar Goel, Radhakishan Damani, Porinju Veliyath,
Mohnish Pabrai, and Ramesh Damani. Profiles and aliases can be disabled or edited in
`services/api/app/followed_investors/india_public_investors.yaml`; inclusion is not an endorsement.

Exact configured aliases are accepted automatically. Fuzzy or ambiguous shareholder names enter a
review queue and do not become evidence until approved. New/revised disclosures and review requests
create in-app notifications; set `NOTIFICATION_WEBHOOK_URL` to an HTTPS endpoint for optional
external delivery. The page also manages NSE symbols and six-digit BSE scrip-code mappings and can
force a selected reporting-quarter batch.

Deterministic parsing runs before model use. The optional fallback order is local Ollama and then
OpenRouter. Set `OPENROUTER_API_KEY`; the pinned default is
`google/gemma-4-26b-a4b-it:free`. `OPENROUTER_MODEL` remains configurable, but the random
`openrouter/free` router is rejected to preserve reproducibility. The CSV template remains an
auditable recovery route.

### Later-phase portfolio views

The roadmap defines a separate **Notional Portfolio** with its own cash/transaction ledger for
forward-testing accepted recommendations against the actual portfolio and a benchmark. It also
defines a separate **IPOs** lifecycle view for official pre-IPO evidence and limited-history
monitoring during the first listed year. These views are planned, not present in the current UI;
their evidence and anti-look-ahead requirements are documented in [EVOLUTION.md](docs/EVOLUTION.md).

## Stop

```powershell
.\stop.ps1
```

## Backup

```powershell
.\backup.ps1
```

Backups are written to `backups/`.

## Restore

```powershell
.\restore.ps1 -BackupFile .\backups\portfolio_YYYYMMDD_HHMMSS.sql
```

Restore replaces the database contents. Stop the UI/API before restoring if other users are connected.

## Reset development database

```powershell
docker compose down -v
docker compose up --build
```

This permanently deletes local database data.

## Run tests without Docker

From `services/api`:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:DATABASE_URL="sqlite+pysqlite:///:memory:"
pytest
```

## Important limitations

- End-of-day prices can be synced from Upstox Analytics or entered manually/imported from CSV.
- Corporate actions are represented as explicit transactions; automated corporate-action processing is not included.
- FIFO is used for realised P&L in the MVP.
- Taxes are recorded as transaction charges but tax reporting is not implemented.
- Recommendations are rule-based research prompts, not investment advice.
- Portfolio-level backtesting, investor digital twins, and grounded report/transcript analysis
  belong to later phases.
