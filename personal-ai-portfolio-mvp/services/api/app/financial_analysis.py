from __future__ import annotations

from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Instrument, ResearchSnapshot, ValuationMetricSnapshot


def _normalise(value: str) -> str:
    return value.lower().replace("_", " ").replace("-", " ").strip()


def _categories(payload: dict, key: str) -> dict[str, list[dict]]:
    return {_normalise(str(item.get("category", ""))): item.get("history") or []
            for item in (payload.get(key) or [])}


def _find_category(categories: dict[str, list[dict]], *terms: str) -> list[dict]:
    for name, history in categories.items():
        if all(term in name for term in terms):
            return history
    return []


def _number(value):
    try:
        return float(str(value).replace("%", "").replace(",", ""))
    except (TypeError, ValueError):
        return None


def _latest(history: list[dict]):
    return _number(history[0].get("value")) if history else None


def _score_high(value: float | None, levels=(20, 15, 10, 5)) -> int | None:
    if value is None:
        return None
    return 100 if value >= levels[0] else 80 if value >= levels[1] else 60 if value >= levels[2] else 40 if value >= levels[3] else 20


def _trend(history: list[dict]) -> list[dict]:
    return [{"period": item.get("period"), "value": _number(item.get("value")),
             "change": item.get("change")} for item in history]


