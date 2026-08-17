from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import desc, select

from .automation_runs import (
    ACTIVE_TRIGGERS,
    begin_run,
    complete_run,
    expected_scheduled_for,
    mark_stalled_runs,
    record_heartbeat,
)
from .config import settings
from .database import Base, SessionLocal, apply_additive_migrations, engine
from .main import _record_workbench_failure, _run_analysis_pipeline, _run_morning_automation
from .models import AutomationRun
from .automation_config import effective_schedule
from .schedule_health import retry_delays


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("analysis_scheduler")
scheduler: BlockingScheduler | None = None
schedule_signature: tuple | None = None


def reload_schedule() -> None:
    global schedule_signature
    if not scheduler:
        return
    db = SessionLocal()
    try:
        config = effective_schedule(db)
        signature = (config.enabled, config.days, config.hour, config.minute, config.timezone,
                     config.updated_at.isoformat())
        if signature == schedule_signature:
            return
        existing = scheduler.get_job("morning_portfolio_automation")
        if existing:
            scheduler.remove_job("morning_portfolio_automation")
        if config.enabled:
            scheduler.add_job(execute_automation_run, trigger="cron", day_of_week=config.days,
                hour=config.hour, minute=config.minute, timezone=config.timezone,
                id="morning_portfolio_automation", replace_existing=True, coalesce=True,
                max_instances=1, misfire_grace_time=settings.analysis_catchup_max_hours * 3600)
        schedule_signature = signature
        logger.info("Applied schedule configuration: enabled=%s %s %02d:%02d %s",
                    config.enabled, config.days, config.hour, config.minute, config.timezone)
    except Exception:
        db.rollback(); logger.exception("Could not reload schedule configuration")
    finally:
        db.close()


def refresh_stock_workbench() -> None:
    db = SessionLocal()
    try:
        logger.info("Starting startup stock-workbench refresh")
        payload, snapshot = _run_analysis_pipeline(db, refresh_market_data=True)
        logger.info(
            "Completed startup refresh at %s: %s owned, %s prospective, %s prices",
            snapshot.generated_at.isoformat(), len(payload["owned"]), len(payload["prospective"]),
            payload["data_refresh"]["market_prices"]["prices_imported"],
        )
    except Exception as exc:
        db.rollback()
        logger.exception("Startup stock-workbench refresh failed")
        try:
            _record_workbench_failure(db, str(exc))
        except Exception:
            db.rollback()
            logger.exception("Could not record startup-refresh failure")
    finally:
        db.close()


def heartbeat() -> None:
    db = SessionLocal()
    try:
        record_heartbeat(db)
    except Exception:
        db.rollback()
        logger.exception("Scheduler heartbeat failed")
    finally:
        db.close()


def _schedule_retry(
    scheduled_for: datetime, attempt: int, failed_actions: set[str]
) -> None:
    if not scheduler or not failed_actions:
        return
    delays = retry_delays(settings.analysis_retry_delays_minutes)
    if attempt > len(delays):
        logger.error(
            "Retry budget exhausted for %s; failed actions: %s",
            scheduled_for.isoformat(), sorted(failed_actions),
        )
        return
    next_attempt = attempt + 1
    run_at = datetime.now(timezone.utc) + timedelta(minutes=delays[attempt - 1])
    scheduler.add_job(
        execute_automation_run,
        trigger="date",
        run_date=run_at,
        args=[scheduled_for, "RETRY", next_attempt, failed_actions],
        id=f"automation_retry_{scheduled_for:%Y%m%d%H%M}_{next_attempt}",
        replace_existing=True,
        misfire_grace_time=settings.analysis_catchup_max_hours * 3600,
    )
    logger.warning(
        "Scheduled retry attempt %s at %s for: %s",
        next_attempt, run_at.isoformat(), sorted(failed_actions),
    )


def execute_automation_run(
    scheduled_for: datetime | None = None,
    trigger: str = "SCHEDULED",
    attempt: int = 1,
    only_actions: set[str] | None = None,
) -> None:
    db = SessionLocal()
    run = None
    scheduled_for = scheduled_for or expected_scheduled_for(db=db)
    try:
        run, created = begin_run(db, scheduled_for, trigger, attempt)
        if not created:
            logger.info("Skipping duplicate automation run %s", run.run_key)
            return
        logger.info(
            "Starting automation run %s (trigger=%s, attempt=%s, actions=%s)",
            run.id, trigger, attempt, sorted(only_actions) if only_actions else "all",
        )
        payload, snapshot = _run_morning_automation(db, only_actions=only_actions)
        action_ids = only_actions or {
            "market_prices", "nifty500_constituents", "financial_statements",
            "nifty500_screening", "investor_disclosures",
        }
        action_status = {
            name: payload["data_refresh"].get(name, {}).get("status", "UNKNOWN")
            for name in sorted(action_ids)
        }
        failed_actions = {name for name, status in action_status.items() if status == "FAILED"}
        status = "PARTIAL" if failed_actions else "SUCCESS"
        complete_run(db, run, status, action_status)
        logger.info(
            "Completed automation run %s at %s with %s: %s",
            run.id, snapshot.generated_at.isoformat(), status, action_status,
        )
        if failed_actions:
            _schedule_retry(scheduled_for, attempt, failed_actions)
    except Exception as exc:
        db.rollback()
        logger.exception("Portfolio automation run failed")
        if run:
            try:
                complete_run(db, run, "FAILED", {}, str(exc))
            except Exception:
                db.rollback()
                logger.exception("Could not persist automation-run failure")
        try:
            _record_workbench_failure(db, str(exc))
        except Exception:
            db.rollback()
            logger.exception("Could not record workbench failure")
        _schedule_retry(scheduled_for, attempt, only_actions or {
            "market_prices", "nifty500_constituents", "financial_statements",
            "nifty500_screening", "investor_disclosures",
        })
    finally:
        db.close()


