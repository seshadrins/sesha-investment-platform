from datetime import datetime, timezone

from app.schedule_health import latest_expected_run, retry_delays
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.automation_config import effective_schedule, update_schedule
from app.automation_runs import (
    automation_metrics,
    begin_run,
    begin_scoped_run,
    complete_run,
    find_active_run,
    schedule_health,
)
from app.models import AppNotification, AutomationRun


def test_latest_expected_run_respects_tuesday_to_saturday_ist():
    # Sunday evening in India should point back to Saturday 06:00 IST.
    now = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
    assert latest_expected_run(now, "tue-sat", 6, 0, "Asia/Kolkata") == datetime(
        2026, 8, 15, 0, 30
    )


def test_retry_delay_parser_is_configurable():
    assert retry_delays("15, 45") == [15, 45]


def test_persisted_schedule_can_be_edited_and_disabled():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine); db = sessionmaker(bind=engine)()
    config = update_schedule(db, enabled=False, days="mon-fri", hour=7, minute=15,
                             timezone_name="Asia/Kolkata")
    assert effective_schedule(db).id == config.id
    assert schedule_health(db, datetime(2026, 8, 17, 3, 0, tzinfo=timezone.utc))["status"] == "DISABLED"


def test_metrics_and_failed_run_notification(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine); db = sessionmaker(bind=engine)()
    monkeypatch.setattr("app.config.settings.notification_webhook_url", None)
    run, _ = begin_run(db, datetime(2026, 8, 17, 0, 30), "SCHEDULED")
    complete_run(db, run, "FAILED", {"market_prices": "FAILED"}, "provider unavailable")
    metrics = automation_metrics(db)
    assert metrics["statuses"]["FAILED"] == 1
    assert metrics["activities"]["market_prices"]["failures"] == 1
    assert db.query(AppNotification).filter_by(category="AUTOMATION").count() == 1


def test_two_forced_triggers_for_the_same_scope_collide_on_run_key():
    # Regression for the fresh-timestamp run_key bug: two concurrent triggers for the
    # same intended activity/window must resolve to the same run_key, so the second one
    # is recognized as a duplicate instead of starting a second concurrent pass.
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine); db = sessionmaker(bind=engine)()
    scheduled_for = datetime(2026, 8, 17, 0, 30)

    first, started_first = begin_scoped_run(db, "FORCED_DISCLOSURES", scheduled_for)
    assert started_first is True
    assert first.status == "RUNNING"

    second, started_second = begin_scoped_run(db, "FORCED_DISCLOSURES", scheduled_for)
    assert started_second is False
    assert second.id == first.id
    assert second.run_key == first.run_key


def test_a_completed_forced_run_does_not_block_a_later_legitimate_rerun():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine); db = sessionmaker(bind=engine)()
    scheduled_for = datetime(2026, 8, 17, 0, 30)

    first, _ = begin_scoped_run(db, "FORCED_DISCLOSURES", scheduled_for)
    complete_run(db, first, "SUCCESS", {"investor_disclosures": "SUCCESS"})

    second, started = begin_scoped_run(db, "FORCED_DISCLOSURES", scheduled_for)
    assert started is True
    assert second.id != first.id
    assert second.run_key != first.run_key


def test_find_active_run_only_reports_running_rows_for_the_matching_trigger_family():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine); db = sessionmaker(bind=engine)()
    scheduled_for = datetime(2026, 8, 17, 0, 30)

    assert find_active_run(db, scheduled_for, ("SCHEDULED", "CATCH_UP", "RETRY")) is None

    scheduled_run, _ = begin_run(db, scheduled_for, "SCHEDULED")
    active = find_active_run(db, scheduled_for, ("SCHEDULED", "CATCH_UP", "RETRY"))
    assert active is not None and active.id == scheduled_run.id

    # A different trigger family (e.g. a forced run's own scope) doesn't see it.
    assert find_active_run(db, scheduled_for, ("FORCED_DISCLOSURES",)) is None

    complete_run(db, scheduled_run, "SUCCESS", {})
    assert find_active_run(db, scheduled_for, ("SCHEDULED", "CATCH_UP", "RETRY")) is None
