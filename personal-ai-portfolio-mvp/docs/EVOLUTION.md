# Evolution Roadmap

This document is the implementation register for the product. Status meanings:

- **Implemented** — available in the running Docker application and covered by verification.
- **In progress** — the data model or user workflow exists, but an identified adapter or automation
  remains to be completed.
- **Planned** — agreed direction with no production implementation yet.

Last reconciled with the running application: **2026-08-17**.

| Phase | Scope | Status |
|---|---|---|
| 1 | Portfolio ledger, holdings, recommendations, thesis, and reconciliation | Implemented |
| 2 | Market/company data adapters and Upstox Analytics | Implemented |
| 3 | Fundamentals, valuation, governance, and financial analysis | Implemented |
| 4 | Investor styles, NIFTY 500 screening, shortlist, and backtests | Implemented; reporting-period screening is resumable |
| 5 | Followed-investor disclosure signals | Implemented; official-source ingestion, validated parser fallbacks, alias review, and notifications are operational |
| 6 | Grounded annual-report/transcript analysis | Planned |
| 7 | Scheduled analysis, monitoring, and notifications | In progress; orchestration, persistent run monitoring, recovery, and forced runs implemented; external notifications remain |
| 8 | Notional portfolio, portfolio-level backtesting, and learning | Planned |
| 9 | IPO lifecycle analysis before listing and through the first listed year | Planned |

## Current verified release snapshot

- All Docker services are healthy, including the scheduler's application-level health check.
- The automated suite passes **39 tests**, and every Streamlit page renders without an application
  exception.
- Live official-source checks discovered and parsed both NSE plain XBRL and BSE inline-XBRL. A
  production June 2026 batch checked 10 mappings, processed its discovered filing, and completed
  without a source or persistence error.
- Scheduler recovery has been exercised against the running stack: a missed scheduled run was
  recorded, catch-up ran only the due work, an initially failed NIFTY 500 activity was retried
  independently, and schedule health returned to `HEALTHY`.
- The most recent successful snapshot remains available when a later refresh is partial or fails.

## Phase 1 — portfolio ledger and review

Status: implemented with an immutable transaction ledger, FIFO lot accounting, holdings,
portfolio reconciliation, thesis tracking, decision journal, and deterministic owned-stock
prompts: `BUY_MORE`, `HOLD`, `REVIEW`, `TRIM`, `SELL`, and `STRONG_SELL`. A hard thesis
invalidation produces `STRONG_SELL`; the application never places an order.

Implemented capabilities:

- Multiple portfolio accounts with broker and currency metadata.
- Opening-position CSV import and subsequent `BUY`, `SELL`, `DIVIDEND`, `ADJUSTMENT_IN`, and
  `ADJUSTMENT_OUT` transactions.
- Atomic oversell protection, transaction charges, FIFO realised profit, remaining cost,
  unrealised profit, dividends, holding period, and portfolio weights.
- Consolidated dashboard with account filtering, allocation, price-date freshness, unpriced-stock
  warnings, holdings detail, and an action centre explaining every prompt.
- Manual prices, price history, complete ledger, and CSV export/import workflows.
- Versioned investment theses with `ACTIVE`, `WATCH`, and `INVALID` states.
- Decision journal retaining recommendation rationale and the user's eventual decision.
- Broker-position CSV reconciliation without changing the ledger.
- Database backup and restore scripts.

## Phase 2 — market and company research adapters

Status: implemented with CSV and read-only Upstox Analytics adapters. Price imports use
`MarketDataProvider`, while company metadata imports use `CompanyResearchProvider`. Upstox sync
resolves stable instrument keys from ISINs, imports reproducible daily historical closes, and
stores company profiles without exposing trading capability.

Provider interfaces remain decoupled from the core ledger so licensed adapters can be added
without changing portfolio accounting.

Implemented capabilities:

- Manual price entry and vendor-neutral price/company-research CSV adapters.
- Read-only Upstox Analytics status, instrument search, daily price, company profile, fundamentals,
  and historical candle integrations.
