# Changes — Set B

A second batch of usability feedback, investigated against the running code and the live
Docker stack before proposing anything — not just taken at face value. Grouped into phases
by dependency and blast radius: an additive column with no logic change first, then a
display change that needs a small backend addition, then a refactor of code already shared
between two pages, then a single-page navigation/dedup cleanup, then the one item that
needed the most judgment (a new commentary surface, and a Streamlit interaction limitation
to work around).

Each item states what was found, then the concrete implementation plan. Nothing in this
document has been implemented yet — it's the plan to review before starting.

## Phase 1 — Portfolio & Thesis: add a Value at Cost column

`services/ui/workbench_view.py` (`render_scope`, OWNED branch of `portfolio_tab`, ~lines
44-90) — the table shows Quantity, Average cost, Current price, Market value, Unrealised
P&L, Return, and Weight, but never the actual rupee amount invested (`Quantity × Average
cost`). A user has to mentally multiply two columns to know how much capital sits in a
position before comparing it against Market value.

That total already exists server-side under the name `remaining_cost` (the FIFO cost
basis) — `services/api/app/services.py:110-129` (`portfolio_snapshot()`, backing `GET
/portfolio`) already returns it per position, and `services/api/app/automation_pipeline.py`
(`_build_stock_workbench()`, backing `GET /stock-workbench`, the endpoint the dashboard
actually renders from) computes it internally per instrument but drops it before returning
the row — only `average_cost`, `quantity`, `market_value`, etc. survive into the row's
`"portfolio"` dict.

**Plan:**
- Add `remaining_cost` to the per-row `"portfolio"` dict inside `_build_stock_workbench()`
  — it's already computed there, so this exposes an existing internal number rather than
  adding new logic.
- In `workbench_view.py`'s OWNED `portfolio_tab`, add a **Value at cost** column (via
  `portfolio_field(row, "remaining_cost")`) positioned right after **Average cost**,
  formatted like Market value (₹, 2 decimals).
- No change needed for the dashboard's per-account filter — the `account_positions`
  override already carries `remaining_cost` from `GET /portfolio`.
- The Notional Portfolio's `notional_positions` override dict (`pages/8_Notional_Portfolio.py`,
  added in Changes-SetA Phase 4) needs a `"remaining_cost"` key added
  (`quantity * average_cost` for the notional holding), since it feeds the same
  `portfolio_field()` lookup.

**How to verify:** Portfolio & Thesis shows **Value at cost** between Average cost and
Current price for owned stocks, matching `Quantity × Average cost` for a sampled row;
Notional Portfolio's Portfolio view tab shows the same column using the notional simulated
position; Prospective-scope rows (no quantity/cost concept) are unaffected since Value at
cost only applies to the OWNED branch.

## Phase 2 — Price movement indicator and a one-click "sync prices only" button

Two related findings about the same gap: nothing distinguishes "the price moved up" from
"moved down" from "is just old," and refreshing prices alone from the dashboard takes more
clicks than it should.

### 2a. No up/down indicator, because no prior price is stored or exposed anywhere

`services/api/app/models.py:302-312` — the `Price` model stores only `instrument_id,
price_date, close_price, source`: a single point in time. A repo-wide search for
`previous_close|prior_close|price_change|pct_change|day_change` returns nothing anywhere in
the API. So a green/red arrow can't be added purely in the UI — it needs the previous
trading day's stored close alongside the latest one, which isn't currently fetched or
carried by either the `/portfolio` or `/stock-workbench` payload.

**Plan:**
- Wherever the latest `Price` row per instrument is currently fetched for `current_price`
  (`services.py` / `automation_pipeline.py`), also fetch the second-most-recent stored
  `Price` row for the same instrument (`ORDER BY price_date DESC LIMIT 2`).
- Expose `previous_close` alongside `current_price` in both the `/portfolio` and
  `/stock-workbench` payloads.
