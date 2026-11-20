"""Demo clock. Seed data is dated relative to CLAWBACK_TODAY (default 2026-11-20).

now() = CLAWBACK_TODAY at a fixed 12:00 so tests are deterministic, unless CLAWBACK_NOW
(ISO datetime) is set. Real modules tomorrow should share one clock; ask Siva which.
"""
import os
from datetime import date, datetime, time


def today() -> date:
    return date.fromisoformat(os.environ.get("CLAWBACK_TODAY", "2026-11-20"))


def now() -> datetime:
    raw = os.environ.get("CLAWBACK_NOW")
    if raw:
        return datetime.fromisoformat(raw)
    return datetime.combine(today(), time(12, 0))


def ts(dt: datetime | None = None) -> str:
    """Portable DATETIME literal for both SQLite and SingleStore."""
    return (dt or now()).strftime("%Y-%m-%d %H:%M:%S")
