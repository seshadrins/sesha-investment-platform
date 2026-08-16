from __future__ import annotations

import calendar
from bisect import bisect_left
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from .financial_analysis import _categories, _find_category, _normalise, _number
from .models import Instrument, Price, ResearchSnapshot


STYLE_DIR = Path(__file__).with_name("investor_styles")
OPERATORS = {
    "gt": lambda actual, target: actual > target,
    "gte": lambda actual, target: actual >= target,
    "lt": lambda actual, target: actual < target,
    "lte": lambda actual, target: actual <= target,
    "eq": lambda actual, target: actual == target,
}


def load_styles() -> list[dict]:
    styles = []
    for path in sorted(STYLE_DIR.glob("*.yaml")):
        style = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not style.get("id") or not isinstance(style.get("rules"), list):
            raise ValueError(f"Invalid investor style: {path.name}")
        style["file"] = path.name
        styles.append(style)
    return styles


def get_style(style_id: str) -> dict:
    style = next((item for item in load_styles() if item["id"] == style_id), None)
    if not style:
        raise ValueError(f"Unknown investor style: {style_id}")
    return style


def _period_end(label: str) -> date:
    parsed = datetime.strptime(label, "%b %Y")
    return date(parsed.year, parsed.month, calendar.monthrange(parsed.year, parsed.month)[1])


def _history_map(items: list[dict]) -> dict[str, float | None]:
    return {str(item.get("period")): _number(item.get("value")) for item in items}


def point_in_time_features(db: Session, instrument_id: int, reporting_lag_days: int = 120) -> list[dict]:
    rows = db.scalars(select(ResearchSnapshot).where(
        ResearchSnapshot.instrument_id == instrument_id,
        ResearchSnapshot.provider == "UPSTOX",
        ResearchSnapshot.research_type.in_(["INCOME_ANNUAL", "CASH_FLOW", "BALANCE_SHEET"]),
    )).all()
    evidence = {row.research_type: row.payload for row in rows}
    if not all(kind in evidence for kind in ("INCOME_ANNUAL", "CASH_FLOW", "BALANCE_SHEET")):
        return []

    income = _categories(evidence["INCOME_ANNUAL"], "income_statement")
    cash = _categories(evidence["CASH_FLOW"], "cash_flow")
    revenue = _history_map(_find_category(income, "revenue"))
    operating = _history_map(_find_category(income, "operating", "profit"))
    profit = _history_map(_find_category(income, "net", "profit"))
    cfo = _history_map(_find_category(cash, "operating"))
    balance = {str(item.get("period")): item for item in evidence["BALANCE_SHEET"].get("history", [])}

    periods = sorted(revenue, key=_period_end)
    results = []
    for index, period in enumerate(periods):
        previous = periods[index - 1] if index else None
        rev, op, net, cash_value = revenue.get(period), operating.get(period), profit.get(period), cfo.get(period)
        previous_rev = revenue.get(previous) if previous else None
        previous_profit = profit.get(previous) if previous else None
        balance_row = balance.get(period, {})
        assets = _number(balance_row.get("total_asset"))
        liabilities = _number(balance_row.get("total_liability"))
        period_end = _period_end(period)
        results.append({
            "period": period,
            "period_end": period_end.isoformat(),
            "decision_date": (period_end + timedelta(days=reporting_lag_days)).isoformat(),
            "revenue": rev,
            "operating_profit": op,
            "net_profit": net,
            "operating_cash_flow": cash_value,
            "revenue_growth_pct": ((rev / previous_rev - 1) * 100
                                   if rev is not None and previous_rev not in (None, 0) else None),
            "net_profit_growth_pct": ((net / previous_profit - 1) * 100
                                      if net is not None and previous_profit not in (None, 0) else None),
            "operating_margin_pct": (op / rev * 100 if op is not None and rev not in (None, 0) else None),
            "cash_conversion": (cash_value / net if cash_value is not None and net not in (None, 0) else None),
            "liabilities_to_assets": (liabilities / assets
                                      if liabilities is not None and assets not in (None, 0) else None),
        })
    return results


