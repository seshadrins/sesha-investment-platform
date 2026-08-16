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
- Transparent rule-based `ADD`, `HOLD`, `TRIM`, `SELL` and `REVIEW` assessments
- Investment-thesis and decision-journal fields
- Portfolio reconciliation report
- PostgreSQL database in Docker
- FastAPI backend and Streamlit user interface
- Database backup and restore scripts
- Automated tests for core portfolio calculations
- Vendor-neutral market-data and company-research provider interfaces
- CSV adapters for end-of-day prices and company metadata

This software is for personal research and decision support. It does not guarantee returns and does not place orders.

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

The recommendation rules are deterministic. A local LLM is intentionally excluded from the first runnable MVP; an optional Ollama integration point is documented under `docs/EVOLUTION.md`.

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

- End-of-day prices must initially be entered manually or imported from CSV.
- Corporate actions are represented as explicit transactions; automated corporate-action processing is not included.
- FIFO is used for realised P&L in the MVP.
- Taxes are recorded as transaction charges but tax reporting is not implemented.
- Recommendations are rule-based research prompts, not investment advice.
- Backtesting, investor digital twins, automated filings ingestion and local LLM analysis belong to later phases.