- Stable NSE/BSE identification using exchange, symbol, and ISIN.
- Provider error capture and partial-result handling for optional datasets.
- Candidate isolation: the 500-stock screening pool does not flood transaction selectors or the
  normal all-portfolio Upstox sync.
- Explicit absence of authentication flows or endpoints that can place, modify, or cancel orders.

## Phase 3 — financial analysis

Status: implemented using read-only Upstox fundamentals with annual and quarterly trends,
sector-aware ROCE/ROE/leverage/cash-conversion scoring, dated valuation observations,
evidence-limited governance flags, company discovery, and versioned theses.

Financial-sector companies are explicitly routed away from industrial leverage and cash-quality
rules until bank/NBFC/insurance-specific criteria are configured.

Implemented capabilities:

- Annual and quarterly income-statement trends, balance sheet, cash flow, ratios, shareholding
  categories, corporate actions, and available competitor evidence.
- Calculated operating margin, net margin, liabilities/assets, and operating-cash/net-profit
  measures.
- Sector-aware financial-quality sub-scores and an explainable overall score.
- Company-versus-sector ratios and dated valuation snapshots with observed range/median readiness.
- Evidence-bounded governance warnings; no inferred or fabricated competitor list.
- Search and individual research for a stock without creating a portfolio transaction.
- Separate Owned, Prospective Strong Buy, and Other Research/Candidate views.
- User-readable missing-data, stale-evidence, sector-inapplicability, and provider-error states.

Next extension:

- Add dedicated bank, NBFC, and insurance measures such as asset quality, capital adequacy,
  provisioning, and underwriting/solvency indicators.

## Phase 4 — investor styles and NIFTY 500 screening

Status: implemented with version-controlled YAML styles, rule-level evidence, conservative
point-in-time reporting lags, Upstox historical daily prices, and disclosed single-stock
forward-return backtests.

The candidate universe is the official NIFTY 500, assembled from NIFTY 100, NIFTY Midcap 150,
and NIFTY Smallcap 250 constituent files. Screening is resumable and interleaves Large, Mid, and
Small cap candidates. Owned stocks are excluded. Every evaluated company is retained in a dated
audit, while only non-owned companies satisfying the deterministic `STRONG_BUY` gate enter the
Prospective Stocks view.

Implemented capabilities:

- Versioned YAML definitions for Quality Compounder, Growth at Quality, and Capital Preservation.
- Configurable rule thresholds, weights, minimum evidence coverage, and match scores.
- Stock-as-rows/style-as-columns matrices, with separate Owned and Prospective views.
- Rule-level observed value, comparison operator, threshold, weight, and pass/fail/no-data result.
- Conservative 120-day annual-report availability lag in historical evaluations.
- Upstox daily-price backtests with configurable forward horizon and disclosed limitations.
- Official constituent refresh with a hard 500-company integrity check and Large/Mid/Small labels.
- Balanced restart-safe batches, owned-stock exclusion, reporting-period screening progress,
  same-day audit upserts, criteria-version capture, and downloadable audit results.
- Strong Buy gate requiring financial quality, multiple style matches, supportive sector-relative
  valuation, and no high-severity governance flag.
- Ordinary research/watchlist entry cannot bypass the Strong Buy gate.

## Phase 5 — followed investor signals

Status: implemented. The versioned 15-investor configuration, disclosure ledger, automated
official-exchange ingestion, deterministic inline-XBRL/XML extraction, validated model fallbacks,
alias review, activity engine, notifications, API, UI, CSV recovery path, and scheduled quarterly
refresh are operational.

Phase 5 follows a versioned, user-editable list of public-market investors. The starter
configuration contains 15 enabled public-market profiles, including Vijay Kedia, Ashish Kacholia,
Mukul Agrawal, Dolly Khanna, Rekha Jhunjhunwala, Akash Bhanshali, Ashish Dhawan, Nemish Shah,
Madhusudan Kela, Sunil Singhania, Anil Kumar Goel, Radhakishan Damani, Porinju Veliyath,
Mohnish Pabrai, and Ramesh Damani, with disclosure-name aliases. Inclusion is not an endorsement,
performance ranking, or prediction of future performance.

