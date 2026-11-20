"""ONE CLOCK, agreed with Siva's gate (backend/pact uses datetime.utcnow(), naive UTC):

  now()   = CLAWBACK_NOW (ISO datetime, naive UTC) if set, else the REAL current UTC time.
  today() = CLAWBACK_TODAY if set, else now().date()  (the UTC date, same as the gate's ctx.today).

On event day leave BOTH unset, so B and the gate read the same real clock. Note the UTC date
flips at 4 PM PT (00:00 UTC), right around code freeze: windows then count from the 21st.
Tests set CLAWBACK_NOW explicitly so they stay deterministic.
"""
import os
from datetime import date, datetime


def now() -> datetime:
    raw = os.environ.get("CLAWBACK_NOW")
    if raw:
        return datetime.fromisoformat(raw)
    return datetime.utcnow().replace(microsecond=0)


def today() -> date:
    raw = os.environ.get("CLAWBACK_TODAY")
    return date.fromisoformat(raw) if raw else now().date()


def pinned() -> bool:
    """True when the clock is overridden (preflight warns: the gate does not read these)."""
    return bool(os.environ.get("CLAWBACK_NOW") or os.environ.get("CLAWBACK_TODAY"))


def ts(dt: datetime | None = None) -> str:
    """Portable DATETIME literal for both SQLite and SingleStore."""
    return (dt or now()).strftime("%Y-%m-%d %H:%M:%S")
