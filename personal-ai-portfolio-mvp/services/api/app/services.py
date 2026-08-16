from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Account, Instrument, Price, Thesis, Transaction
from .portfolio import calculate_position
from .recommendations import recommend


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

    for (account_id, instrument_id), txs in grouped.items():
        result = calculate_position(txs)
        total_realised += result.realised_profit
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
        unrealised = market_value - result.remaining_cost if market_value is not None else None
        return_pct = (
            float(unrealised / result.remaining_cost)
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
                "market_value": float(market_value) if market_value is not None else None,
                "unrealised_profit": float(unrealised) if unrealised is not None else None,
                "return_pct": return_pct,
                "realised_profit": float(result.realised_profit),
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

    for item in provisional:
        weight = (
            item["market_value"] / float(total_market_value)
            if item["market_value"] is not None and total_market_value
            else 0.0
        )
        thesis = theses.get(item["instrument_id"])
        action, reasons = recommend(
            weight=weight,
            return_pct=item["return_pct"],
            holding_days=item["holding_days"],
            thesis_status=thesis.status if thesis else None,
            has_price=item["current_price"] is not None,
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
            "position_count": len(provisional),
        },
        "positions": provisional,
    }
