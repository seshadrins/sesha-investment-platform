# Implementation Review and Recommendations

Independent review of the running application: backend/data model, investment-strategy logic
(screening, style rules, recommendations), the automation/LLM pipeline layer, and the Streamlit
UI. Conducted by reading the full source of every module listed below (not a sample), plus live
observations while using the dashboard.

Reviewed: `services/api/app/*.py` (~7,000 lines), `services/api/app/investor_styles/*.yaml`,
`services/api/app/screening_universes/*.yaml`, `services/ui/app.py` and all 11 pages (~1,900
lines), `compose.yaml`, `.env.example`, and the test suite in `services/api/tests/`.

The running application was also driven directly against the live Docker stack (all four
containers were already up) using headless Chromium via Playwright, since no browser-automation
tool was pre-installed in this environment — `npx playwright` fetched the CLI and a Chromium
build on demand. That session is what surfaced/confirmed several findings below marked "confirmed
live" (H2, H3, H6, H9, and the dashboard-layout item), rather than the visual layout being
inferred from source alone.

Every finding below cites a concrete file and line. Severity reflects real-world impact for a
single-user, self-hosted decision-support tool — "Critical" means it can corrupt data, silently
mislead a real investment decision, or crash core screens; "High" means a clear defect that
degrades trust or reliability; "Medium"/"Low" are correctness or quality issues worth fixing on a
normal cadence.

## Executive summary

The core design is sound and unusually disciplined for a project this size: an immutable,
`Decimal`-based FIFO ledger; deterministic, versioned recommendation rules; a real point-in-time
backtesting design that respects reporting lags; citation-grounded LLM output that's stored as a
draft until a human accepts it; and a scheduler with genuine idempotency primitives (unique
`run_key`, DB-level unique constraints, an SSRF-safe source allowlist). No code path anywhere
places, modifies, or cancels a real order — the "read-only decision support" principle is honored
throughout.

Against that foundation, this review found **two critical bugs that actively undermine the app's
core promises** — one that can silently corrupt trust in backtest results, one that can crash most
of the dashboard from a single bad ledger row — plus a cluster of high-severity gaps concentrated
in three places: error handling at the UI/API boundary, concurrency between the scheduler and
manual triggers, and the point where LLM-extracted data is allowed to become "fact" without human
review. There is also a real gap between the rigor applied to *prospective* stocks (multi-factor
gate) and *owned* stocks (a single position-sizing heuristic drives most `BUY_MORE` calls) — this
surfaced directly while using the app and is one of the more consequential findings here.

Beyond correctness, there's a distinct functional gap worth calling out on its own: the platform
is stronger at *informing* than at *guiding*. The richest performance analytics built (return
history, drawdown, benchmark comparison) went to the simulated notional ledger rather than the
real portfolio; nothing proactively tells the user when an owned stock's recommendation changes;
and the one consolidated summary table is sorted alphabetically rather than by what actually needs
a decision this week. See "Functional and product gaps" below.

## Critical

### C1 — Notional portfolio's anti-look-ahead date logic is inverted, and it's the exact bug the design intends to prevent

`services/api/app/notional_portfolio.py:21-24`

```python
def target_price_date(decision_at: datetime) -> date:
    aware = decision_at.replace(tzinfo=timezone.utc) if decision_at.tzinfo is None else decision_at
    local = aware.astimezone(ZoneInfo("Asia/Kolkata"))
    return local.date() if local.time() >= time(16, 0) else local.date() + timedelta(days=1)
```

NSE closes at 15:30 IST. A decision made **after** 16:00 IST — when that day's close is already
public — is assigned `target_price_date = today`, and `settle_pending_orders`
(`notional_portfolio.py:174`) fills it at that already-known price. A decision made **before**
16:00 (close not yet set) is pushed to the *next* day instead of settling against today's
still-undetermined close. The two branches are swapped relative to a fair no-look-ahead rule.

Concretely: place a simulated BUY at 5pm after checking today's close anywhere online, and if
that EOD price is already imported, `create_trade()` (line 143) calls `settle_pending_orders`
(line 147) immediately and fills at the exact known price — the paper-trading ledger and its
downstream "recommendation learning" performance stats become quietly optimistic in a way that's
invisible in the UI. The existing test
(`services/api/tests/test_notional_portfolio.py:60-64`,
`test_target_date_prevents_same_day_preclose_lookahead`) asserts the *current* (backwards)
behavior, so `pytest` passes while the bug ships. **Fix: swap the two branches** — pre-16:00
decisions should target the next trading day's close (today's isn't set yet); post-16:00
decisions should also target the next trading day's close (today's is already public and must
not be used).

### C2 — A single bad ledger row can crash most of the dashboard and the entire morning automation run

`services/api/app/portfolio.py:68-71` raises a bare `ValueError` on any oversell condition.
`services/api/app/services.py:49` calls it, unguarded, for every account/instrument group inside
`portfolio_snapshot()`. That function is in turn called **without a try/except** from at least ten
places in `main.py`: lines 209, 259, 307, 389, 847, 956, 1157, 1685, 1828, and 2268 — covering
instrument listing, prospective-stock listing, screening status, investor signals, the entire
`/stock-workbench` dashboard payload, and the automation pipeline that builds it. Only three call
sites guard it (527/529, 683/684, 790/792).

One corrupt row — e.g. a same-day CSV import that lists a SELL before its matching BUY (FIFO sorts
same-day transactions by `id`, not true time; `portfolio.py:38`) — produces an oversell exception
that then 500s the dashboard, screening, investor-signal, and automation endpoints simultaneously.
There is no DELETE/PATCH endpoint for transactions anywhere in the API, so once this happens the
user has no in-app way to remove the offending row and recover.

Compounding this: `POST /imports/opening` (`main.py:727-756`) never validates `quantity > 0` and
never calls `portfolio_snapshot(db)` before committing — unlike `create_transaction` and
`/imports/transactions`, which do. A malformed opening-balances CSV writes straight to the ledger
with no integrity check and detonates the first time anyone loads the dashboard.

**Fix:** wrap `portfolio_snapshot()` calls in the read-only routes with the same
`except ValueError` pattern already used at lines 527 and 683 (return a clear 4xx explaining which
position is inconsistent, not a raw 500); add the same pre-commit validation to
`/imports/opening` that `/imports/transactions` already has; and add a way to void/reverse a bad
transaction without needing direct DB access.

## High

### H1 — Owned-stock `BUY_MORE` recommendation ignores every quality/valuation signal the app already computes

`services/api/app/recommendations.py:54-57`:

```python
if weight < settings.max_position_weight * 0.50 and (return_pct or 0) > -0.05:
    reasons.append("Position is well below the configured maximum allocation.")
    reasons.append("No hard thesis or portfolio risk gate is active.")
    return "BUY_MORE", reasons
```

