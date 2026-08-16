from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models import ThesisStatus, TransactionType
from app.portfolio import calculate_position
from app.recommendations import recommend


def tx(i, kind, d, qty, price, charges=0):
    return SimpleNamespace(
        id=i,
        transaction_type=kind,
        trade_date=date.fromisoformat(d),
        quantity=Decimal(str(qty)),
        price=Decimal(str(price)),
        charges=Decimal(str(charges)),
    )


def test_fifo_partial_sale():
    result = calculate_position(
        [
            tx(1, TransactionType.BUY, "2025-01-01", 10, 100),
            tx(2, TransactionType.BUY, "2025-02-01", 10, 120),
            tx(3, TransactionType.SELL, "2025-03-01", 12, 150),
        ]
    )
    assert result.quantity == Decimal("8")
    assert result.average_cost == Decimal("120")
    assert result.realised_profit == Decimal("560")


def test_charges_are_included():
    result = calculate_position(
        [
            tx(1, TransactionType.BUY, "2025-01-01", 10, 100, 10),
            tx(2, TransactionType.SELL, "2025-03-01", 5, 120, 5),
        ]
    )
    assert result.quantity == Decimal("5")
    assert result.average_cost == Decimal("101")
    assert result.realised_profit == Decimal("90")


def test_oversell_fails():
    with pytest.raises(ValueError):
        calculate_position(
            [
                tx(1, TransactionType.BUY, "2025-01-01", 2, 100),
                tx(2, TransactionType.SELL, "2025-03-01", 3, 120),
            ]
        )


def test_dividend_income_uses_quantity_price_and_charges():
    result = calculate_position(
        [
            tx(1, TransactionType.BUY, "2025-01-01", 10, 100),
            tx(2, TransactionType.DIVIDEND, "2025-03-01", 10, 2.5, 1),
        ]
    )
    assert result.dividend_income == Decimal("24.0")


def test_invalid_thesis_is_a_strong_sell():
    action, reasons = recommend(
        weight=.05,
        return_pct=.10,
        holding_days=500,
        thesis_status=ThesisStatus.INVALID,
        has_price=True,
    )
    assert action == "STRONG_SELL"
    assert "no longer holds" in reasons[0]
