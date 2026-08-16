from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from .database import Base, apply_additive_migrations, engine, get_db
from .models import (
    Account,
    AnalysisSnapshot,
    AppNotification,
    AutomationRun,
    DecisionJournal,
    DisclosureSourceMapping,
    Instrument,
    InvestorAliasReview,
    InvestorDisclosure,
    Price,
    ResearchSnapshot,
    ScreeningResult,
    Thesis,
    ThesisVersion,
    Transaction,
    TransactionType,
    UniverseMembership,
    WatchlistItem,
)
from .schemas import (
    AccountCreate,
    AliasReviewDecision,
    AccountOut,
    DecisionCreate,
    DisclosureIngestionRequest,
    DisclosureSourceMappingUpsert,
    InstrumentCreate,
    InstrumentOut,
    PriceCreate,
    ThesisUpsert,
    TransactionCreate,
    UpstoxSyncRequest,
    WatchlistCreate,
)
from .config import settings
from .automation_schedule import (
    latest_disclosure_period_due,
    latest_date_in_payload,
    latest_financial_period_due,
    latest_universe_boundary,
)
from .automation_runs import (
    automation_run_out,
    begin_run,
    complete_run,
    schedule_health,
)
from .services import portfolio_snapshot
from .providers import (
    CsvCompanyResearchProvider,
    CsvMarketDataProvider,
    ProviderConnectionError,
    ProviderDataError,
    ProviderInstrument,
    UpstoxClient,
    UpstoxCompanyResearchProvider,
    UpstoxMarketDataProvider,
    UpstoxFundamentalsProvider,
)
from .research import import_company_research, import_market_prices, store_fundamentals, store_market_prices
from .financial_analysis import build_financial_analysis
from .style_engine import backtest_style, evaluate_current_styles, get_style, load_styles
from .recommendations import recommend_prospective
from .investor_following import (
    InvestorDisclosureError,
    build_investor_signals,
    import_investor_disclosures,
    load_investor_config,
)
from .disclosure_pipeline import (
    DisclosurePipelineError,
    decide_alias_review,
    disclosure_pipeline_status,
    ensure_default_source_mappings,
    mapping_out,
    notification_out,
    review_out,
    run_disclosure_ingestion,
    upsert_source_mapping,
)
from .screening import (
    ScreeningSourceError,
    fetch_universe_constituents,
    get_screening_universe,
    load_screening_universes,
    refresh_universe_memberships,
    select_balanced_memberships,
)

app = FastAPI(
    title="Personal AI Portfolio Manager API",
    version="0.1.0",
    description="Personal portfolio ledger and transparent decision-support MVP.",
)


