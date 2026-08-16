from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from .models import Transaction, TransactionType


ZERO = Decimal("0")


@dataclass
class Lot:
    date: date
    quantity: Decimal
    unit_cost: Decimal


@dataclass
class PositionResult:
    quantity: Decimal
    remaining_cost: Decimal
    average_cost: Decimal
    realised_profit: Decimal
    first_purchase_date: date | None


def calculate_position(transactions: Iterable[Transaction]) -> PositionResult:
    """Calculate one account/instrument position using FIFO lots."""
    lots: list[Lot] = []
    realised = ZERO
    first_purchase: date | None = None

    ordered = sorted(transactions, key=lambda t: (t.trade_date, t.id or 0))
    for tx in ordered:
        qty = Decimal(tx.quantity)
        price = Decimal(tx.price)
        charges = Decimal(tx.charges)

        if tx.transaction_type in {
            TransactionType.OPENING,
            TransactionType.BUY,
            TransactionType.ADJUSTMENT_IN,
        }:
            if qty <= 0:
                continue
            unit_cost = price + (charges / qty if qty else ZERO)
            lots.append(Lot(tx.trade_date, qty, unit_cost))
            first_purchase = min(first_purchase, tx.trade_date) if first_purchase else tx.trade_date

        elif tx.transaction_type in {TransactionType.SELL, TransactionType.ADJUSTMENT_OUT}:
            remaining = qty
            if remaining <= 0:
                continue
            proceeds_per_unit = price - (charges / qty if qty else ZERO)
            while remaining > 0 and lots:
                lot = lots[0]
                consumed = min(remaining, lot.quantity)
                realised += consumed * (proceeds_per_unit - lot.unit_cost)
                lot.quantity -= consumed
                remaining -= consumed
                if lot.quantity == 0:
                    lots.pop(0)
            if remaining > 0:
                raise ValueError(
                    f"Transaction sells {qty} units but only {qty - remaining} are available."
                )

    quantity = sum((lot.quantity for lot in lots), ZERO)
    remaining_cost = sum((lot.quantity * lot.unit_cost for lot in lots), ZERO)
    average_cost = remaining_cost / quantity if quantity else ZERO
    first_open_date = min((lot.date for lot in lots), default=None)

    return PositionResult(
        quantity=quantity,
        remaining_cost=remaining_cost,
        average_cost=average_cost,
        realised_profit=realised,
        first_purchase_date=first_open_date,
    )