Implemented capabilities:

- Validated CSV import of attributable, dated investor disclosures.
- Required HTTPS source link for every observation.
- Quarter-over-quarter `NEW_DISCLOSURE`, `INCREASED`, `UNCHANGED`, `REDUCED`, and
  `EXIT_REPORTED` signals.
- Configurable materiality and stale-data thresholds.
- Stock-by-investor matrix and source-linked activity feed.
- Owned, Prospective, and Research context labels.
- Scheduler-driven quarterly disclosure coverage checks after the configured filing lag.
- Polite, throttled official NSE/BSE source adapters, restart-safe batches, cached source documents,
  content hashes, source mappings, parser versions, and per-source error status.
- Deterministic inline-XBRL/XML parsing before any model is invoked.
- Exact configured aliases are accepted automatically; fuzzy or ambiguous names enter a user
  review queue and do not become evidence until approved.
- In-app notifications for new/revised validated disclosures and alias-review requests, with an
  optional HTTPS webhook outbox.
- Strict separation from Phase 4 recommendations: followed-investor activity is corroborating
  evidence only and cannot create a Strong Buy.

Disclosure acquisition controls:

1. Prefer deterministic NSE/BSE shareholding-pattern XBRL or structured filing extraction.
2. Retain CSV import as the recovery and reconciliation path.
3. Do not scrape SEBI's corporate-filings directory as if it were the underlying dataset; SEBI
   directs users to the NSE and BSE filing systems.
4. Respect exchange access controls, terms, throttling, and source attribution. Never attempt to
   bypass anti-bot protections.
5. Preserve the original filing URL, reported period, filing date, parsed fields, and parser
   version for every automated observation.

Important evidence limitations:

- Shareholding patterns are periodic rather than live trade feeds.
- A missing name is not proof of an exit; sub-threshold holdings may no longer be individually
  disclosed.
- Similar names, family holdings, trusts, and investment entities require explicit alias mapping.
- Only an explicit zero/exit observation may produce `EXIT_REPORTED`.

### Phase 5 LLM policy

LLMs are optional document parsers, never sources of record.

- Primary optional parser: local Ollama, using a pinned model, temperature `0`, and a JSON schema.
- Secondary fallback: OpenRouter using the configurable pinned
  `google/gemma-4-26b-a4b-it:free` model and a privacy-compatible provider route.
- Do not use OpenRouter's random free-model router for reproducible extraction.
- Validate all model output with Pydantic and deterministic business rules.
- Reject unsupported investor names, invalid dates/percentages, missing source links, and facts not
  traceable to the supplied filing.
- Store the provider, model, prompt/schema version, and validation result with automated imports.
- Require user review for ambiguous investor/entity matches.

Ollama remains the first optional fallback because local structured output avoids per-document cost
and works offline. If Ollama is unavailable or the pinned local model is missing, the application
uses the pinned OpenRouter model. The random `openrouter/free` router is prohibited because it
would make parser behavior irreproducible. Provider, model, parser/schema version, validation
result, document hash, and source URL are retained.

NSE can refuse unattended requests under its normal access controls. The adapter records that
failure without bypassing anti-bot controls; a user can map the stock's six-digit BSE scrip code to
use BSE's official iXBRL filing path, or use the source-linked CSV recovery workflow.

## Phase 6 — grounded document analysis

Status: planned. No production document-ingestion or citation workflow exists yet.

Extend the Ollama-first/OpenRouter-fallback adapter to user-imported annual reports and transcripts:

- Summarise reports with citations to stored document sections.
- Extract catalysts, risks, and invalidation conditions into reviewable drafts.
- Generate explanations grounded in stored evidence.
- Never use an LLM for arithmetic, lot accounting, or hard risk gates.

