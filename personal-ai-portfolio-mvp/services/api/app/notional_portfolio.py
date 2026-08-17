from __future__ import annotations

from collections import defaultdict, deque
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Instrument, NotionalPortfolio, NotionalTransaction, Price, UniverseMembership


class NotionalPortfolioError(ValueError):
    pass


ZERO = Decimal("0")


def target_price_date(decision_at: datetime) -> date:
    aware = decision_at.replace(tzinfo=timezone.utc) if decision_at.tzinfo is None else decision_at
    local = aware.astimezone(ZoneInfo("Asia/Kolkata"))
    return local.date() if local.time() >= time(16, 0) else local.date() + timedelta(days=1)


def _executed(db: Session, portfolio_id: int, through: date | None = None):
    query = select(NotionalTransaction).where(
        NotionalTransaction.portfolio_id == portfolio_id,
        NotionalTransaction.status == "EXECUTED",
    )
    if through:
        query = query.where(NotionalTransaction.execution_date <= through)
    return db.scalars(query.order_by(NotionalTransaction.execution_date, NotionalTransaction.id)).all()


def ledger_state(db: Session, portfolio: NotionalPortfolio, through: date | None = None) -> dict:
    cash = Decimal(portfolio.starting_cash)
    lots: dict[int, deque] = defaultdict(deque)
    realised = ZERO
    dividends = ZERO
    external_flows = ZERO
    for tx in _executed(db, portfolio.id, through):
        amount, charges = Decimal(tx.requested_amount or 0), Decimal(tx.charges or 0)
        if tx.transaction_type == "CASH_IN":
            cash += amount; external_flows += amount
        elif tx.transaction_type == "CASH_OUT":
            cash -= amount; external_flows -= amount
        elif tx.transaction_type == "DIVIDEND":
            cash += amount; dividends += amount
        elif tx.transaction_type == "BUY":
            cost = Decimal(tx.quantity) * Decimal(tx.execution_price) + charges
            cash -= cost
            unit_cost = cost / Decimal(tx.quantity)
            lots[tx.instrument_id].append([Decimal(tx.quantity), unit_cost])
        elif tx.transaction_type == "SELL":
            qty, basis = Decimal(tx.quantity), ZERO
            remaining = qty
            while remaining > 0 and lots[tx.instrument_id]:
                lot_qty, unit_cost = lots[tx.instrument_id][0]
                used = min(remaining, lot_qty); basis += used * unit_cost
                lot_qty -= used; remaining -= used
                if lot_qty == 0: lots[tx.instrument_id].popleft()
                else: lots[tx.instrument_id][0][0] = lot_qty
            proceeds = qty * Decimal(tx.execution_price) - charges
            cash += proceeds; realised += proceeds - basis
    holdings = {}
    for instrument_id, instrument_lots in lots.items():
        quantity = sum((lot[0] for lot in instrument_lots), ZERO)
        cost = sum((lot[0] * lot[1] for lot in instrument_lots), ZERO)
        if quantity > 0:
            holdings[instrument_id] = {"quantity": quantity, "remaining_cost": cost,
                                       "average_cost": cost / quantity}
    return {"cash": cash, "holdings": holdings, "realised_profit": realised,
            "dividends": dividends, "external_flows": external_flows}


def _latest_prices(db: Session, instrument_ids: list[int], through: date | None = None) -> dict:
    result = {}
    for instrument_id in instrument_ids:
        query = select(Price).where(Price.instrument_id == instrument_id)
        if through: query = query.where(Price.price_date <= through)
        row = db.scalar(query.order_by(Price.price_date.desc()))
        if row: result[instrument_id] = row
    return result