- In `workbench_view.py`'s Current price column, render a ▲/▼ marker (green/red) based on
  `current_price` vs `previous_close` — likely via the same `pandas.Styler`/badge approach
  already used for Financial Analysis's color-coded scores (Changes-SetA Phase 5), for a
  consistent look.
- When only one stored price exists yet (new instrument, cold start), omit the arrow rather
  than guessing a direction.

### 2b. A prices-only sync already exists — just not from the Portfolio view itself

`services/ui/pages/12_System_Status.py:25-33` already has a working **Prices only** option
in the Forced run scope dropdown, hitting `POST /analysis-schedule/run?job=prices` (wired in
`main.py` to `_refresh_analysis_market_data`). Separately, `services/ui/pages/10_Prices.py`'s
own "Upstox sync" tab has a *different* single combined button (`/providers/upstox/sync`
with `include_prices=True` hardcoded, no prices-only toggle). Neither existing path is one
click away from the Portfolio & Thesis tab itself — a user has to leave the dashboard and
either pick from a dropdown on System Status or find the right tab on Prices.

**Plan:**
- Add a small **"Sync prices only"** button directly on the dashboard (near the existing
  account-filter row), calling the same `/analysis-schedule/run?job=prices` endpoint System
  Status already uses — no new backend route needed.
- On success, `st.rerun()` so refreshed prices (and their new up/down arrows) are visible
  immediately, matching the existing forced-run success pattern on System Status.

**How to verify:** after a manual price change for one owned stock (e.g. via Prices page's
Manual price tab), the Portfolio & Thesis tab shows an up or down marker matching the
direction of that change on next load; the dashboard's new "Sync prices only" button
triggers the same job as System Status's "Prices only" option and needs no navigation away
from the dashboard.

## Phase 3 — Full three-way deep-link parity between Portfolio & Thesis and Summary & Recommendation

`services/ui/workbench_view.py` — `portfolio_tab` and `summary_tab` both already use the
identical row-selection-then-button deep-link pattern (`st.dataframe(...,
on_select="rerun", selection_mode="single-row")` → `st.session_state["deep_link_symbol"]` →
`st.switch_page(...)`), but each only wires up part of the destination set:
`portfolio_tab` only has **Review thesis →** (→ `pages/3_Thesis_and_Review.py`);
`summary_tab` only has **Open Financial Analysis** and **Open Investor Style Fit** (→
`pages/4_Financial_Analysis.py`, `pages/5_Investor_Styles.py`). A user who selects a stock
on Portfolio & Thesis can only jump to its thesis, not its financial analysis or style fit,
and vice versa on Summary & Recommendation.

**Plan:**
- Factor the row-selection-derived jump-buttons into one shared helper in
  `workbench_view.py`, e.g. `render_deep_link_buttons(rows, selected_indices, scope)`,
  emitting all three buttons (Review thesis, Open Financial Analysis, Open Investor Style
  Fit) with distinct widget keys per calling tab so both tabs can host it without key
  collisions.
- Call it from both `portfolio_tab` and `summary_tab` after their respective selection
  block, so both tabs offer the same three destinations.

**How to verify:** selecting a stock on Portfolio & Thesis (Owned or Prospective) shows all
three buttons, each correctly `st.switch_page`-ing with the stock pre-selected on the
destination page; the same check repeated from Summary & Recommendation; Notional
Portfolio's Portfolio view tab (which also calls `render_scope()`, Changes-SetA Phase 4)
gets the same three-button parity for free, no separate change needed there.

## Phase 4 — Investor Styles page: Rule evidence first, less duplication with the dashboard

