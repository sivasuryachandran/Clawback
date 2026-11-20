"""Shared DB contract (section 3.4): query()/execute()/connection().

Uses SingleStore when SINGLESTORE_URL is set. With no SINGLESTORE_URL, falls back to a
local SQLite file (CLAWBACK_DB, default dev/clawback.db -- same path the rehearsal stub
used) so the rest of the team can run everything with no SingleStore credentials.
Callers write portable SQL with %s placeholders; only this module translates them to
SQLite's "?" when SingleStore isn't configured.

execute() returns cur.lastrowid for an INSERT, cur.rowcount otherwise (matches the
dev/stubs/db.py rehearsal stub everyone already built against).
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
        import singlestoredb as s2
        return s2.connect(_singlestore_url())
    conn = sqlite3.connect(_sqlite_path(), timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _adapt(sql: str) -> str:
    return sql if using_singlestore() else sql.replace("%s", "?")


def query(sql: str, params: tuple = ()) -> list[dict]:
    conn = connection()
    try:
        cur = conn.cursor()
        cur.execute(_adapt(sql), params)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall()
        if using_singlestore():
            return [dict(zip(cols, r)) for r in rows]
        return [dict(r) for r in rows]
    finally:
        conn.close()


def execute(sql: str, params: tuple = ()) -> int:
    is_insert = sql.lstrip().upper().startswith("INSERT")
    conn = connection()
    try:
        cur = conn.cursor()
        cur.execute(_adapt(sql), params)
        conn.commit()
        return cur.lastrowid if is_insert else cur.rowcount
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