def portfolio_snapshot(db: Session, portfolio: NotionalPortfolio, through: date | None = None) -> dict:
    state = ledger_state(db, portfolio, through)
    prices = _latest_prices(db, list(state["holdings"]), through)
    holdings, invested = [], ZERO
    unpriced = []
    for instrument_id, position in state["holdings"].items():
        instrument, price = db.get(Instrument, instrument_id), prices.get(instrument_id)
        market_value = Decimal(price.close_price) * position["quantity"] if price else None
        if market_value is None: unpriced.append(f"{instrument.exchange}:{instrument.symbol}")
        else: invested += market_value
        holdings.append({"instrument_id": instrument_id, "stock": f"{instrument.exchange}:{instrument.symbol}",
            "company_name": instrument.company_name, **position,
            "price": Decimal(price.close_price) if price else None,
            "price_date": price.price_date if price else None, "market_value": market_value,
            "unrealised_profit": market_value - position["remaining_cost"] if market_value is not None else None})
    total = state["cash"] + invested
    for item in holdings:
        item["weight"] = item["market_value"] / total if item["market_value"] is not None and total else None
    pending = db.scalars(select(NotionalTransaction).where(
        NotionalTransaction.portfolio_id == portfolio.id,
        NotionalTransaction.status == "PENDING_PRICE").order_by(NotionalTransaction.id)).all()
    contribution = Decimal(portfolio.starting_cash) + state["external_flows"]
    return {"portfolio_id": portfolio.id, "name": portfolio.name, "currency": portfolio.currency,
        "starting_cash": portfolio.starting_cash, "cash": state["cash"], "invested_value": invested,
        "total_value": total, "net_contributions": contribution,
        "total_return": total - contribution, "total_return_pct": (total / contribution - 1) if contribution else None,
        "realised_profit": state["realised_profit"], "dividends": state["dividends"],
        "holdings": sorted(holdings, key=lambda item: item["stock"]), "unpriced": unpriced,
        "data_quality_flags": (["MISSING_CURRENT_PRICES"] if unpriced else []) +
                              ["CORPORATE_ACTION_ADJUSTMENTS_NOT_VERIFIED"],
        "pending_orders": [transaction_out(db, tx) for tx in pending]}


def create_trade(db: Session, portfolio: NotionalPortfolio, instrument_id: int, action: str,
                 quantity: Decimal | None, amount: Decimal | None, reason: str | None,
                 recommendation_snapshot: dict | None) -> NotionalTransaction:
    if not db.get(Instrument, instrument_id): raise NotionalPortfolioError("Instrument not found.")
    action = action.strip().upper()
    if action not in {"BUY", "SELL"}: raise NotionalPortfolioError("action must be BUY or SELL.")
    if bool(quantity) == bool(amount):
        raise NotionalPortfolioError("Provide exactly one of quantity or amount.")
    if action == "SELL" and amount:
        raise NotionalPortfolioError("Sell orders require a quantity.")
    if action == "SELL":
        available = ledger_state(db, portfolio)["holdings"].get(instrument_id, {}).get("quantity", ZERO)
        pending_sell = db.scalars(select(NotionalTransaction).where(
            NotionalTransaction.portfolio_id == portfolio.id,
            NotionalTransaction.instrument_id == instrument_id,
            NotionalTransaction.transaction_type == "SELL",
            NotionalTransaction.status == "PENDING_PRICE")).all()
        reserved = sum((Decimal(item.quantity) for item in pending_sell), ZERO)
        if Decimal(quantity) > available - reserved: raise NotionalPortfolioError("Sell quantity exceeds available notional holdings.")
    now = datetime.utcnow()
    tx = NotionalTransaction(portfolio_id=portfolio.id, instrument_id=instrument_id,
        transaction_type=action, status="PENDING_PRICE", decision_at=now,
        target_price_date=target_price_date(now), quantity=quantity or ZERO,
        requested_amount=amount, recommendation_snapshot=recommendation_snapshot,
        user_reason=reason)
    db.add(tx); db.commit(); db.refresh(tx)
    settle_pending_orders(db, portfolio.id)
    db.refresh(tx)
    return tx


def create_cash_transaction(db: Session, portfolio: NotionalPortfolio, action: str,
                            amount: Decimal, reason: str | None) -> NotionalTransaction:
    action = action.strip().upper()
    if action not in {"CASH_IN", "CASH_OUT", "DIVIDEND"}:
        raise NotionalPortfolioError("action must be CASH_IN, CASH_OUT, or DIVIDEND.")
    if action == "CASH_OUT" and amount > ledger_state(db, portfolio)["cash"]:
        raise NotionalPortfolioError("Cash withdrawal exceeds available cash.")
    now = datetime.utcnow()
    tx = NotionalTransaction(portfolio_id=portfolio.id, transaction_type=action, status="EXECUTED",
        decision_at=now, execution_date=now.date(), requested_amount=amount,
        quantity=ZERO, charges=ZERO, user_reason=reason, executed_at=now)
    db.add(tx); db.commit(); db.refresh(tx); return tx


