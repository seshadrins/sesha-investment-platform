from datetime import datetime, timezone

from app.schedule_health import latest_expected_run, retry_delays
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.automation_config import effective_schedule, update_schedule
from app.automation_runs import automation_metrics, complete_run, begin_run, schedule_health
from app.models import AppNotification


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
