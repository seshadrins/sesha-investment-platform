from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models import ThesisStatus, TransactionType
from app.portfolio import calculate_position
from app.recommendations import recommend, recommend_prospective


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


def test_opening_transaction_seeds_a_lot_like_a_buy():
    result = calculate_position([
        tx(1, TransactionType.OPENING, "2025-01-01", 100, 50),
    ])
    assert result.quantity == Decimal("100")
    assert result.average_cost == Decimal("50")
    assert result.first_purchase_date == date(2025, 1, 1)


def test_adjustment_in_and_out_behave_like_buy_and_sell():
    result = calculate_position([
        tx(1, TransactionType.ADJUSTMENT_IN, "2025-01-01", 100, 40),
        tx(2, TransactionType.ADJUSTMENT_OUT, "2025-02-01", 30, 0),
    ])
    assert result.quantity == Decimal("70")
    assert result.average_cost == Decimal("40")


def test_same_day_transactions_are_ordered_by_id_not_list_order():
    # A same-day SELL declared before its matching BUY in the input list must still be
    # treated as happening after the BUY, since FIFO sorts same-day rows by id (C1/C2
    # context: this is exactly the ordering a same-day CSV import can get backwards).
    result = calculate_position([
        tx(2, TransactionType.SELL, "2025-03-01", 5, 120),
        tx(1, TransactionType.BUY, "2025-03-01", 10, 100),
    ])
    assert result.quantity == Decimal("5")
    assert result.realised_profit == Decimal("100")


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


def test_review_when_price_is_missing():
    action, reasons = recommend(
        weight=.05, return_pct=None, holding_days=10,
        thesis_status=None, has_price=False,
    )
    assert action == "REVIEW"
    assert "price is missing" in reasons[0].lower()


def test_trim_when_weight_exceeds_trim_threshold():
    action, reasons = recommend(
        weight=.25, return_pct=.05, holding_days=100,
        thesis_status=ThesisStatus.ACTIVE, has_price=True,
    )
    assert action == "TRIM"
    assert "trim threshold" in reasons[0]


def test_trim_on_profit_taking_when_overweight_and_up_big():
    action, reasons = recommend(
        weight=.16, return_pct=.35, holding_days=100,
        thesis_status=ThesisStatus.ACTIVE, has_price=True,
    )
    assert action == "TRIM"
    assert "partial profit realisation" in reasons[0]


def test_sell_when_loss_and_thesis_already_on_watch():
    action, reasons = recommend(
        weight=.05, return_pct=-.20, holding_days=100,
        thesis_status=ThesisStatus.WATCH, has_price=True,
    )
    assert action == "SELL"
    assert any("loss-review threshold" in r for r in reasons)
    assert any("already on watch" in r for r in reasons)


def test_review_when_loss_without_watch_thesis():
    action, reasons = recommend(
        weight=.05, return_pct=-.20, holding_days=100,
        thesis_status=ThesisStatus.ACTIVE, has_price=True,
    )
    assert action == "REVIEW"
    assert any("loss-review threshold" in r for r in reasons)


def test_review_when_thesis_on_watch_without_loss():
    action, reasons = recommend(
        weight=.05, return_pct=.02, holding_days=100,
        thesis_status=ThesisStatus.WATCH, has_price=True,
    )
    assert action == "REVIEW"
    assert "reassessed before adding" in reasons[0]


def test_priority_ordering_surfaces_both_loss_and_weight_reasons():
    # Regression for the Medium priority-ordering bug: a position that's both overweight
    # and past the loss-review threshold used to report only the weight reason (TRIM).
    action, reasons = recommend(
        weight=.25, return_pct=-.20, holding_days=100,
        thesis_status=ThesisStatus.ACTIVE, has_price=True,
    )
    assert action == "REVIEW"
    assert any("loss-review threshold" in r for r in reasons)
    assert any("trim threshold" in r for r in reasons)


def test_buy_more_requires_financial_score_and_unstretched_valuation():
    action, reasons = recommend(
        weight=.05, return_pct=.02, holding_days=100,
        thesis_status=ThesisStatus.ACTIVE, has_price=True,
        financial_score=75, valuation_stretched=False,
    )
    assert action == "BUY_MORE"
    assert any("75/100" in r for r in reasons)


