from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from .database import Base, apply_additive_migrations, engine, get_db
from .models import (
    Account,
    AnalysisSnapshot,
    AppNotification,
    AutomationRun,
    AutomationScheduleConfig,
    NotionalPortfolio,
    NotionalTransaction,
    IPOIssue, IPODocument, IPODocumentSection, IPOAnalysis,
    DecisionJournal,
    DisclosureSourceMapping,
    DisclosureDocument,
    Instrument,
    InvestorAliasReview,
    InvestorDisclosure,
    GroundedDocumentAnalysis,
    Price,
    ResearchSnapshot,
    ResearchDocument,
    ResearchDocumentSection,
    ScreeningResult,
    Thesis,
    ThesisVersion,
    Transaction,
    TransactionType,
    UniverseMembership,
    WatchlistItem,
)
from .schemas import (
    AccountCashUpdate,
    AccountCreate,
    AliasReviewDecision,
    AccountOut,
    DecisionCreate,
    DisclosureIngestionRequest,
    DocumentAnalysisRequest,
    DocumentReviewDecision,
    AutomationScheduleUpdate,
    NotionalPortfolioCreate,
    NotionalTradeCreate,
    NotionalCashCreate,
    IPOIssueCreate, IPOIssueUpdate, IPOAnalysisRequest, IPOAnalysisReview,
    DisclosureSourceMappingUpsert,
    InstrumentCreate,
    InstrumentOut,
    PriceCreate,
    ThesisUpsert,
    TransactionCreate,
    UpstoxSyncRequest,
    WatchlistCreate,
    WatchTierCreate,
)
from .config import settings
from .automation_schedule import (
    disclosure_coverage_state,
    latest_disclosure_period_due,
    latest_date_in_payload,
    latest_financial_period_due,
    latest_universe_boundary,
)
from .automation_runs import (
    ACTIVE_TRIGGERS,
    automation_metrics,
    automation_run_out,
    begin_scoped_run,
    complete_run,
    expected_scheduled_for,
    find_active_run,
    schedule_health,
)
from .automation_config import (
    AutomationConfigError, effective_schedule, schedule_out, update_schedule,
)
from .services import portfolio_diversification, portfolio_snapshot
from .real_portfolio_performance import real_performance_history
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
from .document_analysis import (
    DocumentAnalysisError, analysis_out, analyze_document, store_document,
)
from .notional_portfolio import (
    NotionalPortfolioError, create_cash_transaction, create_trade,
    performance_history as notional_performance_history,
    recommendation_learning as notional_recommendation_learning,
    portfolio_snapshot as notional_portfolio_snapshot,
    settle_pending_orders, transaction_out as notional_transaction_out,
)
from .ipo_lifecycle import (IPOError, STAGES, analyze_ipo, discover_sebi_ipos,
    normalize_name, post_listing_monitor, store_ipo_document)
