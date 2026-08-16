from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.encoders import jsonable_encoder
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .models import AnalysisSnapshot, AutomationRun
from .schedule_health import latest_expected_run


ACTIVE_TRIGGERS = ("SCHEDULED", "CATCH_UP", "RETRY")
TRACKED_TRIGGERS = (*ACTIVE_TRIGGERS, "MISSED")


def expected_scheduled_for(now: datetime | None = None) -> datetime:
    return latest_expected_run(
        now or datetime.now(timezone.utc),
        settings.analysis_schedule_days,
        settings.analysis_schedule_hour,
        settings.analysis_schedule_minute,
        settings.analysis_schedule_timezone,
    )


def begin_run(
    db: Session, scheduled_for: datetime, trigger: str, attempt: int = 1
) -> tuple[AutomationRun, bool]:
    scheduled_for = scheduled_for.replace(tzinfo=None)
    run_key = f"{trigger}:{scheduled_for.isoformat()}:{attempt}"
    existing = db.scalar(select(AutomationRun).where(AutomationRun.run_key == run_key))
    if existing:
        return existing, False
    item = AutomationRun(
        run_key=run_key, scheduled_for=scheduled_for, trigger=trigger,
        attempt=attempt, status="RUNNING", started_at=datetime.utcnow(), action_status={},
    )
    db.add(item)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return db.scalar(select(AutomationRun).where(AutomationRun.run_key == run_key)), False
    db.refresh(item)
    return item, True


def complete_run(
    db: Session, item: AutomationRun, status: str, actions: dict, error: str | None = None
) -> AutomationRun:
    item.status = status
    item.completed_at = datetime.utcnow()
    item.action_status = jsonable_encoder(actions)
    item.error = error[:4000] if error else None
    db.commit()
    db.refresh(item)
    return item


def record_heartbeat(db: Session) -> None:
    item = db.scalar(select(AnalysisSnapshot).where(
        AnalysisSnapshot.snapshot_key == "automation:scheduler_heartbeat"
    ))
    now = datetime.utcnow()
    if not item:
        item = AnalysisSnapshot(snapshot_key="automation:scheduler_heartbeat")
        db.add(item)
    item.generated_at = now
    item.last_attempted_at = now
    item.last_status = "SUCCESS"
    item.last_error = None
    item.payload = {"heartbeat_at": now.isoformat()}
    db.commit()


def mark_stalled_runs(db: Session, now: datetime | None = None) -> list[int]:
    now = (now or datetime.utcnow()).replace(tzinfo=None)
    cutoff = now - timedelta(minutes=settings.analysis_stall_minutes)
    rows = db.scalars(select(AutomationRun).where(
        AutomationRun.status == "RUNNING", AutomationRun.started_at < cutoff
    )).all()
    for item in rows:
        item.status = "STALLED"
        item.completed_at = now
        item.error = (
            f"Run exceeded the configured {settings.analysis_stall_minutes}-minute limit."
        )
    if rows:
        db.commit()
    return [item.id for item in rows]


def schedule_health(db: Session, now: datetime | None = None) -> dict:
    aware_now = now or datetime.now(timezone.utc)
    now_utc = aware_now.astimezone(timezone.utc).replace(tzinfo=None)
    expected = expected_scheduled_for(aware_now)
    grace_deadline = expected + timedelta(minutes=settings.analysis_start_grace_minutes)
    runs = db.scalars(select(AutomationRun).where(
        AutomationRun.scheduled_for == expected,
        AutomationRun.trigger.in_(TRACKED_TRIGGERS),
    ).order_by(desc(AutomationRun.attempt), desc(AutomationRun.started_at))).all()
    latest = runs[0] if runs else None
    heartbeat = db.scalar(select(AnalysisSnapshot).where(
        AnalysisSnapshot.snapshot_key == "automation:scheduler_heartbeat"
    ))
    heartbeat_at = heartbeat.generated_at if heartbeat else None
    heartbeat_stale = (
        not heartbeat_at or
        heartbeat_at < now_utc - timedelta(seconds=settings.scheduler_heartbeat_seconds * 3)
    )

    if latest and latest.status == "RUNNING":
        age_minutes = (now_utc - latest.started_at).total_seconds() / 60
        status = "STALLED" if age_minutes > settings.analysis_stall_minutes else "RUNNING"
    elif latest and latest.status == "SUCCESS":
        status = "HEALTHY"
    elif latest and latest.status == "PARTIAL":
        status = "DEGRADED"
    elif latest and latest.status in {"FAILED", "MISSED", "STALLED"}:
        status = latest.status
    elif now_utc > grace_deadline:
        status = "OVERDUE"
    else:
        status = "WAITING"
    if heartbeat_stale and status not in {"OVERDUE", "FAILED", "MISSED", "STALLED"}:
        status = "HEARTBEAT_STALE"

    last_success = db.scalar(select(AutomationRun).where(
        AutomationRun.status == "SUCCESS",
        AutomationRun.trigger.in_(ACTIVE_TRIGGERS),
    ).order_by(desc(AutomationRun.completed_at)))
    return {
        "status": status,
        "expected_scheduled_for": expected.isoformat(),
        "grace_deadline": grace_deadline.isoformat(),
        "heartbeat_at": heartbeat_at.isoformat() if heartbeat_at else None,
        "heartbeat_stale": heartbeat_stale,
        "latest_run": automation_run_out(latest) if latest else None,
        "last_successful_run": automation_run_out(last_success) if last_success else None,
        "start_grace_minutes": settings.analysis_start_grace_minutes,
        "stall_minutes": settings.analysis_stall_minutes,
    }


def automation_run_out(item: AutomationRun | None) -> dict | None:
    if not item:
        return None
    action_status = item.action_status or {}
    if item.status == "SUCCESS":
        action_status = {
            name: "SUCCESS" if status == "UNKNOWN" else status
            for name, status in action_status.items()
        }
    return {
        "id": item.id, "run_key": item.run_key,
        "scheduled_for": item.scheduled_for.isoformat(), "trigger": item.trigger,
        "attempt": item.attempt, "status": item.status,
        "started_at": item.started_at.isoformat() if item.started_at else None,
        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
        "action_status": action_status, "error": item.error,
    }
