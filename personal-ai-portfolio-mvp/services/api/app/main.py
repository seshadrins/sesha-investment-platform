from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import Base, engine, get_db
from .models import (
    Account,
    DecisionJournal,
    Instrument,
    Price,
    Thesis,
    Transaction,
    TransactionType,
)
from .schemas import (
    AccountCreate,
    AccountOut,
    DecisionCreate,
    InstrumentCreate,
    InstrumentOut,
    PriceCreate,
    ThesisUpsert,
    TransactionCreate,
)
from .services import portfolio_snapshot
from .providers import CsvCompanyResearchProvider, CsvMarketDataProvider, ProviderDataError
from .research import import_company_research, import_market_prices

app = FastAPI(
    title="Personal AI Portfolio Manager API",
    version="0.1.0",
    description="Personal portfolio ledger and transparent decision-support MVP.",
)


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/accounts", response_model=AccountOut)
def create_account(payload: AccountCreate, db: Session = Depends(get_db)):
    existing = db.scalar(select(Account).where(Account.name == payload.name))
    if existing:
        return existing
    account = Account(**payload.model_dump())
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


@app.get("/accounts", response_model=list[AccountOut])
def list_accounts(db: Session = Depends(get_db)):
    return db.scalars(select(Account).order_by(Account.name)).all()


@app.post("/instruments", response_model=InstrumentOut)
def create_instrument(payload: InstrumentCreate, db: Session = Depends(get_db)):
    exchange = payload.exchange.upper().strip()
    symbol = payload.symbol.upper().strip()
    existing = db.scalar(
        select(Instrument).where(
            Instrument.exchange == exchange, Instrument.symbol == symbol
        )
    )
    if existing:
        return existing
    data = payload.model_dump()
    data["exchange"] = exchange
    data["symbol"] = symbol
    instrument = Instrument(**data)
    db.add(instrument)
    db.commit()
    db.refresh(instrument)
    return instrument


@app.get("/instruments", response_model=list[InstrumentOut])
def list_instruments(db: Session = Depends(get_db)):
    return db.scalars(
        select(Instrument).order_by(Instrument.exchange, Instrument.symbol)
    ).all()


@app.post("/transactions")
def create_transaction(payload: TransactionCreate, db: Session = Depends(get_db)):
    if payload.transaction_type in {
        TransactionType.OPENING,
        TransactionType.BUY,
        TransactionType.SELL,
        TransactionType.ADJUSTMENT_IN,
        TransactionType.ADJUSTMENT_OUT,
    } and payload.quantity <= 0:
        raise HTTPException(400, "Quantity must be greater than zero.")

    tx = Transaction(**payload.model_dump(), source="MANUAL")
    db.add(tx)
    try:
        db.flush()
        portfolio_snapshot(db)  # validates against overselling
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    db.refresh(tx)
    return {"id": tx.id}


@app.get("/transactions")
def list_transactions(db: Session = Depends(get_db)):
    rows = db.execute(
        select(Transaction, Account, Instrument)
        .join(Account, Transaction.account_id == Account.id)
        .join(Instrument, Transaction.instrument_id == Instrument.id)
        .order_by(Transaction.trade_date.desc(), Transaction.id.desc())
    ).all()
    return [
        {
            "id": tx.id,
            "trade_date": tx.trade_date.isoformat(),
            "account": account.name,
            "exchange": instrument.exchange,
            "symbol": instrument.symbol,
            "transaction_type": tx.transaction_type.value,
            "quantity": float(tx.quantity),
            "price": float(tx.price),
            "charges": float(tx.charges),
            "notes": tx.notes,
            "source": tx.source,
        }
        for tx, account, instrument in rows
    ]


@app.post("/prices")
def upsert_price(payload: PriceCreate, db: Session = Depends(get_db)):
    item = db.scalar(
        select(Price).where(
            Price.instrument_id == payload.instrument_id,
            Price.price_date == payload.price_date,
        )
    )
    if item:
        item.close_price = payload.close_price
        item.source = "MANUAL"
    else:
        item = Price(**payload.model_dump(), source="MANUAL")
        db.add(item)
    db.commit()
    return {"id": item.id}


@app.post("/theses")
def upsert_thesis(payload: ThesisUpsert, db: Session = Depends(get_db)):
    thesis = db.scalar(select(Thesis).where(Thesis.instrument_id == payload.instrument_id))
    if thesis:
        for key, value in payload.model_dump(exclude={"instrument_id"}).items():
            setattr(thesis, key, value)
    else:
        thesis = Thesis(**payload.model_dump())
        db.add(thesis)
    db.commit()
    return {"id": thesis.id}


@app.get("/theses/{instrument_id}")
def get_thesis(instrument_id: int, db: Session = Depends(get_db)):
    thesis = db.scalar(select(Thesis).where(Thesis.instrument_id == instrument_id))
    if not thesis:
        return None
    return {
        "id": thesis.id,
        "instrument_id": thesis.instrument_id,
        "status": thesis.status.value,
        "reason": thesis.reason,
        "catalysts": thesis.catalysts,
        "risks": thesis.risks,
        "invalidation_conditions": thesis.invalidation_conditions,
        "target_horizon_months": thesis.target_horizon_months,
    }


@app.post("/decisions")
def create_decision(payload: DecisionCreate, db: Session = Depends(get_db)):
    decision = DecisionJournal(**payload.model_dump())
    db.add(decision)
    db.commit()
    return {"id": decision.id}


