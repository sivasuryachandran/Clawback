"""Mock Store support desk -> Teammate B's POST /replies/inbound (the "try to fool it" box).

The text typed here is UNTRUSTED. We never interpret it; we forward it verbatim as data and B's
reply handler decides (the gate then stops anything unsafe, e.g. R13 / R10 / R5).

B's endpoint (backend/agent/api.py ReplyIn) REQUIRES all three of these, none of them null:
  {"opportunity_id": <int>, "reply_text": "...", "thread_id": "<str>"}
We also send `text`, `from_addr` and `source`, which B ignores.

Which conversation does the text belong to?
  1. the most recent outgoing claim email on an earbuds opportunity (SKU MS-1001), read-only from `messages`;
  2. if no claim has gone out yet (the demo's first step), the earbuds opportunity with a placeholder
     thread id. B's handler then finds no earlier claim, so the gate stops any follow-up (R0 / R10 / R13)
     and nothing is sent: a visible "Stopped by your rules", never a silent failure;
  3. if there is no earbuds purchase at all, there is nothing to attach the message to: it is NOT sent and
     the presenter view says so.
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


def _earbuds_opportunity(sku: str = EARBUDS_SKU) -> int | None:
    try:
        from backend.debug import dbaccess
        rows = dbaccess.query(
            "SELECT o.id AS opportunity_id FROM opportunities o JOIN line_items li ON li.id = o.line_item_id "
            "WHERE li.sku = %s ORDER BY o.id DESC LIMIT 1", (sku,))
        return int(rows[0]["opportunity_id"]) if rows else None
    except Exception as exc:
        log.info("no earbuds opportunity available: %s", exc)
        return None


def resolve_conversation(sku: str = EARBUDS_SKU) -> dict | None:
    """The conversation a support message is attached to (see the module doc). Always int + str, never null."""
    claim = find_claim_thread(sku)
    if claim and claim.get("thread_id") and claim.get("opportunity_id") is not None:
        return {"opportunity_id": int(claim["opportunity_id"]), "thread_id": str(claim["thread_id"]), "kind": "claim"}
    opp = _earbuds_opportunity(sku)
    if opp is None:
        return None
    return {"opportunity_id": opp, "thread_id": f"thr_unclaimed_{opp}", "kind": "unclaimed"}


def build_payload(text: str, conv: dict) -> dict:
    return dict(opportunity_id=int(conv["opportunity_id"]), thread_id=str(conv["thread_id"]), reply_text=text, text=text,
                from_addr="support@mockstore.invalid", source="mockstore_support")


async def forward(app, text: str, conv: dict | None, transport=None) -> dict:
    """POST to /replies/inbound inside this same app (no network hop). Never raises. With no conversation to
    attach to, nothing is sent and the presenter log says why."""
    rec = dict(at=datetime.now(timezone.utc).strftime("%H:%M:%S"), text=text[:300], ok=False, status=None, error=None,
               target=("no conversation" if not conv else
                       f"thread {conv['thread_id']}" if conv.get("kind") == "claim" else
                       f"no claim yet (placeholder {conv['thread_id']})"))
    if conv is None:
        rec["error"] = "no earbuds purchase in the database yet, so there is nothing to attach this message to; it was not sent"
        log.warning("support message not forwarded: %s", rec["error"])
        _messages.appendleft(rec)
        return rec
    try:
        t = transport or httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=t, base_url="http://clawback.local", timeout=60.0) as c:
            r = await c.post("/replies/inbound", json=build_payload(text, conv))
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
