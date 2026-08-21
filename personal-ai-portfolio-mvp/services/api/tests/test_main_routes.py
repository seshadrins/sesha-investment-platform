from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import Account, DisclosureDocument, Instrument, Transaction, TransactionType


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture
def client(session_factory):
    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    # Not used as a context manager: this skips the app's startup lifespan (which
    # otherwise touches the real DATABASE_URL engine, not the in-memory test one).
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


def _seed_oversell_position(session_factory) -> int:
    """Seed a same-day SELL-before-BUY style ledger corruption: a position that
    sells more than it ever bought, so portfolio_snapshot() raises ValueError."""
    db = session_factory()
    account = Account(name="Broken", broker_name="Manual", currency="INR")
    instrument = Instrument(exchange="NSE", symbol="BAD", company_name="Bad Ltd")
    db.add_all([account, instrument])
    db.flush()
    db.add_all([
        Transaction(account_id=account.id, instrument_id=instrument.id,
            transaction_type=TransactionType.BUY, trade_date=date(2025, 1, 1),
            quantity=Decimal("2"), price=Decimal("100"), charges=Decimal("0")),
        Transaction(account_id=account.id, instrument_id=instrument.id,
            transaction_type=TransactionType.SELL, trade_date=date(2025, 2, 1),
            quantity=Decimal("3"), price=Decimal("120"), charges=Decimal("0")),
    ])
    db.commit()
    sell_id = db.scalars(
        select(Transaction.id).where(Transaction.transaction_type == TransactionType.SELL)
    ).first()
    db.close()
    return sell_id


@pytest.mark.parametrize("route", [
    "/instruments",
    "/prospective-stocks",
    "/stock-workbench",
    "/investor-signals",
])
def test_oversell_ledger_row_returns_4xx_not_500(client, session_factory, route):
    _seed_oversell_position(session_factory)
    response = client.get(route)
    assert response.status_code == 409, response.text
    assert "sells" in response.json()["detail"].lower()


def test_deleting_the_bad_transaction_recovers_the_dashboard(client, session_factory):
    sell_id = _seed_oversell_position(session_factory)
    assert client.get("/instruments").status_code == 409

    delete_response = client.delete(f"/transactions/{sell_id}")
    assert delete_response.status_code == 200

    assert client.get("/instruments").status_code == 200
    assert client.get("/portfolio").status_code == 200


def test_delete_transaction_404_for_unknown_id(client):
    response = client.delete("/transactions/999999")
    assert response.status_code == 404


def test_imports_opening_rejects_non_positive_quantity(client, session_factory):
    csv_body = (
        "account_name,symbol,as_of_date,quantity,average_price\n"
        "Test Account,ABC,2025-01-01,-5,100\n"
    )
    response = client.post(
        "/imports/opening",
        files={"file": ("opening.csv", csv_body.encode("utf-8"), "text/csv")},
    )
    assert response.status_code == 400
    assert "quantity" in response.json()["detail"].lower()

    db = session_factory()
    assert db.query(Transaction).count() == 0
    db.close()


def test_imports_opening_accepts_valid_rows(client, session_factory):
    csv_body = (
        "account_name,symbol,as_of_date,quantity,average_price\n"
        "Test Account,ABC,2025-01-01,10,100\n"
    )
    response = client.post(
        "/imports/opening",
        files={"file": ("opening.csv", csv_body.encode("utf-8"), "text/csv")},
    )
    assert response.status_code == 200
    assert response.json()["imported"] == 1


def test_stock_workbench_forwards_portfolio_summary(client, session_factory):
    db = session_factory()
    account = Account(name="Main", broker_name="Manual", currency="INR")
    instrument = Instrument(exchange="NSE", symbol="GOOD", company_name="Good Ltd")
    db.add_all([account, instrument])
    db.flush()
    db.add(Transaction(account_id=account.id, instrument_id=instrument.id,
        transaction_type=TransactionType.BUY, trade_date=date(2025, 1, 1),
        quantity=Decimal("10"), price=Decimal("100"), charges=Decimal("0")))
    db.commit()
    db.close()

    response = client.get("/stock-workbench")
    assert response.status_code == 200
    payload = response.json()
    assert "summary" in payload
    assert payload["summary"]["position_count"] == 1


