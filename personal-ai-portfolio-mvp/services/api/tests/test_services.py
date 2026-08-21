from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import (
    Account,
    Instrument,
    Price,
    Thesis,
    ThesisStatus,
    Transaction,
    TransactionType,
    UniverseMembership,
)
from app.services import portfolio_diversification, portfolio_snapshot


def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _buy(account, instrument, trade_date, quantity, price):
    return Transaction(account_id=account.id, instrument_id=instrument.id,
        transaction_type=TransactionType.BUY, trade_date=trade_date,
        quantity=Decimal(str(quantity)), price=Decimal(str(price)), charges=Decimal("0"))


# ---- G2: sector / cap-segment diversification ----

def test_diversification_aggregates_market_value_by_sector_and_cap_segment():
    db = session()
    account = Account(name="Main", broker_name="Manual", currency="INR")
    bank_a = Instrument(exchange="NSE", symbol="BANKA", company_name="Bank A", sector="Bank")
    bank_b = Instrument(exchange="NSE", symbol="BANKB", company_name="Bank B", sector="Bank")
    it_co = Instrument(exchange="NSE", symbol="ITCO", company_name="IT Co", sector="IT")
    db.add_all([account, bank_a, bank_b, it_co])
    db.flush()
    db.add_all([
        _buy(account, bank_a, date(2026, 1, 1), 100, 10),
        _buy(account, bank_b, date(2026, 1, 1), 100, 10),
        _buy(account, it_co, date(2026, 1, 1), 100, 10),
        UniverseMembership(universe_id="nifty500", instrument_id=bank_a.id, cap_segment="LARGE",
                           active=True, as_of=date(2026, 1, 1), source_url="https://example.test"),
        UniverseMembership(universe_id="nifty500", instrument_id=it_co.id, cap_segment="MID",
                           active=True, as_of=date(2026, 1, 1), source_url="https://example.test"),
    ])
    db.add_all([
        Price(instrument_id=bank_a.id, price_date=date(2026, 1, 1), close_price=Decimal("10")),
        Price(instrument_id=bank_b.id, price_date=date(2026, 1, 1), close_price=Decimal("10")),
        Price(instrument_id=it_co.id, price_date=date(2026, 1, 1), close_price=Decimal("10")),
    ])
    db.commit()

    result = portfolio_diversification(db)
    assert result["total_market_value"] == 3000.0
    by_sector = {row["label"]: row for row in result["by_sector"]}
    assert by_sector["Bank"]["market_value"] == 2000.0
    assert round(by_sector["Bank"]["weight"], 4) == round(2 / 3, 4)
    assert by_sector["IT"]["market_value"] == 1000.0

    by_cap = {row["label"]: row for row in result["by_cap_segment"]}
    # bank_b has no UniverseMembership row at all -> "Not classified".
    assert by_cap["LARGE"]["market_value"] == 1000.0
    assert by_cap["MID"]["market_value"] == 1000.0
    assert by_cap["Not classified"]["market_value"] == 1000.0


def test_diversification_with_no_positions_returns_empty_breakdowns():
    db = session()
    result = portfolio_diversification(db)
    assert result["by_sector"] == []
    assert result["by_cap_segment"] == []
    assert result["total_market_value"] == 0.0


# ---- G4: elapsed thesis horizon surfaces REVIEW ----

def test_elapsed_thesis_horizon_surfaces_review_even_without_loss_or_overweight():
    db = session()
    account = Account(name="Main", broker_name="Manual", currency="INR")
    anchor = Instrument(exchange="NSE", symbol="ANCHOR", company_name="Anchor Ltd")
    target = Instrument(exchange="NSE", symbol="TARGET", company_name="Target Ltd")
    db.add_all([account, anchor, target])
    db.flush()
    old_date = date.today() - timedelta(days=800)
    db.add_all([
        _buy(account, anchor, date(2020, 1, 1), 9000, 100),  # large anchor keeps target's weight small
        _buy(account, target, old_date, 100, 100),
        Price(instrument_id=anchor.id, price_date=date.today(), close_price=Decimal("100")),
        Price(instrument_id=target.id, price_date=date.today(), close_price=Decimal("100")),
        Thesis(instrument_id=target.id, status=ThesisStatus.ACTIVE, target_horizon_months=6),
    ])
    db.commit()

    snapshot = portfolio_snapshot(db)
    target_position = next(p for p in snapshot["positions"] if p["instrument_id"] == target.id)
    assert target_position["recommendation"] == "REVIEW"
    assert any("horizon has elapsed" in reason for reason in target_position["recommendation_reasons"])