def test_buy_more_falls_back_to_hold_when_financial_evidence_missing():
    action, reasons = recommend(
        weight=.05, return_pct=.02, holding_days=100,
        thesis_status=ThesisStatus.ACTIVE, has_price=True,
        financial_score=None, valuation_stretched=False,
    )
    assert action == "HOLD"
    assert any("evidence is missing" in r.lower() for r in reasons)


def test_buy_more_falls_back_to_hold_when_score_below_floor():
    action, reasons = recommend(
        weight=.05, return_pct=.02, holding_days=100,
        thesis_status=ThesisStatus.ACTIVE, has_price=True,
        financial_score=60, valuation_stretched=False,
    )
    assert action == "HOLD"
    assert any("60/100" in r for r in reasons)


def test_buy_more_falls_back_to_hold_when_valuation_stretched():
    action, reasons = recommend(
        weight=.05, return_pct=.02, holding_days=100,
        thesis_status=ThesisStatus.ACTIVE, has_price=True,
        financial_score=90, valuation_stretched=True,
    )
    assert action == "HOLD"
    assert any("stretched" in r.lower() for r in reasons)


def test_hold_when_position_not_small_enough_for_buy_more_gate():
    action, reasons = recommend(
        weight=.10, return_pct=.02, holding_days=400,
        thesis_status=ThesisStatus.ACTIVE, has_price=True,
    )
    assert action == "HOLD"
    assert "No concentration" in reasons[0]


def _analysis(status="READY", overall_score=None, governance_flags=None, valuation=None, missing=None):
    return {
        "status": status, "overall_score": overall_score,
        "governance_flags": governance_flags or [], "valuation": valuation or {},
        "missing": missing or [],
    }


def _style(matches=True, applicable=True):
    return {"applicable": applicable, "matches": matches}


def test_recommend_prospective_missing_data():
    action, reasons = recommend_prospective(
        _analysis(status="MISSING_DATA", missing=["INCOME_ANNUAL", "BALANCE_SHEET"]), []
    )
    assert action == "REVIEW"
    assert "INCOME_ANNUAL" in reasons[0]


def test_recommend_prospective_sector_specific_required():
    action, reasons = recommend_prospective(_analysis(status="SECTOR_SPECIFIC_REQUIRED"), [])
    assert action == "REVIEW"
    assert "financial-sector" in reasons[0].lower()


def test_recommend_prospective_avoid_on_high_governance_flag():
    analysis = _analysis(overall_score=85, governance_flags=[
        {"severity": "HIGH", "message": "Profit is positive but operating cash flow is negative."}
    ])
    action, reasons = recommend_prospective(analysis, [_style()])
    assert action == "AVOID"
    assert reasons == ["Profit is positive but operating cash flow is negative."]


def test_recommend_prospective_avoid_on_low_score():
    action, reasons = recommend_prospective(_analysis(overall_score=40), [])
    assert action == "AVOID"
    assert "40/100" in reasons[0]


def test_recommend_prospective_strong_buy():
    analysis = _analysis(overall_score=85, valuation={
        "P/E": {"current": 15, "sector": 20},
        "P/B": {"current": 3, "sector": 4},
    })
    action, reasons = recommend_prospective(analysis, [_style(), _style()])
    assert action == "STRONG_BUY"


def test_recommend_prospective_buy():
    analysis = _analysis(overall_score=72, valuation={"P/E": {"current": 18, "sector": 20}})
    action, reasons = recommend_prospective(analysis, [_style()])
    assert action == "BUY"


def test_recommend_prospective_watch_when_no_style_match_and_no_valuation_evidence():
    analysis = _analysis(overall_score=65)
    action, reasons = recommend_prospective(analysis, [_style(matches=False)])
    assert action == "WATCH"
    assert any("does not currently match" in r.lower() for r in reasons)
    assert any("valuation evidence is unavailable" in r.lower() for r in reasons)