def settle_pending_orders(db: Session, portfolio_id: int | None = None) -> dict:
    query = select(NotionalTransaction).where(NotionalTransaction.status == "PENDING_PRICE")
    if portfolio_id: query = query.where(NotionalTransaction.portfolio_id == portfolio_id)
    rows = db.scalars(query.order_by(NotionalTransaction.id)).all()
    settled = rejected = 0
    for tx in rows:
        portfolio = db.get(NotionalPortfolio, tx.portfolio_id)
        price = db.scalar(select(Price).where(Price.instrument_id == tx.instrument_id,
            Price.price_date >= tx.target_price_date).order_by(Price.price_date))
        if not price: continue
        raw_price = Decimal(price.close_price)
        slip = Decimal(portfolio.slippage_pct)
        execution_price = raw_price * (1 + slip if tx.transaction_type == "BUY" else 1 - slip)
        quantity = Decimal(tx.quantity)
        if tx.requested_amount:
            quantity = (Decimal(tx.requested_amount) / execution_price).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
        gross = quantity * execution_price
        charges = gross * (Decimal(portfolio.brokerage_pct) +
                           (Decimal(portfolio.tax_pct) if tx.transaction_type == "SELL" else ZERO))
        state = ledger_state(db, portfolio, price.price_date)
        error = None
        if quantity <= 0: error = "Calculated quantity is zero."
        elif tx.transaction_type == "BUY" and gross + charges > state["cash"]:
            error = "Insufficient notional cash at execution."
        elif tx.transaction_type == "SELL" and quantity > state["holdings"].get(tx.instrument_id, {}).get("quantity", ZERO):
            error = "Insufficient notional quantity at execution."
        if not error and tx.transaction_type == "BUY":
            projected_total = portfolio_snapshot(db, portfolio, price.price_date)["total_value"]
            current_value = state["holdings"].get(tx.instrument_id, {}).get("quantity", ZERO) * raw_price
            if projected_total and (current_value + gross) / projected_total > Decimal(portfolio.max_position_weight):
                error = "Trade exceeds the configured maximum position weight."
        if error:
            tx.status, tx.error = "REJECTED", error; rejected += 1
        else:
            tx.status, tx.quantity, tx.execution_price = "EXECUTED", quantity, execution_price
            tx.execution_date, tx.executed_at, tx.charges = price.price_date, datetime.utcnow(), charges
            settled += 1
    db.commit()
    return {"pending_checked": len(rows), "settled": settled, "rejected": rejected}


def transaction_out(db: Session, tx: NotionalTransaction) -> dict:
    instrument = db.get(Instrument, tx.instrument_id) if tx.instrument_id else None
    return {"id": tx.id, "portfolio_id": tx.portfolio_id, "instrument_id": tx.instrument_id,
        "stock": f"{instrument.exchange}:{instrument.symbol}" if instrument else None,
        "company_name": instrument.company_name if instrument else None,
        "transaction_type": tx.transaction_type, "status": tx.status,
        "decision_at": tx.decision_at, "target_price_date": tx.target_price_date,
        "execution_date": tx.execution_date, "quantity": tx.quantity,
        "requested_amount": tx.requested_amount, "execution_price": tx.execution_price,
        "charges": tx.charges, "recommendation_snapshot": tx.recommendation_snapshot,
        "user_reason": tx.user_reason, "error": tx.error}