def test_thesis_within_horizon_does_not_trigger_review():
    db = session()
    account = Account(name="Main", broker_name="Manual", currency="INR")
    anchor = Instrument(exchange="NSE", symbol="ANCHOR", company_name="Anchor Ltd")
    target = Instrument(exchange="NSE", symbol="TARGET", company_name="Target Ltd")
    db.add_all([account, anchor, target])
    db.flush()
    recent_date = date.today() - timedelta(days=30)
    db.add_all([
        _buy(account, anchor, date(2020, 1, 1), 9000, 100),
        _buy(account, target, recent_date, 100, 100),
        Price(instrument_id=anchor.id, price_date=date.today(), close_price=Decimal("100")),
        Price(instrument_id=target.id, price_date=date.today(), close_price=Decimal("100")),
        Thesis(instrument_id=target.id, status=ThesisStatus.ACTIVE, target_horizon_months=12),
    ])
    db.commit()

    snapshot = portfolio_snapshot(db)
    target_position = next(p for p in snapshot["positions"] if p["instrument_id"] == target.id)
    assert target_position["recommendation"] != "REVIEW"


# ---- G9: LTCG-eligibility annotation on TRIM/SELL/STRONG_SELL reasons ----

def test_strong_sell_from_invalid_thesis_is_annotated_with_ltcg_eligibility():
    db = session()
    account = Account(name="Main", broker_name="Manual", currency="INR")
    instrument = Instrument(exchange="NSE", symbol="BADTHESIS", company_name="Bad Thesis Ltd")
    db.add_all([account, instrument])
    db.flush()
    long_term_date = date.today() - timedelta(days=400)  # already long-term eligible
    short_term_date = date.today() - timedelta(days=30)  # still short-term
    db.add_all([
        _buy(account, instrument, long_term_date, 10, 100),
        _buy(account, instrument, short_term_date, 5, 100),
        Price(instrument_id=instrument.id, price_date=date.today(), close_price=Decimal("100")),
        Thesis(instrument_id=instrument.id, status=ThesisStatus.INVALID),
    ])
    db.commit()

    snapshot = portfolio_snapshot(db)
    position = snapshot["positions"][0]
    assert position["recommendation"] == "STRONG_SELL"
    ltcg_reasons = [r for r in position["recommendation_reasons"] if "long-term-eligible" in r]
    assert len(ltcg_reasons) == 1
    assert ltcg_reasons[0].startswith("5 of 15 shares are still short-term")


def test_hold_recommendation_is_not_annotated_with_ltcg_note():
    db = session()
    account = Account(name="Main", broker_name="Manual", currency="INR")
    anchor = Instrument(exchange="NSE", symbol="ANCHOR2", company_name="Anchor Ltd")
    target = Instrument(exchange="NSE", symbol="HOLDME", company_name="Hold Me Ltd")
    db.add_all([account, anchor, target])
    db.flush()
    recent_date = date.today() - timedelta(days=10)
    db.add_all([
        _buy(account, anchor, date(2020, 1, 1), 9000, 100),
        _buy(account, target, recent_date, 100, 100),
        Price(instrument_id=anchor.id, price_date=date.today(), close_price=Decimal("100")),
        Price(instrument_id=target.id, price_date=date.today(), close_price=Decimal("100")),
    ])
    db.commit()

    snapshot = portfolio_snapshot(db)
    target_position = next(p for p in snapshot["positions"] if p["instrument_id"] == target.id)
    assert target_position["recommendation"] in {"HOLD", "BUY_MORE"}
    assert not any("long-term-eligible" in r for r in target_position["recommendation_reasons"])
