from datetime import date

from app.automation_schedule import (
    latest_disclosure_period_due,
    latest_date_in_payload,
    latest_financial_period_due,
    latest_universe_boundary,
)


def test_financial_period_waits_for_reporting_deadline():
    assert latest_financial_period_due(date(2026, 8, 13)) == date(2026, 3, 31)
    assert latest_financial_period_due(date(2026, 8, 14)) == date(2026, 6, 30)
    assert latest_financial_period_due(date(2026, 5, 29)) == date(2025, 12, 31)
    assert latest_financial_period_due(date(2026, 5, 30)) == date(2026, 3, 31)


def test_disclosure_period_uses_configured_lag():
    assert latest_disclosure_period_due(date(2026, 7, 20), 21) == date(2026, 3, 31)
    assert latest_disclosure_period_due(date(2026, 7, 21), 21) == date(2026, 6, 30)


def test_universe_boundary_is_semiannual_by_default():
    assert latest_universe_boundary(date(2026, 8, 16)) == date(2026, 4, 1)
    assert latest_universe_boundary(date(2026, 10, 1)) == date(2026, 10, 1)


def test_latest_date_is_found_in_nested_provider_payload():
    payload = {"rows": [
        {"period": "2026-03-31"}, {"period": "30-06-2026"}, {"period": "Sep 2025"}
    ]}
    assert latest_date_in_payload(payload) == date(2026, 6, 30)


def test_upstox_month_label_maps_to_period_end():
    assert latest_date_in_payload({"period": "Jun 2026"}) == date(2026, 6, 30)