## Phase 7 — scheduled analysis, monitoring, and alerts

Detailed operating contract: [Scheduled automation](SCHEDULED_AUTOMATION.md).

Status: in progress. A dedicated APScheduler Docker worker now runs one configurable morning
orchestrator (default Tuesday–Saturday at `06:00 Asia/Kolkata`). The orchestrator conditionally
refreshes prior-close prices, newly due financial evidence, a rolling NIFTY 500 batch, semi-annual
index membership, and quarterly investor-disclosure coverage before materializing the complete
stock workbench. Persistent heartbeat and run records support missed/overdue detection, bounded
activity-only retries, and restart catch-up. The dashboard serves the last successful snapshot,
displays its generation time, and continues serving that snapshot if a later refresh fails. A
cold-start calculation exists only as an availability fallback before the worker's first successful
run.

Implemented boundaries:

- One scheduler process, one explicit morning job ID, coalescing, and single-instance execution.
- Schedule configured by environment variable with an explicit time zone.
- User-invoked forced scopes for the full morning workflow or an individual activity.
- Each scheduled or forced run first uses read-only Upstox historical candles to store the latest
  EOD price on or before the preceding calendar day for normal portfolio-sync instruments.
- Financial-period detection based on the normal 45-day quarterly and 60-day annual result windows;
  unchanged older Upstox evidence remains pending rather than being presented as a new result.
- A configurable, cap-segment-balanced NIFTY 500 batch (default 10 per run) that completes a
  reporting-period cycle in about 10 weeks and resumes safely after restarts.
- Semi-annual constituent refresh after the March/September reconstitutions, enforced by an exact
  500-member integrity check.
- Quarterly followed-investor exchange discovery, parsing, and coverage checks after the configured
  21-day filing lag, processed in restart-safe batches.
- Independent persisted status/error information for every activity, shown in the dashboard.
- Durable run history, scheduler heartbeat, missed/overdue detection, 90-minute stalled-run
  detection, bounded activity-only retries, and catch-up after restarts within 48 hours.
- Docker scheduler health check plus dashboard health states for `HEALTHY`, `OVERDUE`, `STALLED`,
  `FAILED`, and recovery activity.
- Snapshot generation then uses the refreshed prices plus stored portfolio, fundamental, style,
  and investor evidence.
- No scheduler action places orders or infers investor exits from absent disclosures.

Planned:

- In-app schedule editing, richer operational metrics, and external notification channels.
- Alerts must remain review prompts rather than automatic orders.

## Phase 8 — notional portfolio, backtesting, and learning

Status: planned. Add a separate top-level **Notional Portfolio** tab with an independent cash and
transaction ledger. The user can add or remove stocks notionally, including accepting a dated
recommendation as a proposed notional trade, without changing actual holdings. Actual portfolio
changes continue to arrive through manual transactions or periodic broker uploads and reconciliation.

Planned notional-portfolio controls and views:

- Configurable starting cash, position size, maximum allocation, transaction costs, taxes/slippage,
  and reinvestment policy.
- Manual notional Buy, Sell, Add More, Trim, and cash adjustments; no automatic order placement.
- Optional acceptance of a recommendation into the notional ledger. Execution uses the next
  observable close after the decision timestamp, never a price already known to the analysis.
- Cash, holdings, cost basis, realised/unrealised return, dividends, total portfolio value, allocation,
  turnover, and drawdown history.
- Side-by-side actual portfolio, notional portfolio, and broad-market benchmark values, with both
  time-weighted and money-weighted returns where inputs permit.
- A decision link from every notional trade to the immutable recommendation, investor-style version,
  evidence dates, confidence/limitations, and the user's reason for accepting or rejecting it.
- Corporate-action handling and explicit data-quality flags so splits, bonuses, mergers, or missing
  closes do not look like investment performance.
- Clear labelling that notional results are simulated and may differ from executable prices,
  liquidity, taxes, and the user's real portfolio constraints.

