from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from .config import settings
from .models import AutomationScheduleConfig


class AutomationConfigError(ValueError):
    pass


def effective_schedule(db: Session) -> AutomationScheduleConfig:
    item = db.get(AutomationScheduleConfig, 1)
    if item:
        return item
    item = AutomationScheduleConfig(id=1, enabled=True, days=settings.analysis_schedule_days,
        hour=settings.analysis_schedule_hour, minute=settings.analysis_schedule_minute,
        timezone=settings.analysis_schedule_timezone)
    db.add(item)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return db.get(AutomationScheduleConfig, 1)
    db.refresh(item)
    return item


def validate_schedule(days: str, hour: int, minute: int, timezone_name: str) -> None:
    try:
        ZoneInfo(timezone_name)
        CronTrigger(day_of_week=days, hour=hour, minute=minute, timezone=timezone_name)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise AutomationConfigError(f"Invalid schedule: {exc}") from exc


def update_schedule(db: Session, *, enabled: bool, days: str, hour: int,
                    minute: int, timezone_name: str) -> AutomationScheduleConfig:
    days, timezone_name = days.strip().lower(), timezone_name.strip()
    validate_schedule(days, hour, minute, timezone_name)
    item = effective_schedule(db)
    item.enabled, item.days, item.hour, item.minute = enabled, days, hour, minute
    item.timezone, item.updated_at = timezone_name, datetime.utcnow()
    db.commit(); db.refresh(item)
    return item


def schedule_out(item: AutomationScheduleConfig) -> dict:
    return {"enabled": item.enabled, "days": item.days, "hour": item.hour,
            "minute": item.minute, "timezone": item.timezone,
            "updated_at": item.updated_at.isoformat()}
