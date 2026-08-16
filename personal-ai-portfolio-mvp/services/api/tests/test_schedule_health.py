from datetime import datetime, timezone

from app.schedule_health import latest_expected_run, retry_delays


def test_latest_expected_run_respects_tuesday_to_saturday_ist():
    # Sunday evening in India should point back to Saturday 06:00 IST.
    now = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
    assert latest_expected_run(now, "tue-sat", 6, 0, "Asia/Kolkata") == datetime(
        2026, 8, 15, 0, 30
    )


def test_retry_delay_parser_is_configurable():
    assert retry_delays("15, 45") == [15, 45]
