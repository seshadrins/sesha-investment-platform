# Scheduled automation

The `analysis-scheduler` container owns one morning orchestration job. By default it starts at
`06:00 Asia/Kolkata` every Tuesday through Saturday. Every activity below is evaluated by that
single job; an activity either runs, reports that its evidence is current, or records why it could
not run. The dashboard is rebuilt after the checks finish.

## Morning run order

| Order | Activity | Due policy | Default work per run |
|---:|---|---|---:|
| 1 | Previous-close prices | Every Tuesday–Saturday | All eligible owned and active prospective stocks |
| 2 | NIFTY 500 membership | First scheduled run after the April and October boundaries | Exactly 500 constituents |
| 3 | Owned/prospective financial statements | After a financial-reporting period becomes due | 10 unchecked companies |
| 4 | NIFTY 500 evidence and screening | Once per newly due financial-reporting period | 10 non-owned companies |
| 5 | Followed-investor disclosures | After the quarterly disclosure filing window | 10 exchange source mappings |
| 6 | Stock workbench snapshot | After the other actions finish | All owned and Strong Buy prospective stocks |

### Previous-close prices

At 06:00, the target is the preceding calendar day. The Upstox adapter requests a ten-day candle
window and stores the latest close on or before that target. This makes Tuesday pick Monday,
Saturday pick Friday, and safely handles exchange holidays. It does not request an intraday price
or place an order.

### Financial statements

The scheduler opens a new evidence cycle only after the normal SEBI filing window has elapsed:
45 days after June, September, and December quarter-end, and 60 days after the March financial
year-end. It prioritizes owned stocks and active prospective stocks.

For each company, the scheduler compares the returned Upstox bundle with stored evidence and
extracts the latest reporting date. It stores changed evidence only. A company is complete for the
cycle only when the provider evidence covers the newly due reporting period. Older unchanged data
remains pending and is retried after other unchecked companies, so a provider delay cannot be
mistaken for a new result.

### NIFTY 500 screening

The first run for a newly due financial period starts a new full-universe cycle. Each morning batch
is interleaved across Large, Mid, and Small cap memberships, refreshes Upstox fundamentals, stores
the evidence, evaluates every configured investor style, and records a dated recommendation audit.
Owned companies are excluded from the prospective funnel. Only `STRONG_BUY` results are promoted
to the active prospective list.

The default batch of 10 companies on five mornings per week completes approximately 500 companies
in 50 runs, or about 10 weeks. Progress is keyed to the financial period, so a restart or the next
morning continues where the previous run stopped instead of returning to the first symbol.

### NIFTY membership

NIFTY 500 membership is checked against an April/October boundary and refreshed on the first
configured morning after it becomes due. The import requires exactly 500 unique constituents and
retains the Large/Mid/Small cap labels. NSE Indices publishes NIFTY 500 changes semi-annually,
effective on the last working day of March and September.

### Followed-investor disclosures

The scheduler determines the latest quarter whose configured 21-day shareholding-pattern filing
window has elapsed, then processes the next restart-safe exchange-source batch. NSE and BSE
adapters use official HTTPS hosts with polite throttling. Downloaded documents are cached with a
content hash and parser version. Inline-XBRL/XML extraction is deterministic; only documents that
cannot be parsed this way use Ollama and then the pinned OpenRouter fallback.

Exact configured aliases are imported automatically. Fuzzy or ambiguous matches enter the review
queue, and new/revised validated observations create notifications. The job then compares stored
dates for all followed investors and reports `CURRENT` or `ACTION_REQUIRED`. It never invents a
zero holding or infers an exit from a missing row. NSE access failures are recorded without
bypassing anti-bot controls; a BSE scrip-code mapping or source-linked CSV is the recovery path.

## Configuration

All times use `ANALYSIS_SCHEDULE_TIMEZONE`.

