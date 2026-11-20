"""send_email(decision, to, subject, body, transport, thread_id=None) -> message id

Walls, in order: decision must be ALLOW; never a real retailer's domain; in DEMO_MODE (on
unless the env var is exactly "false") `to` must be exactly in DEMO_ALLOWED_RECIPIENTS; the body
must come from a template (footer present). Then the transport sends and we store the message.
"""
import itertools
import json
import os
import uuid
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

from backend.agent import clock
from backend.agent.deps import db, track

FOOTER_MARK = "Sent by Clawback on behalf of "
SENDER = "clawback@clawback.test"


class EmailRefused(Exception):
    pass


def demo_mode() -> bool:
    return os.environ.get("DEMO_MODE") != "false"


def allowed_recipients() -> frozenset:
    raw = os.environ.get("DEMO_ALLOWED_RECIPIENTS", "")
    return frozenset(a.strip().lower() for a in raw.split(",") if a.strip())


def _retailer_domains() -> set[str]:
    out = {"target.com", "bestbuy.com", "amazon.com"}
    for r in db.query("SELECT store, support_domains FROM store_policies"):
        if r["store"] != "mockstore" and r["support_domains"]:
            doms = r["support_domains"]
            out |= set(json.loads(doms) if isinstance(doms, str) else doms)
    return out


# ---------- transports ----------

class Transport(Protocol):
    def send(self, to: str, subject: str, body: str, thread_id: str | None) -> tuple[str, str]:
        """Returns (message_id, thread_id)."""


class FileTransport:
    """Default tonight: each email becomes outbox/<timestamp>.eml."""
    _n = itertools.count(1)

    def __init__(self, folder: str | None = None):
        self.folder = Path(folder or os.environ.get("OUTBOX_DIR", "outbox"))

    def send(self, to, subject, body, thread_id):
        self.folder.mkdir(parents=True, exist_ok=True)
        msg_id = f"<{uuid.uuid4().hex}@clawback.test>"
        thread_id = thread_id or f"thr_{uuid.uuid4().hex[:12]}"
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = SENDER, to, subject
        m["Message-ID"], m["X-Clawback-Thread"] = msg_id, thread_id
        m.set_content(body)
        name = f"{datetime.now():%Y%m%d-%H%M%S-%f}-{next(self._n):03d}.eml"
        (self.folder / name).write_bytes(bytes(m))
        return msg_id, thread_id


class AirbyteGmailTransport:
    """Airbyte Gmail agent connector, hosted mode. Built from the connector's REFERENCE.md and
    AUTH.md (github.com/airbytehq/airbyte-agent-connectors, read 2026-11-20). NOT RUN live yet.

    Documented call:  execute("messages", "create", {"raw": <base64url RFC 2822>, "threadId"?})
      -> record with id and threadId.   (Python SDK: gmail.messages.create(raw=, thread_id=))
    Setup (once, by a human): `airbyte-agent login`, then
      `airbyte-agent connectors create --json '{"workspace": "<ws>", "name": "gmail"}'`
      which opens the hosted Google OAuth flow. Then in code:
      `from airbyte_agent_sdk import connect; connect("gmail", workspace_name=...)`, which reads
      AIRBYTE_CLIENT_ID / AIRBYTE_CLIENT_SECRET from the environment.
    Gotchas: airbyte-agent-gmail 0.1.31 requires Python >= 3.13 (team spec says 3.11); the
    connector can't attach files or forward. NEVER hand the connector's tools to the LLM
    (build_connector_tools): only send_email, behind its walls, may call execute().

    Thread ids: ours look like "thr_..." for a brand-new conversation; Gmail must not get those,
    so they mean "new thread" and Gmail's returned threadId is what send_email stores.
    """

    def __init__(self, connector=None):
        self._connector = connector  # tests pass a fake with an async execute()

    def _conn(self):
        if self._connector is None:
            from airbyte_agent_sdk import connect  # lazy: Python >= 3.13 package
            self._connector = connect("gmail", workspace_name=os.environ["AIRBYTE_WORKSPACE_NAME"])
        return self._connector

    def send(self, to, subject, body, thread_id):
        import asyncio
        import base64
        m = EmailMessage()
        m["To"], m["Subject"] = to, subject
        m.set_content(body)
        raw = base64.urlsafe_b64encode(bytes(m)).decode("ascii")
        params = {"raw": raw}
        if thread_id and not thread_id.startswith("thr_"):
            params["threadId"] = thread_id  # a real Gmail thread: reply in it
        res = asyncio.run(self._conn().execute("messages", "create", params))
        rec = res if isinstance(res, dict) else getattr(res, "data", None) or {}
        if isinstance(rec, list):
            rec = rec[0] if rec else {}
        msg_id, gmail_thread = rec.get("id"), rec.get("threadId")
        if not msg_id:
            raise RuntimeError(f"Airbyte Gmail send returned no message id: {str(res)[:200]}")
        return msg_id, gmail_thread or thread_id