This is the entire gate for adding to an owned position: under half the max allocation (default
7.5% of a 15% cap) and not down more than 5%. It is a fallback bucket — anything not overweight,
not deeply in loss, and not on `WATCH` lands here by default, which is why most owned stocks in a
lightly concentrated portfolio show `BUY_MORE`.

This stands in sharp contrast to `recommend_prospective()` (`recommendations.py:67-113`), which
requires a financial-quality score ≥80, at least two matched investor styles, and supportive
sector-relative valuation before it will say `STRONG_BUY` — all evidence the app *already
computes and displays* for owned stocks in the Financial Analysis and Investor Style Fit tabs, but
never consults for the owned-stock recommendation. The bar for "add to what you already own" is
currently lower than the bar for "start a new position," which inverts the risk profile (you have
more capital and more thesis history at stake in an existing position).

**Fix:** gate `BUY_MORE` on the same evidence already computed elsewhere in the app — e.g. require
financial score above some floor and no `valuation_stretched` signal, the same way
`recommend_prospective` does, rather than sizing alone.

### H2 — Dashboard's total portfolio value is computed but never reaches the UI

`services/api/app/services.py:133-144` — `portfolio_snapshot()` returns a `summary` block with
`market_value`, `unrealised_profit`, `realised_profit`, and `total_profit` across the whole
portfolio. `services/api/app/main.py:954-1080` (`_build_stock_workbench`, the function that feeds
the real dashboard) pulls `snapshot["positions"]` but never forwards `snapshot["summary"]` — it's
computed and silently dropped before reaching `services/ui/app.py`. The only place "Total value"
appears anywhere in the UI is the separate, simulated Notional Portfolio page
(`pages/10_Notional_Portfolio.py:46`) — the real dashboard has no headline portfolio value, total
unrealised P&L, or total return figure at all. This was confirmed live: it's not a display bug, the
number simply never leaves the backend.

**Fix:** include `snapshot["summary"]` in the `/stock-workbench` response and render it as a
header metric row above the Owned/Prospective tabs in `app.py`.

### H3 — No day-over-day price movement anywhere in the system

There is no `previous_close`, `day_change`, or equivalent field stored or computed anywhere in the
backend (confirmed by search across `services/api/app/`). Only the latest stored close is ever
retained, so there's no way for the UI to show whether a stock is up or down today even if it
wanted to — this is a missing capability, not a rendering bug. Would require storing (or deriving
from Upstox historical candles, already fetched) the prior trading day's close alongside the
latest one.

### H4 — LLM-extracted disclosure data can bypass human review entirely on an exact alias match

`services/api/app/disclosure_pipeline.py:606-619` treats an LLM-extracted observation
(`OLLAMA:*`/`OPENROUTER:*`) identically to a deterministic XBRL one for the auto-apply decision —
only alias *ambiguity* routes to the review queue, not extraction method. This is worsened by
`_llm_prompt` (`disclosure_pipeline.py:373-382`), which embeds the full list of configured
followed-investor names into the prompt itself ("Only retain names plausibly matching these
configured aliases: …") — increasing the chance a model "corrects" a noisy OCR'd name toward one
of the exact strings it was just shown, producing a false exact match that skips
`InvestorAliasReview` and writes straight to `InvestorDisclosure`
(`disclosure_pipeline.py:518-544`). This is the one concrete gap in the project's stated policy
that "LLM output is validated, never trusted directly" — Pydantic schema validation is not the
same guarantee as requiring human review for model-sourced facts.

**Fix:** route every observation whose parser is `OLLAMA:*`/`OPENROUTER:*` through
`InvestorAliasReview` regardless of alias-match confidence, or require the same fact to also
appear via a second independent signal before auto-applying.

### H5 — Manual/forced automation runs race with the scheduler with no effective coordination

Two separate containers (`api`, `analysis-scheduler` in `compose.yaml`) share one database.
`POST /investor-disclosures/ingest` (`main.py:887-903`) calls the ingestion pipeline directly,
entirely outside the `begin_run`/`run_key` idempotency layer. `POST /analysis-schedule/run`
(`main.py:1612-1660`) does call `begin_run`, but builds `run_key` from
`scheduled_for=datetime.utcnow()` — a fresh microsecond timestamp every call — so the "unique" key
is different every time and provides no real dedup. Triggering a forced run while the 06:00
scheduled run is in flight starts two concurrent passes over the same
`DisclosureSourceMapping`/`DisclosureDocument` rows, doubling live NSE/BSE request volume and
racing on `mapping.last_status` (last write wins). A related, unhandled race:
`_upsert_disclosure` (`disclosure_pipeline.py:518-544`) does check-then-insert against a table
with a real unique constraint, but — unlike `begin_run`, which explicitly catches
`IntegrityError` — has no exception handling, so a genuine race raises an unhandled
`IntegrityError` that rolls back an entire mapping's batch and mislabels it `FAILED` with a
confusing constraint-violation message.

**Fix:** derive `run_key` from the *intended* activity/scope rather than a fresh timestamp so
concurrent triggers for the same work actually collide and dedup; wrap `_upsert_disclosure` in the
same catch-and-refetch pattern as `begin_run`.

### H6 — Editing a thesis (or other live data) doesn't update the main dashboard until the next scheduled or forced snapshot

`GET /stock-workbench` (`main.py:1528-1539`) does not compute anything live — it reads a cached
`AnalysisSnapshot` row keyed `"stock_workbench"`, last written by `_store_workbench_snapshot()`
during the scheduler's daily run or a forced "Run now." `POST /theses` (`main.py:599-615`) writes
a new thesis version to the live `Thesis`/`ThesisVersion` tables immediately, and the Thesis &
Review page itself reflects it right away because it queries `/theses/{id}` directly
(`pages/4_Thesis_and_Review.py:50`) — but the main dashboard's "Portfolio & Thesis" tab pulls
`thesis_status`/`thesis_reason` from the stale cached snapshot (`main.py:1044-1045`), so a just-
saved thesis change is invisible there until the next scheduled run (once/day, Tue-Sat) or a
manual "Dashboard snapshot only" forced run. This confirmed live: saving a thesis shows the update
on the Thesis & Review page but not on the Owned-stocks dashboard. The only signal this is
happening is the small "Cached analysis generated …" caption at the top of the page — nothing
tells the user their edit specifically didn't take effect. The same staleness applies to any other
edit that isn't itself part of the scheduled pipeline (e.g. a manually entered price or a new
transaction) until the next snapshot rebuild.

