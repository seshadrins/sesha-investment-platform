"""The automation/orchestration pipeline: builds the cached dashboard workbench snapshot,
refreshes each data source (market prices, financial statements, NIFTY 500 screening,
followed-investor disclosures, IPO lifecycle), and runs the scheduled/forced morning
sequence that ties them together.

Previously embedded directly in main.py with no dedicated test coverage, unlike
screening.py, style_engine.py, and disclosure_pipeline.py, which are separate modules with
their own test files. Moved here to match that pattern — main.py's routes now call into
this module instead of defining the logic inline, and scheduler.py imports directly from
here instead of pulling in the whole FastAPI app just to reach three functions.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from .automation_config import effective_schedule
from .automation_schedule import (
    disclosure_coverage_state,
    latest_date_in_payload,
    latest_disclosure_period_due,
    latest_financial_period_due,
    latest_universe_boundary,
)
from .config import settings
from .disclosure_pipeline import run_disclosure_ingestion
from .financial_analysis import build_financial_analysis
from .investor_following import build_investor_signals, load_investor_config
from .ipo_lifecycle import discover_sebi_ipos, post_listing_monitor
from .models import (
    Account,
    AnalysisSnapshot,
    AppNotification,
    DisclosureDocument,
    DisclosureSourceMapping,
    Instrument,
    InvestorAliasReview,
    InvestorDisclosure,
    IPOIssue,
    NotionalTransaction,
    Price,
    ResearchSnapshot,
    ScreeningResult,
    Thesis,
    UniverseMembership,
    WatchlistItem,
)
from .notional_portfolio import settle_pending_orders
from .providers import (
    ProviderInstrument,
    UpstoxClient,
    UpstoxFundamentalsProvider,
    UpstoxMarketDataProvider,
)
from .recommendations import recommend_prospective
from .research import import_market_prices, store_fundamentals
from .screening import (
    fetch_universe_constituents,
    get_screening_universe,
    refresh_universe_memberships,
    select_balanced_memberships,
    ScreeningSourceError,
)
from .services import portfolio_diversification, portfolio_snapshot
from .style_engine import evaluate_current_styles, load_styles


def _snapshot_or_409(db: Session) -> dict:
    """portfolio_snapshot(), but a corrupt ledger row (e.g. an oversell) becomes a
    clean 4xx instead of an unhandled 500 for every read that needs the snapshot."""
    try:
        return portfolio_snapshot(db)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


def list_prospective_stocks_data(db: Session) -> list[dict]:
    owned_ids = {position["instrument_id"] for position in _snapshot_or_409(db)["positions"]}
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


def list_watching_stocks_data(db: Session) -> list[dict]:
    """The mid-funnel watchlist (G7): stocks the user is deliberately tracking that don't
    (yet) pass the Strong Buy gate, distinct from Owned and from Prospective-Strong-Buy.
    Shows whatever recommend_prospective() currently rates them, unfiltered — that's the
    whole point of a "tell me if it changes" list."""
    owned_ids = {position["instrument_id"] for position in _snapshot_or_409(db)["positions"]}
    rows = db.execute(select(WatchlistItem, Instrument).join(
        Instrument, WatchlistItem.instrument_id == Instrument.id
    ).where(WatchlistItem.status == "WATCHING").order_by(Instrument.company_name)).all()
    output = []
    for item, instrument in rows:
        if instrument.id in owned_ids:
            continue
        analysis = build_financial_analysis(db, instrument)
        styles = evaluate_current_styles(db, instrument)
        action, reasons = recommend_prospective(analysis, styles)
        output.append({"watchlist_id": item.id, "instrument_id": instrument.id,
            "exchange": instrument.exchange, "symbol": instrument.symbol,
            "company_name": instrument.company_name, "isin": instrument.isin,
            "sector": instrument.sector, "added_at": item.added_at.isoformat(), "notes": item.notes,
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


def _refresh_screening_universe(db: Session, universe_id: str = "nifty500") -> dict:
    config = get_screening_universe(universe_id)
    constituents = fetch_universe_constituents(config)
    if len(constituents) != 500:
        raise ScreeningSourceError(
            f"Expected 500 unique constituents but received {len(constituents)}."
        )
    result = refresh_universe_memberships(db, config, constituents)
    return {"universe_id": universe_id, **result}


def _screen_universe_batch(db: Session, universe_id: str, batch_size: int) -> dict:
    if not settings.upstox_token:
        raise RuntimeError("UPSTOX_ANALYTICS_TOKEN is not configured.")
    config = get_screening_universe(universe_id)

    owned_ids = {position["instrument_id"] for position in _snapshot_or_409(db)["positions"]}
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


def _build_stock_workbench(db: Session):
    """Return fixed, stock-level rows for every dashboard evidence tab."""
    snapshot = _snapshot_or_409(db)
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
            "previous_close": position["previous_close"],
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

    prospective = {item["instrument_id"]: item for item in list_prospective_stocks_data(db)}
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
        recent_prices = db.scalars(select(Price).where(
            Price.instrument_id == instrument_id
        ).order_by(Price.price_date.desc(), Price.id.desc()).limit(2)).all()
        latest_price = recent_prices[0] if recent_prices else None
        previous_price = recent_prices[1] if len(recent_prices) > 1 else None
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
                "remaining_cost": scope_data.get("remaining_cost"),
                "current_price": scope_data.get("current_price", float(latest_price.close_price)
                                                   if latest_price else None),
                "price_date": scope_data.get("price_date", latest_price.price_date.isoformat()
                                                if latest_price else None),
                "previous_close": scope_data.get("previous_close", float(previous_price.close_price)
                                                    if previous_price else None),
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
    # Owned rows lead with what actually needs a decision this week (G6): STRONG_SELL down
    # to BUY_MORE, not alphabetical order, which gave a STRONG_SELL no more visual priority
    # than a BUY_MORE 29 rows below it. Prospective rows are all STRONG_BUY by construction
    # (list_prospective_stocks_data() already filters to that), so severity carries no
    # information there — alphabetical stays the useful order.
    owned_rows.sort(key=lambda item: (
        -severity.get(item["summary"]["recommendation"], 0), item["company_name"], item["symbol"],
    ))
    prospective_rows.sort(key=lambda item: (item["company_name"], item["symbol"]))
    return {
        "as_of": snapshot["as_of"],
        "summary": snapshot["summary"],
        "style_definitions": [{"id": item["id"], "name": item["name"],
                               "version": item["version"]} for item in style_definitions],
        "investor_profiles": [{"id": item["id"], "name": item["name"]}
                              for item in investor_data["profiles"]],
        "owned": owned_rows,
        "prospective": prospective_rows,
    }


def _notify_recommendation_changes(db: Session, previous_payload: dict | None, current_payload: dict) -> None:
    """Emit an AppNotification for every owned stock whose recommendation changed since the
    last stored snapshot (G5). The scheduler already recomputes every owned recommendation
    on every run — this is the difference between that being visible only if the user
    happens to open the dashboard and notice it in the table, versus something that tells
    the user when it actually needs attention. Skipped on the very first snapshot (no prior
    payload to diff against, so nothing has "changed" yet)."""
    if not previous_payload:
        return
    previous_by_id = {row["instrument_id"]: row["summary"]["recommendation"]
                      for row in previous_payload.get("owned", [])}
    changed = False
    for row in current_payload.get("owned", []):
        old = previous_by_id.get(row["instrument_id"])
        new = row["summary"]["recommendation"]
        if old is None or old == new:
            continue
        stock = f"{row['exchange']}:{row['symbol']}"
        reasons = row["summary"].get("reasons") or []
        severity = "WARNING" if new in {"SELL", "STRONG_SELL", "TRIM", "REVIEW"} else "INFO"
        db.add(AppNotification(
            event_key=f"recommendation-change:{row['instrument_id']}:{date.today().isoformat()}:{old}->{new}",
            category="RECOMMENDATION", severity=severity,
            title=f"{stock} recommendation changed: {old.replace('_', ' ')} → {new.replace('_', ' ')}",
            message=f"{row['company_name']} ({stock}) moved from {old.replace('_', ' ')} to "
                    f"{new.replace('_', ' ')}." + (f" {reasons[0]}" if reasons else ""),
            payload={"instrument_id": row["instrument_id"], "stock": stock,
                     "old_recommendation": old, "new_recommendation": new, "reasons": reasons},
        ))
        changed = True
    if not changed:
        return
    try:
        db.commit()
        from .disclosure_pipeline import deliver_notifications
        deliver_notifications(db)
    except IntegrityError:
        db.rollback()


def _store_workbench_snapshot(db: Session, payload: dict) -> AnalysisSnapshot:
    snapshot = db.scalar(select(AnalysisSnapshot).where(
        AnalysisSnapshot.snapshot_key == "stock_workbench"
    ))
    _notify_recommendation_changes(db, snapshot.payload if snapshot else None, payload)
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


def deployment_plan_data(db: Session) -> dict:
    """"I have cash to deploy — here's where it should go" (G8): a single ranked view
    spanning both rebalancing (trim overweight/deteriorating owned positions) and new-idea
    sourcing (Prospective Strong Buy + the G7 watching tier), scoped by what's actually
    deployable (G3's account cash) and the portfolio's current sector/cap-segment mix (G2)
    so a suggestion doesn't just add to an already-concentrated corner of the portfolio."""
    snapshot = db.scalar(select(AnalysisSnapshot).where(
        AnalysisSnapshot.snapshot_key == "stock_workbench"
    ))
    payload = snapshot.payload if snapshot and snapshot.payload else _build_stock_workbench(db)
    available_cash = db.scalar(select(func.sum(Account.cash_balance))) or Decimal("0")

    trim_candidates = [{
        "instrument_id": row["instrument_id"], "stock": f"{row['exchange']}:{row['symbol']}",
        "company_name": row["company_name"], "recommendation": row["summary"]["recommendation"],
        "reasons": row["summary"]["reasons"], "market_value": row["portfolio"]["market_value"],
        "weight": row["portfolio"]["weight"],
    } for row in payload["owned"] if row["summary"]["recommendation"] in {"TRIM", "SELL", "STRONG_SELL"}]

    buy_candidates = [{
        "instrument_id": row["instrument_id"], "stock": f"{row['exchange']}:{row['symbol']}",
        "company_name": row["company_name"], "sector": row["sector"],
        "tier": "PROSPECTIVE_STRONG_BUY", "recommendation": row["summary"]["recommendation"],
        "financial_score": row["financials"]["overall_score"],
        "reasons": row["summary"]["reasons"],
    } for row in payload["prospective"]]
    for item in list_watching_stocks_data(db):
        if item["recommendation"] in {"STRONG_BUY", "BUY"}:
            buy_candidates.append({
                "instrument_id": item["instrument_id"],
                "stock": f"{item['exchange']}:{item['symbol']}",
                "company_name": item["company_name"], "sector": item["sector"],
                "tier": "WATCHING", "recommendation": item["recommendation"],
                "financial_score": item["financial_score"],
                "reasons": item["recommendation_reasons"],
            })
    buy_candidates.sort(key=lambda item: (
        item["recommendation"] != "STRONG_BUY", -(item["financial_score"] or 0),
    ))

    return {
        "as_of": payload.get("as_of"), "available_cash": float(available_cash),
        "trim_candidates": trim_candidates, "buy_candidates": buy_candidates,
        "diversification": portfolio_diversification(db),
    }


def _patch_workbench_thesis(db: Session, instrument_id: int, thesis: Thesis | None) -> None:
    """Update the cached workbench snapshot's thesis fields for one instrument in place,
    so a saved thesis edit shows up on the dashboard immediately instead of waiting for the
    next scheduled/forced snapshot rebuild."""
    snapshot = db.scalar(select(AnalysisSnapshot).where(
        AnalysisSnapshot.snapshot_key == "stock_workbench"
    ))
    if not snapshot or not snapshot.payload:
        return
    changed = False
    for scope_rows in (snapshot.payload.get("owned", []), snapshot.payload.get("prospective", [])):
        for row in scope_rows:
            if row.get("instrument_id") == instrument_id:
                row["portfolio"]["thesis_status"] = thesis.status.value if thesis else None
                row["portfolio"]["thesis_reason"] = thesis.reason if thesis else None
                changed = True
    if changed:
        flag_modified(snapshot, "payload")
        db.commit()


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
    owned_ids = {position["instrument_id"] for position in _snapshot_or_409(db)["positions"]}
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
    active_mappings = db.scalar(select(func.count(DisclosureSourceMapping.id)).where(
        DisclosureSourceMapping.active.is_(True))) or 0
    checked_mappings = db.scalar(select(func.count(DisclosureSourceMapping.id)).where(
        DisclosureSourceMapping.active.is_(True),
        DisclosureSourceMapping.last_report_period == period)) or 0
    failed_mappings = db.scalar(select(func.count(DisclosureSourceMapping.id)).where(
        DisclosureSourceMapping.active.is_(True),
        DisclosureSourceMapping.last_report_period == period,
        DisclosureSourceMapping.last_status == "FAILED")) or 0
    parser_failures = db.scalar(select(func.count(DisclosureDocument.id)).where(
        DisclosureDocument.report_date == period,
        DisclosureDocument.status == "PARSER_FAILED")) or 0
    pending_aliases = db.scalar(select(func.count(InvestorAliasReview.id)).where(
        InvestorAliasReview.report_date == period,
        InvestorAliasReview.status == "PENDING")) or 0
    remaining_mappings = max(0, active_mappings - checked_mappings)
    blockers = {
        "failed_mappings": failed_mappings,
        "parser_failures": parser_failures,
        "pending_alias_reviews": pending_aliases,
        "automated_ingestion_disabled": not settings.disclosure_auto_ingest_enabled,
    }
    coverage_status = disclosure_coverage_state(remaining_mappings=remaining_mappings,
        failed_mappings=failed_mappings, parser_failures=parser_failures,
        pending_aliases=pending_aliases, ingestion_enabled=settings.disclosure_auto_ingest_enabled,
        missing_investors=len(missing))
    if coverage_status == "ACTION_REQUIRED":
        message = "Coverage has source, parser, or configuration blockers requiring intervention."
    elif coverage_status == "REVIEW_PENDING":
        message = (
            f"{pending_aliases} shareholder-name match(es) awaiting your review. This is "
            "routine, expected, self-clearing work — not a failure."
        )
    elif coverage_status == "IN_PROGRESS":
        message = f"Coverage is progressing normally; {remaining_mappings} active source mappings remain unchecked."
    elif coverage_status == "NO_ATTRIBUTABLE_DISCLOSURE":
        message = ("The source-mapping cycle is complete, but one or more profiles have no "
                   "attributable disclosure for the period. Absence is not treated as an exit.")
    else:
        message = "Stored disclosures cover the expected reporting period."
    return {
        "status": "FAILED" if ingestion.get("status") == "FAILED" else "SUCCESS",
        "coverage_status": coverage_status,
        "expected_period": period.isoformat(),
        "filing_lag_days": settings.investor_disclosure_lag_days,
        "latest_by_investor": {
            profile["id"]: (
                latest_by_investor[profile["id"]].isoformat()
                if profile["id"] in latest_by_investor else None
            ) for profile in profiles
        },
        "missing_investors": missing,
        "investor_coverage": {
            profile["id"]: (
                "CURRENT" if latest_by_investor.get(profile["id"], date.min) >= period
                else ("PENDING_CYCLE" if remaining_mappings else "NO_ATTRIBUTABLE_DISCLOSURE")
            ) for profile in profiles
        },
        "coverage_progress": {"active_mappings": active_mappings,
                              "checked_mappings": checked_mappings,
                              "remaining_mappings": remaining_mappings},
        "blockers": blockers,
        "ingestion": ingestion,
        "message": message,
    }


def _refresh_ipo_lifecycle(db: Session) -> dict:
    discovery = discover_sebi_ipos(db) if settings.ipo_discovery_enabled else {"status": "SKIPPED"}
    monitored = 0
    for issue in db.scalars(select(IPOIssue).where(
        IPOIssue.stage.in_(["LISTED", "POST_LISTING_MONITORING"]))).all():
        post_listing_monitor(db, issue); monitored += 1
    return {"status": discovery.get("status", "SUCCESS"), "discovery": discovery,
            "post_listing_monitored": monitored}


def _upstox_instruments(db: Session) -> tuple[list[ProviderInstrument], list[str]]:
    candidate_ids = set(db.scalars(select(UniverseMembership.instrument_id).where(
        UniverseMembership.active.is_(True)
    )).all())
    owned_ids = {position["instrument_id"] for position in _snapshot_or_409(db)["positions"]}
    prospective_ids = set(db.scalars(select(WatchlistItem.instrument_id).where(
        WatchlistItem.status == "ACTIVE"
    )).all())
    notional_ids = set(db.scalars(select(NotionalTransaction.instrument_id).where(
        NotionalTransaction.instrument_id.is_not(None),
        NotionalTransaction.status.in_(["PENDING_PRICE", "EXECUTED"])
    )).all())
    prospective_ids.update(notional_ids)
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
        "nifty500_screening", "investor_disclosures", "notional_settlement", "ipo_lifecycle",
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
        "notional_settlement": lambda: {"status": "SUCCESS", **settle_pending_orders(db)},
        "ipo_lifecycle": lambda: _refresh_ipo_lifecycle(db),
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


def _automation_job_definitions(db: Session) -> list[dict]:
    config = effective_schedule(db)
    schedule = (
        f"{config.days} at {config.hour:02d}:{config.minute:02d} {config.timezone}"
    )
    return [
        {"id": "market_prices", "name": "Previous-close market prices", "frequency": schedule,
         "policy": "Latest Upstox close on or before the preceding calendar day."},
        {"id": "notional_settlement", "name": "Pending notional trades", "frequency": schedule,
         "policy": "Execute only against the first stored close on or after each decision's target date."},
        {"id": "ipo_lifecycle", "name": "IPO discovery and lifecycle", "frequency": schedule,
         "policy": "Discover official SEBI public-issue filings and refresh listed IPO milestones through day 365."},
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


def _snapshot_metadata(snapshot: AnalysisSnapshot, mode: str, db: Session) -> dict:
    config = effective_schedule(db)
    return {
        "mode": mode,
        "generated_at": snapshot.generated_at.isoformat() if snapshot.generated_at else None,
        "last_attempted_at": snapshot.last_attempted_at.isoformat(),
        "last_status": snapshot.last_status,
        "last_error": snapshot.last_error,
        "schedule": {
            "days": config.days, "hour": config.hour, "minute": config.minute,
            "timezone": config.timezone, "enabled": config.enabled,
        },
    }
