from datetime import date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from decimal import Decimal

from sqlalchemy import select

from app.automation_pipeline import (
    _automation_job_definitions,
    _build_stock_workbench,
    _latest_automation_results,
    _priority_financial_instruments,
    _record_automation_status,
    _refresh_universe_if_due,
    _snapshot_metadata,
    _store_workbench_snapshot,
    _upstox_instruments,
)
from app.database import Base
from app.models import (
    Account,
    AnalysisSnapshot,
    AppNotification,
    Instrument,
    NotionalTransaction,
    Price,
    Thesis,
    ThesisStatus,
    Transaction,
    TransactionType,
    UniverseMembership,
    WatchlistItem,
)


def session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _own(db, symbol: str, isin: str | None = "INE000A00000", exchange: str = "NSE") -> Instrument:
    account = db.query(Account).filter_by(name="Main").first()
    if not account:
        account = Account(name="Main", broker_name="Manual", currency="INR")
        db.add(account)
        db.flush()
    instrument = Instrument(exchange=exchange, symbol=symbol, company_name=f"{symbol} Ltd", isin=isin)
    db.add(instrument)
    db.flush()
    db.add(Transaction(account_id=account.id, instrument_id=instrument.id,
        transaction_type=TransactionType.BUY, trade_date=date(2025, 1, 1),
        quantity=10, price=100, charges=0))
    db.commit()
    return instrument


def test_priority_financial_instruments_includes_owned_and_prospective_with_an_isin():
    db = session()
    owned = _own(db, "OWNED")
    prospective = Instrument(exchange="NSE", symbol="PROS", company_name="Prospective Ltd",
                             isin="INE111B11111")
    no_isin = Instrument(exchange="NSE", symbol="NOISIN", company_name="No ISIN Ltd", isin=None)
    unlisted_exchange = Instrument(exchange="MCX", symbol="COMM", company_name="Commodity Ltd",
                                   isin="INE222C22222")
    db.add_all([prospective, no_isin, unlisted_exchange])
    db.flush()
    db.add_all([
        WatchlistItem(instrument_id=prospective.id, status="ACTIVE"),
        WatchlistItem(instrument_id=no_isin.id, status="ACTIVE"),
        WatchlistItem(instrument_id=unlisted_exchange.id, status="ACTIVE"),
    ])
    db.commit()

    result = {item.id for item in _priority_financial_instruments(db)}
    assert result == {owned.id, prospective.id}


def test_priority_financial_instruments_empty_when_nothing_owned_or_prospective():
    db = session()
    assert _priority_financial_instruments(db) == []


def test_upstox_instruments_hides_universe_members_that_are_not_owned_prospective_or_notional():
    db = session()
    owned = _own(db, "OWNED")
    hidden = Instrument(exchange="NSE", symbol="HIDDEN", company_name="Hidden Ltd",
                        isin="INE333D33333")
    notional_only = Instrument(exchange="NSE", symbol="NOTIONAL", company_name="Notional Ltd",
                               isin="INE444E44444")
    db.add_all([hidden, notional_only])
    db.flush()
    db.add_all([
        UniverseMembership(universe_id="nifty500", instrument_id=owned.id, cap_segment="LARGE",
                           active=True, as_of=date(2026, 1, 1), source_url="https://example.test"),
        UniverseMembership(universe_id="nifty500", instrument_id=hidden.id, cap_segment="MID",
                           active=True, as_of=date(2026, 1, 1), source_url="https://example.test"),
        UniverseMembership(universe_id="nifty500", instrument_id=notional_only.id,
                           cap_segment="SMALL", active=True, as_of=date(2026, 1, 1),
                           source_url="https://example.test"),
        NotionalTransaction(portfolio_id=1, instrument_id=notional_only.id,
                            transaction_type="BUY", status="PENDING_PRICE",
                            decision_at=datetime(2026, 1, 1)),
    ])
    db.commit()

    supported, skipped = _upstox_instruments(db)
    symbols = {item.symbol for item in supported}
    assert symbols == {"OWNED", "NOTIONAL"}
    assert "HIDDEN" not in symbols


def test_upstox_instruments_skips_instruments_without_an_isin():
    db = session()
    _own(db, "OWNED", isin=None)
    supported, skipped = _upstox_instruments(db)
    assert supported == []
    assert skipped == ["NSE:OWNED"]


def test_latest_automation_results_only_returns_automation_prefixed_snapshots_with_a_payload():
    db = session()
    _record_automation_status(db, "market_prices", {"status": "SUCCESS", "prices_imported": 5})
    db.add(AnalysisSnapshot(snapshot_key="stock_workbench", payload={"owned": []}))
    db.add(AnalysisSnapshot(snapshot_key="automation:ipo_lifecycle", payload=None,
                            last_status="FAILED"))
    db.commit()

    results = _latest_automation_results(db)
    assert results == {"market_prices": {"status": "SUCCESS", "prices_imported": 5}}


def test_record_automation_status_updates_an_existing_snapshot_in_place():
    db = session()
    first = _record_automation_status(db, "market_prices", {"status": "SUCCESS"})
    second = _record_automation_status(db, "market_prices", {"status": "FAILED"},
                                       status="FAILED", error="provider unavailable")
    assert first.id == second.id
    assert second.last_status == "FAILED"
    assert second.last_error == "provider unavailable"
    assert second.payload == {"status": "FAILED"}


def test_automation_job_definitions_covers_every_pipeline_stage():
    db = session()
    definitions = _automation_job_definitions(db)
    ids = {item["id"] for item in definitions}
    assert ids == {
        "market_prices", "notional_settlement", "ipo_lifecycle", "nifty500_constituents",
        "financial_statements", "nifty500_screening", "investor_disclosures",
        "morning_orchestrator",
    }
    assert all(item["frequency"] and item["policy"] for item in definitions)


