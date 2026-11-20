"""Airbyte CORE (self-hosted, on the demo laptop) -> raw_emails -> parse.   (Teammate B, agreed with A)

Airbyte Cloud signups are closed today, so Siva runs Airbyte Core locally (UI http://localhost:8100)
with the Gmail SOURCE syncing into a DESTINATION. This module reads what that sync wrote and
inserts it into raw_emails (gmail_msg_id, from_addr, subject, received_at, body, parsed=0),
skipping ids already present, then calls A's parse_unparsed_emails().

    python -m backend.ingest.airbyte_core          # prints exactly what it read, or why it fell back

Config ONLY from the environment (.env, git-ignored):
  AIRBYTE_CORE_URL            default http://localhost:8100 (only checked for reachability; printed)
  AIRBYTE_CORE_CLIENT_ID / AIRBYTE_CORE_CLIENT_SECRET   accepted for the API; not needed to READ a destination
  AIRBYTE_CORE_DEST           where the Gmail sync landed. One of:
      jsonl:<file or folder>        Local JSON destination (*.jsonl; records may be wrapped in _airbyte_data)
      sqlite:<file>                 SQLite destination
      duckdb:<file>                 DuckDB destination (needs `pip install duckdb`)
      postgresql://user:pw@host/db  Postgres destination (needs `pip install psycopg`)
  AIRBYTE_CORE_STREAM         table / file stem of the Gmail messages stream (default: messages)

Gmail records are read in either shape: the Gmail API message (id, threadId, payload.headers,
payload.parts[].body.data base64url, internalDate) or already-flat columns (from, subject, body).
If the destination isn't configured, reachable or readable, it FALLS BACK to seed/receipts/*.eml
and says so loudly (result["source"] == "seed_fallback" with the reason). No silent fakes.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from backend.ingest.gmail_ingest import _header, _ingest_from_seed, _insert_if_new, _plain_text_body


class DestinationUnavailable(Exception):
    pass


def config() -> dict:
    return {
        "url": os.environ.get("AIRBYTE_CORE_URL", "http://localhost:8100"),
        "dest": (os.environ.get("AIRBYTE_CORE_DEST") or "").strip(),
        "stream": (os.environ.get("AIRBYTE_CORE_STREAM") or "messages").strip(),
        "has_api_credentials": bool(os.environ.get("AIRBYTE_CORE_CLIENT_ID") and os.environ.get("AIRBYTE_CORE_CLIENT_SECRET")),
    }


def reachable(url: str, timeout: float = 3.0) -> bool:
    """True if anything answers HTTP at the Airbyte Core URL (status code irrelevant)."""
    import httpx
    try:
        httpx.get(url, timeout=timeout, follow_redirects=False)
        return True
    except Exception:
        return False


# ---------- destination readers: each yields raw records (dicts) ----------

def _unwrap(rec: dict) -> dict:
    data = rec.get("_airbyte_data", rec.get("data") if "_airbyte_ab_id" in rec else None)
    if data is None:
        return rec
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return rec
    return data if isinstance(data, dict) else rec


def _read_jsonl(target: str, stream: str) -> Iterator[dict]:
    p = Path(os.path.expandvars(target)).expanduser()   # seed/airbyte_demo/README uses ~/airbyte-local/...
    if p.is_dir():
        files = sorted(f for f in p.rglob("*.jsonl") if stream in f.stem)
    elif p.is_file():
        files = [p]
    else:
        raise DestinationUnavailable(f"no such file or folder: {p}")
    if not files:
        raise DestinationUnavailable(f"no *.jsonl for stream '{stream}' under {p}")
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield _unwrap(json.loads(line))


def _tables_like(names: Iterable[str], stream: str) -> list[str]:
    names = list(names)
    exact = [n for n in names if n.lower() in (stream, f"_airbyte_raw_{stream}")]
    return exact or [n for n in names if stream in n.lower()]


def _rows_as_dicts(cursor, sql: str) -> Iterator[dict]:
    cursor.execute(sql)
    cols = [c[0] for c in cursor.description]
    for row in cursor.fetchall():
        yield _unwrap(dict(zip(cols, row)))


def _read_sqlite(target: str, stream: str) -> Iterator[dict]:
    target = str(Path(os.path.expandvars(target)).expanduser())
    if not Path(target).is_file():
        raise DestinationUnavailable(f"no SQLite file at {target}")
    con = sqlite3.connect(target)
    try:
        names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        tables = _tables_like(names, stream)
        if not tables:
            raise DestinationUnavailable(f"no table for stream '{stream}' in {target} (tables: {names})")
        yield from _rows_as_dicts(con.cursor(), f'SELECT * FROM "{tables[0]}"')
    finally:
        con.close()


def _read_duckdb(target: str, stream: str) -> Iterator[dict]:
    try:
        import duckdb  # optional
    except ImportError as e:
        raise DestinationUnavailable("duckdb destination needs `pip install duckdb`") from e
    con = duckdb.connect(target, read_only=True)
    try:
        names = [r[0] for r in con.execute("SELECT table_schema || '.' || table_name FROM information_schema.tables").fetchall()]
        tables = _tables_like(names, stream)
        if not tables:
            raise DestinationUnavailable(f"no table for stream '{stream}' in {target}")
        yield from _rows_as_dicts(con.cursor(), f"SELECT * FROM {tables[0]}")
    finally:
        con.close()


def _read_postgres(url: str, stream: str) -> Iterator[dict]:
    try:
        import psycopg  # optional
    except ImportError as e:
        raise DestinationUnavailable("postgres destination needs `pip install psycopg`") from e
    with psycopg.connect(url, connect_timeout=5) as con:
        cur = con.cursor()
        cur.execute("SELECT table_schema || '.' || table_name FROM information_schema.tables "
                    "WHERE table_schema NOT IN ('pg_catalog','information_schema')")
        tables = _tables_like([r[0] for r in cur.fetchall()], stream)
        if not tables:
            raise DestinationUnavailable(f"no table for stream '{stream}' in the Postgres destination")
        yield from _rows_as_dicts(cur, f"SELECT * FROM {tables[0]}")


def read_destination(dest: str, stream: str) -> list[dict]:
    if not dest:
        raise DestinationUnavailable("AIRBYTE_CORE_DEST is not set (ask Siva where the Gmail sync writes)")
    try:
        if dest.startswith("jsonl:"):
            return list(_read_jsonl(dest[6:], stream))
        if dest.startswith("sqlite:"):
            return list(_read_sqlite(dest[7:], stream))
        if dest.startswith("duckdb:"):
            return list(_read_duckdb(dest[7:], stream))
        if dest.startswith(("postgres://", "postgresql://")):
            return list(_read_postgres(dest, stream))
    except DestinationUnavailable:
        raise
    except Exception as e:  # unreadable file, bad credentials, network
        raise DestinationUnavailable(f"{type(e).__name__}: {e}"[:300]) from e
    raise DestinationUnavailable(f"unknown AIRBYTE_CORE_DEST form: {dest.split(':', 1)[0]}:...")


# ---------- Gmail record -> raw_emails row ----------

def _as_obj(v):
    if isinstance(v, str) and v[:1] in "{[":
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return v
    return v


def to_raw_email(rec: dict) -> dict | None:
    msg_id = rec.get("id") or rec.get("message_id") or rec.get("gmail_msg_id")
    if not msg_id:
        return None
    payload = _as_obj(rec.get("payload")) or {}
    headers = payload.get("headers") if isinstance(payload, dict) else None
    from_addr = _header(headers, "From") if headers else (rec.get("from") or rec.get("from_addr") or "")
    subject = _header(headers, "Subject") if headers else (rec.get("subject") or "")
    received = _header(headers, "Date") if headers else (rec.get("date") or rec.get("received_at") or "")
    if not received and rec.get("internalDate"):
        received = datetime.fromtimestamp(int(rec["internalDate"]) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    body = _plain_text_body(payload) if isinstance(payload, dict) and payload else ""
    body = body or rec.get("body") or rec.get("text") or rec.get("snippet") or ""
    return {"gmail_msg_id": str(msg_id), "from_addr": from_addr, "subject": subject,
            "received_at": received, "body": body}


def ingest() -> dict:
    """Read the Airbyte Core destination into raw_emails and parse. Falls back loudly."""
    from backend.ingest.parser import parse_unparsed_emails
    cfg = config()
    out = {"airbyte_core_url": cfg["url"], "airbyte_reachable": reachable(cfg["url"]),
           "dest": cfg["dest"].split("@")[-1] if cfg["dest"] else None, "stream": cfg["stream"]}
    try:
        records = read_destination(cfg["dest"], cfg["stream"])
        rows = [r for r in (to_raw_email(x) for x in records) if r]
        inserted = sum(_insert_if_new(**r) for r in rows)
        out.update(source="airbyte_core", read=len(records), usable=len(rows), inserted=inserted,
                   skipped_existing=len(rows) - inserted)
    except DestinationUnavailable as e:
        import re
        reason = re.sub(r"(\w+://)[^@/\s]+@", r"\1***@", str(e))  # never echo a password from a DSN
        inserted = _ingest_from_seed()
        out.update(source="seed_fallback", reason=reason, inserted=inserted)
        print(f"!! AIRBYTE CORE NOT USED: fell back to seed/receipts ({inserted} new). Reason: {reason}")
    out["parsed"] = parse_unparsed_emails()
    return out


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(".env")
    print(json.dumps(ingest(), indent=2, default=str))
