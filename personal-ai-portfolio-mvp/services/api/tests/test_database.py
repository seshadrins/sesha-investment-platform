from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Account, Instrument, Transaction, TransactionType


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    yield db
    db.close()


def _seed_account_and_instrument(db) -> tuple[int, int]:
    account = Account(name="Constraint Test", broker_name="Manual", currency="INR")
    instrument = Instrument(exchange="NSE", symbol="CONSTR", company_name="Constraint Ltd")
    db.add_all([account, instrument])
    db.commit()
    return account.id, instrument.id


@pytest.mark.parametrize("quantity", [Decimal("-5"), Decimal("0")])
def test_non_positive_quantity_is_rejected_at_the_db_level_even_via_the_orm(session, quantity):
    """The API layer already rejects quantity <= 0 before it reaches the ORM (see
    create_transaction), so this bypasses that check entirely — constructing the row
    directly through the session, the way a future code path or a bulk import might —
    to prove the CHECK constraint is a real backstop, not just a pre-commit convenience."""
    account_id, instrument_id = _seed_account_and_instrument(session)
    session.add(Transaction(
        account_id=account_id, instrument_id=instrument_id,
        transaction_type=TransactionType.BUY, trade_date=date(2025, 1, 1),
        quantity=quantity, price=Decimal("100"), charges=Decimal("0"),
    ))
    with pytest.raises(IntegrityError, match="ck_transaction_quantity_positive"):
        session.commit()


def test_negative_price_is_rejected_at_the_db_level_even_via_the_orm(session):
    account_id, instrument_id = _seed_account_and_instrument(session)
    session.add(Transaction(
        account_id=account_id, instrument_id=instrument_id,
        transaction_type=TransactionType.BUY, trade_date=date(2025, 1, 1),
        quantity=Decimal("10"), price=Decimal("-1"), charges=Decimal("0"),
    ))
    with pytest.raises(IntegrityError, match="ck_transaction_price_non_negative"):
        session.commit()


def test_negative_charges_is_rejected_at_the_db_level_even_via_the_orm(session):
    account_id, instrument_id = _seed_account_and_instrument(session)
    session.add(Transaction(
        account_id=account_id, instrument_id=instrument_id,
        transaction_type=TransactionType.BUY, trade_date=date(2025, 1, 1),
        quantity=Decimal("10"), price=Decimal("100"), charges=Decimal("-1"),
    ))
    with pytest.raises(IntegrityError, match="ck_transaction_charges_non_negative"):
        session.commit()


def test_invalid_screening_recommendation_is_rejected_at_the_db_level(session):
    instrument = Instrument(exchange="NSE", symbol="SCRTEST", company_name="Screen Test Ltd")
    session.add(instrument)
    session.commit()
    with pytest.raises(IntegrityError, match="ck_screening_result_recommendation_vocabulary"):
        session.execute(text(
            "INSERT INTO screening_results "
            "(universe_id, instrument_id, screened_on, recommendation, style_matches, "
            "analysis_status, reasons, criteria_version, created_at) "
            "VALUES ('nifty500', :instrument_id, :screened_on, 'NOT_A_REAL_RECOMMENDATION', "
            "0, 'READY', '[]', 'v1', :created_at)"
        ), {"instrument_id": instrument.id, "screened_on": date(2025, 1, 1).isoformat(),
            "created_at": "2025-01-01T00:00:00"})


def _explain_plan(session, sql: str, params: dict | None = None) -> str:
    rows = session.execute(text(f"EXPLAIN QUERY PLAN {sql}"), params or {}).all()
    return " | ".join(str(row) for row in rows)


def test_transactions_instrument_id_filter_uses_the_new_index(session):
    plan = _explain_plan(
        session, "SELECT * FROM transactions WHERE instrument_id = :instrument_id",
        {"instrument_id": 1},
    )
    assert "ix_transactions_instrument_id" in plan, plan
    assert "SCAN" not in plan.upper() or "USING INDEX" in plan.upper(), plan


def test_transactions_account_id_filter_uses_the_new_index(session):
    plan = _explain_plan(
        session, "SELECT * FROM transactions WHERE account_id = :account_id", {"account_id": 1}
    )
    assert "ix_transactions_account_id" in plan, plan


def test_transactions_trade_date_ordering_uses_the_new_index_instead_of_a_temp_sort(session):
    # This is the exact ordering portfolio_snapshot() runs on every read
    # (select(Transaction).order_by(Transaction.trade_date, Transaction.id)); without the
    # index SQLite must materialise a temp B-tree to satisfy the ORDER BY.
    plan = _explain_plan(session, "SELECT * FROM transactions ORDER BY trade_date, id")
    assert "ix_transactions_trade_date" in plan, plan
    assert "TEMP B-TREE" not in plan.upper(), plan
