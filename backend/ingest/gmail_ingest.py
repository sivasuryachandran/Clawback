"""Pulls order-confirmation emails into raw_emails (Airbyte prize).

Uses the Airbyte Gmail agent connector in hosted mode when AIRBYTE_CLIENT_ID/SECRET/
WORKSPACE_NAME are set -- same SDK and auth as backend/actor/email.py's
AirbyteGmailTransport (airbyte-agent-sdk; connectors/gmail REFERENCE.md and AUTH.md,
github.com/airbytehq/airbyte-agent-sdk, read 2026-11-20). NOT RUN live yet: that
connector requires Python >= 3.13 (this machine only has 3.9.6).

Documented calls:
  messages.list(q=<Gmail search>, maxResults=, pageToken=) -> {id, threadId} rows + nextPageToken
  messages.get(messageId=, format="full") -> {id, threadId, labelIds, snippet, internalDate,
    payload: {headers: [{name, value}], body / parts (MIME, base64url)}}

Falls back to loading the seeded .eml files in seed/receipts/ so ingest works end to end
before Airbyte credentials exist (the team doc's own fallback note). Idempotent: dedups
on gmail_msg_id.
"""

from __future__ import annotations

import base64
import email
import os
from datetime import datetime, timezone
from pathlib import Path

from backend.db import execute, query

AIRBYTE_CLIENT_ID = os.getenv("AIRBYTE_CLIENT_ID")
AIRBYTE_CLIENT_SECRET = os.getenv("AIRBYTE_CLIENT_SECRET")
AIRBYTE_WORKSPACE_NAME = os.getenv("AIRBYTE_WORKSPACE_NAME")
GMAIL_SEARCH_QUERY = os.getenv("GMAIL_INGEST_QUERY", "subject:(order OR receipt OR shipped OR confirmation)")

SEED_DIR = Path(__file__).resolve().parent.parent.parent / "seed" / "receipts"


def _airbyte_configured() -> bool:
    return bool(AIRBYTE_CLIENT_ID and AIRBYTE_CLIENT_SECRET and AIRBYTE_WORKSPACE_NAME)


def _insert_if_new(gmail_msg_id: str, from_addr: str, subject: str, received_at: str, body: str) -> bool:
    if query("SELECT id FROM raw_emails WHERE gmail_msg_id=%s", (gmail_msg_id,)):
        return False
    execute(
        "INSERT INTO raw_emails (gmail_msg_id, from_addr, subject, received_at, body, parsed) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (gmail_msg_id, from_addr, subject, received_at, body, False),
    )
    return True


def _header(headers: list[dict], name: str) -> str:
    for h in headers or []:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _decode_part_body(part: dict) -> str:
    data = (part.get("body") or {}).get("data")
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _plain_text_body(payload: dict) -> str:
    """Walk the MIME payload (same shape as the plain Gmail REST API) for the first
    text/plain part; falls back to a top-level body, then text/html stripped of tags."""
    if not payload:
        return ""
    stack = [payload]
    html_fallback = ""
    while stack:
        part = stack.pop(0)
        mime = part.get("mimeType", "")
        if mime == "text/plain":
            text = _decode_part_body(part)
            if text:
                return text
        elif mime == "text/html" and not html_fallback:
            html_fallback = _decode_part_body(part)
        stack.extend(part.get("parts") or [])
    if html_fallback:
        import re

        return re.sub(r"<[^>]+>", " ", html_fallback)
    return _decode_part_body(payload)


def _ingest_from_airbyte() -> int:
    import asyncio

    from airbyte_agent_sdk import connect  # lazy: Python >= 3.13 package

    connector = connect("gmail", workspace_name=AIRBYTE_WORKSPACE_NAME)
    count = 0
    page_token = None
    while True:
        params = {"q": GMAIL_SEARCH_QUERY, "maxResults": 100}
        if page_token:
            params["pageToken"] = page_token
        listing = asyncio.run(connector.execute("messages", "list", params))
        rows = listing.get("data", listing) if isinstance(listing, dict) else listing
        for row in rows or []:
            msg_id = row.get("id") if isinstance(row, dict) else row
            if query("SELECT id FROM raw_emails WHERE gmail_msg_id=%s", (msg_id,)):
                continue  # skip the get() call entirely for messages we already have
            full = asyncio.run(connector.execute("messages", "get", {"messageId": msg_id, "format": "full"}))
            payload = full.get("payload", {})
            headers = payload.get("headers", [])
            received_at = full.get("internalDate")
            if received_at:
                received_at = datetime.fromtimestamp(int(received_at) / 1000, tz=timezone.utc).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            if _insert_if_new(
                gmail_msg_id=full.get("id", msg_id),
                from_addr=_header(headers, "From"),
                subject=_header(headers, "Subject"),
                received_at=received_at or "",
                body=_plain_text_body(payload) or full.get("snippet", ""),
            ):
                count += 1
        meta = listing.get("meta", {}) if isinstance(listing, dict) else {}
        page_token = meta.get("nextPageToken") or listing.get("nextPageToken") if isinstance(listing, dict) else None
        if not page_token:
            break
    return count


def _ingest_from_seed() -> int:
    if not SEED_DIR.exists():
        return 0
    count = 0
    for eml_path in sorted(SEED_DIR.glob("*.eml")):
        msg = email.message_from_bytes(eml_path.read_bytes())
        if msg.is_multipart():
            body = ""
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    body = part.get_payload(decode=True).decode(errors="replace")
                    break
        else:
            payload = msg.get_payload(decode=True)
            body = payload.decode(errors="replace") if payload else msg.get_payload()
        if _insert_if_new(
            gmail_msg_id=eml_path.stem,
            from_addr=msg.get("From", ""),
            subject=msg.get("Subject", ""),
            received_at=msg.get("Date", ""),
            body=body,
        ):
            count += 1
    return count


def ingest_new_emails() -> int:
    if _airbyte_configured():
        try:
            return _ingest_from_airbyte()
        except Exception:
            pass  # don't let a live-API hiccup block the demo; fall back to seed data
    return _ingest_from_seed()