Extend the existing Phase 4 single-stock style backtests with immutable recommendation snapshots:

- Forward 3/6/12-month return.
- Maximum adverse and favourable excursion.
- Benchmark-relative return.
- Calibration of confidence scores.
- Performance by market regime, cap segment, and investor style.
- Separate evaluation of fundamental signals and followed-investor corroboration.

## Phase 9 — IPO lifecycle analysis

Status: planned. IPOs will use a separate top-level **IPOs** tab because pre-listing evidence and
first-year uncertainty are not comparable to the mature-company evidence used by Owned and
Prospective Stocks. The tab will contain **Pre-IPO** and **Post-listing (0–12 months)** views; after
the first listed year, an eligible company graduates into the normal owned/prospective workflow.

Planned pre-IPO evidence and workflow:

- Official DRHP, RHP, addenda, exchange notices, price band, lot size, timetable, and issue status.
- Fresh-issue versus offer-for-sale split, proposed use of proceeds, promoter dilution and lock-ins.
- Restated financial statements, cash flow, debt, related-party transactions, auditor qualifications,
  litigation, material risk factors, and peer-comparison disclosures.
- Issue-price valuation versus disclosed peers with explicit warnings when peer selection or history
  is weak.
- Conservative `AVOID`, `WATCH`, or `CONSIDER` research outcomes rather than applying the mature
  listed-stock Strong Buy gate before market evidence exists.
- Event-driven refreshes when an RHP/addendum, price band, subscription result, basis of allotment,
  or listing notice becomes available. Unofficial grey-market premiums will not be treated as
  authoritative evidence.

Planned post-listing workflow for the first 12 months:

- Daily closes from listing day and transparent return comparisons against issue price and a broad
  benchmark; no long-history technical conclusions when the sample is insufficient.
- Quarterly statement, proceeds-utilisation, promoter/anchor lock-in, corporate-action, governance,
  and material-disclosure monitoring.
- Explicit reviews around 30, 90, 180, and 365 calendar days after listing, plus event-driven reviews.
- Separate handling for an IPO bought by the user: it remains visible in Owned Stocks while the IPO
  tab supplies the first-year evidence and limited-history warnings.
- Graduation at one year into the standard financial/style analysis. NIFTY 500 screening eligibility
  still depends on official index membership rather than age alone.

The implementation must use official SEBI, exchange, and issuer documents with source URLs and
document dates. OCR or LLM extraction may create reviewable drafts, but deterministic checks and
human-verifiable citations remain mandatory.

## Cross-cutting product capabilities

Implemented:

- Task-focused Streamlit pages plus a stock-centric dashboard with **Owned** and **Prospective**
  top-level tabs.
- Descriptive analysis tabs replace internal phase numbers; each tab shows the same fixed,
  alphabetically ordered stock rows in a scrollable table, followed by a consolidated **Summary &
  Recommendation** table.
- FastAPI backend, PostgreSQL/pgvector database, and Docker Compose deployment bound to localhost.
- Transparent read-only decision support with no order execution.
- Persistent source/evidence dates, provider labels, rule versions, and human-readable reasons.
- Owned and prospective stocks use different actions and cannot be silently mixed.
- Configurable YAML investor-style definitions and a versioned 15-profile followed-investor list.
- Scheduled background analysis with persistent health, activity results, run history, forced-run
  controls, catch-up, and bounded retry behavior.
- Automated API/domain tests and render tests for every UI page.
- Manual/CSV recovery paths when a third-party data source is unavailable.

Continuing requirements for every new phase:

- Preserve user data across upgrades and introduce explicit schema migration handling.
- Keep secrets in environment variables and never expose them in API/UI responses or logs.
- Prefer official/licensed sources, respect their access terms, and retain source provenance.
- Surface missing, stale, partial, and inapplicable evidence instead of filling gaps with guesses.
- Version rules, prompts, schemas, models, and material configuration changes.
- Keep all recommendations advisory, explainable, and reviewable by the user.