def performance_history(db: Session, portfolio: NotionalPortfolio) -> dict:
    instrument_ids = {tx.instrument_id for tx in _executed(db, portfolio.id) if tx.instrument_id}
    dates = db.scalars(select(Price.price_date).where(
        Price.instrument_id.in_(instrument_ids),
        Price.price_date >= portfolio.created_at.date()
    ).distinct().order_by(Price.price_date)).all() if instrument_ids else []
    rows, peak, max_drawdown = [], None, ZERO
    for value_date in dates:
        snap = portfolio_snapshot(db, portfolio, value_date)
        value = Decimal(snap["total_value"])
        peak = value if peak is None else max(peak, value)
        drawdown = value / peak - 1 if peak else ZERO
        max_drawdown = min(max_drawdown, drawdown)
        rows.append({"date": value_date, "value": value, "net_contributions": snap["net_contributions"],
                     "return_pct": snap["total_return_pct"], "drawdown": drawdown})
    twr_factor, previous_value, previous_contribution = Decimal("1"), None, None
    for row in rows:
        flow = row["net_contributions"] - previous_contribution if previous_contribution is not None else row["net_contributions"]
        if previous_value is None:
            period_return = row["value"] / flow - 1 if flow else ZERO
        else:
            period_return = (row["value"] - flow) / previous_value - 1 if previous_value else ZERO
        twr_factor *= 1 + period_return
        row["time_weighted_return"] = twr_factor - 1
        previous_value, previous_contribution = row["value"], row["net_contributions"]
    benchmark_available = False
    if portfolio.benchmark_instrument_id and rows:
        benchmark_prices = _latest_prices(db, [portfolio.benchmark_instrument_id], rows[0]["date"])
        base = benchmark_prices.get(portfolio.benchmark_instrument_id)
        if base:
            benchmark_available = True
            base_price = Decimal(base.close_price)
            for row in rows:
                observed = _latest_prices(db, [portfolio.benchmark_instrument_id], row["date"]).get(portfolio.benchmark_instrument_id)
                row["benchmark_value"] = (Decimal(portfolio.starting_cash) * Decimal(observed.close_price) / base_price) if observed else None
    cash_flows = [(portfolio.created_at.date(), -Decimal(portfolio.starting_cash))]
    for tx in _executed(db, portfolio.id):
        if tx.transaction_type == "CASH_IN": cash_flows.append((tx.execution_date, -Decimal(tx.requested_amount)))
        elif tx.transaction_type == "CASH_OUT": cash_flows.append((tx.execution_date, Decimal(tx.requested_amount)))
    if rows: cash_flows.append((rows[-1]["date"], rows[-1]["value"]))
    money_weighted = _xirr(cash_flows)
    return {"rows": rows, "max_drawdown": max_drawdown,
            "time_weighted_return": rows[-1]["time_weighted_return"] if rows else None,
            "money_weighted_return": money_weighted, "benchmark_available": benchmark_available,
            "limitations": ["Values use stored closing prices, not executable intraday quotes.",
                "Missing closes carry the latest earlier stored close; corporate actions require verified adjusted data.",
                "Benchmark values are normalized to starting cash and do not model later contributions."]}


def _xirr(cash_flows: list[tuple[date, Decimal]]) -> Decimal | None:
    if len(cash_flows) < 2 or cash_flows[-1][0] <= cash_flows[0][0]: return None
    origin = cash_flows[0][0]
    def npv(rate: float) -> float:
        return sum(float(amount) / ((1 + rate) ** ((day - origin).days / 365))
                   for day, amount in cash_flows)
    low, high = -.9999, 10.0
    if npv(low) * npv(high) > 0: return None
    for _ in range(100):
        middle = (low + high) / 2
        if npv(low) * npv(middle) <= 0: high = middle
        else: low = middle
    return Decimal(str((low + high) / 2))


