# Changes — Set A

A batch of usability feedback from actually using the app, investigated against the running
code and (where relevant) the live Docker stack before proposing a fix — not just taken at
face value. Grouped into phases by dependency and blast radius: navigation/content moves
first (safe, no logic changes), then workflow changes, then features that touch shared
rendering code, then the one item that needed live investigation to root-cause.

Each item states what was found, then the concrete implementation plan. Nothing in this
document has been implemented yet — it's the plan to review before I start.

## Phase 1 — Navigation cleanup

Goal: the sidebar and dashboard reflect how the app is actually used. No logic changes,
just moving existing content and renumbering pages (Streamlit's sidebar order follows the
`N_Name.py` filename prefix).

### 1. Move automation controls off the main dashboard onto System Status

`services/ui/app.py:27-112` — the "All due morning activities / Run now" row and "Today's
data refresh details" expander sit at the very top of the dashboard, above the stock
tables. System Status (`pages/12_System_Status.py`) already owns the schedule/metrics/run
history for this exact subsystem (moved there in an earlier phase for the same reason:
keep the dashboard about stocks, not automation internals). This is the same class of fix,
just a section that got missed.

**Plan:** Cut the `schedule_info/force_choice/force_action` row and the "Today's data
refresh details" expander out of `app.py` and paste them into `12_System_Status.py`
(the forced-run button needs its `st.rerun()` to stay local to that page). Leave the
compact health banner (`st.error`/`st.warning`/`st.info` based on `health["status"]`) on
the dashboard — that's the "something needs attention" signal a user should see without
navigating away; only the controls and detail expander move.

### 2. Reorder pages: Prices and Reconcile move down

`services/ui/pages/3_Prices.py`, `5_Reconcile.py` — both are infrequently used (Prices
mainly during initial setup or an occasional manual price fix; Reconcile only when
cross-checking against a broker statement). Their current numbering (3, 5) puts them
ahead of Financial Analysis, Investor Styles, and Followed Investors, which are used far
more often.

**Plan:** Renumber the `pages/` files so frequently-used pages sort first. Proposed order:

| # | Page | Rationale |
|---|---|---|
| 1 | Portfolio Setup | Accounts + cash, unchanged |
| 2 | Transactions | Upload-centric after Phase 2 below |
| 3 | Thesis and Review | Used every time a position is reviewed |
| 4 | Financial Analysis | Core research workflow |
| 5 | Investor Styles | Core research workflow |
| 6 | Followed Investors | Core research workflow |
| 7 | Document Analysis | Occasional but still research-path |
| 8 | Notional Portfolio | Simulation, not daily |
| 9 | IPOs | Occasional |
| 10 | Prices | Setup/troubleshooting only |
| 11 | Reconcile | Occasional cross-check |
| 12 | System Status | Unchanged position, operational |
| 13 | Watchlist and Strategy | Unchanged position |

This is a batch `git mv` of files (Streamlit has no separate nav-order config — the
filename prefix *is* the order) plus updating the handful of hardcoded
`st.page_link("pages/N_...")`/`st.switch_page(...)` references that currently bake in the
old numbers (`app.py`'s "Manage data and workflows" links, the Summary & Recommendation
tab's deep-link buttons, Financial Analysis's "Add an instrument under Transactions"
pointer, etc. — a grep for `pages/\d+_` finds every call site).

**How to verify:** sidebar order matches the table above; every `page_link`/`switch_page`
still resolves (no broken deep links from the dashboard's "Open Financial Analysis" /
"Open Investor Style Fit" buttons).

## Phase 2 — Transactions: upload-only workflow

`services/ui/pages/2_Transactions.py:42-71` — the "Record transaction" tab is a one-row
manual-entry form. It's confirmed unused: transactions are only ever entered via the CSV
uploaders on Portfolio Setup (`Opening holdings` and `Transaction history` tabs). Having a
manual-entry form as a top-level tab suggests it's the primary path when it isn't, and the
CSV upload — the path actually used — lives on a different page entirely (Portfolio Setup),
disconnected from the Transactions/Ledger page.

