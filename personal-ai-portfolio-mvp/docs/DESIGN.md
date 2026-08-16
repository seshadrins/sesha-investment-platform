# MVP Design

## Design goals

1. Keep the transaction ledger authoritative and auditable.
2. Derive holdings from transactions rather than editing balances.
3. Separate calculations from AI-generated explanations.
4. Continue working if every external data source is unavailable.
5. Make recommendation rules visible and configurable.
6. Bind all exposed services to localhost.

## Modules

### Portfolio ledger

Stores accounts, instruments and immutable transactions. Corrections are made using reversal or adjustment transactions rather than silently modifying history.

### Lot engine

Processes Buy, Sell and Opening Balance transactions in date order. The MVP closes lots using FIFO. It calculates current quantity, remaining cost, average cost and realised profit.

### Price service

Stores one price per instrument and date. The latest stored price drives current portfolio valuation.

### Thesis service

Stores the current investment thesis, expected holding horizon, catalysts, risks and invalidation conditions.

### Recommendation engine

Produces portfolio-aware research prompts:

- `ADD`: thesis healthy, allocation below limit and valuation/performance conditions supportive.
- `HOLD`: no material portfolio rule requires action.
- `TRIM`: position exceeds concentration limit or large profit plus concentration warrants review.
- `SELL`: thesis explicitly marked invalid or severe loss with broken thesis.
- `REVIEW`: information is incomplete or a risk threshold is crossed.

The engine does not predict a guaranteed future return.

## Data flow

```text
CSV/manual transaction
  -> validation
  -> transaction ledger
  -> FIFO lot calculation
  -> current position
  -> price valuation
  -> portfolio constraints
  -> recommendation
  -> decision journal
```

## Security

- API and UI are bound to `127.0.0.1`.
- Database has no host port.
- Credentials are supplied using `.env`.
- Imported files stay on the local machine.
- No external LLM receives portfolio data.
