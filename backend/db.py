"""Shared DB contract (section 3.4): query()/execute()/connection().

Uses SingleStore when SINGLESTORE_URL is set. With no SINGLESTORE_URL, falls back to a
local SQLite file (CLAWBACK_DB, default dev/clawback.db -- same path the rehearsal stub
used) so the rest of the team can run everything with no SingleStore credentials.
Callers write portable SQL with %s placeholders; only this module translates them to
SQLite's "?" when SingleStore isn't configured.

execute() always returns cur.lastrowid, and connection() always returns a brand-new
DB-API connection (B closes it after execute_once, so it must never be shared) --
matches backend/pact/devkit.py's stand-in, which the rest of the team built against.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

IntegrityError = sqlite3.IntegrityError

_SCHEMA_DIR = Path(__file__).resolve().parent


def _singlestore_url() -> str | None:
    return os.environ.get("SINGLESTORE_URL")


def using_singlestore() -> bool:
    return bool(_singlestore_url())


def _sqlite_path() -> str:
    path = os.environ.get("CLAWBACK_DB", os.path.join("dev", "clawback.db"))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    return path


def connection():
    """DB-API connection, for execute_once's conditional-update pattern."""
    if using_singlestore():
        import time
        import singlestoredb as s2
        # SingleStore Helios sometimes fails a connection with 1042 ("Unable to get address information for host
        # <client ip>.<isp>"): a server-side reverse-DNS lookup of OUR public IP that works on the next try. Retry briefly.
        last = None
        for attempt in range(8):
            try:
                return s2.connect(_singlestore_url(), connect_timeout=10)
            except s2.exceptions.OperationalError as exc:
                last = exc
                code = exc.args[0] if exc.args else None
                if code not in (1042, 2003, 2013) or attempt == 7:
                    raise
                time.sleep(0.4 * (attempt + 1))
        raise last  # pragma: no cover
    conn = sqlite3.connect(_sqlite_path(), timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _adapt(sql: str) -> str:
    return sql if using_singlestore() else sql.replace("%s", "?")


import threading

_local = threading.local()          # one reusable SingleStore connection per thread (a new TLS connection costs about a second)


def _shared():
    c = getattr(_local, "conn", None)
    if c is None:
        c = _local.conn = connection()
    return c


def _drop_shared():
    c = getattr(_local, "conn", None)
    _local.conn = None
    try:
        if c is not None:
            c.close()
    except Exception:
        pass


def _run_singlestore(fn):
    """Run fn(conn) on the thread's shared connection; on a dead connection reconnect once and retry."""
    try:
        return fn(_shared())
    except Exception as exc:
        if type(exc).__name__ not in ("OperationalError", "InterfaceError", "InternalError", "DatabaseError"):
            raise
        _drop_shared()
        return fn(_shared())


def query(sql: str, params: tuple = ()) -> list[dict]:
    if using_singlestore():
        def go(conn):
            cur = conn.cursor()
            cur.execute(_adapt(sql), params)
            cols = [d[0] for d in cur.description] if cur.description else []
            return [dict(zip(cols, r)) for r in cur.fetchall()]
        return _run_singlestore(go)
    conn = connection()
    try:
        cur = conn.cursor()
        cur.execute(_adapt(sql), params)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def execute(sql: str, params: tuple = ()) -> int:
    """Returns lastrowid, matching backend/pact/devkit.py's stand-in exactly (the
    whole team built against it)."""
    if using_singlestore():
        def go(conn):
            cur = conn.cursor()
            cur.execute(_adapt(sql), params)
            conn.commit()
            return cur.lastrowid
        return _run_singlestore(go)
    conn = connection()
    try:
        cur = conn.cursor()
        cur.execute(_adapt(sql), params)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def init_schema() -> None:
    """Applies schema.sql (SingleStore) or schema_sqlite.sql (local fallback).
    Idempotent: both use CREATE TABLE IF NOT EXISTS."""
    schema_file = "schema.sql" if using_singlestore() else "schema_sqlite.sql"
    sql_text = (_SCHEMA_DIR / schema_file).read_text()
    # Strip "-- ..." comments: several contain a literal ";" (type-translation notes),
    # which would otherwise split a CREATE TABLE statement in half.
    sql_text = "\n".join(line.split("--", 1)[0] for line in sql_text.splitlines())
    conn = connection()
    try:
        cur = conn.cursor()
        for statement in sql_text.split(";"):
            statement = statement.strip()
            if statement:
                cur.execute(statement)
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    init_schema()
    print(f"Schema applied ({'SingleStore' if using_singlestore() else f'SQLite at {_sqlite_path()}'}).")