@app.get("/decisions")
def list_decisions(db: Session = Depends(get_db)):
    rows = db.execute(
        select(DecisionJournal, Instrument)
        .join(Instrument, DecisionJournal.instrument_id == Instrument.id)
        .order_by(DecisionJournal.decision_date.desc())
    ).all()
    return [
        {
            "id": d.id,
            "decision_date": d.decision_date.isoformat(),
            "exchange": i.exchange,
            "symbol": i.symbol,
            "recommendation": d.recommendation,
            "rationale": d.rationale,
            "user_decision": d.user_decision,
            "notes": d.notes,
        }
        for d, i in rows
    ]


@app.get("/portfolio")
def get_portfolio(db: Session = Depends(get_db)):
    try:
        return portfolio_snapshot(db)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


def _get_or_create_account(db: Session, name: str, broker: str) -> Account:
    account = db.scalar(select(Account).where(Account.name == name))
    if not account:
        account = Account(name=name, broker_name=broker or "Manual", currency="INR")
        db.add(account)
        db.flush()
    return account


def _get_or_create_instrument(
    db: Session, exchange: str, symbol: str, company_name: str, isin: str | None
) -> Instrument:
    exchange, symbol = exchange.upper().strip(), symbol.upper().strip()
    instrument = db.scalar(
        select(Instrument).where(
            Instrument.exchange == exchange, Instrument.symbol == symbol
        )
    )
    if not instrument:
        instrument = Instrument(
            exchange=exchange,
            symbol=symbol,
            company_name=company_name or symbol,
            isin=isin or None,
        )
        db.add(instrument)
        db.flush()
    return instrument


def _read_csv(upload: UploadFile) -> list[dict[str, str]]:
    raw = upload.file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(400, "CSV must use UTF-8 encoding.") from exc
    return list(csv.DictReader(io.StringIO(text)))


@app.post("/imports/opening")
def import_opening(file: UploadFile = File(...), db: Session = Depends(get_db)):
    rows = _read_csv(file)
    count = 0
    for row in rows:
        account = _get_or_create_account(
            db, row["account_name"].strip(), row.get("broker_name", "Manual").strip()
        )
        instrument = _get_or_create_instrument(
            db,
            row.get("exchange", "NSE"),
            row["symbol"],
            row.get("company_name", row["symbol"]),
            row.get("isin"),
        )
        tx = Transaction(
            account_id=account.id,
            instrument_id=instrument.id,
            transaction_type=TransactionType.OPENING,
            trade_date=date.fromisoformat(row["as_of_date"]),
            quantity=Decimal(row["quantity"]),
            price=Decimal(row["average_price"]),
            charges=Decimal("0"),
            notes=row.get("notes"),
            source="CSV_OPENING",
        )
        db.add(tx)
        count += 1
    db.commit()
    return {"imported": count}


@app.post("/imports/transactions")
def import_transactions(file: UploadFile = File(...), db: Session = Depends(get_db)):
    rows = _read_csv(file)
    count = 0
    for row in rows:
        account = _get_or_create_account(
            db, row["account_name"].strip(), row.get("broker_name", "Manual").strip()
        )
        instrument = _get_or_create_instrument(
            db,
            row.get("exchange", "NSE"),
            row["symbol"],
            row.get("company_name", row["symbol"]),
            row.get("isin"),
        )
        db.add(
            Transaction(
                account_id=account.id,
                instrument_id=instrument.id,
                transaction_type=TransactionType(row["transaction_type"].upper()),
                trade_date=date.fromisoformat(row["trade_date"]),
                quantity=Decimal(row.get("quantity") or "0"),
                price=Decimal(row.get("price") or "0"),
                charges=Decimal(row.get("charges") or "0"),
                notes=row.get("notes"),
                source="CSV_TRANSACTION",
            )
        )
        count += 1
    try:
        db.flush()
        portfolio_snapshot(db)
        db.commit()
    except (ValueError, KeyError) as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    return {"imported": count}


@app.post("/imports/prices")
def import_prices(file: UploadFile = File(...), db: Session = Depends(get_db)):
    try:
        count = import_market_prices(db, CsvMarketDataProvider(file.file.read()))
    except ProviderDataError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    return {"imported": count, "provider": "CSV"}


@app.post("/imports/company-research")
def import_research(file: UploadFile = File(...), db: Session = Depends(get_db)):
    try:
        count = import_company_research(db, CsvCompanyResearchProvider(file.file.read()))
    except ProviderDataError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    return {"imported": count, "provider": "CSV_RESEARCH"}


@app.post("/reconcile")
def reconcile(file: UploadFile = File(...), db: Session = Depends(get_db)):
    broker_rows = _read_csv(file)
    snapshot = portfolio_snapshot(db)
    calculated = {
        (p["account_name"], p["exchange"], p["symbol"]): Decimal(str(p["quantity"]))
        for p in snapshot["positions"]
    }
    results = []
    seen = set()
    for row in broker_rows:
        key = (
            row["account_name"].strip(),
            row["exchange"].upper().strip(),
            row["symbol"].upper().strip(),
        )
        seen.add(key)
        broker_qty = Decimal(row["broker_quantity"])
        calc_qty = calculated.get(key, Decimal("0"))
        results.append(
            {
                "account_name": key[0],
                "exchange": key[1],
                "symbol": key[2],
                "calculated_quantity": float(calc_qty),
                "broker_quantity": float(broker_qty),
                "difference": float(broker_qty - calc_qty),
                "status": "MATCH" if broker_qty == calc_qty else "DIFFERENCE",
            }
        )
    for key, calc_qty in calculated.items():
        if key not in seen:
            results.append(
                {
                    "account_name": key[0],
                    "exchange": key[1],
                    "symbol": key[2],
                    "calculated_quantity": float(calc_qty),
                    "broker_quantity": 0.0,
                    "difference": float(-calc_qty),
                    "status": "MISSING_FROM_BROKER_FILE",
                }
            )
    return {"results": results}