def build_financial_analysis(db: Session, instrument: Instrument) -> dict:
    snapshots = db.scalars(select(ResearchSnapshot).where(
        ResearchSnapshot.instrument_id == instrument.id,
        ResearchSnapshot.provider == "UPSTOX",
    )).all()
    evidence = {row.research_type: row for row in snapshots}
    required = {"INCOME_ANNUAL", "INCOME_QUARTERLY", "BALANCE_SHEET", "CASH_FLOW", "KEY_RATIOS"}
    missing = sorted(required - evidence.keys())
    if missing:
        return {"status": "MISSING_DATA", "missing": missing, "instrument_id": instrument.id,
                "available_datasets": sorted(evidence),
                "provider_errors": (evidence.get("PROVIDER_ERRORS").payload.get("errors", [])
                                    if evidence.get("PROVIDER_ERRORS") else [])}

    annual_payload = evidence["INCOME_ANNUAL"].payload
    quarterly_payload = evidence["INCOME_QUARTERLY"].payload
    annual = _categories(annual_payload, "income_statement")
    quarterly = _categories(quarterly_payload, "income_statement")
    revenue = _find_category(annual, "revenue")
    operating = _find_category(annual, "operating", "profit")
    net_profit = _find_category(annual, "net", "profit")
    q_revenue = _find_category(quarterly, "revenue")
    q_operating = _find_category(quarterly, "operating", "profit")
    q_net = _find_category(quarterly, "net", "profit")

    ratios = {str(item.get("name", "")).upper(): {
        "company": _number(item.get("company_value")), "sector": _number(item.get("sector_value"))
    } for item in evidence["KEY_RATIOS"].payload}

    balance_payload = evidence["BALANCE_SHEET"].payload
    balance_history = balance_payload.get("history") or []
    assets = _number(balance_history[0].get("total_asset")) if balance_history else None
    liabilities = _number(balance_history[0].get("total_liability")) if balance_history else None
    leverage_proxy = liabilities / assets if assets not in (None, 0) and liabilities is not None else None

    cash = _categories(evidence["CASH_FLOW"].payload, "cash_flow")
    operating_cash = _find_category(cash, "operating")
    latest_cfo, latest_profit = _latest(operating_cash), _latest(net_profit)
    cash_conversion = latest_cfo / latest_profit if latest_profit not in (None, 0) and latest_cfo is not None else None

    latest_revenue, latest_operating, latest_net = _latest(revenue), _latest(operating), _latest(net_profit)
    operating_margin = latest_operating / latest_revenue * 100 if latest_revenue not in (None, 0) and latest_operating is not None else None
    net_margin = latest_net / latest_revenue * 100 if latest_revenue not in (None, 0) and latest_net is not None else None

    sector = (instrument.sector or "").lower()
    financial_sector = any(word in sector for word in ("bank", "financial", "finance", "insurance"))
    scores = {
        "roce": _score_high((ratios.get("ROCE") or {}).get("company")),
        "roe": _score_high((ratios.get("ROE") or {}).get("company")),
        "leverage": None if financial_sector or leverage_proxy is None else (
            100 if leverage_proxy <= .4 else 75 if leverage_proxy <= .6 else 50 if leverage_proxy <= .75 else 25
        ),
        "cash_conversion": None if financial_sector or cash_conversion is None else (
            100 if cash_conversion >= 1 else 80 if cash_conversion >= .8 else 55 if cash_conversion >= .5 else 30 if cash_conversion >= 0 else 10
        ),
    }
    available_scores = [value for value in scores.values() if value is not None]

    flags = []
    if latest_profit is not None and latest_profit > 0 and latest_cfo is not None and latest_cfo < 0:
        flags.append({"severity": "HIGH", "message": "Profit is positive but operating cash flow is negative."})
    if cash_conversion is not None and cash_conversion < .5 and not financial_sector:
        flags.append({"severity": "MEDIUM", "message": "Operating cash conversion is below 50% of net profit."})
    holdings = evidence.get("SHARE_HOLDINGS")
    if holdings:
        promoter = next((item for item in holdings.payload if "promoter" in _normalise(str(item.get("category", "")))), None)
        history = promoter.get("history", []) if promoter else []
        if len(history) >= 2:
            newest, previous = _number(history[0].get("value")), _number(history[1].get("value"))
            if newest is not None and previous is not None and previous - newest >= 2:
                flags.append({"severity": "MEDIUM", "message": f"Promoter holding fell {previous - newest:.2f} percentage points in the latest quarter."})

    valuation_history = db.scalars(select(ValuationMetricSnapshot).where(
        ValuationMetricSnapshot.instrument_id == instrument.id,
        ValuationMetricSnapshot.metric.in_(["P/E", "P/B", "EV/EBITDA"]),
    ).order_by(ValuationMetricSnapshot.as_of)).all()
    by_metric: dict[str, list[ValuationMetricSnapshot]] = {}
    for item in valuation_history:
        by_metric.setdefault(item.metric, []).append(item)
    valuation = {}
    for metric, items in by_metric.items():
        values = [float(item.value) for item in items]
        valuation[metric] = {"current": values[-1], "sector": float(items[-1].sector_value) if items[-1].sector_value is not None else None,
            "observations": len(values), "min": min(values), "median": median(values), "max": max(values),
            "history": [{"date": item.as_of.isoformat(), "value": float(item.value)} for item in items],
            "band_status": "READY" if len(values) >= 4 else "BUILDING_HISTORY"}

    return {
        "status": "SECTOR_SPECIFIC_REQUIRED" if financial_sector else "READY",
        "instrument_id": instrument.id,
        "company_name": instrument.company_name,
        "sector": instrument.sector,
        "source": "UPSTOX",
        "as_of": max(row.as_of for row in evidence.values()).isoformat(),
        "statement_type": annual_payload.get("type", "consolidated"),
        "units": annual_payload.get("units_in", "crore"),
        "overall_score": round(sum(available_scores) / len(available_scores)) if available_scores else None,
        "scores": scores,
        "ratios": ratios,
        "metrics": {"operating_margin_pct": operating_margin, "net_margin_pct": net_margin,
            "liabilities_to_assets": leverage_proxy, "cash_conversion": cash_conversion},
        "annual_trends": {"revenue": _trend(revenue), "operating_profit": _trend(operating),
            "net_profit": _trend(net_profit), "operating_cash_flow": _trend(operating_cash)},
        "quarterly_trends": {"revenue": _trend(q_revenue), "operating_profit": _trend(q_operating),
            "net_profit": _trend(q_net)},
        "valuation": valuation,
        "governance_flags": flags,
        "governance_scope": "Automated checks cover cash-quality and promoter-holding changes only; absence of flags is not evidence of good governance.",
        "available_datasets": sorted(kind for kind in evidence if kind != "PROVIDER_ERRORS"),
        "provider_errors": (evidence.get("PROVIDER_ERRORS").payload.get("errors", [])
                            if evidence.get("PROVIDER_ERRORS") else []),
        "competitors": evidence.get("COMPETITORS").payload if evidence.get("COMPETITORS") else [],
    }
