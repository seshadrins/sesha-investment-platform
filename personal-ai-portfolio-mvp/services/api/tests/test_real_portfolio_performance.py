from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Account, Instrument, Price, Transaction, TransactionType
from app.real_portfolio_performance import real_performance_history


def db_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed_account_instrument(db, symbol="ABC"):
    account = Account(name="Main", broker_name="Manual", currency="INR")
    instrument = Instrument(exchange="NSE", symbol=symbol, company_name=f"{symbol} Ltd")
    db.add_all([account, instrument])
    db.commit()
    return account, instrument


def test_no_transactions_yet_returns_empty_history_without_crashing():
    db = db_session()
    result = real_performance_history(db)
    assert result["rows"] == []
    assert result["max_drawdown"] is None
    assert result["time_weighted_return"] is None
    assert result["money_weighted_return"] is None
    assert result["benchmark_available"] is False


def test_single_lot_no_price_change_shows_zero_return():
    db = db_session()
    account, instrument = _seed_account_instrument(db)
    db.add(Transaction(account_id=account.id, instrument_id=instrument.id,
        transaction_type=TransactionType.BUY, trade_date=date(2026, 1, 1),
        quantity=Decimal("10"), price=Decimal("100"), charges=Decimal("0")))
    db.add(Price(instrument_id=instrument.id, price_date=date(2026, 1, 1), close_price=Decimal("100")))
    db.commit()

    result = real_performance_history(db)
    assert len(result["rows"]) == 1
    row = result["rows"][0]
    assert row["market_value"] == Decimal("1000")
    assert row["net_contributions"] == Decimal("1000")
    assert row["total_return"] == Decimal("0")
    assert row["total_return_pct"] == Decimal("0")
    assert result["max_drawdown"] == Decimal("0")


def test_single_lot_price_appreciation_produces_matching_return_and_no_drawdown():
    db = db_session()
    account, instrument = _seed_account_instrument(db)
    db.add(Transaction(account_id=account.id, instrument_id=instrument.id,
        transaction_type=TransactionType.BUY, trade_date=date(2026, 1, 1),
        quantity=Decimal("10"), price=Decimal("100"), charges=Decimal("0")))
    db.add_all([
        Price(instrument_id=instrument.id, price_date=date(2026, 1, 1), close_price=Decimal("100")),
        Price(instrument_id=instrument.id, price_date=date(2026, 2, 1), close_price=Decimal("150")),
    ])
    db.commit()

    result = real_performance_history(db)
    assert len(result["rows"]) == 2
    final = result["rows"][-1]
    assert final["market_value"] == Decimal("1500")
    assert final["total_return"] == Decimal("500")
    assert final["total_return_pct"] == Decimal("0.5")
    assert result["max_drawdown"] == Decimal("0")


def test_price_dip_then_recovery_produces_a_measurable_drawdown():
    db = db_session()
    account, instrument = _seed_account_instrument(db)
    db.add(Transaction(account_id=account.id, instrument_id=instrument.id,
        transaction_type=TransactionType.BUY, trade_date=date(2026, 1, 1),
        quantity=Decimal("10"), price=Decimal("100"), charges=Decimal("0")))
    db.add_all([
        Price(instrument_id=instrument.id, price_date=date(2026, 1, 1), close_price=Decimal("100")),
        Price(instrument_id=instrument.id, price_date=date(2026, 2, 1), close_price=Decimal("80")),
        Price(instrument_id=instrument.id, price_date=date(2026, 3, 1), close_price=Decimal("100")),
    ])
    db.commit()

    result = real_performance_history(db)
    assert result["max_drawdown"] == Decimal("-0.2")


def test_full_exit_at_a_profit_produces_a_matching_total_return_via_realised_profit():
    # Regression for the case that's easy to get wrong: once every share is sold,
    # market_value drops to zero, but total_return must still reflect the profit taken,
    # not read as a loss just because nothing is held any more.
    db = db_session()
    account, instrument = _seed_account_instrument(db)
    db.add_all([
        Transaction(account_id=account.id, instrument_id=instrument.id,
            transaction_type=TransactionType.BUY, trade_date=date(2026, 1, 1),
            quantity=Decimal("20"), price=Decimal("10"), charges=Decimal("0")),
        Transaction(account_id=account.id, instrument_id=instrument.id,
            transaction_type=TransactionType.SELL, trade_date=date(2026, 2, 1),
            quantity=Decimal("20"), price=Decimal("15"), charges=Decimal("0")),
    ])
    db.add_all([
        Price(instrument_id=instrument.id, price_date=date(2026, 1, 1), close_price=Decimal("10")),
        Price(instrument_id=instrument.id, price_date=date(2026, 2, 1), close_price=Decimal("15")),
    ])
    db.commit()

    result = real_performance_history(db)
    final = result["rows"][-1]
    assert final["market_value"] == Decimal("0")
    assert final["total_return"] == Decimal("100")  # 20 * (15 - 10)


