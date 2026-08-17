from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Instrument, NotionalPortfolio, NotionalTransaction, Price
from app.notional_portfolio import (
    create_trade, ledger_state, portfolio_snapshot, recommendation_learning,
    settle_pending_orders, target_price_date,
)


def db_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_trade_waits_for_next_observable_close_then_updates_notional_only():
    db = db_session()
    instrument = Instrument(exchange="NSE", symbol="ABC", company_name="ABC Ltd", isin="INE000A01000")
    portfolio = NotionalPortfolio(name="Test", starting_cash=Decimal("100000"), max_position_weight=Decimal("1"))
    db.add_all([instrument, portfolio]); db.commit()
    tx = create_trade(db, portfolio, instrument.id, "BUY", Decimal("10"), None, "test", {"recommendation": "STRONG_BUY"})
    assert tx.status == "PENDING_PRICE"
    db.add(Price(instrument_id=instrument.id, price_date=tx.target_price_date,
                 close_price=Decimal("100"), source="TEST")); db.commit()
    assert settle_pending_orders(db, portfolio.id)["settled"] == 1
    snapshot = portfolio_snapshot(db, portfolio)
    assert snapshot["cash"] == Decimal("99000")
    assert snapshot["holdings"][0]["quantity"] == Decimal("10")
    assert tx.recommendation_snapshot["recommendation"] == "STRONG_BUY"


def test_fifo_sale_and_oversell_reservation():
    db = db_session()
    instrument = Instrument(exchange="NSE", symbol="XYZ", company_name="XYZ Ltd")
    portfolio = NotionalPortfolio(name="FIFO", starting_cash=Decimal("10000"), max_position_weight=Decimal("1"))
    db.add_all([instrument, portfolio]); db.commit()
    for tx in (
        NotionalTransaction(portfolio_id=portfolio.id, instrument_id=instrument.id,
            transaction_type="BUY", status="EXECUTED", decision_at=datetime.utcnow(),
            execution_date=date(2026, 1, 1), quantity=10, execution_price=100, charges=0),
        NotionalTransaction(portfolio_id=portfolio.id, instrument_id=instrument.id,
            transaction_type="BUY", status="EXECUTED", decision_at=datetime.utcnow(),
            execution_date=date(2026, 2, 1), quantity=10, execution_price=120, charges=0),
        NotionalTransaction(portfolio_id=portfolio.id, instrument_id=instrument.id,
            transaction_type="SELL", status="EXECUTED", decision_at=datetime.utcnow(),
            execution_date=date(2026, 3, 1), quantity=12, execution_price=150, charges=0),
    ): db.add(tx)
    db.commit()
    state = ledger_state(db, portfolio)
    assert state["holdings"][instrument.id]["quantity"] == Decimal("8")
    assert state["holdings"][instrument.id]["average_cost"] == Decimal("120")
    assert state["realised_profit"] == Decimal("560")


def test_target_date_prevents_same_day_preclose_lookahead():
    before_close_utc = datetime(2026, 8, 17, 8, 0)  # 13:30 IST
    after_close_utc = datetime(2026, 8, 17, 11, 0)  # 16:30 IST
    assert target_price_date(before_close_utc) == date(2026, 8, 18)
    assert target_price_date(after_close_utc) == date(2026, 8, 17)


def test_recommendation_learning_keeps_future_horizons_pending_and_measures_available_ones():
    db = db_session()
    stock = Instrument(exchange="NSE", symbol="LEARN", company_name="Learn Ltd")
    benchmark = Instrument(exchange="NSE", symbol="INDEX", company_name="Index")
    db.add_all([stock, benchmark]); db.flush()
    portfolio = NotionalPortfolio(name="Learning", starting_cash=10000, max_position_weight=1,
        benchmark_instrument_id=benchmark.id, created_at=datetime(2025, 1, 1))
    db.add(portfolio); db.flush()
    db.add(NotionalTransaction(portfolio_id=portfolio.id, instrument_id=stock.id,
        transaction_type="BUY", status="EXECUTED", decision_at=datetime(2025, 1, 1),
        execution_date=date(2025, 1, 2), quantity=10, execution_price=100, charges=0,
        recommendation_snapshot={"recommendation": "STRONG_BUY", "financial_score": 85,
                                 "style_matches": ["Quality"]}))
    for instrument_id, values in ((stock.id, [(date(2025, 1, 2), 100), (date(2025, 4, 2), 120)]),
                                  (benchmark.id, [(date(2025, 1, 2), 100), (date(2025, 4, 2), 110)])):
        for day, value in values: db.add(Price(instrument_id=instrument_id, price_date=day,
                                                close_price=value, source="TEST"))
    db.commit()
    result = recommendation_learning(db, portfolio)["outcomes"][0]
    assert result["horizons"]["3m"]["forward_return"] == Decimal("0.2")
    assert result["horizons"]["3m"]["benchmark_relative_return"] == Decimal("0.1")
    assert result["horizons"]["6m"]["status"] == "PENDING"
