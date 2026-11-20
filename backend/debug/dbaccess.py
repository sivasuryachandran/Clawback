"""Read-only database access for Teammate C's code (stats, Under the hood, voice, support box).

Uses Teammate A's `backend.db.query(sql, params) -> list[dict]` (section 3.4), imported lazily so
the app still starts before A has pushed `db.py` or when SingleStore is down; callers catch the
exception and show "Unavailable".

Only SELECTs go through here. `query()` refuses anything else, so a bug in a panel can never
change data (Under the hood is read-only by contract).
"""
from __future__ import annotations

import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable

_override: Callable[[str, tuple], list[dict]] | None = None   # tests inject a fake here
# Request-scoped override: sample-data mode (?mock=1) points one request at an in-memory
# database without any chance of touching other requests.
_ctx_override: ContextVar[Callable[[str, tuple], list[dict]] | None] = ContextVar("clawback_db_override", default=None)

_SELECT_ONLY = re.compile(r"^\s*(select|with)\b", re.I)


class DatabaseUnavailable(Exception):
    pass


@contextmanager
def use_override(fn: Callable[[str, tuple], list[dict]]):
    token = _ctx_override.set(fn)
    try:
        yield
    finally:
        _ctx_override.reset(token)


def query(sql: str, params: tuple = ()) -> list[dict]:
    if not _SELECT_ONLY.match(sql) or ";" in sql.strip().rstrip(";"):
        raise ValueError("Under the hood is read-only: only a single SELECT is allowed")
    fn = _ctx_override.get() or _override
    if fn is not None:
        return fn(sql, tuple(params))
    try:
        from backend import db  # Teammate A
    except Exception as exc:
        raise DatabaseUnavailable(f"backend.db is not available yet ({exc.__class__.__name__})") from exc
    return db.query(sql, tuple(params))


def jsonish(v: Any) -> Any:
    """JSON columns may arrive as str or already parsed, depending on the driver."""
    if isinstance(v, (bytes, bytearray)):
        v = v.decode("utf-8", "replace")
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (ValueError, TypeError):
            return v
    return v


def clean(v: Any) -> Any:
    """Make a DB value JSON friendly: Decimal -> float, datetime -> ISO string (UTC if naive)."""
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.isoformat() + ("Z" if v.tzinfo is None else "")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", "replace")
    return v


def clean_row(row: dict) -> dict:
    return {k: clean(v) for k, v in row.items()}


def to_dt(v: Any) -> datetime | None:
    """datetime / ISO string -> aware UTC datetime (naive values are UTC, same as the gate)."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        d = v
    else:
        try:
            d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default