| Variable | Default | Purpose |
|---|---|---|
| `ANALYSIS_SCHEDULE_DAYS` | `tue-sat` | Days on which the morning orchestrator runs |
| `ANALYSIS_SCHEDULE_HOUR` | `6` | Morning start hour |
| `ANALYSIS_SCHEDULE_MINUTE` | `0` | Morning start minute |
| `ANALYSIS_SCHEDULE_TIMEZONE` | `Asia/Kolkata` | Scheduler time zone |
| `ANALYSIS_RUN_ON_STARTUP` | `true` | Build an availability snapshot and price refresh when the worker starts |
| `FUNDAMENTALS_BATCH_SIZE` | `10` | Owned/prospective companies checked per due-period run |
| `SCREENING_BATCH_SIZE` | `10` | NIFTY 500 companies processed per due-period run |
| `UNIVERSE_SCHEDULE_MONTHS` | `4,10` | Membership refresh boundaries |
| `INVESTOR_DISCLOSURE_LAG_DAYS` | `21` | Delay after quarter-end before disclosure coverage is expected |
| `DISCLOSURE_AUTO_INGEST_ENABLED` | `true` | Run official exchange ingestion during the morning workflow |
| `DISCLOSURE_BATCH_SIZE` | `10` | Exchange source mappings processed per run |
| `DISCLOSURE_REQUEST_INTERVAL_SECONDS` | `1.0` | Minimum delay between exchange requests |
| `DISCLOSURE_CACHE_HOURS` | `24` | Minimum interval before an unchanged source mapping is checked again |
| `DISCLOSURE_FUZZY_MATCH_THRESHOLD` | `0.84` | Minimum similarity for a possible alias to enter review |
| `DISCLOSURE_LLM_PROVIDER` | `auto` | `auto`, `ollama`, `openrouter`, or `disabled` parser fallback policy |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | Local Ollama endpoint visible to Docker |
| `OLLAMA_MODEL` | `qwen2.5:7b-instruct` | Pinned local structured-output model |
| `OPENROUTER_API_KEY` | empty | Enables the secondary OpenRouter fallback |
| `OPENROUTER_MODEL` | `google/gemma-4-26b-a4b-it:free` | Pinned OpenRouter fallback; random routing is rejected |
| `NOTIFICATION_WEBHOOK_URL` | empty | Optional HTTPS webhook for disclosure and automation notifications |
| `AUTOMATION_ALERTS_ENABLED` | `true` | Create alerts for unhealthy and recovered scheduled runs |
| `ANALYSIS_START_GRACE_MINUTES` | `15` | Delay before a non-started expected run becomes missed/overdue |
| `ANALYSIS_STALL_MINUTES` | `90` | Maximum running time before a run is marked stalled |
| `ANALYSIS_CATCHUP_MAX_HOURS` | `48` | Maximum age for automatic catch-up after downtime |
| `ANALYSIS_RETRY_DELAYS_MINUTES` | `30,60` | Bounded retry delays for failed activities |
| `SCHEDULER_HEARTBEAT_SECONDS` | `60` | Heartbeat interval used by health monitoring |

After changing `.env`, rebuild or restart the API and scheduler containers so both processes receive
the same settings.

The dashboard can persistently override the default days, time, IANA time zone, and enabled state.
The worker validates and applies this database-backed configuration within 30 seconds, so later
schedule edits do not require a container restart.

## Forced runs

The dashboard's **Forced run scope** control supports:

- all due morning activities;
- prices only;
- due financial statements;
- the next NIFTY 500 batch;
- an immediate constituent download;
- the investor-disclosure ingestion and coverage batch; or
- a dashboard-only snapshot rebuild.

The matching API is `POST /analysis-schedule/run?job=<scope>`. A forced financial run still obeys
the due-period and evidence rules. A forced constituent run intentionally bypasses the semi-annual
freshness check.

## Persistence and failures

Every invocation has a durable run-history record with its expected time, trigger, attempt,
`RUNNING`, `SUCCESS`, `PARTIAL`, `FAILED`, `MISSED`, or `STALLED` status, action summary, timestamps,
and error. Each activity also retains its independent last result. One failed activity does not
prevent later activities from being attempted.

The scheduler writes a heartbeat every 60 seconds. If an expected run has not started 15 minutes
after its configured time, the watchdog records `MISSED` and queues one catch-up using the original
scheduled timestamp. Container startup performs the same check for runs no more than 48 hours old.
If a run exceeds 90 minutes it is marked `STALLED`. Failed activities—not successful ones—receive
bounded retries after 30 and 60 minutes. An all-source disclosure failure is retried; missing source
mappings, parser review, and disclosure `ACTION_REQUIRED` states remain visible for user
intervention rather than retrying indefinitely.

Disclosure coverage uses four distinct states: `IN_PROGRESS` while active mappings remain unchecked;
`CURRENT` when the cycle is complete and each configured profile has attributable evidence;
`NO_ATTRIBUTABLE_DISCLOSURE` when the cycle completed without evidence for one or more profiles; and
`ACTION_REQUIRED` only for failed mappings, parser failures, pending alias reviews, or disabled
automated ingestion. Each result includes checked/remaining mapping counts and a per-investor state.

The Compose service has a heartbeat healthcheck, the API reports calculated schedule health, and
the dashboard displays a prominent recovery banner plus the latest 20 runs. If the entire computer
or Docker engine is off, no local process can alert while it is off; catch-up begins when the
scheduler next starts within the configured recovery window.

The dashboard reports rolling 30-day success rate, recovery count, duration percentiles, and
per-activity attempts and failures. Missed, failed, stalled, partial, and recovered runs create
durable in-app alerts. When `NOTIFICATION_WEBHOOK_URL` is configured, those alerts use the existing
retryable HTTPS notification delivery path.

The dashboard continues to serve the last successful workbench snapshot if a rebuild fails.
Screening results and fundamental evidence are committed company by company, allowing a later run
to resume safely without repeating completed period-keyed work.

## Timing basis

- [SEBI expert committee report](https://www.sebi.gov.in/sebi_data/commondocs/jun-2024/Expert%20Committee%20report%20on%20ICDR%20and%20LODR-new_p.pdf): Regulation 33 reporting windows and Regulation 31 shareholding-pattern timing.
- [NSE Indices reconstitution calendar](https://niftyindices.com/resources/index-rebalancing-schedule): NIFTY 500 semi-annual reconstitution.

These timings are scheduling defaults for research evidence, not legal advice or a guarantee that a
particular provider publishes data immediately at the regulatory deadline.