**Plan:**
- Remove the "Record transaction" tab and its form from `2_Transactions.py`.
- Move the "Transaction history" CSV uploader (currently
  `1_Portfolio_Setup.py`'s `history_tab`) into `2_Transactions.py`, so the page becomes:
  **Instruments** (unchanged — CSV rows still need instruments to exist or auto-create
  cleanly with ISIN/sector prefilled) → **Upload transactions** (the moved CSV uploader) →
  **Ledger** (unchanged, read-only view).
- Leave "Opening holdings" CSV upload on Portfolio Setup — that's a one-time setup action
  tied to account creation, not an ongoing transaction-entry activity, so it stays where the
  accounts themselves are configured.
- No backend change: `POST /transactions` (used only by the removed form) stays in the API
  for completeness/scripting use; nothing else calls it from the UI.

**How to verify:** uploading a transactions CSV from the Transactions page produces the
same `imported` count and ledger rows as today's Portfolio Setup uploader; the manual
single-row form is gone; Portfolio Setup's Opening holdings flow is untouched.

## Phase 3 — Thesis visibility and a mis-attributed setting

### 3. Replace the inline Thesis/Thesis-summary columns with a link to Thesis & Review

`services/ui/app.py` (`render_scope`, the `Portfolio & Thesis` tab) — the table carries two
free-text columns, `Thesis` and `Thesis summary`, that read "Not recorded" for every stock
without a saved thesis (most of the portfolio right now, since thesis entry is a manual,
per-stock activity nobody's done yet for older holdings). That's not a bug — it's a sparse,
optional field rendered as if it were always-populated data — but it does make those two
columns nearly always dead weight, and there's no way to *act* on what little text is
there without leaving the dashboard, searching the stock again on Thesis & Review, and
re-selecting it.

**Plan:** Replace the two text columns with a single `Review thesis →` link/button per row
that deep-links to `pages/4_Thesis_and_Review.py` with the stock pre-selected — the same
`st.session_state["deep_link_symbol"]` + `st.switch_page(...)` pattern already used by the
Summary & Recommendation tab's "Open Financial Analysis" button (`app.py:311-316`).
Streamlit's `st.dataframe` doesn't support a real per-row button inside a data grid, so this
needs the same shape as the existing Summary & Recommendation tab: a row-selection
dataframe (`on_select="rerun", selection_mode="single-row"`) plus a button below it that
fires the deep link for whichever row is selected. Apply that same selection+button pattern
to the Portfolio & Thesis tab instead of plain columns.

### 4. Decouple "configured maximum allocation" from the Thesis flow, and surface where it lives

Root cause, confirmed by reading `config.py`: `max_position_weight` (15%) and
`trim_position_weight` (20%) are plain env-var settings (`.env` / `compose.yaml`) with
**no database row, no settings UI anywhere, and no page that shows their current value** —
unlike the automation schedule, which is DB-backed and editable on System Status. The
*only* place these numbers become visible to the user is inside recommendation reason
text — e.g. `recommendations.py:90`, "Position is well below the **configured maximum
allocation**, but financial-quality/valuation evidence does not clear the bar to add" —
which surfaces on the Thesis & Review page's "Why this prompt was generated" section
(`pages/4_Thesis_and_Review.py:35-37`) right next to thesis-specific reasoning. Reading it
there, with no visibility into what "configured" means or where, reasonably reads as if
the thesis itself controls allocation sizing. It doesn't — it's a global, portfolio-wide
risk parameter applied identically to every stock, entirely independent of any individual
thesis.

**Plan:**
- Add a small **read-only "Portfolio risk settings"** panel to System Status (same page
  that already shows the automation schedule) listing `max_position_weight`,
  `trim_position_weight`, `loss_review_threshold`, `profit_review_threshold`, and
  `buy_more_min_financial_score` with a one-line description of what each one gates —
  sourced from a new small `GET /settings` route that just returns the relevant `Settings`
  fields (no new table; this is visibility, not editability, to start).
  - The "settings" module.
- Reword the affected `recommend()` reason strings (`recommendations.py:59-64, 71-74,
  90-97`) from "the configured maximum allocation" to "the portfolio's {N}% maximum
  position weight (see System Status → Portfolio risk settings)" so the text itself points
  at where the number lives instead of implying it's thesis-derived.
- On the Thesis & Review page, add a one-line caption above "Why this prompt was
  generated" clarifying that concentration/loss-threshold reasons come from portfolio-wide
  settings, not the thesis being edited on that page.
- Editability (turning this into a DB-backed, UI-editable setting like the automation
  schedule) is a reasonable follow-up but is out of scope here — flagging it as a fast
  follow rather than bundling a schema change into a UX-clarity fix.

**How to verify:** System Status shows the five thresholds with plain-language
descriptions; a HOLD/TRIM reason mentioning the weight threshold now names where it's
configured; Thesis & Review visibly separates thesis-specific reasoning from portfolio-wide
policy.

## Phase 4 — Notional Portfolio: parity with the Owned workbench

`services/ui/pages/10_Notional_Portfolio.py` — Notional Portfolio's "Holdings & trade" tab
is a flat table (Stock, Quantity, Average cost, Latest price, Market value, Unrealised P&L,
Weight). The real Owned portfolio gets a much richer view for the same stocks — Data &
Freshness, Financial Analysis, Investor Style Fit, Followed Investors, and Summary &
Recommendation tabs, all built once in `app.py`'s `render_scope()`. A notional holding is
by definition also either an Owned or Prospective/Watching stock already covered by that
workbench (`notional_candidates()` in `main.py` already restricts notional buys to
owned-or-recommended stocks) — so the same evidence rows already exist in the cached
`stock_workbench` snapshot; Notional Portfolio just isn't reusing them.

**Plan:**
- Factor `render_scope()` out of `app.py` into a shared UI module (e.g.
  `services/ui/workbench_view.py`) so both `app.py` and `10_Notional_Portfolio.py` import
  the same rendering function instead of forking it.
- Add a **"Portfolio view"** tab to Notional Portfolio, positioned first (before "Holdings
  & trade"), that calls the shared `render_scope()` against the workbench rows whose
  `instrument_id` matches the notional portfolio's current holdings — sourced by cross-
  referencing `GET /stock-workbench`'s `owned`/`prospective` rows against
  `GET /notional-portfolios/{id}`'s `holdings` list (same instrument-id join already used
  by `notional_candidates()` on the backend).
- A notional holding of a stock that's since dropped out of both Owned and Prospective
  (rare, but possible if a Strong Buy stock's rating later slips) won't have a workbench
  row to join against — show a plain "No live evidence view for this holding; it no longer
  appears in Owned or Prospective" message for those rows rather than a blank/broken tab.
- "Holdings & trade" stays as-is for the buy/sell action itself (this is the notional
  ledger's own control surface, not evidence).

**How to verify:** Notional Portfolio's new Portfolio view tab shows the same
Financial-Analysis/Investor-Style/Summary tabs, for the same stocks, as the main dashboard;
`app.py`'s existing Owned/Prospective tabs render identically after the `render_scope()`
extraction (pure refactor — no visual diff expected there).

## Phase 5 — Financial Analysis: color-code good/bad indicators

`services/ui/pages/6_Financial_Analysis.py` (`score_tab`, lines 120-134) — every quality
score (Overall, ROCE, ROE, Leverage, Cash conversion) and every company-vs-sector ratio
row renders as plain black text via `st.metric`/`st.dataframe`. The scores are already
0–100 normalized (`analysis["scores"]`, described in-app as "Scores range from 0–100") but
carry no visual signal for "is this good" — a 25 and an 85 look identical at a glance.

**Plan:**
- **Quality scores** (`score_cols` loop, line 122-124): replace the bare `col.metric(name,
  value)` calls with a small helper that also sets Streamlit's built-in
  `delta`/`delta_color` on the metric — e.g. `delta=f"{value-70:+.0f} vs 70 floor"` with
  `delta_color="normal"` (green when above, red when below) or, if the arrow+delta reads
  oddly for a raw score, fall back to color-coded `st.markdown` badges (green ≥70, amber
  50–69, red <50 — matching the 70 floor `recommend()` already uses for the BUY_MORE gate,
  so the coloring is consistent with what actually drives a recommendation, not an
  arbitrary new cutoff).
- **Company-vs-sector ratio table** (`rows`/`pd.DataFrame`, line 126-129): coloring "is
  this ratio good" requires knowing whether *higher* or *lower* is favorable per metric
  (ROE/ROCE: higher is better; Debt/Equity-style leverage ratios: lower is better; P/E,
  P/B: contextual, not simply "lower is better" independent of growth/quality — leave these
  uncolored rather than guess). `financial_analysis.py` doesn't currently carry this
  direction metadata anywhere — add a small `RATIO_DIRECTION = {"ROE": "HIGHER",
  "ROCE": "HIGHER", "DEBT/EQUITY": "LOWER", ...}` lookup in `financial_analysis.py`,
  include it in the `ratios` payload the API already returns, and apply a pandas
  `Styler.apply()` background color in the UI only for metrics with a known direction —
  metrics without a direction entry render uncolored rather than being colored
  arbitrarily.
- Governance flags (`governance_tab`) already use `st.error`/`st.warning` correctly by
  severity — no change needed there.

**How to verify:** a company with ROE/ROCE ≥70 shows those metrics in green, one with a
score <50 shows red; the company-vs-sector table colors ROE/ROCE/leverage rows but leaves
P/E-style ratios uncolored; nothing crashes when a ratio in the direction lookup is missing
from a given company's payload (governance-flag rendering untouched).

## Phase 6 — Followed Investor Signals: root-caused, not a rendering bug

The screenshot's "ACTION_REQUIRED" banner and "35 unread alerts" look like something is
broken. Checked live against the running stack (`GET /investor-disclosures/status`,
`/investor-disclosures/reviews`, `/notifications`) before proposing anything — the pipeline
itself is working correctly; the *presentation* of routine, working-as-designed state is
what's misleading. Three distinct findings:

### 6a. "ACTION_REQUIRED" conflates real failures with routine pending review

`automation_schedule.py:89-98` (`disclosure_coverage_state`) — the function returns
`ACTION_REQUIRED` if *any* of `failed_mappings`, `parser_failures`, or `pending_aliases` is
nonzero, with no distinction between them. Live, `failed_mappings=0` and
`parser_failures=0` — the only reason coverage shows `ACTION_REQUIRED` is
`pending_aliases=2`, and those 2 are genuinely ambiguous shareholder-name matches sitting
in the review queue exactly as designed (confirmed via `/investor-disclosures/reviews`:
"Radhakishan Shivkishan Damani HUF" at 0.94 confidence and "Gopikishan Shivkishan Damani"
at 0.84, both plausible but below the auto-approve bar for a followed investor). That's
routine, expected, self-clearing-once-reviewed work — not a failure — but it's badged
identically to a parser crash or a disabled ingestion pipeline.

**Plan:** split the single `ACTION_REQUIRED` outcome into two: keep `ACTION_REQUIRED` (red)
for genuine failures (`failed_mappings`, `parser_failures`, `not ingestion_enabled`), and
introduce `REVIEW_PENDING` (amber/info, not red) for the pending-aliases-only case. Update
`8_Followed_Investors.py`'s banner rendering to match the new state with calmer framing
("2 shareholder-name match(es) awaiting your review" rather than "Coverage has ... blockers
requiring intervention").

### 6b. Near-miss alias notifications are pure noise: 32 of 35 unread alerts are near-zero-confidence non-matches

Checked `GET /notifications?unread_only=true` directly: of 35 unread, 32 are
`ALIAS_REVIEW`-category "Shareholder name fell below the alias-match threshold" events —
and their actual confidence scores are 0.56, 0.48, 0.32, 0.36, etc. A 0.32-confidence
"match" (e.g. "S GOPALAKRISHNAN" against a configured investor) isn't a near-miss worth a
user's attention; it's routine noise from every shareholding filing that happens to share a
surname pattern. This is a real design gap in the "near-miss notification trail" feature
added in an earlier phase (Phase 2 of `Recommendations.md`, intended to "retain a trail for
below-threshold shareholder-name matches") — it was built to create an `AppNotification`
for *every* below-threshold candidate with no floor, so it drowns the 3 notifications that
actually matter (`Followed-investor disclosure new`) in 32 that don't.

**Plan:** add a floor below which a near-miss is logged but does **not** create an
`AppNotification` — e.g. only notify when confidence is within a reviewable range of the
auto-approve threshold (`disclosure_fuzzy_match_threshold=0.84`), such as ≥0.60, and simply
record anything below that as a queryable trail (already-existing storage, e.g. a
lightweight table or the existing alias-review-adjacent log) without pushing it into the
unread-notification count. This keeps the audit trail Phase 2 intended while stopping it
from paging the user for matches nobody would plausibly act on.

### 6c. "60 of 528 mappings checked" has no pacing context

`disclosure_batch_size=10` per run means a full cycle across 528 active source mappings
takes ~53 processing days at the current schedule — by design, to avoid hammering NSE/BSE
with requests. Nothing in the UI states this, so "60 of 528" reads as stalled progress
rather than week-11-of-53 of an intentionally slow, polite crawl.

**Plan:** add a caption next to the coverage-progress metric on `8_Followed_Investors.py`:
"`{checked}/{active}` mappings checked this cycle · ~{active/batch_size} scheduled runs to
complete a full pass at {batch_size}/run" — turns an alarming-looking raw fraction into an
explained rate.

**How to verify (whole phase):** with the current live data (0 failed mappings, 0 parser
failures, 2 pending aliases), the page shows `REVIEW_PENDING` framing, not
`ACTION_REQUIRED`; the unread-alert count drops from 35 to 3 (the genuinely new
disclosures) with the 32 low-confidence near-misses still inspectable via their trail, just
not counted as alerts; the coverage caption states the expected full-cycle pace.
