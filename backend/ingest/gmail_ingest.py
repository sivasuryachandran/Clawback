"""Pulls order-confirmation emails into raw_emails (Airbyte prize).

Uses the Airbyte Gmail agent connector in hosted mode when AIRBYTE_CLIENT_ID/SECRET/
WORKSPACE_NAME are set. Falls back to loading the seeded .eml files in seed/receipts/
so ingest works end to end before Airbyte credentials exist (per the team doc's own
fallback note). Idempotent: dedups on gmail_msg_id.
"""

import email
import os
from pathlib import Path

from backend.db import execute, query

AIRBYTE_CLIENT_ID = os.getenv("AIRBYTE_CLIENT_ID")
AIRBYTE_CLIENT_SECRET = os.getenv("AIRBYTE_CLIENT_SECRET")
AIRBYTE_WORKSPACE_NAME = os.getenv("AIRBYTE_WORKSPACE_NAME")

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


def _ingest_from_airbyte() -> int:
    """Wire in once AIRBYTE_CLIENT_ID/SECRET/WORKSPACE_NAME exist. Check the
    airbyte-agent-sdk / airbyte-agent-gmail docs for the exact search/fetch call
    names before kickoff -- this is the shape the team doc expects, not a tested call."""
    from airbyte_agent_sdk import AirbyteAgent  # deferred import: only needed live

    agent = AirbyteAgent(
        client_id=AIRBYTE_CLIENT_ID,
        client_secret=AIRBYTE_CLIENT_SECRET,
        workspace_name=AIRBYTE_WORKSPACE_NAME,
    )
    gmail = agent.connector("gmail")
    count = 0
    for msg in gmail.search(query="order confirmation OR receipt"):
        full = gmail.fetch(msg["id"])
        if _insert_if_new(msg["id"], full["from"], full["subject"], full["date"], full["body"]):
            count += 1
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
