"""Return/drawdown/benchmark analytics for the real ledger — the equivalent of what
notional_portfolio.py already computes for the simulated paper-trading portfolio (G1 in
docs/Recommendations.md). The real ledger has no modelled cash account (Account has no
cash balance until it's set — see the ``cash_balance`` field), so this can't reuse
notional_portfolio.py's cash-bucket approach directly; instead every BUY/OPENING/
ADJUSTMENT_IN is treated as capital contributed from outside the tracked system, and every
SELL/ADJUSTMENT_OUT/DIVIDEND as capital returned to it, mirroring how NotionalPortfolio
treats CASH_IN/CASH_OUT/DIVIDEND. ``xirr()`` is shared with notional_portfolio.py via
performance_math.py so both modules compute money-weighted return the same way.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Price, Transaction, TransactionType
from .performance_math import xirr
from .portfolio import calculate_position

ZERO = Decimal("0")

_CONTRIBUTION_TYPES = (TransactionType.BUY, TransactionType.OPENING, TransactionType.ADJUSTMENT_IN)
_WITHDRAWAL_TYPES = (TransactionType.SELL, TransactionType.ADJUSTMENT_OUT)


def _flow_amount(tx: Transaction) -> Decimal:
    """Gross cash value of one transaction, matching calculate_position()'s own unit-cost /
    proceeds-per-unit formulas exactly (price plus/minus charges), so contributions and
    withdrawals stay consistent with the realised-profit figures the FIFO engine reports."""
    qty, price, charges = Decimal(tx.quantity), Decimal(tx.price), Decimal(tx.charges)
    if tx.transaction_type in _CONTRIBUTION_TYPES:
        return qty * price + charges
    return qty * price - charges  # SELL / ADJUSTMENT_OUT / DIVIDEND


def _grouped_transactions(db: Session) -> dict[tuple[int, int], list[Transaction]]:
    grouped: dict[tuple[int, int], list[Transaction]] = defaultdict(list)
    for tx in db.scalars(select(Transaction).order_by(Transaction.trade_date, Transaction.id)).all():
        grouped[(tx.account_id, tx.instrument_id)].append(tx)
    return grouped


def _price_on_or_before(prices: list[Price], through: date) -> Price | None:
    """`prices` must be pre-sorted ascending by price_date."""
    result = None
    for row in prices:
        if row.price_date > through:
            break
        result = row
    return result


def _value_at(
    grouped: dict[tuple[int, int], list[Transaction]],
    prices_by_instrument: dict[int, list[Price]],
    through: date,
) -> dict:
    """Point-in-time real-ledger valuation. Deliberately skips recommendation/financial-
    analysis work (unlike portfolio_snapshot()) since performance history calls this once
    per historical price date.

    total_value = market_value + cumulative dividends (dividends have nowhere else to be
    "held" without a cash account, so they're banked into the curve directly).
    net_contributions = cumulative BUY/OPENING/ADJUSTMENT_IN cost minus cumulative
    SELL/ADJUSTMENT_OUT proceeds — the real-ledger analogue of NotionalPortfolio's
    starting_cash + external CASH_IN/CASH_OUT flows. total_return = total_value -
    net_contributions always equals unrealised + realised + dividend profit (see the module
    docstring's derivation); this is what makes the SELL case net out correctly without
    double-counting realised profit.
    """
    market_value = remaining_cost = realised_profit = dividend_income = net_contributions = ZERO
    for (_, instrument_id), txs in grouped.items():
        scoped = [tx for tx in txs if tx.trade_date <= through]
        if not scoped:
            continue
        result = calculate_position(scoped)
        realised_profit += result.realised_profit
        dividend_income += result.dividend_income
        if result.quantity > 0:
            remaining_cost += result.remaining_cost
            price = _price_on_or_before(prices_by_instrument.get(instrument_id, []), through)
            if price is not None:
                market_value += result.quantity * Decimal(price.close_price)
        for tx in scoped:
            if tx.transaction_type in _CONTRIBUTION_TYPES:
                net_contributions += _flow_amount(tx)
            elif tx.transaction_type in _WITHDRAWAL_TYPES:
                net_contributions -= _flow_amount(tx)
    total_value = market_value + dividend_income
    total_return = total_value - net_contributions
    return {
        "date": through, "market_value": market_value, "remaining_cost": remaining_cost,
        "realised_profit": realised_profit, "dividend_income": dividend_income,
        "net_contributions": net_contributions, "total_value": total_value,
        "total_return": total_return,
        "total_return_pct": (total_return / net_contributions) if net_contributions else None,
    }


def real_performance_history(db: Session, benchmark_instrument_id: int | None = None) -> dict:
    grouped = _grouped_transactions(db)
    limitations = [
        "Values use stored closing prices, not executable intraday quotes.",
        "Missing closes carry the latest earlier stored close; corporate actions require "
        "verified adjusted data.",
        "No real-account cash balance is modelled before a value is set on the account "
        "(see Portfolio Setup); buys/sells/dividends are treated as capital moving between "
        "the market and the outside world, not into a tracked cash bucket.",
    ]
    if not grouped:
        return {"rows": [], "max_drawdown": None, "time_weighted_return": None,
                "money_weighted_return": None, "benchmark_available": False,
                "limitations": limitations}

    instrument_ids = {instrument_id for (_, instrument_id) in grouped}
    prices_by_instrument: dict[int, list[Price]] = defaultdict(list)
    for price in db.scalars(select(Price).where(Price.instrument_id.in_(instrument_ids))
                            .order_by(Price.price_date)).all():
        prices_by_instrument[price.instrument_id].append(price)

    earliest = min(tx.trade_date for txs in grouped.values() for tx in txs)
    dates = db.scalars(select(Price.price_date).where(
        Price.instrument_id.in_(instrument_ids), Price.price_date >= earliest,
    ).distinct().order_by(Price.price_date)).all()
    rows = [_value_at(grouped, prices_by_instrument, value_date) for value_date in dates]

    peak, max_drawdown = None, ZERO
    for row in rows:
        value = row["total_value"]
        peak = value if peak is None else max(peak, value)
        row["drawdown"] = (value / peak - 1) if peak else ZERO
        max_drawdown = min(max_drawdown, row["drawdown"])

    twr_factor, previous_value, previous_contribution = Decimal("1"), None, None
    for row in rows:
        flow = (row["net_contributions"] - previous_contribution
                if previous_contribution is not None else row["net_contributions"])
        if previous_value is None:
            period_return = row["total_value"] / flow - 1 if flow else ZERO
        else:
            period_return = (row["total_value"] - flow) / previous_value - 1 if previous_value else ZERO
        twr_factor *= 1 + period_return
        row["time_weighted_return"] = twr_factor - 1
        previous_value, previous_contribution = row["total_value"], row["net_contributions"]

    cash_flows: list[tuple[date, Decimal]] = []
    for txs in grouped.values():
        for tx in txs:
            if tx.transaction_type in _CONTRIBUTION_TYPES:
                cash_flows.append((tx.trade_date, -_flow_amount(tx)))
            elif tx.transaction_type in _WITHDRAWAL_TYPES or tx.transaction_type == TransactionType.DIVIDEND:
                cash_flows.append((tx.trade_date, _flow_amount(tx)))
    cash_flows.sort(key=lambda item: item[0])
    if rows:
        cash_flows.append((rows[-1]["date"], rows[-1]["market_value"]))
    money_weighted = xirr(cash_flows)

    benchmark_available = False
    if benchmark_instrument_id and rows:
        benchmark_prices = db.scalars(select(Price).where(
            Price.instrument_id == benchmark_instrument_id
        ).order_by(Price.price_date)).all()
        base = _price_on_or_before(benchmark_prices, rows[0]["date"])
        if base:
            benchmark_available = True
            base_price = Decimal(base.close_price)
            base_contribution = rows[0]["net_contributions"]
            for row in rows:
                observed = _price_on_or_before(benchmark_prices, row["date"])
                row["benchmark_value"] = (
                    base_contribution * Decimal(observed.close_price) / base_price
                    if observed else None
                )

    return {"rows": rows, "max_drawdown": max_drawdown,
            "time_weighted_return": rows[-1]["time_weighted_return"] if rows else None,
            "money_weighted_return": money_weighted, "benchmark_available": benchmark_available,
            "limitations": limitations + [
                "Benchmark values are normalized to the earliest tracked contribution and do "
                "not model later contributions.",
            ]}