`services/ui/pages/5_Investor_Styles.py:27-30` — five tabs in this order: **Owned vs
prospective, NIFTY 500 screening, Rule evidence, Style configuration, Point-in-time
backtest**. The first tab, "Owned vs prospective" (a stock-by-style grid from a fresh
`/investor-styles/matrix` call), is largely redundant with the dashboard's own Investor
Style Fit tab (`workbench_view.py`'s `style_tab`), which already shows the same grid — in
fact a superset, adding evidence-coverage % and explicit NO DATA/NOT APPLICABLE states —
sourced from the already-cached workbench snapshot. A user landing on this page's default
tab sees a slightly worse version of something they likely just saw on the dashboard,
before reaching "Rule evidence" (the page's genuinely distinct per-rule pass/fail detail,
shown nowhere else) two tabs later. It's also where the dashboard's "Open Investor Style
Fit" deep link already lands and pre-selects a company — currently on the third tab, an
extra click away from that landing.

**Plan:**
- Reorder tabs so **Rule evidence** is first.
- Demote "Owned vs prospective" to the last position rather than removing it — its
  Prospective sub-tab has a "why these stocks qualified" recommendation table the dashboard
  tab doesn't show, so it stays as a reference view.
- Proposed order: Rule evidence, NIFTY 500 screening, Style configuration, Point-in-time
  backtest, Owned vs prospective.

**How to verify:** opening Investor Styles directly shows Rule evidence first; the
dashboard's "Open Investor Style Fit" button lands on this same first tab with the stock
pre-selected (no extra click); "Owned vs prospective" still works identically, just moved
to the last tab position.

## Phase 5 — Followed Investor Signals: per-disclosure commentary

`services/ui/pages/6_Followed_Investors.py` (`tabs[0]`, Stock × investor matrix) — every
cell is plain text (`f"{signal} · {ownership_pct:.2f}%{stale}"`) inside a `st.dataframe(...)`
call with no `on_select`/`selection_mode` — unlike every other table in this app (Portfolio
& Thesis, Summary & Recommendation both already use row-selection), this matrix has no
click or selection mechanism at all today. The percentage shown is `ownership_pct` (the
investor's currently disclosed stake) — easy to mistake for a confidence/match score, since
a *different* `confidence` field does exist elsewhere in the app (the alias-name-matching
score on `InvestorAliasReview`, shown only in the separate "Alias review" tab, entirely
unrelated data). Nothing on this page explains what the percentage means or how it should
inform a decision, beyond the page-level "Public disclosures can be delayed or incomplete.
Verify the original filing before acting." warning at the very bottom.

**Plan:**
- Add row-selection to the matrix (`on_select="rerun", selection_mode="single-row"`),
  matching the pattern already used elsewhere in this app.
- Streamlit's `st.dataframe` selection is row-level only — there's no native per-cell
  click — so "clicking a disclosure" becomes: select the stock's row, and a commentary
  panel opens below listing one card per followed investor with a disclosure for that stock
  (most rows will have exactly one, since the matrix is sparse).
- Each card renders the already-available-but-currently-hidden fields per cell
  (`report_date`, `filed_on`, `previous_ownership_pct`, `change_percentage_points`,
  `signal`, `source_url`, `stale`, `document_id`) plus a **template-based, deterministic**
  explanation of what the numbers mean — e.g. "*{investor} disclosed {ownership_pct}%
  ownership as of {report_date}, {change_percentage_points:+.2f} points versus the prior
  filing ({previous_ownership_pct}%) — classified as {signal}. This is corroborating
  evidence only: it does not itself drive a BUY/SELL recommendation and can lag real
  trading activity by the exchange's filing lag.*"
- **Decision point, flagged rather than decided here:** an LLM-generated qualitative
  commentary (reusing the Ollama/OpenRouter infra already wired for parsing) would be a
  richer follow-up, but is deliberately left out of this plan. It would be new
  judgment-generating surface area in a pipeline that has so far kept LLM usage scoped to
  deterministic-parsing fallback only, never judgment — worth its own review rather than
  folding into a UI fix.

**How to verify:** selecting a stock row with at least one disclosure opens a commentary
card per investor; the card's numbers match the same `ownership_pct` /
`change_percentage_points` / `report_date` already visible in the Activity feed tab for
that stock/investor pair; a stock row with no disclosures for any followed investor shows
no cards (not a blank/broken panel); the explanatory text never uses the word "confidence"
(that field doesn't exist on this data) and never recommends an action.
