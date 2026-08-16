from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger


def scheduled_trigger(days: str, hour: int, minute: int, timezone_name: str) -> CronTrigger:
    return CronTrigger(
        day_of_week=days, hour=hour, minute=minute, timezone=ZoneInfo(timezone_name)
    )


def latest_expected_run(
    now: datetime, days: str, hour: int, minute: int, timezone_name: str
) -> datetime:
    """Return the latest cron fire time at or before ``now`` as naive UTC."""
    zone = ZoneInfo(timezone_name)
    local_now = now.astimezone(zone) if now.tzinfo else now.replace(tzinfo=timezone.utc).astimezone(zone)
    trigger = scheduled_trigger(days, hour, minute, timezone_name)
    cursor = local_now - timedelta(days=8)
    candidate = trigger.get_next_fire_time(None, cursor)
    latest = None
    while candidate and candidate <= local_now:
        latest = candidate
        candidate = trigger.get_next_fire_time(candidate, candidate)
    if latest is None:
        raise RuntimeError("Could not calculate the latest configured automation run.")
    return latest.astimezone(timezone.utc).replace(tzinfo=None)


def retry_delays(value: str) -> list[int]:
    delays = [int(item.strip()) for item in value.split(",") if item.strip()]
    if any(delay <= 0 for delay in delays):
        raise ValueError("ANALYSIS_RETRY_DELAYS_MINUTES must contain positive integers.")
    return delays