def test_saving_thesis_patches_cached_workbench_snapshot_immediately(client, session_factory):
    db = session_factory()
    account = Account(name="Main", broker_name="Manual", currency="INR")
    instrument = Instrument(exchange="NSE", symbol="GOOD", company_name="Good Ltd")
    db.add_all([account, instrument])
    db.flush()
    db.add(Transaction(account_id=account.id, instrument_id=instrument.id,
        transaction_type=TransactionType.BUY, trade_date=date(2025, 1, 1),
        quantity=Decimal("10"), price=Decimal("100"), charges=Decimal("0")))
    db.commit()
    instrument_id = instrument.id
    db.close()

    # Force the cached "stock_workbench" snapshot to exist before the thesis edit.
    first = client.get("/stock-workbench")
    assert first.status_code == 200
    assert first.json()["owned"][0]["portfolio"]["thesis_status"] is None

    thesis_response = client.post("/theses", json={
        "instrument_id": instrument_id,
        "status": "WATCH",
        "reason": "Reassessing after a governance flag.",
    })
    assert thesis_response.status_code == 200

    second = client.get("/stock-workbench")
    assert second.status_code == 200
    owned_row = second.json()["owned"][0]
    assert owned_row["portfolio"]["thesis_status"] == "WATCH"
    assert owned_row["portfolio"]["thesis_reason"] == "Reassessing after a governance flag."


def test_same_day_sell_before_buy_is_rejected_by_the_oversell_guard(client, session_factory):
    db = session_factory()
    account = Account(name="RouteTest", broker_name="Manual", currency="INR")
    instrument = Instrument(exchange="NSE", symbol="ROUTE", company_name="Route Ltd")
    db.add_all([account, instrument])
    db.commit()
    account_id, instrument_id = account.id, instrument.id
    db.close()

    # Posted in this order (SELL before its matching BUY exists), both dated the same day —
    # the oversell guard must reject the SELL immediately rather than let a same-day
    # ordering quirk silently corrupt the ledger.
    sell_response = client.post("/transactions", json={
        "account_id": account_id, "instrument_id": instrument_id,
        "transaction_type": "SELL", "trade_date": "2026-01-05",
        "quantity": 5, "price": 120, "charges": 0,
    })
    assert sell_response.status_code == 400

    buy_response = client.post("/transactions", json={
        "account_id": account_id, "instrument_id": instrument_id,
        "transaction_type": "BUY", "trade_date": "2026-01-05",
        "quantity": 10, "price": 100, "charges": 0,
    })
    assert buy_response.status_code == 200

    db = session_factory()
    assert db.query(Transaction).count() == 1
    db.close()


def test_disclosure_document_content_endpoint_returns_stored_bytes(client, session_factory):
    db = session_factory()
    instrument = Instrument(exchange="NSE", symbol="DOCTEST", company_name="Doc Test Ltd")
    db.add(instrument)
    db.flush()
    document = DisclosureDocument(
        instrument_id=instrument.id, exchange="NSE", report_date=date(2026, 3, 31),
        source_url="https://www.nseindia.com/doc-test.xml", content_type="application/xml",
        content=b"<xbrl>raw filing bytes</xbrl>",
    )
    db.add(document)
    db.commit()
    document_id = document.id
    db.close()

    response = client.get(f"/investor-disclosures/documents/{document_id}/content")
    assert response.status_code == 200
    assert response.content == b"<xbrl>raw filing bytes</xbrl>"
    assert response.headers["content-type"].startswith("application/xml")


def test_disclosure_document_content_endpoint_404_when_no_content_stored(client, session_factory):
    db = session_factory()
    instrument = Instrument(exchange="NSE", symbol="NODOC", company_name="No Doc Ltd")
    db.add(instrument)
    db.flush()
    document = DisclosureDocument(
        instrument_id=instrument.id, exchange="NSE", report_date=date(2026, 3, 31),
        source_url="https://www.nseindia.com/no-content.xml",
    )
    db.add(document)
    db.commit()
    document_id = document.id
    db.close()

    response = client.get(f"/investor-disclosures/documents/{document_id}/content")
    assert response.status_code == 404
