"""Mock Store support desk -> Teammate B's POST /replies/inbound (the "try to fool it" box).

The text typed here is UNTRUSTED. We never interpret it; we forward it verbatim as data and B's
reply handler decides (the gate then stops anything unsafe, e.g. R13 / R5).

Payload sent to /replies/inbound (a superset, so B can read whichever names it chose):
  {"opportunity_id": <int|null>, "thread_id": <str|null>, "reply_text": "...", "text": "...",
   "from_addr": "support@mockstore.invalid", "source": "mockstore_support"}
The conversation is the most recent outgoing claim email for the earbuds (SKU MS-1001), looked up
read-only in the `messages` table. With no claim yet, ids are null and B decides what to do.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from datetime import datetime, timezone

import httpx

log = logging.getLogger("clawback.mockstore.support")

EARBUDS_SKU = "MS-1001"
_messages: deque = deque(maxlen=30)

RATE_LIMIT = 10          # messages ...
RATE_WINDOW_S = 60.0     # ... per minute, for the whole box (it is the one public input judges can type into)
_sent_at: deque = deque()


def allow_message(now: float | None = None) -> bool:
    """True if another message may go through; records it. A runaway script or a judge mashing Enter
    would otherwise fire an LLM call (and possibly an email) per message."""
    t = time.monotonic() if now is None else now
    while _sent_at and t - _sent_at[0] > RATE_WINDOW_S:
        _sent_at.popleft()
    if len(_sent_at) >= RATE_LIMIT:
        return False
    _sent_at.append(t)
    return True


def find_claim_thread(sku: str = EARBUDS_SKU) -> dict | None:
    """Most recent outgoing message on an opportunity for this SKU. Read-only. None if unavailable."""
    try:
        from backend.debug import dbaccess
        rows = dbaccess.query(
            "SELECT m.thread_id, m.opportunity_id, m.to_addr, m.subject, m.sent_at "
            "FROM messages m "
            "JOIN opportunities o ON o.id = m.opportunity_id "
            "JOIN line_items li ON li.id = o.line_item_id "
            "WHERE li.sku = %s AND (m.direction IS NULL OR LOWER(m.direction) NOT IN ('in', 'inbound', 'incoming', 'received')) ORDER BY m.id DESC LIMIT 1",
            (sku,))
        return rows[0] if rows else None
    except Exception as exc:  # no database yet, no table yet, no claim yet
        log.info("no claim thread available: %s", exc)
        return None


def build_payload(text: str, thread: dict | None) -> dict:
    return dict(opportunity_id=(thread or {}).get("opportunity_id"), thread_id=(thread or {}).get("thread_id"),
                reply_text=text, text=text, from_addr="support@mockstore.invalid", source="mockstore_support")


async def forward(app, text: str, thread: dict | None, transport=None) -> dict:
    """POST to /replies/inbound inside this same app (no network hop). Never raises."""
    rec = dict(at=datetime.now(timezone.utc).strftime("%H:%M:%S"), text=text[:300],
               target=("thread " + str(thread["thread_id"])) if thread and thread.get("thread_id")
               else ("opportunity %s" % thread["opportunity_id"] if thread else "no claim thread yet"),
               ok=False, status=None, error=None)
    try:
        t = transport or httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=t, base_url="http://clawback.local", timeout=60.0) as c:
            r = await c.post("/replies/inbound", json=build_payload(text, thread))
        rec["status"] = r.status_code
        rec["ok"] = 200 <= r.status_code < 300
        if not rec["ok"]:
            rec["error"] = f"HTTP {r.status_code}: {r.text[:160]}"
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"
    if not rec["ok"]:
        log.warning("support forward failed: %s", rec["error"])
    _messages.appendleft(rec)
    return rec


def recent() -> list[dict]:
    return list(_messages)


def clear() -> None:
    _messages.clear()
    _sent_at.clear()
