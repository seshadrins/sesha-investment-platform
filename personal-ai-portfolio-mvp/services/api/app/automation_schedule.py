from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Any


QUARTER_ENDS = ((3, 31), (6, 30), (9, 30), (12, 31))


def _quarter_ends_around(value: date) -> list[date]:
    periods = []
    for year in (value.year - 1, value.year):
        periods.extend(date(year, month, day) for month, day in QUARTER_ENDS)
    return periods


def latest_financial_period_due(value: date) -> date | None:
    """Latest results period whose SEBI filing window has elapsed.

    March year-end results use 60 days; the other quarters use 45 days.
    The function defines when the scheduler starts checking, not a claim that a
    particular issuer filed exactly on the deadline.
    """
    due = []
    for period in _quarter_ends_around(value):
        filing_days = 60 if period.month == 3 else 45
        if period + timedelta(days=filing_days) <= value:
            due.append(period)
    return max(due, default=None)


def latest_disclosure_period_due(value: date, lag_days: int = 21) -> date | None:
    """Latest shareholding quarter whose configured filing lag has elapsed."""
    due = [period for period in _quarter_ends_around(value)
           if period + timedelta(days=lag_days) <= value]
    return max(due, default=None)


def latest_universe_boundary(value: date, months: str = "4,10") -> date:
    """Most recent configured index-membership refresh boundary."""
    configured = sorted({int(month.strip()) for month in months.split(",") if month.strip()})
    if not configured or any(month < 1 or month > 12 for month in configured):
        raise ValueError("UNIVERSE_SCHEDULE_MONTHS must contain calendar months from 1 to 12.")
    candidates = [date(year, month, 1) for year in (value.year - 1, value.year)
                  for month in configured if date(year, month, 1) <= value]
    return max(candidates)


def next_quarter_start(value: date) -> date:
    next_month = ((value.month - 1) // 3 + 1) * 3 + 1
    if next_month > 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, next_month, 1)


def latest_date_in_payload(payload: Any) -> date | None:
    """Find the latest recognizable reporting date in nested provider evidence."""
    found: list[date] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str):
            text = value.strip()
            for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%Y"):
                try:
                    found.append(date.fromisoformat(text[:10]) if fmt == "%Y-%m-%d"
                                 else datetime.strptime(text[:10], fmt).date())
                    return
                except ValueError:
                    continue
            for fmt in ("%b %Y", "%B %Y"):
                try:
                    parsed = datetime.strptime(text, fmt)
                    found.append(date(
                        parsed.year, parsed.month, monthrange(parsed.year, parsed.month)[1]
                    ))
                    return
                except ValueError:
                    continue

    visit(payload)
    return max(found, default=None)
def disclosure_coverage_state(*, remaining_mappings: int, failed_mappings: int,
                              parser_failures: int, pending_aliases: int,
                              ingestion_enabled: bool, missing_investors: int) -> str:
    # ACTION_REQUIRED is reserved for genuine failures — a disabled pipeline, a source that
    # errored, or a parser crash. Pending alias reviews are routine, self-clearing work (an
    # ambiguous shareholder name sitting in the review queue exactly as designed) and get
    # their own calmer REVIEW_PENDING state instead of being badged like a real failure.
    if not ingestion_enabled or failed_mappings or parser_failures:
        return "ACTION_REQUIRED"
    if pending_aliases:
        return "REVIEW_PENDING"
    if remaining_mappings:
        return "IN_PROGRESS"
    if missing_investors:
        return "NO_ATTRIBUTABLE_DISCLOSURE"
    return "CURRENT"