def recommendation_learning(db: Session, portfolio: NotionalPortfolio) -> dict:
    buys = db.scalars(select(NotionalTransaction).where(
        NotionalTransaction.portfolio_id == portfolio.id,
        NotionalTransaction.transaction_type == "BUY",
        NotionalTransaction.status == "EXECUTED",
        NotionalTransaction.recommendation_snapshot.is_not(None),
    ).order_by(NotionalTransaction.execution_date, NotionalTransaction.id)).all()
    outcomes = []
    for tx in buys:
        entry = Decimal(tx.execution_price)
        snapshot = tx.recommendation_snapshot or {}
        membership = db.scalar(select(UniverseMembership).where(
            UniverseMembership.instrument_id == tx.instrument_id,
            UniverseMembership.active.is_(True)).order_by(UniverseMembership.as_of.desc()))
        row = {"transaction_id": tx.id, "instrument_id": tx.instrument_id,
            "stock": transaction_out(db, tx)["stock"], "decision_at": tx.decision_at,
            "execution_date": tx.execution_date, "entry_price": entry,
            "recommendation": snapshot.get("recommendation"),
            "confidence_score": snapshot.get("financial_score"),
            "cap_segment": membership.cap_segment if membership else None,
            "styles": snapshot.get("style_matches") or [],
            "fundamental_signal": snapshot.get("financial_score"),
            "investor_corroboration_count": snapshot.get("investor_corroboration_count", 0),
            "market_regime": _market_regime(db, portfolio.benchmark_instrument_id, tx.execution_date),
            "horizons": {}}
        for label, days in (("3m", 90), ("6m", 180), ("12m", 365)):
            target = tx.execution_date + timedelta(days=days)
            end = db.scalar(select(Price).where(Price.instrument_id == tx.instrument_id,
                Price.price_date >= target).order_by(Price.price_date))
            prices = db.scalars(select(Price).where(Price.instrument_id == tx.instrument_id,
                Price.price_date >= tx.execution_date,
                Price.price_date <= (end.price_date if end else target)).order_by(Price.price_date)).all()
            if not end:
                row["horizons"][label] = {"status": "PENDING", "target_date": target}
                continue
            values = [Decimal(item.close_price) for item in prices]
            forward = Decimal(end.close_price) / entry - 1
            benchmark_return = _benchmark_return(db, portfolio.benchmark_instrument_id,
                                                 tx.execution_date, end.price_date)
            row["horizons"][label] = {"status": "AVAILABLE", "target_date": target,
                "observed_date": end.price_date, "forward_return": forward,
                "maximum_adverse_excursion": min(values) / entry - 1 if values else None,
                "maximum_favourable_excursion": max(values) / entry - 1 if values else None,
                "benchmark_return": benchmark_return,
                "benchmark_relative_return": forward - benchmark_return if benchmark_return is not None else None}
        outcomes.append(row)
    available_12m = [row for row in outcomes if row["horizons"]["12m"]["status"] == "AVAILABLE"]
    buckets = {}
    for row in available_12m:
        score = row["confidence_score"]
        bucket = "unknown" if score is None else ("80-100" if score >= 80 else "60-79" if score >= 60 else "below-60")
        item = buckets.setdefault(bucket, {"observations": 0, "positive": 0, "average_return": ZERO})
        value = row["horizons"]["12m"]["forward_return"]
        item["observations"] += 1; item["positive"] += value > 0; item["average_return"] += value
    for item in buckets.values():
        item["positive_rate"] = Decimal(item["positive"]) / item["observations"]
        item["average_return"] /= item["observations"]
    return {"outcomes": outcomes, "calibration_12m": buckets,
        "limitations": ["Pending horizons remain unevaluated; no future price is substituted.",
            "Returns use stored closes and omit dividends unless separately recorded.",
            "Regime uses the configured benchmark's trailing 90-day stored-price direction."]}


def _benchmark_return(db: Session, instrument_id: int | None, start: date, end: date) -> Decimal | None:
    if not instrument_id: return None
    first = db.scalar(select(Price).where(Price.instrument_id == instrument_id,
        Price.price_date >= start).order_by(Price.price_date))
    last = db.scalar(select(Price).where(Price.instrument_id == instrument_id,
        Price.price_date <= end).order_by(Price.price_date.desc()))
    if not first or not last or last.price_date < first.price_date: return None
    return Decimal(last.close_price) / Decimal(first.close_price) - 1


def _market_regime(db: Session, instrument_id: int | None, value_date: date) -> str:
    if not instrument_id: return "UNKNOWN"
    change = _benchmark_return(db, instrument_id, value_date - timedelta(days=90), value_date)
    if change is None: return "UNKNOWN"
    if change >= Decimal("0.05"): return "RISING"
    if change <= Decimal("-0.05"): return "FALLING"
    return "SIDEWAYS"