from .automation_pipeline import (
    _automation_job_definitions,
    _build_stock_workbench,
    _check_investor_disclosures,
    _latest_automation_results,
    _patch_workbench_thesis,
    _priority_financial_instruments,
    _record_automation_status,
    _record_workbench_failure,
    _refresh_analysis_market_data,
    _refresh_due_financial_statements,
    _refresh_ipo_lifecycle,
    _refresh_screening_universe,
    _refresh_universe_if_due,
    _run_automation_action,
    _run_morning_automation,
    _screen_universe_batch,
    _screening_result_out,
    _snapshot_metadata,
    _snapshot_or_409,
    _store_workbench_snapshot,
    _upstox_instruments,
    deployment_plan_data,
    list_prospective_stocks_data,
    list_watching_stocks_data,
    reconcile_prospective_promotions,
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


# Read-only visibility into the portfolio-wide risk settings that drive recommend()'s
# concentration/loss-review gates (see Changes-SetA Phase 3). These are plain env-var
# settings today, not a DB-backed table, so this just exposes the running values and a
# plain-language description of what each one gates.
PORTFOLIO_RISK_SETTINGS = [
    ("max_position_weight", "max_position_weight",
     "A stock above this share of the portfolio is a candidate for TRIM/HOLD instead of BUY_MORE."),
    ("trim_position_weight", "trim_position_weight",
     "A stock above this share of the portfolio triggers a TRIM recommendation."),
    ("loss_review_threshold", "loss_review_threshold",
     "An unrealised return at or below this level triggers a REVIEW/SELL recommendation."),
    ("profit_review_threshold", "profit_review_threshold",
     "Combined with the maximum position weight, an unrealised return at or above this level "
     "can trigger a TRIM recommendation to realise gains."),
    ("buy_more_min_financial_score", "buy_more_min_financial_score",
     "The financial-quality score (0-100) a stock must meet or exceed to qualify for BUY_MORE."),
]


@app.get("/settings")
def get_settings() -> dict:
    return {
        "portfolio_risk_settings": [
            {"name": name, "value": getattr(settings, attr), "description": description}
            for name, attr, description in PORTFOLIO_RISK_SETTINGS
        ]
    }


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


@app.patch("/accounts/{account_id}/cash", response_model=AccountOut)
def update_account_cash(account_id: int, payload: AccountCashUpdate, db: Session = Depends(get_db)):
    """Set the account's deployable/uninvested cash figure (G3). There is no cash
    transaction ledger — the user sets this directly, the same way a brokerage statement's
    cash balance is a point-in-time figure the app has no other way to know."""
    account = db.get(Account, account_id)
    if not account:
        raise HTTPException(404, "Account not found.")
    account.cash_balance = payload.cash_balance
    db.commit()
    db.refresh(account)
    return account


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
        owned_ids = {position["instrument_id"] for position in _snapshot_or_409(db)["positions"]}
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
    if action not in {"STRONG_BUY", "BUY"}:
        raise HTTPException(409, {
            "message": "Only stocks currently rated Buy or Strong Buy can enter Prospective Stocks.",
            "current_recommendation": action,
            "reasons": reasons,
        })
    manual_source = f"MANUAL_{action}"
    item = db.scalar(select(WatchlistItem).where(WatchlistItem.instrument_id == instrument.id))
    if item:
        item.status = "ACTIVE"
        item.source = manual_source
        item.notes = payload.notes if payload.notes is not None else item.notes
    else:
        item = WatchlistItem(instrument_id=instrument.id, status="ACTIVE",
                             source=manual_source, notes=payload.notes)
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


@app.post("/watchlist/{instrument_id}/watch")
def add_to_watching(instrument_id: int, payload: WatchTierCreate, db: Session = Depends(get_db)):
    """The G7 mid-funnel tier: track a stock the user is researching without requiring it
    to already pass the Strong Buy gate — unlike POST /watchlist, which does."""
    instrument = db.get(Instrument, instrument_id)
    if not instrument:
        raise HTTPException(404, "Instrument not found.")
    item = db.scalar(select(WatchlistItem).where(WatchlistItem.instrument_id == instrument_id))
    if item:
        item.status = "WATCHING"
        item.source = "MANUAL_WATCHING"
        item.notes = payload.notes if payload.notes is not None else item.notes
    else:
        item = WatchlistItem(instrument_id=instrument_id, status="WATCHING",
                             source="MANUAL_WATCHING", notes=payload.notes)
        db.add(item)
    db.commit()
    db.refresh(item)
    return {"id": item.id, "instrument_id": item.instrument_id, "status": item.status}


@app.get("/watchlist/watching")
def list_watching(db: Session = Depends(get_db)):
    return list_watching_stocks_data(db)


@app.get("/prospective-stocks")
def list_prospective_stocks(db: Session = Depends(get_db)):
    return list_prospective_stocks_data(db)


@app.get("/screening-universes")
def list_screening_universe_status(db: Session = Depends(get_db)):
    owned_ids = {position["instrument_id"] for position in _snapshot_or_409(db)["positions"]}
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
        # Deliberately hardcoded rather than config["shortlist_recommendations"] (which now
        # also includes BUY, Changes-SetB Phase 6): this progress counter has always meant
        # "how many Strong Buys today," a narrower metric than "how many entered Prospective."
        strong_buys = db.scalar(select(func.count()).select_from(ScreeningResult).where(
            ScreeningResult.universe_id == config["id"],
            ScreeningResult.criteria_version.contains(cycle_marker),
            ScreeningResult.recommendation == "STRONG_BUY",
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


@app.post("/screening-universes/{universe_id}/reconcile")
def reconcile_screening_promotions(universe_id: str, db: Session = Depends(get_db)):
    """Catch up any already-screened company that qualifies for Prospective under the
    *current* shortlist criteria but was screened before that criteria applied (e.g. widened
    from Strong-Buy-only to Buy+Strong-Buy) and so was never promoted. No external API calls
    — pure re-evaluation of stored screening results against today's rules."""
    try:
        return reconcile_prospective_promotions(db, universe_id)
    except KeyError as exc:
        raise HTTPException(404, "Screening universe not found.") from exc


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
    # No transaction type has a meaningful zero quantity — including DIVIDEND, whose
    # quantity is the shares held, not just BUY/SELL/OPENING/ADJUSTMENT_*.
    if payload.quantity <= 0:
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
    except IntegrityError as exc:
        # Defense-in-depth: the checks above should already catch this, but a DB-level
        # constraint violation (e.g. a negative price/charges) must still come back as a
        # clean 4xx, not an unhandled 500.
        db.rollback()
        raise HTTPException(400, "This transaction violates a ledger data constraint.") from exc
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


@app.delete("/transactions/{transaction_id}")
def delete_transaction(transaction_id: int, db: Session = Depends(get_db)):
    """Void a transaction. The primary use case is recovering from a corrupt
    ledger row (e.g. a same-day SELL recorded before its matching BUY) that
    makes portfolio_snapshot() raise for every account/instrument group it
    touches — this is the only in-app way to remove such a row."""
    tx = db.get(Transaction, transaction_id)
    if not tx:
        raise HTTPException(404, "Transaction not found.")
    db.delete(tx)
    db.commit()
    return {"deleted": transaction_id}


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
    _patch_workbench_thesis(db, payload.instrument_id, thesis)
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
    return _snapshot_or_409(db)


@app.get("/portfolio/performance")
def get_portfolio_performance(
    benchmark_instrument_id: int | None = Query(default=None), db: Session = Depends(get_db)
):
    """Real-ledger return/drawdown/benchmark history (G1) — the equivalent of what
    /notional-portfolios/{id}/performance already computes for the simulated ledger."""
    return jsonable_encoder(real_performance_history(db, benchmark_instrument_id))


@app.get("/portfolio/diversification")
def get_portfolio_diversification(db: Session = Depends(get_db)):
    """Portfolio-level sector/cap-segment breakdown (G2)."""
    return portfolio_diversification(db)


@app.get("/portfolio/deployment-plan")
def get_deployment_plan(db: Session = Depends(get_db)):
    """"I have cash to deploy — where should it go" (G8)."""
    return jsonable_encoder(deployment_plan_data(db))


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
    for row in rows:
        if Decimal(row["quantity"]) <= 0:
            raise HTTPException(
                400, f"Row for {row.get('symbol', '?')} has quantity <= 0; opening balances "
                     "must be positive."
            )
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
    try:
        db.flush()
        _snapshot_or_409(db)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            400, "One or more rows violate a ledger data constraint (e.g. a negative price)."
        ) from exc
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
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            400, "One or more rows violate a ledger data constraint (e.g. a zero or "
                 "negative quantity)."
        ) from exc
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
    owned_ids = {position["instrument_id"] for position in _snapshot_or_409(db)["positions"]}
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
    scheduled_for = expected_scheduled_for(datetime.utcnow(), db)
    active = find_active_run(db, scheduled_for, ACTIVE_TRIGGERS)
    if active:
        raise HTTPException(
            409,
            f"A {active.trigger.lower()} analysis run is already in progress for this "
            f"scheduled window (started {active.started_at.isoformat()}). Try again once "
            "it completes.",
        )
    run, started = begin_scoped_run(db, "FORCED_DISCLOSURES", scheduled_for)
    if not started:
        raise HTTPException(
            409, f"A disclosure ingestion run is already in progress (run {run.id})."
        )
    try:
        result = run_disclosure_ingestion(
            db, period, force=payload.force, mapping_ids=payload.mapping_ids
        )
    except DisclosurePipelineError as exc:
        db.rollback()
        complete_run(db, run, "FAILED", {}, str(exc))
        raise HTTPException(502, str(exc)) from exc
    complete_run(
        db, run, result.get("status", "SUCCESS"), {"investor_disclosures": result.get("status")},
    )
    return result


@app.get("/investor-disclosures/reviews")
def investor_alias_reviews(
    status: str = Query(default="PENDING"), db: Session = Depends(get_db)
):
    query = select(InvestorAliasReview)
    if status.upper() != "ALL":
        query = query.where(InvestorAliasReview.status == status.upper())
    reviews = db.scalars(query.order_by(InvestorAliasReview.created_at.desc())).all()
    return [review_out(db, item) for item in reviews]


@app.get("/investor-disclosures/documents/{document_id}/content")
def get_disclosure_document_content(document_id: int, db: Session = Depends(get_db)):
    document = db.get(DisclosureDocument, document_id)
    if not document or not document.content:
        raise HTTPException(404, "No stored content for this disclosure document.")
    filename = f"disclosure-{document_id}.{'xml' if 'xml' in (document.content_type or '') else 'html'}"
    return Response(
        content=document.content,
        media_type=document.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


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
    unread_only: bool = Query(default=False), category: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    query = select(AppNotification)
    if unread_only:
        query = query.where(AppNotification.read_at.is_(None))
    if category:
        query = query.where(AppNotification.category == category.strip().upper())
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


@app.get("/stock-workbench")
def stock_workbench(db: Session = Depends(get_db)):
    snapshot = db.scalar(select(AnalysisSnapshot).where(
        AnalysisSnapshot.snapshot_key == "stock_workbench"
    ))
    if snapshot and snapshot.payload:
        return {**snapshot.payload, "snapshot": _snapshot_metadata(snapshot, "CACHED", db)}

    # Cold-start recovery only. The scheduler normally creates this before a user opens the UI.
    payload = _build_stock_workbench(db)
    snapshot = _store_workbench_snapshot(db, payload)
    return {**payload, "snapshot": _snapshot_metadata(snapshot, "COLD_START", db)}


@app.get("/analysis-schedule")
def analysis_schedule_status(db: Session = Depends(get_db)):
    snapshot = db.scalar(select(AnalysisSnapshot).where(
        AnalysisSnapshot.snapshot_key == "stock_workbench"
    ))
    definitions = _automation_job_definitions(db)
    config = effective_schedule(db)
    job_snapshots = {
        item.snapshot_key.removeprefix("automation:"): item
        for item in db.scalars(select(AnalysisSnapshot).where(
            AnalysisSnapshot.snapshot_key.in_([
                f"automation:{definition['id']}" for definition in definitions
            ])
        )).all()
    }
    return {
        "enabled": config.enabled,
        "run_on_startup": settings.analysis_run_on_startup,
        "health": schedule_health(db),
        "recovery_policy": {
            "start_grace_minutes": settings.analysis_start_grace_minutes,
            "stall_minutes": settings.analysis_stall_minutes,
            "catchup_max_hours": settings.analysis_catchup_max_hours,
            "retry_delays_minutes": settings.analysis_retry_delays_minutes,
        },
        "schedule": schedule_out(config),
        "metrics": automation_metrics(db),
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
        "snapshot": _snapshot_metadata(snapshot, "CACHED", db) if snapshot else None,
    }


@app.put("/analysis-schedule")
def edit_analysis_schedule(payload: AutomationScheduleUpdate, db: Session = Depends(get_db)):
    try:
        item = update_schedule(db, enabled=payload.enabled, days=payload.days,
            hour=payload.hour, minute=payload.minute, timezone_name=payload.timezone)
    except AutomationConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return schedule_out(item)


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
    scheduled_for = expected_scheduled_for(datetime.utcnow(), db)
    if job in {"morning", "disclosures"}:
        active = find_active_run(db, scheduled_for, ACTIVE_TRIGGERS)
        if active:
            raise HTTPException(
                409,
                f"A {active.trigger.lower()} analysis run is already in progress for this "
                f"scheduled window (started {active.started_at.isoformat()}). Try again once "
                "it completes.",
            )
    run, started = begin_scoped_run(db, f"FORCED_{job.upper()}", scheduled_for)
    if not started:
        raise HTTPException(
            409, f"A {job} run for this scheduled window is already in progress (run {run.id})."
        )
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
        "snapshot": _snapshot_metadata(snapshot, "FORCED", db),
    }


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
    owned_ids = {position["instrument_id"] for position in _snapshot_or_409(db)["positions"]}
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


def _document_out(db: Session, document: ResearchDocument) -> dict:
    section_count = db.scalar(select(func.count()).select_from(ResearchDocumentSection).where(
        ResearchDocumentSection.document_id == document.id)) or 0
    latest = db.scalar(select(GroundedDocumentAnalysis).where(
        GroundedDocumentAnalysis.document_id == document.id).order_by(GroundedDocumentAnalysis.id.desc()))
    instrument = db.get(Instrument, document.instrument_id)
    return {"id": document.id, "instrument_id": document.instrument_id,
        "stock": f"{instrument.exchange}:{instrument.symbol}", "company_name": instrument.company_name,
        "document_type": document.document_type, "title": document.title,
        "report_date": document.report_date, "source_url": document.source_url,
        "filename": document.filename, "content_type": document.content_type,
        "content_hash": document.content_hash, "page_count": document.page_count,
        "section_count": section_count, "created_at": document.created_at,
        "latest_analysis_id": latest.id if latest else None,
        "analysis_status": latest.status if latest else "NOT_ANALYSED"}


def _ipo_out(db: Session, item: IPOIssue) -> dict:
    latest = db.scalar(select(IPOAnalysis).where(IPOAnalysis.ipo_id == item.id).order_by(IPOAnalysis.id.desc()))
    return {"id": item.id, "company_name": item.company_name, "cin": item.cin, "isin": item.isin,
        "board": item.board, "stage": item.stage, "symbol": item.symbol, "exchange": item.exchange,
        "instrument_id": item.instrument_id, "discovered_on": item.discovered_on,
        "drhp_date": item.drhp_date, "rhp_date": item.rhp_date,
        "issue_open_date": item.issue_open_date, "issue_close_date": item.issue_close_date,
        "listing_date": item.listing_date, "price_band_low": item.price_band_low,
        "price_band_high": item.price_band_high, "issue_price": item.issue_price,
        "lot_size": item.lot_size, "fresh_issue_amount": item.fresh_issue_amount,
        "ofs_amount": item.ofs_amount, "source_url": item.source_url, "source_type": item.source_type,
        "analysis_outcome": latest.outcome if latest else None, "analysis_status": latest.status if latest else None}


@app.post("/ipos/discover")
def discover_ipos(db: Session = Depends(get_db)):
    try: return discover_sebi_ipos(db)
    except IPOError as exc: raise HTTPException(502, str(exc)) from exc


@app.post("/ipos")
def create_ipo(payload: IPOIssueCreate, db: Session = Depends(get_db)):
    if not payload.source_url.startswith("https://"): raise HTTPException(422, "Source URL must use HTTPS.")
    key = normalize_name(payload.company_name)
    existing = db.scalar(select(IPOIssue).where(IPOIssue.normalized_name == key))
    if existing: return jsonable_encoder(_ipo_out(db, existing))
    item = IPOIssue(**payload.model_dump(), normalized_name=key, stage="DISCOVERED", source_type="MANUAL_OFFICIAL")
    db.add(item); db.commit(); db.refresh(item); return jsonable_encoder(_ipo_out(db, item))


@app.get("/ipos")
def list_ipos(stage: str | None = None, board: str | None = None, db: Session = Depends(get_db)):
    query = select(IPOIssue).order_by(IPOIssue.discovered_on.desc(), IPOIssue.company_name)
    if stage: query = query.where(IPOIssue.stage == stage.upper())
    if board: query = query.where(IPOIssue.board == board.upper())
    return jsonable_encoder([_ipo_out(db, item) for item in db.scalars(query).all()])


@app.patch("/ipos/{ipo_id}")
def update_ipo(ipo_id: int, payload: IPOIssueUpdate, db: Session = Depends(get_db)):
    item = db.get(IPOIssue, ipo_id)
    if not item: raise HTTPException(404, "IPO not found.")
    values = payload.model_dump(exclude_none=True)
    if "stage" in values and values["stage"].upper() not in {*STAGES, "WITHDRAWN", "POSTPONED", "EXPIRED", "LISTING_CANCELLED"}:
        raise HTTPException(422, "Invalid lifecycle stage.")
    for key, value in values.items(): setattr(item, key, value.upper() if key in {"stage", "board", "exchange"} else value)
    db.commit(); db.refresh(item); return jsonable_encoder(_ipo_out(db, item))


@app.post("/ipos/{ipo_id}/documents")
async def upload_ipo_document(ipo_id: int, document_type: str = Form(...), document_date: date = Form(...),
        title: str = Form(...), source_url: str = Form(...), file: UploadFile = File(...),
        db: Session = Depends(get_db)):
    issue = db.get(IPOIssue, ipo_id)
    if not issue: raise HTTPException(404, "IPO not found.")
    content = await file.read(settings.document_max_bytes + 1)
    if len(content) > settings.document_max_bytes: raise HTTPException(413, "Document exceeds upload limit.")
    try: doc = store_ipo_document(db, issue, document_type=document_type, document_date=document_date,
                                  title=title, source_url=source_url, content=content)
    except IPOError as exc: raise HTTPException(422, str(exc)) from exc
    return {"id": doc.id, "page_count": doc.page_count, "content_hash": doc.content_hash}


@app.get("/ipos/{ipo_id}/documents")
def ipo_documents(ipo_id: int, db: Session = Depends(get_db)):
    rows = db.scalars(select(IPODocument).where(IPODocument.ipo_id == ipo_id).order_by(IPODocument.document_date.desc())).all()
    return jsonable_encoder([{"id": x.id, "document_type": x.document_type, "document_date": x.document_date,
        "title": x.title, "source_url": x.source_url, "page_count": x.page_count} for x in rows])


@app.post("/ipos/{ipo_id}/documents/{document_id}/analyze")
def analyze_ipo_document(ipo_id: int, document_id: int, payload: IPOAnalysisRequest, db: Session = Depends(get_db)):
    issue, doc = db.get(IPOIssue, ipo_id), db.get(IPODocument, document_id)
    if not issue or not doc or doc.ipo_id != ipo_id: raise HTTPException(404, "IPO document not found.")
    existing = db.scalar(select(IPOAnalysis).where(IPOAnalysis.document_id == document_id).order_by(IPOAnalysis.id.desc()))
    if existing and not payload.force: return jsonable_encoder(_ipo_analysis_out(db, existing))
    try: return jsonable_encoder(_ipo_analysis_out(db, analyze_ipo(db, issue, doc)))
    except IPOError as exc: raise HTTPException(422, str(exc)) from exc


def _ipo_analysis_out(db: Session, item: IPOAnalysis) -> dict:
    ids = set()
    for value in item.payload.values():
        if isinstance(value, dict): ids.update(value.get("section_ids", []))
        elif isinstance(value, list):
            for claim in value:
                if isinstance(claim, dict): ids.update(claim.get("section_ids", []))
    sections = db.scalars(select(IPODocumentSection).where(IPODocumentSection.id.in_(ids))).all() if ids else []
    return {"id": item.id, "ipo_id": item.ipo_id, "document_id": item.document_id,
        "outcome": item.outcome, "status": item.status, "payload": item.payload,
        "provider": item.provider, "model": item.model, "prompt_version": item.prompt_version,
        "review_note": item.review_note, "citations": {str(s.id): {"page_number": s.page_number,
            "heading": s.heading, "excerpt": s.text[:1200]} for s in sections}}


@app.get("/ipos/{ipo_id}/analysis")
def get_ipo_analysis(ipo_id: int, db: Session = Depends(get_db)):
    item = db.scalar(select(IPOAnalysis).where(IPOAnalysis.ipo_id == ipo_id).order_by(IPOAnalysis.id.desc()))
    if not item: raise HTTPException(404, "No IPO analysis exists.")
    return jsonable_encoder(_ipo_analysis_out(db, item))


@app.post("/ipo-analyses/{analysis_id}/review")
def review_ipo_analysis(analysis_id: int, payload: IPOAnalysisReview, db: Session = Depends(get_db)):
    item = db.get(IPOAnalysis, analysis_id)
    if not item: raise HTTPException(404, "IPO analysis not found.")
    decision = payload.decision.upper()
    if decision not in {"ACCEPTED", "REJECTED"}: raise HTTPException(422, "Decision must be ACCEPTED or REJECTED.")
    item.status, item.review_note = decision, payload.note; db.commit(); db.refresh(item)
    return jsonable_encoder(_ipo_analysis_out(db, item))


@app.get("/ipos/{ipo_id}/monitoring")
def ipo_monitoring(ipo_id: int, db: Session = Depends(get_db)):
    item = db.get(IPOIssue, ipo_id)
    if not item: raise HTTPException(404, "IPO not found.")
    return jsonable_encoder(post_listing_monitor(db, item))


def _notional_portfolio_out(item: NotionalPortfolio) -> dict:
    return {"id": item.id, "name": item.name, "currency": item.currency,
        "starting_cash": item.starting_cash, "max_position_weight": item.max_position_weight,
        "brokerage_pct": item.brokerage_pct, "tax_pct": item.tax_pct,
        "slippage_pct": item.slippage_pct, "reinvest_dividends": item.reinvest_dividends,
        "benchmark_instrument_id": item.benchmark_instrument_id, "active": item.active,
        "created_at": item.created_at, "updated_at": item.updated_at}


@app.post("/notional-portfolios")
def create_notional_portfolio(payload: NotionalPortfolioCreate, db: Session = Depends(get_db)):
    if payload.benchmark_instrument_id and not db.get(Instrument, payload.benchmark_instrument_id):
        raise HTTPException(404, "Benchmark instrument not found.")
    item = NotionalPortfolio(**payload.model_dump())
    db.add(item)
    try: db.commit()
    except Exception as exc:
        db.rollback(); raise HTTPException(409, "A notional portfolio with this name already exists.") from exc
    db.refresh(item)
    return jsonable_encoder(_notional_portfolio_out(item))


@app.get("/notional-portfolios")
def list_notional_portfolios(db: Session = Depends(get_db)):
    return jsonable_encoder([_notional_portfolio_out(item) for item in db.scalars(
        select(NotionalPortfolio).where(NotionalPortfolio.active.is_(True)).order_by(NotionalPortfolio.name)).all()])


@app.get("/notional-portfolios/candidates")
def notional_candidates(db: Session = Depends(get_db)):
    workbench = _build_stock_workbench(db)
    rows = []
    for scope, candidates in (("OWNED", workbench["owned"]), ("RECOMMENDED", workbench["prospective"])):
        for row in candidates:
            rows.append({"instrument_id": row["instrument_id"], "stock": f"{row['exchange']}:{row['symbol']}",
                "company_name": row["company_name"], "scope": scope,
                "recommendation": row["summary"]["recommendation"],
                "recommendation_snapshot": {"recommendation": row["summary"]["recommendation"],
                    "brief_reason": row["summary"]["brief_reason"],
                    "style_matches": row["summary"]["style_matches"],
                    "financial_score": row["financials"]["overall_score"],
                    "financial_evidence_date": row["financials"]["as_of"],
                    "governance_flags": row["financials"]["governance_flags"],
                    "investor_corroboration_count": len(row["investors"]),
                    "price_date": row["portfolio"]["price_date"]}})
    return jsonable_encoder(sorted(rows, key=lambda item: (item["scope"], item["stock"])))


@app.get("/notional-portfolios/{portfolio_id}")
def get_notional_portfolio(portfolio_id: int, db: Session = Depends(get_db)):
    item = db.get(NotionalPortfolio, portfolio_id)
    if not item: raise HTTPException(404, "Notional portfolio not found.")
    return jsonable_encoder(notional_portfolio_snapshot(db, item))


@app.post("/notional-portfolios/{portfolio_id}/trades")
def place_notional_trade(portfolio_id: int, payload: NotionalTradeCreate,
                         db: Session = Depends(get_db)):
    portfolio = db.get(NotionalPortfolio, portfolio_id)
    if not portfolio: raise HTTPException(404, "Notional portfolio not found.")
    candidates = {item["instrument_id"]: item for item in notional_candidates(db)}
    current = notional_portfolio_snapshot(db, portfolio)
    existing_ids = {item["instrument_id"] for item in current["holdings"]}
    if payload.action.strip().upper() == "BUY" and payload.instrument_id not in candidates and payload.instrument_id not in existing_ids:
        raise HTTPException(422, "Stock must be owned or currently recommended before its first notional buy.")
    recommendation = candidates.get(payload.instrument_id, {}).get("recommendation_snapshot")
    try:
        tx = create_trade(db, portfolio, payload.instrument_id, payload.action,
            payload.quantity, payload.amount, payload.user_reason, recommendation)
    except NotionalPortfolioError as exc:
        raise HTTPException(422, str(exc)) from exc
    return jsonable_encoder(notional_transaction_out(db, tx))


@app.post("/notional-portfolios/{portfolio_id}/cash")
def adjust_notional_cash(portfolio_id: int, payload: NotionalCashCreate,
                         db: Session = Depends(get_db)):
    portfolio = db.get(NotionalPortfolio, portfolio_id)
    if not portfolio: raise HTTPException(404, "Notional portfolio not found.")
    try:
        if (payload.action.strip().upper() == "DIVIDEND" and portfolio.reinvest_dividends
                and not payload.instrument_id):
            raise NotionalPortfolioError("A holding is required when dividend reinvestment is enabled.")
        tx = create_cash_transaction(db, portfolio, payload.action, payload.amount, payload.user_reason)
        if payload.action.strip().upper() == "DIVIDEND" and portfolio.reinvest_dividends:
            create_trade(db, portfolio, payload.instrument_id, "BUY", None, payload.amount,
                         "Automatic reinvestment of recorded dividend", None)
    except NotionalPortfolioError as exc: raise HTTPException(422, str(exc)) from exc
    return jsonable_encoder(notional_transaction_out(db, tx))


@app.post("/notional-portfolios/{portfolio_id}/settle")
def settle_notional_portfolio(portfolio_id: int, db: Session = Depends(get_db)):
    if not db.get(NotionalPortfolio, portfolio_id): raise HTTPException(404, "Notional portfolio not found.")
    return settle_pending_orders(db, portfolio_id)


@app.get("/notional-portfolios/{portfolio_id}/transactions")
def notional_transactions(portfolio_id: int, db: Session = Depends(get_db)):
    rows = db.scalars(select(NotionalTransaction).where(
        NotionalTransaction.portfolio_id == portfolio_id).order_by(NotionalTransaction.id.desc())).all()
    return jsonable_encoder([notional_transaction_out(db, item) for item in rows])


@app.get("/notional-portfolios/{portfolio_id}/performance")
def notional_performance(portfolio_id: int, db: Session = Depends(get_db)):
    portfolio = db.get(NotionalPortfolio, portfolio_id)
    if not portfolio: raise HTTPException(404, "Notional portfolio not found.")
    return jsonable_encoder(notional_performance_history(db, portfolio))


@app.get("/notional-portfolios/{portfolio_id}/learning")
def notional_learning(portfolio_id: int, db: Session = Depends(get_db)):
    portfolio = db.get(NotionalPortfolio, portfolio_id)
    if not portfolio: raise HTTPException(404, "Notional portfolio not found.")
    return jsonable_encoder(notional_recommendation_learning(db, portfolio))


@app.post("/documents")
async def upload_research_document(
    instrument_id: int = Form(...), document_type: str = Form(...), title: str = Form(...),
    report_date: date = Form(...), source_url: str | None = Form(None),
    file: UploadFile = File(...), db: Session = Depends(get_db),
):
    if not db.get(Instrument, instrument_id):
        raise HTTPException(status_code=404, detail="Instrument not found")
    kind = document_type.strip().upper()
    if kind not in {"ANNUAL_REPORT", "TRANSCRIPT"}:
        raise HTTPException(status_code=422, detail="document_type must be ANNUAL_REPORT or TRANSCRIPT")
    if source_url and not source_url.startswith("https://"):
        raise HTTPException(status_code=422, detail="Source URL must use HTTPS")
    content = await file.read(settings.document_max_bytes + 1)
    if len(content) > settings.document_max_bytes:
        raise HTTPException(status_code=413, detail="Document exceeds configured upload limit")
    filename = (file.filename or "document")[:240]
    content_type = (file.content_type or "application/octet-stream").lower()
    if not (filename.lower().endswith((".pdf", ".txt")) or content_type in {"application/pdf", "text/plain"}):
        raise HTTPException(status_code=415, detail="Only PDF and UTF-8 text documents are supported")
    try:
        document = store_document(db, instrument_id=instrument_id, document_type=kind,
            title=title.strip()[:240], report_date=report_date, source_url=source_url or None,
            filename=filename, content_type=content_type, content=content)
    except DocumentAnalysisError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return jsonable_encoder(_document_out(db, document))


@app.get("/documents")
def list_research_documents(instrument_id: int | None = None, db: Session = Depends(get_db)):
    query = select(ResearchDocument).order_by(ResearchDocument.report_date.desc(), ResearchDocument.id.desc())
    if instrument_id is not None:
        query = query.where(ResearchDocument.instrument_id == instrument_id)
    return jsonable_encoder([_document_out(db, item) for item in db.scalars(query).all()])


@app.get("/documents/{document_id}/sections")
def get_document_sections(document_id: int, db: Session = Depends(get_db)):
    if not db.get(ResearchDocument, document_id):
        raise HTTPException(status_code=404, detail="Document not found")
    rows = db.scalars(select(ResearchDocumentSection).where(
        ResearchDocumentSection.document_id == document_id).order_by(ResearchDocumentSection.section_index)).all()
    return jsonable_encoder([{"id": row.id, "section_index": row.section_index,
        "page_number": row.page_number, "heading": row.heading, "text": row.text} for row in rows])


@app.post("/documents/{document_id}/analyze")
def create_document_analysis(document_id: int, payload: DocumentAnalysisRequest,
                             db: Session = Depends(get_db)):
    document = db.get(ResearchDocument, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        return jsonable_encoder(analysis_out(db, analyze_document(db, document, payload.force)))
    except DocumentAnalysisError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/documents/{document_id}/analysis")
def get_document_analysis(document_id: int, db: Session = Depends(get_db)):
    analysis = db.scalar(select(GroundedDocumentAnalysis).where(
        GroundedDocumentAnalysis.document_id == document_id).order_by(GroundedDocumentAnalysis.id.desc()))
    if not analysis:
        raise HTTPException(status_code=404, detail="No analysis exists for this document")
    return jsonable_encoder(analysis_out(db, analysis))


@app.post("/document-analyses/{analysis_id}/review")
def review_document_analysis(analysis_id: int, payload: DocumentReviewDecision,
                             db: Session = Depends(get_db)):
    analysis = db.get(GroundedDocumentAnalysis, analysis_id)
    if not analysis:
        raise HTTPException(status_code=404, detail="Analysis not found")
    decision = payload.decision.strip().upper()
    if decision not in {"ACCEPTED", "REJECTED"}:
        raise HTTPException(status_code=422, detail="decision must be ACCEPTED or REJECTED")
    analysis.status = decision
    analysis.review_note = payload.note
    analysis.reviewed_at = datetime.utcnow()
    db.commit(); db.refresh(analysis)
    return jsonable_encoder(analysis_out(db, analysis))


@app.post("/reconcile")
def reconcile(file: UploadFile = File(...), db: Session = Depends(get_db)):
    broker_rows = _read_csv(file)
    snapshot = _snapshot_or_409(db)
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