def evaluate_style(style: dict, features: dict) -> dict:
    evaluated, earned, available_weight, total_weight = [], 0.0, 0.0, 0.0
    for rule in style["rules"]:
        weight = float(rule.get("weight", 1))
        total_weight += weight
        actual = features.get(rule["feature"])
        passed = None if actual is None else OPERATORS[rule["operator"]](actual, float(rule["value"]))
        if passed is not None:
            available_weight += weight
            if passed:
                earned += weight
        evaluated.append({**rule, "actual": actual, "passed": passed})
    score = earned / available_weight * 100 if available_weight else 0.0
    coverage = available_weight / total_weight * 100 if total_weight else 0.0
    matches = score >= float(style["minimum_score"]) and coverage >= float(style["minimum_coverage"])
    return {"style_id": style["id"], "style_name": style["name"], "style_version": style["version"],
            "score": round(score, 2), "coverage": round(coverage, 2), "matches": matches,
            "rules": evaluated, "features": features}


def evaluate_current_styles(db: Session, instrument: Instrument) -> list[dict]:
    points = point_in_time_features(db, instrument.id)
    if not points:
        return []
    is_financial = any(word in (instrument.sector or "").lower()
                       for word in ("bank", "financial", "finance", "insurance"))
    output = []
    for style in load_styles():
        result = evaluate_style(style, points[-1])
        result["applicable"] = not (style.get("applicability") == "non_financial" and is_financial)
        if not result["applicable"]:
            result["matches"] = False
        output.append(result)
    return output


def backtest_style(db: Session, instrument: Instrument, style: dict, horizon_days: int = 365) -> dict:
    points = point_in_time_features(db, instrument.id)
    price_rows = db.scalars(select(Price).where(Price.instrument_id == instrument.id)
                            .order_by(Price.price_date)).all()
    dates = [row.price_date for row in price_rows]

    def price_on_or_after(target: date, tolerance: int = 10):
        index = bisect_left(dates, target)
        if index < len(price_rows) and price_rows[index].price_date <= target + timedelta(days=tolerance):
            return price_rows[index]
        return None

    decisions = []
    for point in points:
        evaluation = evaluate_style(style, point)
        decision_date = date.fromisoformat(point["decision_date"])
        entry = price_on_or_after(decision_date)
        exit_price = price_on_or_after(decision_date + timedelta(days=horizon_days))
        forward_return = (float(exit_price.close_price / entry.close_price - 1)
                          if entry and exit_price else None)
        decisions.append({
            "period": point["period"], "decision_date": point["decision_date"],
            "score": evaluation["score"], "coverage": evaluation["coverage"],
            "signal": evaluation["matches"],
            "entry_date": entry.price_date.isoformat() if entry else None,
            "entry_price": float(entry.close_price) if entry else None,
            "exit_date": exit_price.price_date.isoformat() if exit_price else None,
            "exit_price": float(exit_price.close_price) if exit_price else None,
            "forward_return": forward_return,
            "rules": evaluation["rules"],
        })
    completed = [item for item in decisions if item["signal"] and item["forward_return"] is not None]
    baseline = [item for item in decisions if item["forward_return"] is not None]
    returns = [item["forward_return"] for item in completed]
    return {"instrument_id": instrument.id, "symbol": instrument.symbol,
            "style": {key: style[key] for key in ("id", "name", "version", "description")},
            "methodology": {"reporting_lag_days": 120, "horizon_days": horizon_days,
                            "price_tolerance_days": 10, "point_in_time": True},
            "summary": {"signals": sum(item["signal"] for item in decisions),
                        "completed_signals": len(completed),
                        "average_forward_return": sum(returns) / len(returns) if returns else None,
                        "positive_return_rate": sum(value > 0 for value in returns) / len(returns) if returns else None,
                        "all_period_average_return": (sum(item["forward_return"] for item in baseline) / len(baseline)
                                                      if baseline else None)},
            "decisions": decisions,
            "limitations": ["Annual fundamentals are assumed available 120 days after period end.",
                            "Results exclude taxes, fees, dividends, survivorship effects, and benchmark returns.",
                            "A small sample is illustrative and not evidence that a style will outperform."]}
