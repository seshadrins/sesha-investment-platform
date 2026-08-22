---
name: project-context
description: Canonical, load-bearing context for the Personal AI Portfolio Manager MVP — architecture, key files, docs index, dev/test/verify workflows, and hands-on gotchas. Read before non-trivial work in this repo. Shared verbatim between Claude Code (via CLAUDE.md) and Codex (via AGENTS.md) — update it, not them, whenever something below goes stale.
---

# Project context — Personal AI Portfolio Manager

This file is the single source of truth for reusable, repo-wide context. `CLAUDE.md` and
`AGENTS.md` at the repo root both just point here so Claude Code and Codex stay in sync from
one file instead of two drifting copies. **Update this file in the same change** whenever
architecture, endpoints, docker/compose config, testing approach, conventions, or the docs
index below actually change — treat it as living, not a one-time snapshot. Small, factual
edits only; don't let it grow into a second README.

## What this is

A self-hosted, Windows-first portfolio ledger and decision-support app — FIFO holdings,
rule-based recommendations, thesis/decision journal, financial analysis, investor-style
screening, followed-investor disclosure signals, document analysis, a notional (simulated)
portfolio, and IPO tracking. It never places real orders. Full detail lives in
[README.md](../../../personal-ai-portfolio-mvp/README.md) — don't duplicate it here, just point
to it.

The actual project root is `personal-ai-portfolio-mvp/` (this skill's own repo also holds two
unrelated personal portfolio CSV exports one level up, at the git root — not part of the app).

## Architecture

```
Browser → Streamlit UI :8501 → FastAPI :8000 → PostgreSQL + pgvector
                                     ^
                          analysis-scheduler (built from the same source
                          as api, but a SEPARATE image — see gotcha below)
```

Docker Compose services (`personal-ai-portfolio-mvp/compose.yaml`): `db`, `api`,
`analysis-scheduler`, `ui`. Recommendation rules are deterministic; LLMs (Ollama first, then
OpenRouter) are used only as schema-validated parsing fallbacks or draft-commentary
generators — never to change a recommendation.

## Key files

- `services/api/app/main.py` — FastAPI routes.
- `services/api/app/services.py` — `portfolio_snapshot()` / core ledger math, backs `GET /portfolio`.
- `services/api/app/automation_pipeline.py` — `_build_stock_workbench()` (backs `GET
  /stock-workbench`, what the dashboard actually renders from), scheduled job bodies, and the
  cached-snapshot read/write helpers (see gotcha below).
- `services/ui/app.py` — dashboard entrypoint (per-account filter, summary tiles).
- `services/ui/workbench_view.py` — shared table/tab rendering (`render_scope`, `portfolio_tab`,
  `summary_tab`, etc.) used by **both** the main dashboard and the Notional Portfolio page — a
  change here affects both.
- `services/ui/pages/` — one Streamlit page per sidebar entry (`N_Name.py`, sidebar order
  follows the numeric prefix).
- `services/api/app/investor_styles/*.yaml` — versioned investor-style rule sets.
- `services/api/app/followed_investors/india_public_investors.yaml` — followed-investor config.
- `services/api/tests/`, `services/ui/tests/` — pytest suites.

## Docs index (`docs/`)

- `DESIGN.md` — design goals and module boundaries.
- `EVOLUTION.md` — phase-by-phase implementation register with Implemented/In progress/Planned
  status; reconciled against the running app.
- `SCHEDULED_AUTOMATION.md` — the morning orchestration job: order, due policy, config env vars.
- `Recommendations.md` — independent review findings from reading the full source + driving the
  live app; the source of the `Changes-SetA.md` / `Changes-SetB.md` phased plans below.
- `UserGuide.md` — plain-language walkthrough for end users, not for agents.
- `Changes-Set*.md` — the working convention for proposed changes: investigate against the
  running code (and live stack where relevant) *before* proposing anything, group into phases
  by dependency/blast-radius, and for each item state what was found, the concrete plan, and
  "How to verify". Nothing in one of these docs is implemented until actually done — implement
  and verify phase by phase, don't jump ahead. Matching commits are titled
  `Changes-SetX - Phase N done`. Start a new `Changes-Set*.md` rather than appending forever to
  an old one once it's fully implemented.

## Dev workflow gotchas (learned hands-on, keep current)

- **Docker images bake in the code — there are no live volume mounts for `api`/`ui`/
  `analysis-scheduler` in `compose.yaml`.** After editing anything under `services/api` or
  `services/ui`, a plain `docker compose restart` reuses the stale image and silently no-ops
  your change. Always: `docker compose build api ui && docker compose up -d api ui`.
  **`analysis-scheduler` builds from the same `services/api` source but is its own image —
  rebuilding `api` does NOT rebuild it.** It's easy to forget since it never needs touching
  for a UI-only change, but any backend edit does need
  `docker compose build analysis-scheduler && docker compose up -d analysis-scheduler` too —
  it runs `ANALYSIS_RUN_ON_STARTUP` on every recreate plus a `tue-sat 06:00 Asia/Kolkata` job,
  and *every* run calls `_store_workbench_snapshot()`, overwriting the same cached
  `AnalysisSnapshot` row `api` serves from. Forgetting this doesn't error at deploy time — it
  silently reintroduces old bugs (a stale-shaped payload, e.g. missing a field a recent phase
  added) the next time the scheduler's job fires, which can be hours later and look like a
  regression in whatever shipped most recently, even though that code is fine. If a payload
  shape looks wrong after a deploy that should have fixed it, check
  `docker compose ps --format "table {{.Name}}\t{{.CreatedAt}}"` for a stale
  `analysis-scheduler` before debugging the application code.
- **`GET /stock-workbench` is served from a cached `AnalysisSnapshot` row (`snapshot_key =
  "stock_workbench"`), not rebuilt per request.** After a backend change that alters its
  payload shape, force a rebuild before verifying via curl or the UI:
  `POST /analysis-schedule/run?job=snapshot` (cheap, no external calls) — or `job=prices` /
  `job=morning` etc. if the change also depends on fresher source data.
- **Tests, inside the running containers:**
  `docker compose exec -T api python -m pytest tests -q` and
  `docker compose exec -T ui python -m pytest tests -q`. A no-Docker venv path for the API
  suite is documented in `README.md` under "Run tests without Docker".
- **Visual/browser verification:** no project skill or browser-automation tool is pre-installed.
  Ad hoc approach that has worked twice now: `python -m pip install playwright && python -m
  playwright install chromium`, then a short sync-API script that screenshots
  `http://localhost:8501` (and specific `pages/...` routes). Worth promoting to a real project
  skill via `/run-skill-generator` if this keeps recurring.
- **Git root is the parent directory**, one level above `personal-ai-portfolio-mvp/` — don't
  assume the repo root and the project root are the same directory.