def test_adjustment_in_treated_as_a_contribution_like_a_buy():
    # "Corporate action" edge case: a quantity correction via ADJUSTMENT_IN (e.g. a bonus
    # allotment recorded manually) must not blow up the valuation or contribution math.
    db = db_session()
    account, instrument = _seed_account_instrument(db)
    db.add_all([
        Transaction(account_id=account.id, instrument_id=instrument.id,
            transaction_type=TransactionType.BUY, trade_date=date(2026, 1, 1),
            quantity=Decimal("10"), price=Decimal("100"), charges=Decimal("0")),
        Transaction(account_id=account.id, instrument_id=instrument.id,
            transaction_type=TransactionType.ADJUSTMENT_IN, trade_date=date(2026, 2, 1),
            quantity=Decimal("5"), price=Decimal("0"), charges=Decimal("0")),
    ])
    db.add(Price(instrument_id=instrument.id, price_date=date(2026, 2, 1), close_price=Decimal("100")))
    db.commit()

    result = real_performance_history(db)
    final = result["rows"][-1]
    assert final["market_value"] == Decimal("1500")  # 15 shares * 100
    assert final["net_contributions"] == Decimal("1000")  # the free 5 shares cost nothing
    assert final["total_return"] == Decimal("500")


def test_dividends_are_banked_into_total_value_without_touching_contributions():
    db = db_session()
    account, instrument = _seed_account_instrument(db)
    db.add_all([
        Transaction(account_id=account.id, instrument_id=instrument.id,
            transaction_type=TransactionType.BUY, trade_date=date(2026, 1, 1),
            quantity=Decimal("10"), price=Decimal("100"), charges=Decimal("0")),
        Transaction(account_id=account.id, instrument_id=instrument.id,
            transaction_type=TransactionType.DIVIDEND, trade_date=date(2026, 2, 1),
            quantity=Decimal("10"), price=Decimal("5"), charges=Decimal("0")),
    ])
    db.add(Price(instrument_id=instrument.id, price_date=date(2026, 2, 1), close_price=Decimal("100")))
    db.commit()

    result = real_performance_history(db)
    final = result["rows"][-1]
    assert final["dividend_income"] == Decimal("50")
    assert final["net_contributions"] == Decimal("1000")
    assert final["total_value"] == Decimal("1050")
    assert final["total_return"] == Decimal("50")


def test_benchmark_curve_is_normalised_to_the_earliest_tracked_contribution():
    db = db_session()
    account, instrument = _seed_account_instrument(db, "ABC")
    benchmark = Instrument(exchange="NSE", symbol="NIFTY", company_name="Index")
    db.add(benchmark)
    db.flush()
    db.add(Transaction(account_id=account.id, instrument_id=instrument.id,
        transaction_type=TransactionType.BUY, trade_date=date(2026, 1, 1),
        quantity=Decimal("10"), price=Decimal("100"), charges=Decimal("0")))
    db.add_all([
        Price(instrument_id=instrument.id, price_date=date(2026, 1, 1), close_price=Decimal("100")),
        Price(instrument_id=instrument.id, price_date=date(2026, 2, 1), close_price=Decimal("100")),
        Price(instrument_id=benchmark.id, price_date=date(2026, 1, 1), close_price=Decimal("20000")),
        Price(instrument_id=benchmark.id, price_date=date(2026, 2, 1), close_price=Decimal("22000")),
    ])
    db.commit()

    result = real_performance_history(db, benchmark_instrument_id=benchmark.id)
    assert result["benchmark_available"] is True
    final = result["rows"][-1]
    # Starting contribution (1000) grown by the benchmark's own 10% move.
    assert final["benchmark_value"] == Decimal("1100")