def _latest_cycle_run(db, scheduled_for: datetime) -> AutomationRun | None:
    return db.scalar(select(AutomationRun).where(
        AutomationRun.scheduled_for == scheduled_for,
        AutomationRun.trigger.in_(ACTIVE_TRIGGERS),
    ).order_by(desc(AutomationRun.attempt), desc(AutomationRun.started_at)))


def check_and_recover() -> bool:
    """Detect missed/stalled work and enqueue one idempotent catch-up or retry."""
    if not scheduler:
        return False
    db = SessionLocal()
    try:
        stalled_ids = mark_stalled_runs(db)
        if stalled_ids:
            logger.error("Marked stalled automation runs: %s", stalled_ids)
        config = effective_schedule(db)
        if not config.enabled:
            return False
        expected = expected_scheduled_for(db=db)
        age = datetime.utcnow() - expected
        if age < timedelta(minutes=settings.analysis_start_grace_minutes):
            return False
        if age > timedelta(hours=settings.analysis_catchup_max_hours):
            return False
        latest = _latest_cycle_run(db, expected)
        if latest and latest.status in {"SUCCESS", "RUNNING"}:
            return False
        if latest and latest.status == "PARTIAL":
            failed = {name for name, status in (latest.action_status or {}).items()
                      if status == "FAILED"}
            if failed:
                max_attempts = 1 + len(retry_delays(settings.analysis_retry_delays_minutes))
                if latest.attempt >= max_attempts:
                    logger.error(
                        "Partial-run recovery exhausted for %s after %s attempts",
                        expected.isoformat(), latest.attempt,
                    )
                    return False
                scheduler.add_job(
                    execute_automation_run, trigger="date",
                    run_date=datetime.now(timezone.utc) + timedelta(seconds=2),
                    args=[expected, "RETRY", latest.attempt + 1, failed],
                    id=f"recovered_retry_{expected:%Y%m%d%H%M}", replace_existing=True,
                )
                return True
            return False
        if latest and latest.status == "FAILED":
            max_attempts = 1 + len(retry_delays(settings.analysis_retry_delays_minutes))
            if latest.attempt >= max_attempts:
                logger.error(
                    "Automatic recovery exhausted for %s after %s attempts",
                    expected.isoformat(), latest.attempt,
                )
                return False
            failed = {name for name, status in (latest.action_status or {}).items()
                      if status == "FAILED"} or {
                "market_prices", "nifty500_constituents", "financial_statements",
                "nifty500_screening", "investor_disclosures",
            }
            scheduler.add_job(
                execute_automation_run, trigger="date",
                run_date=datetime.now(timezone.utc) + timedelta(seconds=2),
                args=[expected, "RETRY", latest.attempt + 1, failed],
                id=f"recovered_failure_{expected:%Y%m%d%H%M}", replace_existing=True,
            )
            return True

        missed, created = begin_run(db, expected, "MISSED", 0)
        if created:
            complete_run(db, missed, "MISSED", {}, "Expected 06:00 run did not complete.")
        scheduler.add_job(
            execute_automation_run, trigger="date",
            run_date=datetime.now(timezone.utc) + timedelta(seconds=2),
            args=[expected, "CATCH_UP", 1, None],
            id=f"catch_up_{expected:%Y%m%d%H%M}", replace_existing=True,
            misfire_grace_time=settings.analysis_catchup_max_hours * 3600,
        )
        logger.warning("Scheduled catch-up for missed run %s", expected.isoformat())
        return True
    except Exception:
        db.rollback()
        logger.exception("Missed-run recovery check failed")
        return False
    finally:
        db.close()


def main() -> None:
    global scheduler
    Base.metadata.create_all(bind=engine)
    apply_additive_migrations()
    scheduler = BlockingScheduler(timezone=settings.analysis_schedule_timezone)
    reload_schedule()
    scheduler.add_job(
        heartbeat, trigger="interval", seconds=settings.scheduler_heartbeat_seconds,
        id="scheduler_heartbeat", replace_existing=True, coalesce=True, max_instances=1,
    )
    scheduler.add_job(
        check_and_recover, trigger="interval", minutes=5,
        id="automation_watchdog", replace_existing=True, coalesce=True, max_instances=1,
    )
    scheduler.add_job(
        reload_schedule, trigger="interval", seconds=30,
        id="schedule_config_reload", replace_existing=True, coalesce=True, max_instances=1,
    )
    heartbeat()
    recovery_scheduled = check_and_recover()
    if settings.analysis_run_on_startup and not recovery_scheduled:
        refresh_stock_workbench()
    logger.info(
        "Analysis scheduler started: %s at %02d:%02d %s; grace=%sm, stall=%sm",
        settings.analysis_schedule_days, settings.analysis_schedule_hour,
        settings.analysis_schedule_minute, settings.analysis_schedule_timezone,
        settings.analysis_start_grace_minutes, settings.analysis_stall_minutes,
    )
    scheduler.start()


if __name__ == "__main__":
    main()
