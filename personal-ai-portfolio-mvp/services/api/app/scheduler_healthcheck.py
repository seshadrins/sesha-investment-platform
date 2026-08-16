from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .config import settings
from .database import SessionLocal
from .models import AnalysisSnapshot


def main() -> None:
    db = SessionLocal()
    try:
        heartbeat = db.scalar(select(AnalysisSnapshot).where(
            AnalysisSnapshot.snapshot_key == "automation:scheduler_heartbeat"
        ))
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            seconds=settings.scheduler_heartbeat_seconds * 3
        )
        healthy = bool(heartbeat and heartbeat.generated_at and heartbeat.generated_at >= cutoff)
    except Exception:
        healthy = False
    finally:
        db.close()
    sys.exit(0 if healthy else 1)


if __name__ == "__main__":
    main()