**Fix:** either invalidate/rebuild the cached snapshot's affected row when a thesis is saved (cheap
for a single instrument, versus a full rebuild), or — at minimum — surface a clear "your recent
edits may not be reflected until the next snapshot refresh; force one below" prompt near the save
confirmation.

### H7 — Disclosure source filings (XBRL/XML) are stored as opaque raw bytes with no way to view them, unlike every other document type

`DisclosureDocument.content` (`models.py:164`) stores the raw fetched bytes from NSE/BSE with only
a hash for provenance — no extracted human-readable text is stored anywhere, and there's no API
endpoint or UI page to fetch/download the raw content either (confirmed: no route references
`DisclosureDocument` outside a count query in `main.py:1313-1315`, and `8_Followed_Investors.py`
only shows aggregate `PARSED`/`PARSER_FAILED` counts). This is inconsistent with how every other
document type in the app is handled: annual reports/transcripts get a `ResearchDocumentSection`
table with real extracted `text` per section (`models.py:390-398`), and IPO offer documents get
the equivalent `IPODocumentSection` (`models.py:508-516`) — both fully reviewable in their
respective pages with citations. A followed-investor disclosure signal is the one piece of
evidence in the app a user can't actually go look at through the UI; they'd need direct database
access to inspect the source XBRL/XML behind a `NEW_DISCLOSURE`/`INCREASED`/`EXIT_REPORTED`
signal, which cuts against the project's own "human-verifiable citations" principle.

**Fix:** at minimum, add a raw-content download/view endpoint and a link to it next to each
disclosure's source URL; ideally, extract the parsed XBRL fields into a small human-readable
summary (shareholder name, period, percentage, filing date) stored alongside the raw bytes, mirroring
the section-text pattern already used for the other two document types.

### H8 — API/UI error boundary conflates "service is down" with "the request was invalid," and can crash a page over a non-critical call

`services/ui/common.py:10-18` (`api_get`) calls `st.stop()` on *any* `requests.RequestException`,
including HTTP errors from `response.raise_for_status()`. `services/ui/app.py:18-21` chains four
sequential `api_get` calls before rendering anything — if only the last (notifications, non-
critical) call fails, the entire dashboard vanishes behind "the portfolio service is unavailable,"
even though the primary workbench data loaded fine. The same message is shown for a genuine
connection failure and for a legitimate 404/422/409 from a running API, actively misdirecting the
user toward "check Docker" for what might be a data problem. Separately, `api_post`/`api_put`/
`api_patch` (`common.py:21-63`) assume the JSON `detail` field is always a printable string; it
isn't guaranteed to be (e.g. `main.py:228` raises a dict-valued `HTTPException`, and FastAPI's own
422 validation errors return a list) — such a response renders as a raw Python object repr instead
of a message.

**Fix:** don't abort the whole page for a failed non-critical call; distinguish connection
failures from HTTP error responses in the message; coerce non-string `detail` payloads to a
readable string before display.

### H9 — The Owned-stocks table has too many columns for `width="stretch"`, so it shrinks and truncates text instead of scrolling — confirmed in-browser

`render_table()` (`app.py:171-179`) renders every scope's table with `width="stretch"` and no
per-column width hints. The "Portfolio & Thesis" tab alone has 12 columns
(`app.py:204-219`, including two free-text columns — "Thesis" and "Thesis summary"). Tested
directly against the running app at a normal desktop width (1600px): the rendered grid's DOM
`scrollWidth` (1115px) is barely larger than its `clientWidth` (1106px) — there is no real
horizontal overflow for the browser to scroll, because Streamlit's grid component (glide-data-grid,
canvas-rendered) auto-fits all 12 columns into the available width by shrinking each one, rather
than laying them out at a natural width and letting the container scroll. The result, visible in
the screenshot: the "Thesis" column renders as "Not rec…" (truncated from "Not recorded") and the
final column, "Thesis summary," is pushed off the visible area entirely with no scrollbar to reach
it — confirming the "columns not visible, no scrollbar" behavior reported in testing. A mouse-wheel
horizontal scroll gesture directly over the grid also produced no movement, ruling out a simple
missed-gesture explanation.

**Fix:** this is a column-density problem, not a missing-feature problem — give the two free-text
columns (and any other wide column) an explicit `column_config` width (e.g.
`st.column_config.TextColumn(width="medium")`), or move "Thesis summary" out of this table into a
one-line-per-row expander/tooltip, or reduce the Portfolio & Thesis tab to the numeric columns and
keep thesis text in its own narrower table. Explicit per-column widths generally cause the grid to
report real overflow and produce its native horizontal scrollbar instead of silently shrinking.

## Medium