class GmailApiTransport:
    """Fallback: Gmail API users.messages.send with threadId. NOT WIRED TONIGHT."""

    def send(self, to, subject, body, thread_id):
        raise NotImplementedError("Gmail API send not wired")


def get_transport() -> Transport:
    kind = os.environ.get("EMAIL_TRANSPORT", "file")
    if kind == "airbyte":
        return AirbyteGmailTransport()
    if kind == "gmail_api":
        return GmailApiTransport()
    return FileTransport()


# ---------- the one way out ----------

def send_email(decision, to: str, subject: str, body: str, transport, thread_id: str | None = None) -> str:
    name = getattr(decision, "decision", decision)
    if name != "ALLOW":
        raise EmailRefused(f"decision is {name}, not ALLOW")
    to_n = (to or "").strip().lower()
    if not to_n or "@" not in to_n:
        raise EmailRefused("no recipient")
    if to_n.rsplit("@", 1)[1] in _retailer_domains():
        raise EmailRefused(f"{to_n} is a real retailer; Clawback never emails retailers")
    if demo_mode() and to_n not in allowed_recipients():
        raise EmailRefused(f"demo mode: {to_n} is not on DEMO_ALLOWED_RECIPIENTS")
    if FOOTER_MARK not in body:
        raise EmailRefused("body is not from a template")
    h = getattr(decision, "proposal_hash", None)
    # Order matters for retries (execute_once retries a 'failed' action once):
    #  1. write the row BEFORE sending: if that fails, nothing went out, a retry is safe;
    #  2. send: if that fails, remove the row and raise, a retry is safe;
    #  3. after a successful send NOTHING may raise, or the action is marked failed and the
    #     retry sends a second email. Bookkeeping errors are tracked, not raised.
    thread = thread_id or f"thr_{uuid.uuid4().hex[:12]}"  # ours for a new thread
    opp = db.query("SELECT opportunity_id FROM proposals WHERE proposal_hash = %s", (h,))
    row = db.execute(
        "INSERT INTO messages (opportunity_id, proposal_hash, direction, thread_id, to_addr, "
        "subject, body, sent_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (opp[0]["opportunity_id"] if opp else None, h, "out", thread, to_n, subject, body, None))
    try:
        msg_id, sent_thread = transport.send(to_n, subject, body, thread)
    except Exception:
        try:
            db.execute("DELETE FROM messages WHERE id = %s", (row,))
        except Exception:
            pass  # the row keeps sent_at NULL, which readers treat as not sent
        raise
    try:
        # A real Gmail send may assign its own threadId for a new thread: keep the real one.
        db.execute("UPDATE messages SET thread_id = %s, sent_at = %s WHERE id = %s",
                   (sent_thread or thread, clock.ts(), row))
    except Exception as e:
        track("message_bookkeeping_failed", {"message_row": row, "error": repr(e)[:200]})
    return msg_id


def send_template(decision, p: dict, transport=None) -> str:
    """Render the proposal's template from DB facts and send it."""
    from backend.actor.templates import facts, render
    f = facts(p["opportunity_id"])
    subject, body = render(p["email"]["template"], f)
    return send_email(decision, p["email"]["to"], subject, body, transport or get_transport(),
                      thread_id=p["email"].get("thread_id"))
