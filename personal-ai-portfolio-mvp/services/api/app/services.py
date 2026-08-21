from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .financial_analysis import build_financial_analysis
from .models import Account, Instrument, Price, Thesis, Transaction
from .portfolio import calculate_position
from .recommendations import recommend, valuation_signals


def portfolio_snapshot(db: Session) -> dict:
    transactions = db.scalars(
        select(Transaction).order_by(Transaction.trade_date, Transaction.id)
    ).all()

    grouped: dict[tuple[int, int], list[Transaction]] = defaultdict(list)
    for tx in transactions:
        grouped[(tx.account_id, tx.instrument_id)].append(tx)

    instruments = {x.id: x for x in db.scalars(select(Instrument)).all()}
    accounts = {x.id: x for x in db.scalars(select(Account)).all()}
    theses = {x.instrument_id: x for x in db.scalars(select(Thesis)).all()}

    latest_price_subq = (
        select(Price.instrument_id, func.max(Price.price_date).label("max_date"))
        .group_by(Price.instrument_id)
        .subquery()
    )
    latest_prices = db.execute(
        select(Price).join(
            latest_price_subq,
            (Price.instrument_id == latest_price_subq.c.instrument_id)
            & (Price.price_date == latest_price_subq.c.max_date),
        )
    ).scalars().all()
    price_map = {p.instrument_id: p for p in latest_prices}

    provisional = []
    total_market_value = Decimal("0")
    total_cost = Decimal("0")
    total_realised = Decimal("0")
    total_dividends = Decimal("0")

    for (account_id, instrument_id), txs in grouped.items():
        result = calculate_position(txs)
        total_realised += result.realised_profit
        total_dividends += result.dividend_income
        if result.quantity <= 0:
            continue

        price_obj = price_map.get(instrument_id)
        current_price = Decimal(price_obj.close_price) if price_obj else None
        market_value = result.quantity * current_price if current_price else None
        if market_value is not None:
            total_market_value += market_value
        total_cost += result.remaining_cost

        holding_days = (
            (date.today() - result.first_purchase_date).days
            if result.first_purchase_date
            else 0
        )
        # Division stays in Decimal (ledger precision) all the way through; float() is only
        # applied once, at the very end, right before the value leaves Python for JSON/the
        # recommend() threshold comparisons — not chained through an earlier float roundtrip.
        unrealised = market_value - result.remaining_cost if market_value is not None else None
        return_pct_decimal = (
            unrealised / result.remaining_cost
            if unrealised is not None and result.remaining_cost
            else None
        )
        provisional.append(
            {
                "account_id": account_id,
                "account_name": accounts[account_id].name,
                "instrument_id": instrument_id,
                "exchange": instruments[instrument_id].exchange,
                "symbol": instruments[instrument_id].symbol,
                "company_name": instruments[instrument_id].company_name,
                "quantity": float(result.quantity),
                "average_cost": float(result.average_cost),
                "remaining_cost": float(result.remaining_cost),
                "current_price": float(current_price) if current_price else None,
                "price_date": price_obj.price_date.isoformat() if price_obj else None,
                "market_value_decimal": market_value,
                "market_value": float(market_value) if market_value is not None else None,
                "unrealised_profit": float(unrealised) if unrealised is not None else None,
                "return_pct": float(return_pct_decimal) if return_pct_decimal is not None else None,
                "realised_profit": float(result.realised_profit),
                "dividend_income": float(result.dividend_income),
                "first_purchase_date": (
                    result.first_purchase_date.isoformat()
                    if result.first_purchase_date
                    else None
                ),
                "holding_days": holding_days,
                "thesis_status": (
                    theses[instrument_id].status.value
                    if instrument_id in theses
                    else None
                ),
            }
        )

    analysis_cache: dict[int, dict] = {}
    for item in provisional:
        market_value_decimal = item.pop("market_value_decimal")
        weight = (
            float(market_value_decimal / total_market_value)
            if market_value_decimal is not None and total_market_value
            else 0.0
        )
        thesis = theses.get(item["instrument_id"])
        instrument_id = item["instrument_id"]
        if instrument_id not in analysis_cache:
            analysis_cache[instrument_id] = build_financial_analysis(db, instruments[instrument_id])
        analysis = analysis_cache[instrument_id]
        # A stock's own quality/valuation evidence only counts toward BUY_MORE when the
        # analysis is actually ready — financial-sector companies need dedicated rules
        # (like recommend_prospective()) and missing data must not silently unlock the gate.
        financial_score = analysis.get("overall_score") if analysis.get("status") == "READY" else None
        _, valuation_stretched, _ = valuation_signals(analysis)
        action, reasons = recommend(
            weight=weight,
            return_pct=item["return_pct"],
            holding_days=item["holding_days"],
            thesis_status=thesis.status if thesis else None,
            has_price=item["current_price"] is not None,
            financial_score=financial_score,
            valuation_stretched=valuation_stretched,
        )
        item["weight"] = weight
        item["recommendation"] = action
        item["recommendation_reasons"] = reasons

    unrealised_total = (
        sum(
            Decimal(str(x["unrealised_profit"]))
            for x in provisional
            if x["unrealised_profit"] is not None
        )
        if provisional
        else Decimal("0")
    )

    return {
        "as_of": date.today().isoformat(),
        "summary": {
            "market_value": float(total_market_value),
            "remaining_cost": float(total_cost),
            "unrealised_profit": float(unrealised_total),
            "realised_profit": float(total_realised),
            "dividend_income": float(total_dividends),
            "total_profit": float(unrealised_total + total_realised + total_dividends),
            "position_count": len(provisional),
        },
        "positions": provisional,
    }