- **The main dashboard buries the actual stock tables below a full screen of operational
  chrome.** `services/ui/app.py:26-164` renders, in order before the Owned/Prospective tabs even
  appear (line 344): the forced-run control row, an automation-health banner, three expanders
  ("Automation schedule and latest status," "Automation metrics and alerts," "Automation run
  history"), cold-start/failure notices, and market-refresh/data-refresh captions. On a normal
  browser viewport this pushes the actual stock-level dashboard — the primary reason to open the
  page — below the fold, so every visit starts with a scroll. This was confirmed as a real
  friction point in use. **Fix:** move the automation schedule/metrics/run-history panels to a
  dedicated "Automation" or "System Status" page (there's already a natural home next to the
  other task pages linked at the bottom of `app.py`), and keep the main dashboard limited to a
  compact health banner (only shown when something's actually wrong) plus the stock tables.
- **Recommendation priority can hide the more urgent signal.** `recommendations.py:25-39` checks
  weight-based `TRIM` before the loss-based `REVIEW`/`SELL` check. A position that is both
  overweight and down more than the loss-review threshold gets `TRIM` with reasons text that only
  mentions size — the loss, arguably the more decision-relevant fact, is never surfaced.
- **`total_liability` may include equity, silently disqualifying two of three investor styles.**
  `financial_analysis.py:78-82` and `style_engine.py:78-80` compute
  `leverage_proxy = liabilities / assets`. If the Upstox field labeled `total_liability` follows
  the common Indian-vendor convention of meaning liabilities *plus* equity (i.e. equal to total
  assets by construction), the ratio computes to ≈1.0 for nearly every company, making the
  `liabilities_to_assets` rules in `quality_compounder.yaml:29-33` and
  `capital_preservation.yaml:24-28` effectively unpassable. Not confirmed against a live payload —
  worth checking one real balance-sheet response before trusting either style's leverage rule.
- **Financial-sector detection is duplicated and substring-based**, sourced inconsistently from
  either the NSE constituent CSV's Industry column or a research provider's own `sector` field
  (`financial_analysis.py:94`, `style_engine.py:126-127`, both matching
  `"bank"/"financial"/"finance"/"insurance"` independently). A narrower sector label from either
  source could let a bank/NBFC slip through the exemption and get scored with industrial
  leverage/cash-quality rules it shouldn't be. Centralize into one shared helper.
- **All three investor styles are `applicability: non_financial`**, so no bank/NBFC/insurance
  stock can ever reach `STRONG_BUY`/`BUY` — by design (financial-sector companies need dedicated
  rules per `EVOLUTION.md`), and the reason text does say so, but worth an explicit sign-off since
  it silently excludes a meaningful share of the NIFTY 500 from the prospective list.
- **Promoter-holding-decline governance flag only looks at one quarter-over-quarter change**
  (`financial_analysis.py:112-119`, threshold ≥2pp in one quarter). A promoter steadily selling
  ~1.9pp/quarter for two years — a real governance concern — never trips it.
- **Fuzzy alias-match "ambiguous" flag is dead code.** `disclosure_pipeline.py:500-501`:
  `return ranked[0][1], ranked[0][0], True or ambiguous` — `True or ambiguous` always evaluates to
  `True`, so every fuzzy match, even a 0.99-confidence unambiguous one, is forced into the human
  review queue. Fails safe, but defeats the matcher's purpose and risks reviewer fatigue on
  easy approvals. Almost certainly should just be `ambiguous`.
- **Deterministic XBRL parsing is less defensively coded than its own LLM fallback.**
  `disclosure_pipeline.py:356-358` builds `ExtractedObservation` inline with no `try/except`
  (unlike the LLM path, which catches `ValidationError` per-candidate). A single malformed name
  (e.g. under the 2-character minimum) aborts and rolls back the entire mapping's batch for that
  run instead of skipping the one bad row.
- **Stalled automation runs produce no user-visible alert.** `automation_runs.py:100-114`
  (`mark_stalled_runs`) mutates status directly, bypassing `complete_run` — the only place that
  creates an `AppNotification`. A hung Ollama/NSE call is silent until the dashboard health status
  happens to reflect it.
- **Inconsistent OpenRouter privacy posture between call sites.** `disclosure_pipeline.py:430-436`
  explicitly rejects the free auto-router and opts out of data collection
  (`"data_collection": "deny"`); the shared `_invoke` in `document_analysis.py:104-116` (used by
  both document analysis and IPO analysis) has neither guard — likely an oversight given the
  project's evident care about not letting a provider retain data, and it's the path most likely
  to process private research notes.
- **Unmatched/below-threshold shareholder names vanish with no audit trail.** In
  `_process_document` (`disclosure_pipeline.py:606-619`), any observation whose best fuzzy match
  scores under the configured threshold is simply dropped — no row, no notification, only an
  aggregate count delta. If a followed investor's disclosed name changes and now scores just under
  threshold, there's no way to see the near-miss without manually re-parsing the filing.
- **No DB-level positivity/consistency constraints; Pydantic is the only gate.** No
  `CheckConstraint` exists anywhere in `models.py`. Any path that bypasses the API layer (bulk
  import, a future admin script, direct DB access) can insert negative/zero values with nothing at
  the storage layer to stop it. `TransactionCreate.quantity` (`schemas.py:38-40`) also allows
  exactly `0`, which is silently accepted then no-op-skipped in `calculate_position`
  (`portfolio.py:49-50`) — stored forever, no warning shown.
- **Missing indexes on the hottest ledger columns.** `Transaction.account_id`,
  `.instrument_id`, `.trade_date` (`models.py:260-276`) have no index despite being exactly what
  every dashboard/portfolio request groups and sorts by; `portfolio_snapshot` recomputes from
  scratch on nearly every request with no caching layer.
- **`main.py` absorbs substantial orchestration logic that belongs in dedicated, testable
  modules.** At 2,308 lines, functions like `_build_stock_workbench` (954-1087),
  `_refresh_due_financial_statements` (1171-1283), and `_run_morning_automation` (1442-1474) are
  80-130 lines of business logic with no unit tests, unlike `screening.py`, `style_engine.py`,
  `disclosure_pipeline.py`, etc., which are separate modules with their own test files.
- **Notional Portfolio table formatting is inconsistent with the rest of the app.**
  `pages/10_Notional_Portfolio.py` uses no `column_config` anywhere — currency columns show as raw
  unrounded floats with no ₹ symbol, unlike every other financial table in the app.
  `pages/11_IPOs.py:46` also uses free-text date entry (`YYYY-MM-DD` as a hint string) where every
  other page uses a real date picker, and treats `0` as "unset" on numeric fields
  (`price_band_low": low or None`), silently dropping an intentional zero.
- **Hardcoded, ticker-specific debug leftover.**
  `pages/6_Financial_Analysis.py:75` — `if "VAIGLO" in label` controls the default preselected
  stock. Undocumented, unrelated to any stated policy, will confuse future maintainers.
- **The deepest "why" behind a recommendation requires manual page-hopping.** The dashboard's
  six-tab `render_scope` design (`app.py:197-334`) is a strong transparency pattern, but
  governance-flag text and per-rule threshold/actual-value evidence are only reachable by
  re-navigating to Financial Analysis or Investor Style Fit and reselecting the same stock from a
  dropdown — the dashboard itself only shows counts. No deep link carries the selected stock
  across pages.

## Low / polish

- `recommendations.py:54` uses a hard-coded `0.50` multiplier instead of a configurable setting
  like every other threshold in the file.
- `services.py:81-90` converts precise `Decimal` ledger math to `float` before comparing against
  threshold constants — inconsistent with `portfolio.py`'s otherwise scrupulous `Decimal` use.
- `providers.py:174` assumes the Upstox JSON response is always a dict; a list response raises an
  unhandled `AttributeError` rather than a clean provider error.
- `financial_analysis.py:38-41` — `_score_high` floors every score at 20 regardless of how
  negative the underlying ratio is, compressing "mediocre" and "value-destroying" into the same
  score.
- `style_engine.py:26-34` never validates a rule's `operator` against the supported set at
  YAML-load time; a typo surfaces as an uncaught `KeyError` deep inside evaluation instead of
  failing fast at startup.
- `evaluate_current_styles` (live STRONG_BUY gate) doesn't apply the same 120-day point-in-time
  boundary check against "today" that `backtest_style` strictly enforces — likely fine for live
  screening (the report is already public) but worth documenting as an intentional asymmetry.
- Percentage decimal-precision is inconsistent across pages (some `.1%`, some `.2%` for
  comparable metrics) — cosmetic only.
- Newer pages (`9_Document_Analysis.py`, `10_Notional_Portfolio.py`, `11_IPOs.py`) use dense,
  semicolon-chained one-liners that depart from the one-statement-per-line style of pages 1-8,
  making them harder to diff.
- Raw `st.json()` dumps appear in a couple of places (`11_IPOs.py:89`, `8_Followed_Investors.py:156`)
  where a structured table/summary would match the rest of the app's presentation.
- Imported free text (thesis reasons, document-analysis summaries, IPO draft claims) is rendered
  through `st.write`/`st.markdown`, which interpret Markdown syntax. Low practical risk in a
  single-user local app, but content from an imported PDF/CSV could still alter rendering rather
  than display literally.
- No cross-process rate limiting for NSE/BSE requests — two concurrent runs (see H5) each
  self-throttle independently, doubling effective request rate; no `Retry-After`/backoff
  classification on 429/5xx.
- Local `.env` holds live-looking Upstox/OpenRouter keys — confirmed correctly gitignored and
  never committed (checked `git log --all -- .env`), just worth the usual reminder to rotate if
  ever pasted elsewhere.

## Test coverage gaps

The suite covers the FIFO happy path well but leaves the two rule engines that drive every
recommendation almost entirely unverified:

- `recommend()` (`recommendations.py`) has exactly one test (INVALID thesis → STRONG_SELL,
  `test_portfolio.py:68`). `TRIM`, `SELL`, `REVIEW`, `HOLD`, and `BUY_MORE` — including the
  priority-ordering issue in the Medium section above — have no coverage at all.
- `recommend_prospective()` — the entire STRONG_BUY/BUY/AVOID/WATCH gate — has **zero** direct
  unit tests.
- `calculate_position()` is tested for basic BUY/SELL/oversell/dividends but not `OPENING`,
  `ADJUSTMENT_IN`/`OUT`, multi-lot chains, or the same-day ordering issue behind C2.
- No route-level/integration tests exist for `main.py` at all — the oversell guard in
  `create_transaction`, the last line of defense against a corrupted ledger, is entirely
  unverified by any test.
- `target_price_date()` in `notional_portfolio.py` *is* tested — but the test encodes the
  inverted behavior (C1) as correct, which is how the bug shipped unnoticed.

## What's working well

- **The FIFO ledger engine** (`portfolio.py`) is clean, `Decimal`-only, consumes lots oldest-first
  correctly, folds charges into cost/proceeds correctly, and defaults to raising on oversell
  rather than silently clamping — the right call for an auditable ledger.
- **Thesis versioning** (`ThesisVersion` alongside current-state `Thesis`) is a solid append-only
  audit-trail design, and `INVALID` thesis unconditionally overriding every other recommendation
  rule is the correct priority.
- **The point-in-time backtest design** in `style_engine.py` is genuinely careful: a fixed
  120-day reporting lag, `bisect_left` price lookups that only ever look forward from a decision
  date, and explicit `limitations` text shipped with every result.
- **`recommend_prospective()`** faithfully implements its documented multi-factor gate and
  degrades to `WATCH`/`REVIEW` — never a silent bypass to `BUY` — whenever evidence is missing.
- **Recommendation learning never fills a pending forward-return horizon with a guessed price** —
  it stays `PENDING` until a real observed close exists.
- **NIFTY 500 universe assembly** enforces an exact 500-member integrity check across the three
  official index files and hard-fails rather than silently deduplicating an ISIN that appears in
  more than one cap segment.
- **The disclosure pipeline's provenance discipline**: unique constraints on source URL and
  content hash, an enforced official-domain allowlist at both discovery and download time (real
  SSRF defense), and a status vocabulary that correctly distinguishes "fetch failed" from "no
  filing this period" from "parser needs review" — genuinely honest UX about absence-of-evidence.
- **Grounded LLM output is architecturally a draft, not a fact**: citations in both document
  analysis and IPO analysis are validated against real section IDs in the source document, and
  results default to `status="DRAFT"` pending explicit human accept/reject.
- **No order-execution path exists anywhere** — confirmed across the API, providers, and UI. The
  "decision support only, never trades" principle is honored consistently, including labelling
  every notional-portfolio action as simulated.
- **The dashboard's six-tab `render_scope` pattern** (portfolio, data freshness, financials,
  style fit, investor signals, summary) reusing the same alphabetically ordered stock rows across
  all six is a genuinely good transparency design — a first pass needs no per-stock navigation.

## Functional and product gaps — guiding long-term strategy

Everything above is about correctness of what's already built. This section is a different lens:
where the platform is technically working as designed but still leaves the user without guidance
a long-term-strategy tool should provide. These are gaps, not bugs — features that either don't
exist yet or exist only for the wrong ledger.

### G1 — The real portfolio has no historical performance tracking at all; only the simulated one does

Phase 8 built genuinely good performance analytics — total return, time-weighted return,
money-weighted return, drawdown history, and normalized benchmark comparison — but every one of
them lives in `notional_portfolio.py` and is only ever computed for the **simulated** paper-trading
ledger (confirmed: `time_weighted`/`money_weighted`/`drawdown`/`benchmark` appear nowhere in
`main.py` outside the notional-portfolio and benchmark-configuration routes). `GET /portfolio`
(`main.py:680`) and `portfolio_snapshot()` (`services.py`) return only a point-in-time snapshot:
current value, current unrealised P&L, nothing over time. There is no equity curve, no CAGR/XIRR,
no drawdown, and no "how has my actual money performed against the Nifty" anywhere for the
portfolio the user actually owns. For a platform whose stated purpose is long-term investment
decision support, this is backwards — the rich performance view was built for the practice ledger,
not the real one.

**Recommendation:** the FIFO ledger already has everything needed (trade dates, cash flows,
priced positions over time) — extend the notional portfolio's return/drawdown/benchmark
calculations to the real ledger, or build one shared module both consume. This is likely the
single highest-value addition for "guides long-term strategy."

### G2 — No portfolio-level diversification view; only single-position concentration is enforced

The recommendation engine checks per-stock weight against `max_position_weight`/
`trim_position_weight` (`recommendations.py:25,41-49`), but nothing aggregates sector exposure or
market-cap-segment mix (Large/Mid/Small — already tracked per instrument via
`UniverseMembership`/`cap_segment`) across the whole portfolio. A user could be significantly
overweight a single sector across five well-sized individual positions and the app would never
say so — every position looks fine in isolation. A long-term strategy view needs a portfolio-level
"here's your sector/cap-segment mix" breakdown, not just per-stock caps.

### G3 — No cash tracking on real accounts, so "how much do I have to deploy" isn't answerable in-app

`Account` (`models.py:28-37`) has no cash-balance field — only the notional portfolio tracks cash
(`NotionalPortfolio.cash`). For the real portfolio, a user has to track deployable/uninvested cash
outside the app entirely, which undercuts every "should I add to this position" or "should I start
a new one" decision, since sizing a purchase requires knowing what's actually available.

### G4 — A thesis's target holding horizon is captured but never acts on anything

`Thesis.target_horizon_months`/`ThesisVersion.target_horizon_months`
(`models.py:336,354`) is set by the user in the Thesis & Review form and displayed back, but
nothing in `recommendations.py` or the scheduler ever checks whether that horizon has elapsed.
A stock bought with a stated 12-month thesis horizon that's now 18 months old gets no different
treatment than one bought yesterday — it's just informational text. For a tool meant to enforce
long-term-strategy discipline, an elapsed horizon is exactly the kind of signal that should surface
a `REVIEW` prompt ("your stated investment horizon for this position has passed — reassess the
thesis") independent of price/weight conditions.

### G5 — Nothing proactively tells the user when a recommendation changes on a stock they own

`AppNotification` (`models.py:203`) is only ever created for automation-run status changes
(`automation_runs.py:67`) and disclosure-pipeline events (`disclosure_pipeline.py:510`) — never
for a change in an owned stock's recommendation. The scheduler recomputes recommendations for
every owned stock every scheduled run, so the system already knows the moment a position flips
from `HOLD` to `TRIM`/`REVIEW`/`SELL`/`STRONG_SELL` — but that's only visible if the user happens
to open the dashboard and notice it in the table. Given the notification infrastructure (including
the optional webhook) already exists for automation events, extending it to recommendation
transitions on owned stocks would turn the app from something you have to remember to check into
one that tells you when something actually needs attention.

### G6 — The Summary & Recommendation table is sorted alphabetically, not by urgency

`main.py:2108` sorts workbench rows by `(scope, stock symbol)` — the same order used everywhere
else in the app for consistency, but it means a `STRONG_SELL` on one stock has no more visual
priority than a `BUY_MORE` on a stock 29 rows below it alphabetically. With more than a handful of
holdings, spotting what actually needs a decision this week requires reading the whole table.
Combined with G5, this compounds: nothing pushes urgent items to the user, and the one place they
are all listed doesn't rank them by urgency either. A "what needs my attention" view — sorted by
recommendation severity (the same severity ranking already computed at `main.py:957-960` for
account aggregation) — would directly serve "guide the user" rather than just "inform the user."

### G7 — No mid-funnel watchlist between "just researching" and "passes the Strong Buy gate"

A stock can be researched via Financial Analysis without being added to the portfolio
(`README.md`'s "Search Upstox to add an unowned company without creating a transaction"), but
there's no persistent "I'm watching this, here's why, tell me if it changes" list distinct from
Owned and from Prospective-Strong-Buy. Today, a stock that's interesting but not yet passing the
`STRONG_BUY` gate (e.g. it's a `WATCH`-tier `recommend_prospective` result) simply isn't listed
anywhere the user would revisit — they'd have to remember to re-search it later. A lightweight
watchlist with the same evidence tabs already built for Owned/Prospective would close this.

### G8 — No connection between "I have cash to deploy" and "here's where it should go"

Rebalancing guidance (TRIM an overweight position) and new-idea sourcing (Prospective Strong Buy
list) are two disconnected views. There's no single "you have new capital — here's a ranked
suggestion across both trimming overweight positions and adding to underweight/prospective ones"
workflow. This is a natural extension once G1 (real cash tracking) and G2 (portfolio-level
diversification) exist.

### G9 — Sell/Trim guidance is entirely tax-blind, even though the ledger has exactly what's needed to fix that

The README explicitly disclaims tax reporting, and that's a reasonable MVP boundary — but the FIFO
engine already tracks each lot's purchase date precisely (`portfolio.py`), which is exactly what's
needed to know whether a given lot is still short of India's one-year LTCG threshold. Right now
`TRIM`/`SELL` reasoning never mentions this, so a user could realize short-term capital gains
tax unnecessarily by acting on a recommendation a few weeks before a lot would have crossed into
long-term treatment. Even a simple annotation on the TRIM/SELL reasons — "N of M shares in this
lot become long-term-eligible on [date]" — would materially improve the quality of the guidance
without building full tax reporting.

## Phased implementation plan

Grouped the way this project already groups its own roadmap — each phase has one goal, ships as a
coherent unit, and unlocks the next. Findings are referenced by the IDs used above (`C` = Critical,
`H` = High, `G` = functional/product gap, plus selected Medium/Low items and test gaps). Nothing
here is a new finding — this is purely a sequencing of everything already documented.

Each phase ends with a **testing sub-phase** with two parts, and isn't considered done until both
pass:

1. **Automated** — the specific `pytest` cases that phase's fixes should add or change, run
   against the full suite so nothing else regresses.
2. **Manual (browser)** — drive the actual running app, not just the API, and look at the result,
   the same way this review did. With the Docker stack up (`docker compose ps`), point headless
   Chromium at `http://localhost:8501` — no browser-automation tool was pre-installed here, so
   `npx playwright install chromium` followed by a short Node script using `chromium.launch()` →
   `page.goto()` → `page.screenshot()` is the fastest path in this environment; reuse that pattern
   rather than re-solving it each phase. Confirm no console errors (`page.on('console', ...)`
   filtered to `type() === 'error'`), and actually look at the screenshot — a page can render its
   shell while the data underneath is still wrong.

Each phase closes with explicit **exit criteria** stating what "done" looks like in both halves.

| Phase | Goal | Key items |
|---|---|---|
| 1 | Stop the bleeding — nothing on screen is wrong, stale, or crashes | C1, C2, H2, H6 |
| 2 | Automation and LLM-derived data can run unattended without a human catching a bad fact | H4, H5, XBRL/stalled-run/OpenRouter Mediums |
| 3 | Recommendations are held to the same rigor for owned stocks as prospective ones, and are tested | H1, recommendation-priority + leverage-ratio + sector-detection Mediums, `recommend()`/`recommend_prospective()` tests |
| 4 | The interface never misleads and is consistent everywhere | H7, H8, H9, dashboard-layout Medium, UI Mediums/Lows |
| 5 | The data layer defends itself even when the API layer is bypassed | DB constraints/indexes Mediums, `main.py` decomposition, Low-severity data items |
| 6 | Move from informing to guiding — the functional gaps | G1, G5+G6, G2–G4, G7–G9 |

### Phase 1 — Stop the bleeding

Goal: nothing the user looks at can be silently wrong, stale, or crash the page. Small, mostly
localized changes; do this first because everything else is easier to trust once the dashboard
itself is trustworthy.

- **C1** — swap the two branches in `target_price_date()`; fix the inverted test alongside it.
- **C2** — guard the unguarded `portfolio_snapshot()` call sites; add validation to
  `/imports/opening`; add a way to void/reverse a bad transaction.
- **H2** — forward `snapshot["summary"]` into `/stock-workbench` and render it on the dashboard.
- **H6** — invalidate/refresh the cached snapshot (or at least surface a clear "may be stale"
  prompt) when a thesis is saved.

#### Phase 1 testing sub-phase

*Automated:* fix `test_target_date_prevents_same_day_preclose_lookahead`
(`test_notional_portfolio.py:60-64`) to assert the corrected pre/post-16:00 behavior, not the
inverted one, and add a case exactly at the 15:30 close boundary; add a regression test that seeds
an oversell condition and asserts `GET /instruments`, `/prospective-stocks`, `/stock-workbench`,
and `/investor-signals` return a clean 4xx instead of an unhandled 500; add a test that
`/imports/opening` rejects a zero/negative-quantity row before commit. Run the full suite
(`pytest`) and confirm no prior test regresses — in particular, re-run
`test_notional_portfolio.py` in full since C1's fix changes settlement timing for every existing
fixture that relies on it.

*Manual (browser):* load the dashboard and confirm a total-portfolio-value metric is now visible
without scrolling into a sub-tab (H2). Edit a thesis in Thesis & Review, return to the dashboard,
and confirm the new thesis text appears without needing a forced run (H6). Import a deliberately
bad opening-balance CSV row (negative/duplicate quantity) through Portfolio Setup and confirm the
UI shows a clear validation error rather than the dashboard 500ing on the next load.

*Exit criteria:* the inverted test now encodes the correct look-ahead rule and passes; a seeded bad
ledger row produces a handled 4xx everywhere `portfolio_snapshot()` is called, confirmed both by
the new regression tests and by reproducing it once in the browser; the dashboard shows total
portfolio value and reflects a thesis edit on first reload.

### Phase 2 — Automation and pipeline safety

Goal: unattended scheduled runs, and any data an LLM touches, can be trusted without a human
watching every run.

- **H4** — route all LLM-sourced disclosure matches through human review regardless of alias
  confidence.
- **H5** — derive `run_key` from intended scope, not a fresh timestamp; wrap `_upsert_disclosure`
  in the same catch-and-refetch pattern as `begin_run`.
- Medium: fix the dead `True or ambiguous` fuzzy-match flag; make XBRL parsing as defensive as its
  own LLM fallback; alert on stalled runs instead of failing silently; align OpenRouter privacy
  settings between `disclosure_pipeline.py` and `document_analysis.py`; retain a trail for
  below-threshold shareholder-name matches.

#### Phase 2 testing sub-phase

*Automated:* add a test that two `_upsert_disclosure` calls for the same
(investor, instrument, period) race (or are simply called twice) and assert the second is handled
via catch-and-refetch, not an unhandled `IntegrityError`; add a test that two `begin_run` calls
for the same intended scope within the same window collide on `run_key` (this requires fixing the
timestamp-based key first — the test should fail against the current implementation and pass after
the fix); add a test asserting `match_investor_alias` returns the real `ambiguous` value, not
`True` unconditionally, covering both a genuinely ambiguous pair and a clean 0.99-confidence match;
add a test that an `OLLAMA:`/`OPENROUTER:`-sourced observation always lands in
`InvestorAliasReview` regardless of alias confidence. Run the full `disclosure_pipeline`/
`automation_runs` test modules.

*Manual (browser):* on Followed Investors, trigger a forced disclosure ingestion and a manual
`POST /analysis-schedule/run` in quick succession (or during a scheduled window) and confirm the
run history doesn't show two concurrent passes over the same mapping (H5). Force a run against a
mapping expected to produce an LLM-parsed (not XBRL) observation and confirm it appears in the
alias review queue rather than being auto-applied (H4). Check that a previously-silent stalled run
now produces a visible notification/alert entry (Medium item).

*Exit criteria:* concurrent-trigger tests pass and reproduce cleanly in the browser (no duplicate
mapping passes in run history); every LLM-sourced disclosure observation appears in the review
queue in a live forced run; a stalled run produces a visible alert without manual intervention.

### Phase 3 — Recommendation logic quality

Goal: owned-stock recommendations get the same evidence-based rigor prospective stocks already
get, and the two rule engines that drive every recommendation stop being untested.

- **H1** — gate `BUY_MORE` on financial quality/valuation, not position size alone.
- Medium: reorder the loss-vs-weight priority check in `recommend()`; verify the `total_liability`
  leverage-ratio assumption against a real payload; centralize financial-sector detection into one
  helper; make the promoter-holding-decline flag look at cumulative multi-quarter change.
- Test coverage: add unit tests for `recommend()` (all six outcomes, not just `STRONG_SELL`) and
  `recommend_prospective()` (currently zero), plus `calculate_position()` edge cases (`OPENING`,
  `ADJUSTMENT_IN`/`OUT`, same-day ordering) and route-level tests for the oversell guard.

#### Phase 3 testing sub-phase

*Automated:* this phase should raise `recommend()`'s test count from one to full-outcome coverage
— add cases for `TRIM` (both the weight-based and profit-taking branches), `SELL`, `REVIEW` (loss
and `WATCH`-thesis), `HOLD`, `BUY_MORE` under the new evidence-based gate, and the
`has_price=False` path; add a case reproducing the current priority-ordering bug (overweight *and*
past the loss threshold) and assert the fix surfaces both reasons. Add the first-ever tests for
`recommend_prospective()` covering `STRONG_BUY`, `BUY`, `AVOID` (both the score<50 and
high-governance-flag paths), `WATCH`, and `MISSING_DATA`/`SECTOR_SPECIFIC_REQUIRED`. Add
`calculate_position()` cases for `OPENING`, `ADJUSTMENT_IN`/`OUT`, and same-day multi-transaction
ordering. Add one route-level test that posts a same-day SELL-before-BUY pair through
`/transactions` and asserts the oversell guard rejects it with a 4xx.

*Manual (browser):* on the dashboard, pick a comfortably-sized, non-overweight owned position and
confirm `BUY_MORE` now cites the same financial-score/valuation evidence shown on that stock's
Financial Analysis tab, rather than only a position-sizing reason (H1). Spot-check a position
that's both overweight and down past the loss threshold and confirm the recommendation reasons now
mention both conditions, not just weight.

*Exit criteria:* `recommend()` and `recommend_prospective()` both have tests for every documented
outcome and the suite is green; a live owned position's `BUY_MORE` reasoning visibly cites
financial/valuation evidence, confirmed in the browser, not just in a unit test.

### Phase 4 — UI trust and consistency

Goal: the interface explains itself, never shows a misleading error, and behaves the same way on
every page.

- **H7** — add a way to view/download disclosure source documents; ideally extract human-readable
  fields alongside the raw bytes, matching the pattern already used for annual reports and IPO
  documents.
- **H8** — stop conflating connection failures with HTTP error responses; don't blank the whole
  page over a non-critical call failing; coerce non-string error `detail` payloads to text.
- **H9** — give wide/free-text columns (Thesis, Thesis summary) an explicit `column_config` width,
  or move them out of the dense numeric table, so the grid produces real horizontal scroll instead
  of silently shrinking and truncating columns.
- Medium: move the automation schedule/metrics/run-history panels off the main dashboard onto a
  dedicated page; fix Notional Portfolio's missing currency/percent formatting; add a deep link
  from the dashboard's governance-flag/style counts to the detail page and stock already selected.
- Low: the remaining UI polish items (percentage precision, `st.json()` dumps, the hardcoded
  `VAIGLO` debug default, free-text IPO dates, code-style consistency).

#### Phase 4 testing sub-phase

*Automated:* add tests for `common.py`'s error-handling helpers directly (not through Streamlit) —
assert a connection failure and an HTTP 4xx/5xx produce visibly different messages, and that a
dict- or list-valued `detail` payload is coerced to readable text rather than passed through
`repr()`. If a shared "disclosure content" endpoint is added for H7, add an API test that it
returns the stored bytes with the correct content type for a known `DisclosureDocument`.

*Manual (browser, primary check for this phase — most of it is visual):* screenshot the dashboard
at a normal desktop width (~1600px) and confirm: the Owned/Prospective tabs are visible without
scrolling past automation panels (Medium layout item); every column in the Portfolio & Thesis
table is either fully visible or reachable via a real scrollbar, with no mid-word truncation like
"Not rec…" (H9 — this is exactly what the original screenshot in this review caught); a
disclosure's source document is downloadable/viewable from Followed Investors (H7). Force an API
error (e.g. stop the `api` container briefly) and confirm the UI shows a clear, correctly-labeled
error rather than blanking the page or showing a raw object repr (H8).

*Exit criteria:* a fresh screenshot at 1600px shows the stock tables above the fold with no
truncated cells; a deliberately triggered API error renders a correctly-labeled message, verified
live, not inferred from code; disclosure source documents are reachable from the UI.

### Phase 5 — Platform hardening

Goal: correctness holds even when something bypasses the API layer — a future script, a direct DB
edit, a migration.

- Medium: add `CheckConstraint`s for quantity/price/charges positivity; add indexes on
  `Transaction.account_id`/`.instrument_id`/`.trade_date`; move `main.py`'s embedded orchestration
  functions into dedicated, testable modules matching the pattern already used by `screening.py`
  and `style_engine.py`.
- Low: `ondelete` cascade behavior on foreign keys; validate `recommendation`/`action` free-text
  fields against real vocabularies; keep decision-boundary arithmetic in `Decimal` instead of
  converting to `float` before threshold comparisons.

#### Phase 5 testing sub-phase

*Automated:* add a test that inserts a negative/zero quantity or price directly through the ORM
session (bypassing Pydantic) and asserts the new `CheckConstraint`s reject it at the database
level; run `EXPLAIN`/a query-count assertion on `portfolio_snapshot()` before and after the new
indexes to confirm the index is actually used, not just present; since `main.py`'s orchestration
functions are moving into dedicated modules, re-run the full suite and confirm test coverage
for those functions increases (this is the first point they become directly importable and
testable in isolation) rather than staying at zero.

*Manual (browser):* this phase is mostly invisible by design (constraints, indexes, module
boundaries) — confirm nothing regressed rather than looking for something new. Reload the
dashboard, Transactions, and Reconcile pages and confirm they render with the same data and at
comparable speed to before the refactor. Attempt one of the previously-crashing inputs from Phase
1's testing sub-phase again — this time via a route that bypasses the API-layer check if one
exists — and confirm the new DB-level constraint rejects it too.

*Exit criteria:* the full suite is green with the new module boundaries; a direct DB-level
constraint violation is rejected even when the API-layer check is bypassed; no visible regression
on any page touched by the `main.py` refactor.

### Phase 6 — Guide long-term strategy

Goal: the platform moves from reporting numbers to actively guiding a long-term decision — this is
the highest-value phase once the foundation above is solid, and it's the direct answer to "make
the platform user-friendly and able to guide long-term strategy."

1. **G1** first — extend the notional portfolio's return/drawdown/benchmark calculations to the
   real ledger. This is the single biggest gap: the platform currently tracks performance history
   for the fake portfolio and not the real one.
2. **G5 + G6** together — notify on recommendation changes for owned stocks, and sort the summary
   table by urgency instead of alphabetically. Both are close to free given what the scheduler
   already computes every run, and together they turn the app from "check it and scan for
   problems" into "it tells you what needs attention."
3. **G2 + G3** — portfolio-level sector/cap-segment diversification view, and real-account cash
   tracking. Both are prerequisites for any sizing or rebalancing guidance.
4. **G4** — turn `target_horizon_months` into an actual review trigger instead of inert text.
5. **G7 + G8** — a watchlist tier between "researching" and "Strong Buy," and a unified
   "here's where new cash should go" view spanning trims and new ideas — natural once G2/G3 exist.
6. **G9** — annotate TRIM/SELL reasoning with LTCG-eligibility dates per lot; the FIFO engine
   already has everything this needs.

#### Phase 6 testing sub-phase

*Automated:* add tests for the real-portfolio return/drawdown/benchmark calculations mirroring the
existing notional-portfolio test suite (same edge cases: no cash flows yet, a single lot, a
corporate action) since G1 is new, previously-untested logic; add a test that a recommendation
transition (e.g. `HOLD`→`SELL`) on an owned stock produces exactly one new `AppNotification`, and
that an unchanged recommendation produces none (G5); add a test asserting workbench rows sort by
recommendation severity, not alphabetically (G6); add tests for the new sector/cap-segment
aggregation matching known fixture portfolios (G2) and for horizon-elapsed detection surfacing a
`REVIEW` prompt (G4).

*Manual (browser, this is the phase where the platform should visibly feel different — verify it
as a user would, not just as data in an API response):* confirm the dashboard shows a real
historical value chart for the actual portfolio, not just a snapshot number (G1); confirm a
recommendation change on an owned stock produces a visible notification without the user having
opened the dashboard first (G5); confirm the Summary & Recommendation tab now leads with
`STRONG_SELL`/`SELL` rows rather than alphabetical order (G6); confirm a sector/cap-segment
breakdown and a cash figure are visible somewhere on the dashboard (G2/G3). Screenshot before and
after to compare.

*Exit criteria:* every G-item has both a passing automated test and a live, screenshotted
confirmation in the browser — this phase isn't done when the code is correct, it's done when the
platform visibly guides rather than just informs.
