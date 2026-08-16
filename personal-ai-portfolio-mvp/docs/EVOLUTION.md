# Evolution Roadmap

## Phase 2 — market and company research adapters

Status: CSV and read-only Upstox Analytics adapters implemented. Price imports use
`MarketDataProvider`, and company metadata imports use `CompanyResearchProvider`. Upstox sync
resolves stable instrument keys from ISINs, imports reproducible daily historical closes, and
stores company profiles without exposing any trading capability. These contracts allow further
licensed adapters to be added without changing the core ledger.

Provider interfaces remain decoupled from the core ledger:

```python
class MarketDataProvider(Protocol):
    def get_eod_prices(self, symbols: list[str], as_of: date) -> list[Price]:
        ...
```

Candidate adapters:

- User-provided CSV
- Broker export
- Exchange-authorised source
- Optional open-source Python package where its data-source terms permit personal use

## Phase 3 — financial analysis

Status: implemented using read-only Upstox fundamentals with annual and quarterly trends,
sector-aware ROCE/ROE/leverage/cash-conversion scoring, dated valuation observations,
evidence-limited governance flags, prospective-stock discovery, and versioned theses.

Add:

- Annual and quarterly fundamentals
- ROCE, ROE, leverage and cash-conversion scoring
- Historical valuation bands
- Earnings and margin trends
- Governance-risk flags
- Versioned investment theses

## Phase 4 — investor-style models

Represent investor philosophies as version-controlled YAML rule sets. Backtest each rule set using only information available at each historical decision date.

## Phase 5 — local LLM

Add Ollama as an optional Compose profile:

- Summarise user-imported annual reports and transcripts.
- Extract catalysts, risks and invalidation conditions.
- Generate explanations grounded in stored evidence.
- Never use the LLM for arithmetic, lot accounting or hard risk gates.

## Phase 6 — monitoring and alerts

Add an APScheduler service for end-of-day jobs and optional email notifications. Alerts should remain review prompts rather than automatic orders.

## Phase 7 — backtesting and learning

Track every recommendation as an immutable snapshot and calculate:

- Forward 3/6/12-month return
- Maximum adverse excursion
- Maximum favourable excursion
- Benchmark-relative return
- Calibration of confidence scores
- Performance by market regime and investor style