def test_snapshot_metadata_formats_timestamps_and_embeds_the_schedule():
    db = session()
    snapshot = AnalysisSnapshot(snapshot_key="stock_workbench",
        generated_at=datetime(2026, 1, 1, 6, 0), last_attempted_at=datetime(2026, 1, 1, 6, 0),
        last_status="SUCCESS", last_error=None)
    metadata = _snapshot_metadata(snapshot, "CACHED", db)
    assert metadata["mode"] == "CACHED"
    assert metadata["generated_at"] == "2026-01-01T06:00:00"
    assert metadata["last_status"] == "SUCCESS"
    assert "schedule" in metadata and "enabled" in metadata["schedule"]


def test_refresh_universe_if_due_reports_current_without_touching_the_network_when_recently_refreshed():
    db = session()
    instrument = Instrument(exchange="NSE", symbol="MEM", company_name="Member Ltd")
    db.add(instrument)
    db.flush()
    db.add(UniverseMembership(universe_id="nifty500", instrument_id=instrument.id,
        cap_segment="LARGE", active=True, as_of=date.today(), source_url="https://example.test"))
    db.commit()

    result = _refresh_universe_if_due(db)
    assert result["status"] == "CURRENT"
    assert result["refreshed"] is False


def _seeded_two_stock_portfolio(db):
    """AAA Company is alphabetically first but ends up a plain HOLD; ZZZ Company is
    alphabetically last but ends up STRONG_SELL via an invalid thesis — a large ANCHOR
    position keeps both positions' weight well under the TRIM threshold so severity, not
    weight, decides the outcome."""
    account = Account(name="Main", broker_name="Manual", currency="INR")
    anchor = Instrument(exchange="NSE", symbol="ANCHOR", company_name="Anchor Ltd")
    aaa = Instrument(exchange="NSE", symbol="AAACO", company_name="AAA Company")
    zzz = Instrument(exchange="NSE", symbol="ZZZCO", company_name="ZZZ Company")
    db.add_all([account, anchor, aaa, zzz])
    db.flush()
    db.add_all([
        Transaction(account_id=account.id, instrument_id=anchor.id,
            transaction_type=TransactionType.BUY, trade_date=date(2020, 1, 1),
            quantity=Decimal("9000"), price=Decimal("100"), charges=Decimal("0")),
        Transaction(account_id=account.id, instrument_id=aaa.id,
            transaction_type=TransactionType.BUY, trade_date=date(2025, 1, 1),
            quantity=Decimal("100"), price=Decimal("100"), charges=Decimal("0")),
        Transaction(account_id=account.id, instrument_id=zzz.id,
            transaction_type=TransactionType.BUY, trade_date=date(2025, 1, 1),
            quantity=Decimal("100"), price=Decimal("100"), charges=Decimal("0")),
        Price(instrument_id=anchor.id, price_date=date.today(), close_price=Decimal("100")),
        Price(instrument_id=aaa.id, price_date=date.today(), close_price=Decimal("100")),
        Price(instrument_id=zzz.id, price_date=date.today(), close_price=Decimal("100")),
    ])
    db.commit()
    return account, anchor, aaa, zzz


# ---- G6: owned rows sort by recommendation severity, not alphabetically ----

def test_owned_workbench_rows_sort_by_severity_not_alphabetically():
    db = session()
    _account, _anchor, aaa, zzz = _seeded_two_stock_portfolio(db)
    db.add(Thesis(instrument_id=zzz.id, status=ThesisStatus.INVALID))
    db.commit()

    payload = _build_stock_workbench(db)
    owned_symbols = [row["symbol"] for row in payload["owned"]]
    assert owned_symbols.index("ZZZCO") < owned_symbols.index("AAACO")
    zzz_row = next(row for row in payload["owned"] if row["symbol"] == "ZZZCO")
    assert zzz_row["summary"]["recommendation"] == "STRONG_SELL"


# ---- G5: a recommendation transition on an owned stock produces exactly one notification ----

def test_recommendation_transition_produces_exactly_one_notification():
    db = session()
    _account, _anchor, aaa, zzz = _seeded_two_stock_portfolio(db)

    first_payload = _build_stock_workbench(db)
    _store_workbench_snapshot(db, first_payload)
    assert db.scalar(select(AppNotification).where(
        AppNotification.category == "RECOMMENDATION"
    )) is None  # no prior snapshot to diff against on the very first store

    db.add(Thesis(instrument_id=zzz.id, status=ThesisStatus.INVALID))
    db.commit()
    second_payload = _build_stock_workbench(db)
    _store_workbench_snapshot(db, second_payload)
    notifications = db.scalars(select(AppNotification).where(
        AppNotification.category == "RECOMMENDATION"
    )).all()
    assert len(notifications) == 1
    assert "STRONG SELL" in notifications[0].title
    assert notifications[0].payload["new_recommendation"] == "STRONG_SELL"
    assert notifications[0].payload["instrument_id"] == zzz.id


def test_unchanged_recommendation_produces_no_new_notification():
    db = session()
    _account, _anchor, aaa, zzz = _seeded_two_stock_portfolio(db)

    first_payload = _build_stock_workbench(db)
    _store_workbench_snapshot(db, first_payload)

    # Rebuild and store again with nothing changed.
    second_payload = _build_stock_workbench(db)
    _store_workbench_snapshot(db, second_payload)

    assert db.scalar(select(AppNotification).where(
        AppNotification.category == "RECOMMENDATION"
    )) is None