@app.on_event("startup")
def startup() -> None:
    Base.metadata.create_all(bind=engine)
    apply_additive_migrations()
    columns = {column["name"] for column in inspect(engine).get_columns("watchlist_items")}
    if "source" not in columns:
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE watchlist_items ADD COLUMN source VARCHAR(60) "
                "DEFAULT 'MANUAL_STRONG_BUY' NOT NULL"
            ))


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
def list_instruments(
    include_candidates: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    query = select(Instrument).order_by(Instrument.exchange, Instrument.symbol)
    if not include_candidates:
        candidate_ids = set(db.scalars(select(UniverseMembership.instrument_id).where(
            UniverseMembership.active.is_(True)
        )).all())
        owned_ids = {position["instrument_id"] for position in portfolio_snapshot(db)["positions"]}
        prospective_ids = set(db.scalars(select(WatchlistItem.instrument_id).where(
            WatchlistItem.status == "ACTIVE"
        )).all())
        hidden_ids = candidate_ids - owned_ids - prospective_ids
        if hidden_ids:
            query = query.where(Instrument.id.not_in(hidden_ids))
    return db.scalars(query).all()


@app.post("/watchlist")
def add_to_watchlist(payload: WatchlistCreate, db: Session = Depends(get_db)):
    instrument = db.get(Instrument, payload.instrument_id)
    if not instrument:
        raise HTTPException(404, "Instrument not found.")
    analysis = build_financial_analysis(db, instrument)
    styles = evaluate_current_styles(db, instrument)
    action, reasons = recommend_prospective(analysis, styles)
    if action != "STRONG_BUY":
        raise HTTPException(409, {
            "message": "Only stocks currently rated Strong Buy can enter Prospective Stocks.",
            "current_recommendation": action,
            "reasons": reasons,
        })
    item = db.scalar(select(WatchlistItem).where(WatchlistItem.instrument_id == instrument.id))
    if item:
        item.status = "ACTIVE"
        item.source = "MANUAL_STRONG_BUY"
        item.notes = payload.notes if payload.notes is not None else item.notes
    else:
        item = WatchlistItem(instrument_id=instrument.id, status="ACTIVE",
                             source="MANUAL_STRONG_BUY", notes=payload.notes)
        db.add(item)
    db.commit()
    db.refresh(item)
    return {"id": item.id, "instrument_id": item.instrument_id, "status": item.status}


@app.post("/watchlist/{instrument_id}/archive")
def archive_watchlist_item(instrument_id: int, db: Session = Depends(get_db)):
    item = db.scalar(select(WatchlistItem).where(WatchlistItem.instrument_id == instrument_id))
    if not item:
        raise HTTPException(404, "Watchlist item not found.")
    item.status = "ARCHIVED"
    db.commit()
    return {"instrument_id": instrument_id, "status": item.status}


@app.get("/prospective-stocks")
def list_prospective_stocks(db: Session = Depends(get_db)):
    owned_ids = {position["instrument_id"] for position in portfolio_snapshot(db)["positions"]}
    rows = db.execute(select(WatchlistItem, Instrument).join(
        Instrument, WatchlistItem.instrument_id == Instrument.id
    ).where(WatchlistItem.status == "ACTIVE").order_by(Instrument.company_name)).all()
    output = []
    for item, instrument in rows:
        if instrument.id in owned_ids:
            continue
        analysis = build_financial_analysis(db, instrument)
        styles = evaluate_current_styles(db, instrument)
        action, reasons = recommend_prospective(analysis, styles)
        if action != "STRONG_BUY":
            continue
        output.append({"watchlist_id": item.id, "instrument_id": instrument.id,
            "exchange": instrument.exchange, "symbol": instrument.symbol,
            "company_name": instrument.company_name, "isin": instrument.isin,
            "sector": instrument.sector, "added_at": item.added_at.isoformat(), "notes": item.notes,
            "source": item.source,
            "recommendation": action, "recommendation_reasons": reasons,
            "financial_score": analysis.get("overall_score"),
            "style_matches": sum(style.get("matches", False) for style in styles),
            "analysis_status": analysis.get("status")})
    return output


def _screening_result_out(result: ScreeningResult, instrument: Instrument,
                          membership: UniverseMembership | None = None) -> dict:
    return {
        "id": result.id,
        "universe_id": result.universe_id,
        "instrument_id": instrument.id,
        "exchange": instrument.exchange,
        "symbol": instrument.symbol,
        "company_name": instrument.company_name,
        "sector": instrument.sector,
        "cap_segment": membership.cap_segment if membership else None,
        "screened_on": result.screened_on.isoformat(),
        "recommendation": result.recommendation,
        "financial_score": float(result.financial_score) if result.financial_score is not None else None,
        "style_matches": result.style_matches,
        "analysis_status": result.analysis_status,
        "reasons": result.reasons,
        "criteria_version": result.criteria_version,
    }


@app.get("/screening-universes")
def list_screening_universe_status(db: Session = Depends(get_db)):
    owned_ids = {position["instrument_id"] for position in portfolio_snapshot(db)["positions"]}
    output = []
    for config in load_screening_universes():
        memberships = db.scalars(select(UniverseMembership).where(
            UniverseMembership.universe_id == config["id"], UniverseMembership.active.is_(True)
        )).all()
        member_ids = {item.instrument_id for item in memberships}
        candidate_ids = member_ids - owned_ids
        cycle_period = latest_financial_period_due(date.today())
        cycle_marker = f"period:{cycle_period.isoformat()}" if cycle_period else "period:none"
        screened_ids = set(db.scalars(select(ScreeningResult.instrument_id).where(
            ScreeningResult.universe_id == config["id"],
            ScreeningResult.criteria_version.contains(cycle_marker),
            ScreeningResult.instrument_id.in_(candidate_ids),
        )).all()) if candidate_ids else set()
        strong_buys = db.scalar(select(func.count()).select_from(ScreeningResult).where(
            ScreeningResult.universe_id == config["id"],
            ScreeningResult.criteria_version.contains(cycle_marker),
            ScreeningResult.recommendation == config["shortlist_recommendation"],
            ScreeningResult.instrument_id.in_(candidate_ids),
        )) if candidate_ids else 0
        segment_counts = {segment: sum(item.cap_segment == segment for item in memberships)
                          for segment in ("LARGE", "MID", "SMALL")}
        output.append({
            **config,
            "constituents": len(memberships),
            "segments": segment_counts,
            "candidate_count": len(candidate_ids),
            "screened_today": len(screened_ids),
            "pending_today": len(candidate_ids - screened_ids),
            "strong_buys_today": strong_buys or 0,
            "cycle_started_on": cycle_period,
            "screened_this_cycle": len(screened_ids),
            "pending_this_cycle": len(candidate_ids - screened_ids),
            "strong_buys_this_cycle": strong_buys or 0,
            "constituents_as_of": max((item.as_of for item in memberships), default=None),
        })
    return output


@app.post("/screening-universes/{universe_id}/refresh")
def refresh_screening_universe(universe_id: str, db: Session = Depends(get_db)):
    try:
        return _refresh_screening_universe(db, universe_id)
    except KeyError as exc:
        raise HTTPException(404, "Screening universe not found.") from exc
    except ScreeningSourceError as exc:
        db.rollback()
        raise HTTPException(502, str(exc)) from exc


def _refresh_screening_universe(db: Session, universe_id: str = "nifty500") -> dict:
    config = get_screening_universe(universe_id)
    constituents = fetch_universe_constituents(config)
    if len(constituents) != 500:
        raise ScreeningSourceError(
            f"Expected 500 unique constituents but received {len(constituents)}."
        )
    result = refresh_universe_memberships(db, config, constituents)
    return {"universe_id": universe_id, **result}


@app.post("/screening-universes/{universe_id}/screen")
def screen_next_universe_batch(
    universe_id: str,
    batch_size: int = Query(default=3, ge=1, le=50),
    db: Session = Depends(get_db),
):
    try:
        return _screen_universe_batch(db, universe_id, batch_size)
    except KeyError as exc:
        raise HTTPException(404, "Screening universe not found.") from exc
    except RuntimeError as exc:
        status = 503 if "TOKEN" in str(exc) else 409
        raise HTTPException(status, str(exc)) from exc


def _screen_universe_batch(db: Session, universe_id: str, batch_size: int) -> dict:
    if not settings.upstox_token:
        raise RuntimeError("UPSTOX_ANALYTICS_TOKEN is not configured.")
    config = get_screening_universe(universe_id)

    owned_ids = {position["instrument_id"] for position in portfolio_snapshot(db)["positions"]}
    memberships = db.scalars(select(UniverseMembership).where(
        UniverseMembership.universe_id == universe_id,
        UniverseMembership.active.is_(True),
    ).order_by(UniverseMembership.cap_segment, UniverseMembership.instrument_id)).all()
    if not memberships:
        raise RuntimeError("Refresh the universe constituents before screening.")
    cycle_period = latest_financial_period_due(date.today())
    cycle_marker = f"period:{cycle_period.isoformat()}" if cycle_period else "period:none"
    screened_ids = set(db.scalars(select(ScreeningResult.instrument_id).where(
        ScreeningResult.universe_id == universe_id,
        ScreeningResult.criteria_version.contains(cycle_marker),
    )).all())
    pending = [item for item in memberships
               if item.instrument_id not in owned_ids and item.instrument_id not in screened_ids]
    selected_memberships = select_balanced_memberships(pending, batch_size)
    if not selected_memberships:
        return {
            "universe_id": universe_id,
            "cycle_started_on": cycle_period.isoformat() if cycle_period else None,
            "processed": 0,
            "remaining": 0,
            "results": [],
        }

    styles_config = load_styles()
    criteria_version = "financial-v1+" + ",".join(
        f"{style['id']}:v{style['version']}" for style in styles_config
    ) + f"+{cycle_marker}"
    client = UpstoxClient(settings.upstox_token, settings.upstox_api_base_url)
    results = []
    try:
        for membership in selected_memberships:
            instrument = db.get(Instrument, membership.instrument_id)
            provider = UpstoxFundamentalsProvider(client)
            bundle = provider.get_bundle(instrument.isin)
            if provider.errors:
                bundle["PROVIDER_ERRORS"] = {"errors": provider.errors}
            store_fundamentals(db, instrument, bundle, "UPSTOX")
            analysis = build_financial_analysis(db, instrument)
            style_results = evaluate_current_styles(db, instrument)
            recommendation, reasons = recommend_prospective(analysis, style_results)
            record = db.scalar(select(ScreeningResult).where(
                ScreeningResult.universe_id == universe_id,
                ScreeningResult.instrument_id == instrument.id,
                ScreeningResult.screened_on == date.today(),
            ))
            values = {
                "recommendation": recommendation,
                "financial_score": analysis.get("overall_score"),
                "style_matches": sum(bool(item.get("matches")) for item in style_results),
                "analysis_status": analysis.get("status", "UNKNOWN"),
                "reasons": reasons,
                "criteria_version": criteria_version,
            }
            if record:
                for field, value in values.items():
                    setattr(record, field, value)
            else:
                record = ScreeningResult(
                    universe_id=universe_id, instrument_id=instrument.id,
                    screened_on=date.today(), **values,
                )
                db.add(record)
            db.flush()

            watchlist = db.scalar(select(WatchlistItem).where(
                WatchlistItem.instrument_id == instrument.id
            ))
            screening_source = f"SCREEN:{universe_id}"
            if recommendation == config["shortlist_recommendation"]:
                if watchlist:
                    watchlist.status = "ACTIVE"
                    watchlist.source = screening_source
                    watchlist.notes = f"Promoted by {config['name']} screen on {date.today().isoformat()}."
                else:
                    db.add(WatchlistItem(
                        instrument_id=instrument.id,
                        status="ACTIVE",
                        source=screening_source,
                        notes=f"Promoted by {config['name']} screen on {date.today().isoformat()}.",
                    ))
            elif watchlist and watchlist.source == screening_source:
                watchlist.status = "ARCHIVED"
            db.commit()
            db.refresh(record)
            results.append(_screening_result_out(record, instrument, membership))
    finally:
        client.close()
    return {
        "universe_id": universe_id,
        "cycle_started_on": cycle_period.isoformat() if cycle_period else None,
        "processed": len(results),
        "remaining": len(pending) - len(results),
        "results": results,
    }


@app.get("/screening-universes/{universe_id}/results")
def list_screening_results(
    universe_id: str,
    screened_on: date | None = Query(default=None),
    db: Session = Depends(get_db),
):
    query = (select(ScreeningResult, Instrument, UniverseMembership)
        .join(Instrument, ScreeningResult.instrument_id == Instrument.id)
        .join(UniverseMembership, (UniverseMembership.instrument_id == Instrument.id) &
              (UniverseMembership.universe_id == ScreeningResult.universe_id))
        .where(ScreeningResult.universe_id == universe_id))
    if screened_on:
        query = query.where(ScreeningResult.screened_on == screened_on)
    else:
        latest = db.scalar(select(func.max(ScreeningResult.screened_on)).where(
            ScreeningResult.universe_id == universe_id
        ))
        if latest:
            query = query.where(ScreeningResult.screened_on == latest)
    rows = db.execute(query.order_by(ScreeningResult.recommendation,
                                     Instrument.company_name)).all()
    return [_screening_result_out(result, instrument, membership)
            for result, instrument, membership in rows]


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


@app.get("/prices")
def list_prices(db: Session = Depends(get_db)):
    rows = db.execute(
        select(Price, Instrument)
        .join(Instrument, Price.instrument_id == Instrument.id)
        .order_by(Price.price_date.desc(), Instrument.exchange, Instrument.symbol)
    ).all()
    return [{
        "id": price.id,
        "instrument_id": instrument.id,
        "exchange": instrument.exchange,
        "symbol": instrument.symbol,
        "company_name": instrument.company_name,
        "price_date": price.price_date.isoformat(),
        "close_price": float(price.close_price),
        "source": price.source,
    } for price, instrument in rows]


@app.post("/theses")
def upsert_thesis(payload: ThesisUpsert, db: Session = Depends(get_db)):
    thesis = db.scalar(select(Thesis).where(Thesis.instrument_id == payload.instrument_id))
    if thesis:
        for key, value in payload.model_dump(exclude={"instrument_id"}).items():
            setattr(thesis, key, value)
    else:
        thesis = Thesis(**payload.model_dump())
        db.add(thesis)
    next_version = (db.scalar(select(func.max(ThesisVersion.version)).where(
        ThesisVersion.instrument_id == payload.instrument_id
    )) or 0) + 1
    db.add(ThesisVersion(**payload.model_dump(), version=next_version))
    db.commit()
    return {"id": thesis.id, "version": next_version}


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


@app.get("/theses/{instrument_id}/history")
def get_thesis_history(instrument_id: int, db: Session = Depends(get_db)):
    rows = db.scalars(select(ThesisVersion).where(
        ThesisVersion.instrument_id == instrument_id
    ).order_by(ThesisVersion.version.desc())).all()
    return [{
        "version": row.version,
        "status": row.status.value,
        "reason": row.reason,
        "catalysts": row.catalysts,
        "risks": row.risks,
        "invalidation_conditions": row.invalidation_conditions,
        "target_horizon_months": row.target_horizon_months,
        "created_at": row.created_at.isoformat(),
    } for row in rows]


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


@app.post("/imports/investor-disclosures")
def import_followed_investor_disclosures(
    file: UploadFile = File(...), db: Session = Depends(get_db)
):
    try:
        return import_investor_disclosures(db, file.file.read())
    except InvestorDisclosureError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc


@app.get("/followed-investors")
def followed_investor_profiles(db: Session = Depends(get_db)):
    config = load_investor_config()
    counts = dict(db.execute(select(
        InvestorDisclosure.investor_id, func.count(InvestorDisclosure.id)
    ).group_by(InvestorDisclosure.investor_id)).all())
    return {
        "config_version": config["version"],
        "config_file": config["file"],
        "methodology": config["methodology"],
        "profiles": [{**profile, "disclosures": counts.get(profile["id"], 0)}
                     for profile in config["investors"]],
    }


@app.get("/investor-signals")
def investor_signals(db: Session = Depends(get_db)):
    result = build_investor_signals(db)
    owned_ids = {position["instrument_id"] for position in portfolio_snapshot(db)["positions"]}
    prospective_ids = set(db.scalars(select(WatchlistItem.instrument_id).where(
        WatchlistItem.status == "ACTIVE"
    )).all())
    for row in result["rows"]:
        row["universe"] = ("OWNED" if row["instrument_id"] in owned_ids else
                           "PROSPECTIVE" if row["instrument_id"] in prospective_ids else "RESEARCH")
    for item in result["activity"]:
        item["universe"] = ("OWNED" if item["instrument_id"] in owned_ids else
                            "PROSPECTIVE" if item["instrument_id"] in prospective_ids else "RESEARCH")
    return result


@app.get("/investor-disclosures/status")
def investor_disclosure_status(db: Session = Depends(get_db)):
    ensure_default_source_mappings(db)
    return disclosure_pipeline_status(db)


@app.get("/investor-disclosures/mappings")
def investor_disclosure_mappings(db: Session = Depends(get_db)):
    ensure_default_source_mappings(db)
    mappings = db.scalars(select(DisclosureSourceMapping).order_by(
        DisclosureSourceMapping.last_status.desc(), DisclosureSourceMapping.id
    )).all()
    return [mapping_out(db, item) for item in mappings]


@app.post("/investor-disclosures/mappings")
def save_investor_disclosure_mapping(
    payload: DisclosureSourceMappingUpsert, db: Session = Depends(get_db)
):
    try:
        item = upsert_source_mapping(db, **payload.model_dump())
    except DisclosurePipelineError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    return mapping_out(db, item)


@app.post("/investor-disclosures/ingest")
def ingest_exchange_disclosures(
    payload: DisclosureIngestionRequest, db: Session = Depends(get_db)
):
    period = payload.period or latest_disclosure_period_due(
        date.today(), settings.investor_disclosure_lag_days
    )
    if not period:
        raise HTTPException(422, "No quarterly disclosure period is due yet.")
    try:
        return run_disclosure_ingestion(
            db, period, force=payload.force, mapping_ids=payload.mapping_ids
        )
    except DisclosurePipelineError as exc:
        db.rollback()
        raise HTTPException(502, str(exc)) from exc


@app.get("/investor-disclosures/reviews")
def investor_alias_reviews(
    status: str = Query(default="PENDING"), db: Session = Depends(get_db)
):
    query = select(InvestorAliasReview)
    if status.upper() != "ALL":
        query = query.where(InvestorAliasReview.status == status.upper())
    reviews = db.scalars(query.order_by(InvestorAliasReview.created_at.desc())).all()
    return [review_out(db, item) for item in reviews]


@app.post("/investor-disclosures/reviews/{review_id}/decision")
def review_investor_alias(
    review_id: int, payload: AliasReviewDecision, db: Session = Depends(get_db)
):
    try:
        item = decide_alias_review(db, review_id, **payload.model_dump())
    except DisclosurePipelineError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    return review_out(db, item)


@app.get("/notifications")
def list_notifications(
    unread_only: bool = Query(default=False), limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    query = select(AppNotification)
    if unread_only:
        query = query.where(AppNotification.read_at.is_(None))
    items = db.scalars(query.order_by(AppNotification.created_at.desc()).limit(limit)).all()
    return [notification_out(item) for item in items]


@app.post("/notifications/{notification_id}/read")
def mark_notification_read(notification_id: int, db: Session = Depends(get_db)):
    item = db.get(AppNotification, notification_id)
    if not item:
        raise HTTPException(404, "Notification not found.")
    item.read_at = datetime.utcnow()
    db.commit()
    db.refresh(item)
    return notification_out(item)


def _build_stock_workbench(db: Session):
    """Return fixed, stock-level rows for every dashboard evidence tab."""
    snapshot = portfolio_snapshot(db)
    severity = {
        "STRONG_SELL": 6, "SELL": 5, "TRIM": 4, "REVIEW": 3,
        "HOLD": 2, "BUY_MORE": 1,
    }
    owned = {}
    for position in snapshot["positions"]:
        item = owned.setdefault(position["instrument_id"], {
            "accounts": [], "quantity": 0.0, "remaining_cost": 0.0,
            "market_value": 0.0, "unrealised_profit": 0.0,
            "realised_profit": 0.0, "dividend_income": 0.0, "weight": 0.0,
            "current_price": position["current_price"], "price_date": position["price_date"],
            "holding_days": position["holding_days"], "thesis_status": position["thesis_status"],
            "recommendation": position["recommendation"], "recommendation_reasons": [],
        })
        item["accounts"].append(position["account_name"])
        for field in ("quantity", "remaining_cost", "market_value", "unrealised_profit",
                      "realised_profit", "dividend_income", "weight"):
            item[field] += position[field] or 0
        item["holding_days"] = max(item["holding_days"], position["holding_days"])
        if severity.get(position["recommendation"], 0) > severity.get(item["recommendation"], 0):
            item["recommendation"] = position["recommendation"]
            item["recommendation_reasons"] = []
        if position["recommendation"] == item["recommendation"]:
            for reason in position["recommendation_reasons"]:
                if reason not in item["recommendation_reasons"]:
                    item["recommendation_reasons"].append(reason)
    for item in owned.values():
        item["accounts"] = sorted(set(item["accounts"]))
        item["average_cost"] = (
            item["remaining_cost"] / item["quantity"] if item["quantity"] else None
        )
        item["return_pct"] = (
            item["unrealised_profit"] / item["remaining_cost"]
            if item["remaining_cost"] and item["current_price"] is not None else None
        )

    prospective = {item["instrument_id"]: item for item in list_prospective_stocks(db)}
    investor_data = build_investor_signals(db)
    investor_activity = {}
    for activity in investor_data["activity"]:
        investor_activity.setdefault(activity["instrument_id"], []).append(activity)
    style_definitions = load_styles()

    def build_row(instrument_id: int, scope: str, scope_data: dict) -> dict:
        instrument = db.get(Instrument, instrument_id)
        research_rows = db.scalars(select(ResearchSnapshot).where(
            ResearchSnapshot.instrument_id == instrument_id
        ).order_by(ResearchSnapshot.research_type)).all()
        analysis = build_financial_analysis(db, instrument)
        evaluations = evaluate_current_styles(db, instrument)
        thesis = db.scalar(select(Thesis).where(Thesis.instrument_id == instrument_id))
        latest_price = db.scalar(select(Price).where(
            Price.instrument_id == instrument_id
        ).order_by(Price.price_date.desc(), Price.id.desc()))
        recommendation = scope_data["recommendation"]
        reasons = scope_data["recommendation_reasons"]
        matched_styles = [item["style_name"] for item in evaluations
                          if item.get("applicable") and item.get("matches")]
        summary_parts = [reasons[0] if reasons else "No primary rationale is available."]
        if analysis.get("overall_score") is not None:
            summary_parts.append(f"Financial score {analysis['overall_score']}/100.")
        summary_parts.append(
            "Style matches: " + (", ".join(matched_styles) if matched_styles else "none") + "."
        )
        disclosures = investor_activity.get(instrument_id, [])
        if disclosures:
            summary_parts.append(f"{len(disclosures)} followed-investor signal(s) available.")
        return {
            "instrument_id": instrument.id,
            "exchange": instrument.exchange,
            "symbol": instrument.symbol,
            "company_name": instrument.company_name,
            "sector": instrument.sector,
            "scope": scope,
            "portfolio": {
                "accounts": scope_data.get("accounts", []),
                "quantity": scope_data.get("quantity"),
                "average_cost": scope_data.get("average_cost"),
                "current_price": scope_data.get("current_price", float(latest_price.close_price)
                                                   if latest_price else None),
                "price_date": scope_data.get("price_date", latest_price.price_date.isoformat()
                                                if latest_price else None),
                "market_value": scope_data.get("market_value"),
                "unrealised_profit": scope_data.get("unrealised_profit"),
                "return_pct": scope_data.get("return_pct"),
                "weight": scope_data.get("weight"),
                "holding_days": scope_data.get("holding_days"),
                "thesis_status": thesis.status.value if thesis else None,
                "thesis_reason": thesis.reason if thesis else None,
                "shortlisted_on": (scope_data.get("added_at") or "")[:10] or None,
            },
            "data": {
                "datasets": [row.research_type for row in research_rows],
                "dataset_count": len(research_rows),
                "latest_evidence": max((row.as_of for row in research_rows), default=None),
                "providers": sorted(set(row.provider for row in research_rows)),
                "provider_errors": analysis.get("provider_errors", []),
            },
            "financials": {
                "status": analysis["status"],
                "as_of": analysis.get("as_of"),
                "overall_score": analysis.get("overall_score"),
                "scores": analysis.get("scores", {}),
                "governance_flags": analysis.get("governance_flags", []),
                "missing": analysis.get("missing", []),
            },
            "styles": evaluations,
            "investors": disclosures,
            "summary": {
                "recommendation": recommendation,
                "brief_reason": " ".join(summary_parts),
                "reasons": reasons,
                "style_matches": matched_styles,
            },
        }

    owned_rows = [build_row(instrument_id, "OWNED", data)
                  for instrument_id, data in owned.items()]
    prospective_rows = [build_row(instrument_id, "PROSPECTIVE", data)
                        for instrument_id, data in prospective.items()]
    for rows in (owned_rows, prospective_rows):
        rows.sort(key=lambda item: (item["company_name"], item["symbol"]))
    return {
        "as_of": snapshot["as_of"],
        "style_definitions": [{"id": item["id"], "name": item["name"],
                               "version": item["version"]} for item in style_definitions],
        "investor_profiles": [{"id": item["id"], "name": item["name"]}
                              for item in investor_data["profiles"]],
        "owned": owned_rows,
        "prospective": prospective_rows,
    }


def _store_workbench_snapshot(db: Session, payload: dict) -> AnalysisSnapshot:
    snapshot = db.scalar(select(AnalysisSnapshot).where(
        AnalysisSnapshot.snapshot_key == "stock_workbench"
    ))
    now = datetime.utcnow()
    if not snapshot:
        snapshot = AnalysisSnapshot(snapshot_key="stock_workbench")
        db.add(snapshot)
    snapshot.generated_at = now
    snapshot.payload = jsonable_encoder(payload)
    snapshot.last_attempted_at = now
    snapshot.last_status = "SUCCESS"
    snapshot.last_error = None
    db.commit()
    db.refresh(snapshot)
    return snapshot


def _record_workbench_failure(db: Session, error: str) -> None:
    snapshot = db.scalar(select(AnalysisSnapshot).where(
        AnalysisSnapshot.snapshot_key == "stock_workbench"
    ))
    if not snapshot:
        snapshot = AnalysisSnapshot(snapshot_key="stock_workbench")
        db.add(snapshot)
    snapshot.last_attempted_at = datetime.utcnow()
    snapshot.last_status = "FAILED"
    snapshot.last_error = error[:2000]
    db.commit()


def _record_automation_status(
    db: Session, job_id: str, payload: dict, status: str = "SUCCESS", error: str | None = None
) -> AnalysisSnapshot:
    snapshot = db.scalar(select(AnalysisSnapshot).where(
        AnalysisSnapshot.snapshot_key == f"automation:{job_id}"
    ))
    now = datetime.utcnow()
    if not snapshot:
        snapshot = AnalysisSnapshot(snapshot_key=f"automation:{job_id}")
        db.add(snapshot)
    snapshot.generated_at = now if status == "SUCCESS" else snapshot.generated_at
    snapshot.payload = jsonable_encoder(payload)
    snapshot.last_attempted_at = now
    snapshot.last_status = status
    snapshot.last_error = error[:2000] if error else None
    db.commit()
    db.refresh(snapshot)
    return snapshot


def _refresh_universe_if_due(db: Session, force: bool = False) -> dict:
    boundary = latest_universe_boundary(date.today(), settings.universe_schedule_months)
    latest_as_of = db.scalar(select(func.max(UniverseMembership.as_of)).where(
        UniverseMembership.universe_id == "nifty500",
        UniverseMembership.active.is_(True),
    ))
    if not force and latest_as_of and latest_as_of >= boundary:
        return {
            "status": "CURRENT", "boundary": boundary.isoformat(),
            "constituents_as_of": latest_as_of.isoformat(), "refreshed": False,
        }
    result = _refresh_screening_universe(db, "nifty500")
    return {"status": "REFRESHED", "boundary": boundary.isoformat(), "refreshed": True, **result}


def _priority_financial_instruments(db: Session) -> list[Instrument]:
    owned_ids = {position["instrument_id"] for position in portfolio_snapshot(db)["positions"]}
    prospective_ids = set(db.scalars(select(WatchlistItem.instrument_id).where(
        WatchlistItem.status == "ACTIVE"
    )).all())
    ids = owned_ids | prospective_ids
    if not ids:
        return []
    return db.scalars(select(Instrument).where(
        Instrument.id.in_(ids),
        Instrument.exchange.in_(("NSE", "BSE")),
        Instrument.isin.is_not(None),
    ).order_by(Instrument.symbol)).all()


def _refresh_due_financial_statements(db: Session, batch_size: int | None = None) -> dict:
    period = latest_financial_period_due(date.today())
    if not period:
        return {"status": "NOT_DUE", "processed": 0, "updated": 0, "remaining": 0}
    if not settings.upstox_token:
        return {
            "status": "SKIPPED", "period": period.isoformat(), "processed": 0,
            "updated": 0, "remaining": 0,
            "errors": ["UPSTOX_ANALYTICS_TOKEN is not configured."],
        }
    required_types = {
        "INCOME_ANNUAL", "INCOME_QUARTERLY", "BALANCE_SHEET", "CASH_FLOW", "KEY_RATIOS"
    }
    instruments = _priority_financial_instruments(db)
    marker_keys = {item.id: f"automation:fund:{item.id}:{period:%Y%m%d}" for item in instruments}
    markers = {item.snapshot_key: item for item in db.scalars(select(AnalysisSnapshot).where(
        AnalysisSnapshot.snapshot_key.in_(marker_keys.values())
    )).all()} if marker_keys else {}
    completed = {key for key, item in markers.items() if item.last_status == "SUCCESS"}
    stored_by_instrument: dict[int, dict] = {}
    if instruments:
        for evidence in db.scalars(select(ResearchSnapshot).where(
            ResearchSnapshot.instrument_id.in_([item.id for item in instruments]),
            ResearchSnapshot.provider == "UPSTOX",
            ResearchSnapshot.research_type.in_(list(required_types)),
        )).all():
            stored_by_instrument.setdefault(evidence.instrument_id, {})[
                evidence.research_type
            ] = evidence.payload
    for instrument in instruments:
        marker_key = marker_keys[instrument.id]
        if marker_key in completed:
            continue
        evidence_period = latest_date_in_payload(stored_by_instrument.get(instrument.id, {}))
        if evidence_period and evidence_period >= period:
            _record_automation_status(db, marker_key.removeprefix("automation:"), {
                "instrument_id": instrument.id, "symbol": instrument.symbol,
                "period": period.isoformat(), "changed": False,
                "latest_evidence_period": evidence_period.isoformat(),
                "message": "Stored Upstox evidence already covers the due reporting period.",
            })
            completed.add(marker_key)
    due = [item for item in instruments if marker_keys[item.id] not in completed]
    due.sort(key=lambda item: (
        marker_keys[item.id] in markers,
        markers[marker_keys[item.id]].last_attempted_at
        if marker_keys[item.id] in markers else datetime.min,
        item.symbol,
    ))
    selected = due[:batch_size or settings.fundamentals_batch_size]
    processed, updated, succeeded, errors = 0, 0, 0, []
    client = UpstoxClient(settings.upstox_token, settings.upstox_api_base_url)
    try:
        for instrument in selected:
            provider = UpstoxFundamentalsProvider(client)
            bundle = provider.get_bundle(instrument.isin)
            present_required = required_types & set(bundle)
            existing = {item.research_type: item.payload for item in db.scalars(
                select(ResearchSnapshot).where(
                    ResearchSnapshot.instrument_id == instrument.id,
                    ResearchSnapshot.provider == "UPSTOX",
                    ResearchSnapshot.research_type.in_(list(bundle)),
                )
            ).all()} if bundle else {}
            changed = [kind for kind, value in bundle.items() if existing.get(kind) != value]
            changed_financials = sorted(required_types & set(changed))
            evidence_period = latest_date_in_payload({
                kind: value for kind, value in bundle.items() if kind in required_types
            })
            if changed:
                store_fundamentals(db, instrument, bundle, "UPSTOX")
                updated += 1
            missing = sorted(required_types - present_required)
            marker_payload = {
                "instrument_id": instrument.id,
                "symbol": instrument.symbol,
                "period": period.isoformat(),
                "changed": bool(changed_financials),
                "changed_evidence": changed,
                "latest_evidence_period": evidence_period.isoformat() if evidence_period else None,
                "provider_errors": provider.errors,
                "missing_required": missing,
            }
            if missing:
                message = f"{instrument.symbol}: missing {', '.join(missing)}"
                errors.append(message)
                _record_automation_status(
                    db, marker_keys[instrument.id].removeprefix("automation:"), marker_payload,
                    status="FAILED", error=message,
                )
            elif evidence_period and evidence_period >= period:
                _record_automation_status(
                    db, marker_keys[instrument.id].removeprefix("automation:"), marker_payload
                )
                succeeded += 1
            else:
                marker_payload["message"] = (
                    "Upstox has not returned newer core financial evidence yet; this company "
                    "will be checked again after the other due companies."
                )
                _record_automation_status(
                    db, marker_keys[instrument.id].removeprefix("automation:"), marker_payload,
                    status="PENDING",
                )
            processed += 1
    finally:
        client.close()
    remaining = len(due) - succeeded
    return {
        "status": "PARTIAL" if errors else ("COMPLETE" if remaining == 0 else "IN_PROGRESS"),
        "period": period.isoformat(), "processed": processed, "updated": updated,
        "unchanged": processed - updated, "remaining": remaining, "errors": errors,
    }


def _check_investor_disclosures(db: Session) -> dict:
    period = latest_disclosure_period_due(date.today(), settings.investor_disclosure_lag_days)
    if not period:
        return {"status": "NOT_DUE", "expected_period": None, "missing_investors": []}
    ingestion = (
        run_disclosure_ingestion(db, period)
        if settings.disclosure_auto_ingest_enabled
        else {"status": "SKIPPED", "errors": ["Automated ingestion is disabled."]}
    )
    profiles = load_investor_config()["investors"]
    latest_by_investor = {
        investor_id: latest for investor_id, latest in db.execute(
            select(InvestorDisclosure.investor_id, func.max(InvestorDisclosure.report_date))
            .group_by(InvestorDisclosure.investor_id)
        ).all()
    }
    missing = [profile["id"] for profile in profiles
               if latest_by_investor.get(profile["id"], date.min) < period]
    return {
        "status": "FAILED" if ingestion.get("status") == "FAILED" else "SUCCESS",
        "coverage_status": "CURRENT" if not missing else "ACTION_REQUIRED",
        "expected_period": period.isoformat(),
        "filing_lag_days": settings.investor_disclosure_lag_days,
        "latest_by_investor": {
            profile["id"]: (
                latest_by_investor[profile["id"]].isoformat()
                if profile["id"] in latest_by_investor else None
            ) for profile in profiles
        },
        "missing_investors": missing,
        "ingestion": ingestion,
        "message": (
            "Automated exchange discovery completed, but one or more configured investor "
            "profiles still have no attributable filing for the expected period. Review source "
            "mappings, parser failures, and pending aliases; CSV remains the recovery path."
        ) if missing else "Stored disclosures cover the expected reporting period.",
    }


def _refresh_analysis_market_data(db: Session) -> dict:
    target_date = date.today() - timedelta(days=1)
    if not settings.upstox_token:
        return {
            "status": "SKIPPED", "target_date": target_date.isoformat(),
            "prices_imported": 0, "errors": ["UPSTOX_ANALYTICS_TOKEN is not configured."],
        }
    instruments, skipped = _upstox_instruments(db)
    if not instruments:
        return {
            "status": "SKIPPED", "target_date": target_date.isoformat(),
            "prices_imported": 0, "errors": ["No eligible owned or prospective instruments."],
        }
    client = UpstoxClient(settings.upstox_token, settings.upstox_api_base_url)
    try:
        provider = UpstoxMarketDataProvider(instruments, client)
        imported = import_market_prices(db, provider, as_of=target_date)
        errors = [*provider.errors, *[f"Skipped {item}" for item in skipped]]
    finally:
        client.close()
    return {
        "status": "SUCCESS" if not errors else "PARTIAL",
        "target_date": target_date.isoformat(),
        "prices_imported": imported,
        "eligible_instruments": len(instruments),
        "errors": errors,
    }


def _run_analysis_pipeline(db: Session, refresh_market_data: bool = True) -> tuple[dict, AnalysisSnapshot]:
    market_refresh = (_refresh_analysis_market_data(db) if refresh_market_data else {
        "status": "SKIPPED", "target_date": None, "prices_imported": 0,
        "errors": ["Market refresh was disabled for this run."],
    })
    if refresh_market_data:
        _record_automation_status(db, "market_prices", market_refresh)
    payload = _build_stock_workbench(db)
    payload["data_refresh"] = {"market_prices": market_refresh}
    snapshot = _store_workbench_snapshot(db, payload)
    return payload, snapshot


def _run_automation_action(db: Session, job_id: str, action) -> dict:
    try:
        result = action()
        result.setdefault("status", "SUCCESS")
        _record_automation_status(db, job_id, result)
        return result
    except Exception as exc:
        db.rollback()
        result = {"status": "FAILED", "error": str(exc)}
        _record_automation_status(db, job_id, result, status="FAILED", error=str(exc))
        return result


def _latest_automation_results(db: Session) -> dict:
    job_ids = (
        "market_prices", "nifty500_constituents", "financial_statements",
        "nifty500_screening", "investor_disclosures",
    )
    rows = db.scalars(select(AnalysisSnapshot).where(
        AnalysisSnapshot.snapshot_key.in_([f"automation:{job_id}" for job_id in job_ids])
    )).all()
    return {row.snapshot_key.removeprefix("automation:"): row.payload for row in rows if row.payload}


def _run_morning_automation(
    db: Session, only_actions: set[str] | None = None
) -> tuple[dict, AnalysisSnapshot]:
    """Run all due activities, or only failed activities during a bounded retry."""
    action_functions = {
        "market_prices": lambda: _refresh_analysis_market_data(db),
        "nifty500_constituents": lambda: _refresh_universe_if_due(db),
        "financial_statements": lambda: _refresh_due_financial_statements(db),
        "nifty500_screening": lambda: _screen_universe_batch(
            db, "nifty500", settings.screening_batch_size
        ),
        "investor_disclosures": lambda: _check_investor_disclosures(db),
    }
    executed = {}
    for job_id, action in action_functions.items():
        if only_actions is None or job_id in only_actions:
            executed[job_id] = _run_automation_action(db, job_id, action)
    actions = _latest_automation_results(db)
    actions.update(executed)
    payload = _build_stock_workbench(db)
    payload["data_refresh"] = actions
    snapshot = _store_workbench_snapshot(db, payload)
    _record_automation_status(db, "morning_orchestrator", {
        "status": "PARTIAL" if any(
            item.get("status") == "FAILED" for item in executed.values()
        ) else "SUCCESS",
        "actions": {name: item.get("status", "UNKNOWN") for name, item in executed.items()},
        "retry_scope": sorted(only_actions) if only_actions else None,
        "snapshot_generated_at": snapshot.generated_at.isoformat(),
    })
    return payload, snapshot


def _automation_job_definitions() -> list[dict]:
    schedule = (
        f"{settings.analysis_schedule_days} at {settings.analysis_schedule_hour:02d}:"
        f"{settings.analysis_schedule_minute:02d} {settings.analysis_schedule_timezone}"
    )
    return [
        {"id": "market_prices", "name": "Previous-close market prices", "frequency": schedule,
         "policy": "Latest Upstox close on or before the preceding calendar day."},
        {"id": "nifty500_constituents", "name": "NIFTY 500 membership", "frequency": schedule,
         "policy": f"Refresh on the first scheduled run after months {settings.universe_schedule_months}."},
        {"id": "financial_statements", "name": "Owned/prospective financial statements",
         "frequency": schedule,
         "policy": (
             f"After SEBI result windows, {settings.fundamentals_batch_size} unchecked companies per run; "
             "unchanged provider evidence is not rewritten."
         )},
        {"id": "nifty500_screening", "name": "NIFTY 500 rolling screen", "frequency": schedule,
         "policy": (
             f"{settings.screening_batch_size} companies per run, balanced by cap segment, "
             "once per newly due financial-reporting period."
         )},
        {"id": "investor_disclosures", "name": "Followed-investor disclosure check",
         "frequency": schedule,
         "policy": (
             f"Discover and parse exchange filings after a {settings.investor_disclosure_lag_days}-day "
             f"lag in restart-safe batches of {settings.disclosure_batch_size}; CSV is the recovery path."
         )},
        {"id": "morning_orchestrator", "name": "Dashboard snapshot", "frequency": schedule,
         "policy": "Rebuild after all due actions finish; retain the last successful snapshot on failure."},
    ]


def _snapshot_metadata(snapshot: AnalysisSnapshot, mode: str) -> dict:
    return {
        "mode": mode,
        "generated_at": snapshot.generated_at.isoformat() if snapshot.generated_at else None,
        "last_attempted_at": snapshot.last_attempted_at.isoformat(),
        "last_status": snapshot.last_status,
        "last_error": snapshot.last_error,
        "schedule": {
            "days": settings.analysis_schedule_days,
            "hour": settings.analysis_schedule_hour,
            "minute": settings.analysis_schedule_minute,
            "timezone": settings.analysis_schedule_timezone,
        },
    }


@app.get("/stock-workbench")
def stock_workbench(db: Session = Depends(get_db)):
    snapshot = db.scalar(select(AnalysisSnapshot).where(
        AnalysisSnapshot.snapshot_key == "stock_workbench"
    ))
    if snapshot and snapshot.payload:
        return {**snapshot.payload, "snapshot": _snapshot_metadata(snapshot, "CACHED")}

    # Cold-start recovery only. The scheduler normally creates this before a user opens the UI.
    payload = _build_stock_workbench(db)
    snapshot = _store_workbench_snapshot(db, payload)
    return {**payload, "snapshot": _snapshot_metadata(snapshot, "COLD_START")}


@app.get("/analysis-schedule")
def analysis_schedule_status(db: Session = Depends(get_db)):
    snapshot = db.scalar(select(AnalysisSnapshot).where(
        AnalysisSnapshot.snapshot_key == "stock_workbench"
    ))
    definitions = _automation_job_definitions()
    job_snapshots = {
        item.snapshot_key.removeprefix("automation:"): item
        for item in db.scalars(select(AnalysisSnapshot).where(
            AnalysisSnapshot.snapshot_key.in_([
                f"automation:{definition['id']}" for definition in definitions
            ])
        )).all()
    }
    return {
        "enabled": True,
        "run_on_startup": settings.analysis_run_on_startup,
        "health": schedule_health(db),
        "recovery_policy": {
            "start_grace_minutes": settings.analysis_start_grace_minutes,
            "stall_minutes": settings.analysis_stall_minutes,
            "catchup_max_hours": settings.analysis_catchup_max_hours,
            "retry_delays_minutes": settings.analysis_retry_delays_minutes,
        },
        "schedule": {
            "days": settings.analysis_schedule_days,
            "hour": settings.analysis_schedule_hour,
            "minute": settings.analysis_schedule_minute,
            "timezone": settings.analysis_schedule_timezone,
        },
        "jobs": [{
            **definition,
            "last_attempted_at": (
                job_snapshots[definition["id"]].last_attempted_at.isoformat()
                if definition["id"] in job_snapshots else None
            ),
            "last_status": (
                job_snapshots[definition["id"]].last_status
                if definition["id"] in job_snapshots else "NOT_RUN"
            ),
            "last_error": (
                job_snapshots[definition["id"]].last_error
                if definition["id"] in job_snapshots else None
            ),
            "last_result": (
                job_snapshots[definition["id"]].payload
                if definition["id"] in job_snapshots else None
            ),
        } for definition in definitions],
        "snapshot": _snapshot_metadata(snapshot, "CACHED") if snapshot else None,
    }


@app.get("/analysis-schedule/runs")
def analysis_run_history(
    limit: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)
):
    rows = db.scalars(select(AutomationRun).order_by(
        AutomationRun.started_at.desc(), AutomationRun.id.desc()
    ).limit(limit)).all()
    return [automation_run_out(item) for item in rows]


@app.post("/analysis-schedule/run")
def force_analysis_run(
    job: str = Query(default="morning"), db: Session = Depends(get_db)
):
    actions = {
        "prices": ("market_prices", lambda: _refresh_analysis_market_data(db)),
        "fundamentals": (
            "financial_statements", lambda: _refresh_due_financial_statements(db)
        ),
        "screening": (
            "nifty500_screening",
            lambda: _screen_universe_batch(db, "nifty500", settings.screening_batch_size),
        ),
        "constituents": (
            "nifty500_constituents", lambda: _refresh_universe_if_due(db, force=True)
        ),
        "disclosures": ("investor_disclosures", lambda: _check_investor_disclosures(db)),
    }
    if job not in {"morning", "snapshot", *actions}:
        raise HTTPException(
            422,
            "job must be morning, prices, fundamentals, screening, constituents, "
            "disclosures, or snapshot.",
        )
    run, _ = begin_run(db, datetime.utcnow(), "FORCED", 1)
    current_action_ids: set[str] = set()
    try:
        if job == "morning":
            payload, snapshot = _run_morning_automation(db)
            current_action_ids = {
                "market_prices", "nifty500_constituents", "financial_statements",
                "nifty500_screening", "investor_disclosures",
            }
        elif job == "snapshot":
            payload = _build_stock_workbench(db)
            payload["data_refresh"] = _latest_automation_results(db)
            snapshot = _store_workbench_snapshot(db, payload)
        elif job in actions:
            job_id, action = actions[job]
            current_action_ids = {job_id}
            result = _run_automation_action(db, job_id, action)
            payload = _build_stock_workbench(db)
            payload["data_refresh"] = _latest_automation_results(db)
            payload["data_refresh"][job_id] = result
            snapshot = _store_workbench_snapshot(db, payload)
    except Exception as exc:
        db.rollback()
        complete_run(db, run, "FAILED", {}, str(exc))
        _record_workbench_failure(db, str(exc))
        raise HTTPException(500, f"Forced analysis failed: {exc}") from exc
    current_status = {
        name: payload.get("data_refresh", {}).get(name, {}).get("status", "UNKNOWN")
        for name in current_action_ids
    }
    failed_actions = [name for name, status in current_status.items() if status == "FAILED"]
    run_status = "PARTIAL" if failed_actions else "SUCCESS"
    complete_run(db, run, run_status, current_status)
    return {
        "status": run_status,
        "job": job,
        "owned": len(payload["owned"]),
        "prospective": len(payload["prospective"]),
        "data_refresh": payload["data_refresh"],
        "failed_actions": failed_actions,
        "run": automation_run_out(run),
        "snapshot": _snapshot_metadata(snapshot, "FORCED"),
    }


def _upstox_instruments(db: Session) -> tuple[list[ProviderInstrument], list[str]]:
    candidate_ids = set(db.scalars(select(UniverseMembership.instrument_id).where(
        UniverseMembership.active.is_(True)
    )).all())
    owned_ids = {position["instrument_id"] for position in portfolio_snapshot(db)["positions"]}
    prospective_ids = set(db.scalars(select(WatchlistItem.instrument_id).where(
        WatchlistItem.status == "ACTIVE"
    )).all())
    hidden_ids = candidate_ids - owned_ids - prospective_ids
    query = select(Instrument).order_by(Instrument.exchange, Instrument.symbol)
    if hidden_ids:
        query = query.where(Instrument.id.not_in(hidden_ids))
    instruments = db.scalars(query).all()
    supported, skipped = [], []
    for instrument in instruments:
        if instrument.exchange.upper() not in {"NSE", "BSE"} or not instrument.isin:
            skipped.append(f"{instrument.exchange}:{instrument.symbol}")
            continue
        supported.append(ProviderInstrument(
            exchange=instrument.exchange.upper(), symbol=instrument.symbol.upper(), isin=instrument.isin
        ))
    return supported, skipped


@app.get("/providers/upstox")
def upstox_status(db: Session = Depends(get_db)):
    supported, skipped = _upstox_instruments(db)
    return {
        "configured": bool(settings.upstox_token),
        "mode": "analytics_read_only",
        "eligible_instruments": len(supported),
        "skipped_instruments": skipped,
    }


@app.get("/providers/upstox/search")
def search_upstox_instruments(q: str = Query(min_length=2, max_length=50)):
    if not settings.upstox_token:
        raise HTTPException(503, "UPSTOX_ANALYTICS_TOKEN is not configured.")
    client = UpstoxClient(settings.upstox_token, settings.upstox_api_base_url)
    try:
        return UpstoxFundamentalsProvider(client).search_instruments(q)
    except ProviderConnectionError as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        client.close()


@app.post("/providers/upstox/sync")
def sync_upstox(payload: UpstoxSyncRequest, db: Session = Depends(get_db)):
    if not settings.upstox_token:
        raise HTTPException(503, "UPSTOX_ANALYTICS_TOKEN is not configured.")
    instruments, skipped = _upstox_instruments(db)
    if not instruments:
        raise HTTPException(400, "No NSE/BSE instruments with an ISIN are available to sync.")

    client = UpstoxClient(settings.upstox_token, settings.upstox_api_base_url)
    prices_imported = profiles_imported = 0
    provider_errors: list[str] = []
    try:
        if payload.include_prices:
            market_provider = UpstoxMarketDataProvider(instruments, client)
            prices_imported = import_market_prices(
                db, market_provider, as_of=payload.as_of
            )
            provider_errors.extend(market_provider.errors)
        if payload.include_company_profiles:
            company_provider = UpstoxCompanyResearchProvider(instruments, client)
            profiles_imported = import_company_research(
                db, company_provider
            )
            provider_errors.extend(company_provider.errors)
    except ProviderDataError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    except ProviderConnectionError as exc:
        db.rollback()
        raise HTTPException(502, str(exc)) from exc
    finally:
        client.close()
    return {
        "provider": "UPSTOX",
        "as_of": payload.as_of.isoformat(),
        "prices_imported": prices_imported,
        "profiles_imported": profiles_imported,
        "skipped_instruments": skipped,
        "provider_errors": provider_errors,
    }


@app.post("/providers/upstox/fundamentals/{instrument_id}/sync")
def sync_upstox_fundamentals(instrument_id: int, db: Session = Depends(get_db)):
    instrument = db.get(Instrument, instrument_id)
    if not instrument:
        raise HTTPException(404, "Instrument not found.")
    if not instrument.isin:
        raise HTTPException(400, "An ISIN is required for fundamental analysis.")
    if not settings.upstox_token:
        raise HTTPException(503, "UPSTOX_ANALYTICS_TOKEN is not configured.")
    client = UpstoxClient(settings.upstox_token, settings.upstox_api_base_url)
    try:
        provider = UpstoxFundamentalsProvider(client)
        bundle = provider.get_bundle(instrument.isin)
        if provider.errors:
            bundle["PROVIDER_ERRORS"] = {"errors": provider.errors}
        imported = store_fundamentals(db, instrument, bundle, "UPSTOX")
    except ProviderConnectionError as exc:
        db.rollback()
        raise HTTPException(502, str(exc)) from exc
    finally:
        client.close()
    return {"provider": "UPSTOX", "instrument_id": instrument.id,
            "datasets_imported": imported, "provider_errors": provider.errors}


@app.get("/analysis/{instrument_id}")
def get_financial_analysis(instrument_id: int, db: Session = Depends(get_db)):
    instrument = db.get(Instrument, instrument_id)
    if not instrument:
        raise HTTPException(404, "Instrument not found.")
    return build_financial_analysis(db, instrument)


@app.get("/investor-styles")
def list_investor_styles():
    return load_styles()


@app.get("/investor-styles/evaluate/{instrument_id}")
def evaluate_investor_styles(instrument_id: int, db: Session = Depends(get_db)):
    instrument = db.get(Instrument, instrument_id)
    if not instrument:
        raise HTTPException(404, "Instrument not found.")
    return evaluate_current_styles(db, instrument)


@app.get("/investor-styles/matrix")
def investor_style_matrix(
    include_other: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    styles = load_styles()
    owned_ids = {position["instrument_id"] for position in portfolio_snapshot(db)["positions"]}
    prospective_ids = set(db.scalars(select(WatchlistItem.instrument_id).where(
        WatchlistItem.status == "ACTIVE"
    )).all())
    visible_ids = owned_ids | prospective_ids
    instrument_query = select(Instrument).order_by(Instrument.company_name)
    if not include_other:
        instrument_query = instrument_query.where(Instrument.id.in_(visible_ids))
    instruments = db.scalars(instrument_query).all() if include_other or visible_ids else []
    rows = []
    for instrument in instruments:
        evaluations = {item["style_id"]: item for item in evaluate_current_styles(db, instrument)}
        cells = {}
        for style in styles:
            result = evaluations.get(style["id"])
            if not result:
                cells[style["id"]] = {"status": "NO_DATA", "score": None, "coverage": 0}
            elif not result["applicable"]:
                cells[style["id"]] = {"status": "NOT_APPLICABLE", "score": result["score"],
                                      "coverage": result["coverage"]}
            else:
                cells[style["id"]] = {"status": "MATCH" if result["matches"] else "NO_MATCH",
                                      "score": result["score"], "coverage": result["coverage"]}
        rows.append({"instrument_id": instrument.id, "exchange": instrument.exchange,
                     "symbol": instrument.symbol, "company_name": instrument.company_name,
                     "sector": instrument.sector,
                     "universe": ("OWNED" if instrument.id in owned_ids else
                                  "PROSPECTIVE" if instrument.id in prospective_ids else "OTHER"),
                     "styles": cells})
    return {"styles": [{"id": style["id"], "name": style["name"], "version": style["version"]}
                       for style in styles], "rows": rows}


@app.post("/providers/upstox/history/{instrument_id}/sync")
def sync_upstox_history(
    instrument_id: int,
    from_date: date = Query(),
    to_date: date = Query(default_factory=date.today),
    db: Session = Depends(get_db),
):
    instrument = db.get(Instrument, instrument_id)
    if not instrument:
        raise HTTPException(404, "Instrument not found.")
    if not instrument.isin:
        raise HTTPException(400, "An ISIN is required for historical price sync.")
    if from_date > to_date:
        raise HTTPException(400, "from_date must be on or before to_date.")
    if (to_date - from_date).days > 3653:
        raise HTTPException(400, "A maximum of ten years can be synced per request.")
    if not settings.upstox_token:
        raise HTTPException(503, "UPSTOX_ANALYTICS_TOKEN is not configured.")
    descriptor = ProviderInstrument(instrument.exchange, instrument.symbol, instrument.isin)
    client = UpstoxClient(settings.upstox_token, settings.upstox_api_base_url)
    try:
        provider = UpstoxMarketDataProvider([descriptor], client)
        prices = provider.get_price_history(descriptor, from_date, to_date)
        imported = store_market_prices(db, prices)
    except ProviderDataError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    except ProviderConnectionError as exc:
        db.rollback()
        raise HTTPException(502, str(exc)) from exc
    finally:
        client.close()
    return {"provider": "UPSTOX", "instrument_id": instrument.id, "prices_imported": imported,
            "from_date": from_date.isoformat(), "to_date": to_date.isoformat()}


@app.get("/backtests/{instrument_id}/{style_id}")
def run_style_backtest(
    instrument_id: int,
    style_id: str,
    horizon_days: int = Query(default=365, ge=30, le=1095),
    db: Session = Depends(get_db),
):
    instrument = db.get(Instrument, instrument_id)
    if not instrument:
        raise HTTPException(404, "Instrument not found.")
    try:
        style = get_style(style_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return backtest_style(db, instrument, style, horizon_days)


@app.get("/research/{instrument_id}")
def get_research(instrument_id: int, db: Session = Depends(get_db)):
    rows = db.scalars(select(ResearchSnapshot).where(
        ResearchSnapshot.instrument_id == instrument_id
    ).order_by(ResearchSnapshot.research_type, ResearchSnapshot.provider)).all()
    return [{
        "provider": row.provider,
        "research_type": row.research_type,
        "as_of": row.as_of.isoformat(),
        "payload": row.payload,
    } for row in rows]


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
