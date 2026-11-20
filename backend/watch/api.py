"""Endpoints A owns (docs/C_CONTRACTS.md ROUTER_MODULES): POST /watch/tick,
POST /watch/price-changed, GET /activity."""

from __future__ import annotations

import json
import os
from collections import deque

from fastapi import APIRouter, HTTPException, Request

from backend.db import query
from backend.watch.price_watch import on_price_change, tick

router = APIRouter()

_seen_event_ids: deque = deque(maxlen=500)


@router.post("/watch/tick")
def watch_tick():
    return {"opportunity_ids": tick()}


@router.post("/watch/price-changed")
async def price_changed(request: Request):
    """Verified with the Mock Store's own reference verifier (mockstore.webhook.verify)
    so the signature format can never drift between the two sides."""
    raw = await request.body()
    secret = os.environ.get("MOCK_WEBHOOK_SECRET", "")
    header = request.headers.get("X-Mockstore-Signature", "")
    from mockstore import webhook as mockstore_webhook

    if not mockstore_webhook.verify(raw, header, secret):
        raise HTTPException(status_code=401, detail="bad signature")
    payload = json.loads(raw)
    event_id = payload.get("event_id")
    if event_id and event_id in _seen_event_ids:
        return {"opportunity_ids": [], "deduped": True}
    if event_id:
        _seen_event_ids.append(event_id)
    opp_ids = on_price_change(payload["listing_id"], payload["price"], refresh=bool(payload.get("refresh")))
    return {"opportunity_ids": opp_ids}


def _jsonish(v):
    """JSON columns may arrive as str or already parsed, depending on the driver."""
    if v is None:
        return None
    return v if isinstance(v, (list, dict)) else json.loads(v)


@router.get("/activity")
def list_activity():
    """Field names match backend/pact/devkit.py's activity() exactly -- frontend/app.js
    and the rest of the team already read these. Newest first."""
    out = []
    for a in query("SELECT * FROM audit_log ORDER BY id DESC", ()):
        prop_rows = query("SELECT * FROM proposals WHERE id=%s", (a["proposal_id"],))
        if not prop_rows:
            continue
        prop = prop_rows[0]
        p = _jsonish(prop["payload_json"]) or {}
        opp_rows = query("SELECT * FROM opportunities WHERE id=%s", (p.get("opportunity_id", prop.get("opportunity_id")),))
        if not opp_rows:
            continue
        opp = opp_rows[0]
        it = query(
            "SELECT li.*, o.order_date, o.order_no FROM line_items li JOIN orders o ON o.id=li.order_id WHERE li.id=%s",
            (opp["line_item_id"],),
        )[0]
        pr = (
            query(
                "SELECT price, seen_at FROM price_history WHERE listing_id=%s ORDER BY seen_at DESC LIMIT 1",
                (opp["listing_id"],),
            )
            if opp.get("listing_id")
            else []
        )
        pc = query(
            "SELECT * FROM policy_checks WHERE opportunity_id=%s ORDER BY id DESC LIMIT 1", (opp["id"],)
        )
        results = _jsonish(a.get("rule_results_json")) or []
        failed = next((r["rule"] for r in results if not r.get("pass")), None)
        appr = (
            query("SELECT id FROM approvals WHERE proposal_hash=%s AND status='pending'", (a["proposal_hash"],))
            if a["decision"] == "ASK"
            else []
        )
        pk = (
            query("SELECT pact_text FROM pacts WHERE pact_id=%s AND version=%s", (a["pact_id"], a["pact_version"]))
            if a.get("pact_id")
            else []
        )
        cs = query("SELECT chat_script FROM claim_packets WHERE proposal_hash=%s", (a["proposal_hash"],))
        out.append(
            {
                "audit_id": a["id"], "ts": a["ts"], "opportunity_id": opp["id"], "line_item_id": it["id"],
                "proposal_id": p.get("proposal_id", prop["id"]),
                "item_title": it["title"], "variant": it["variant"], "store": it["store"],
                "price_paid": it["price_paid"], "order_date": it["order_date"],
                "found_price": pr[0]["price"] if pr else None, "seen_at": pr[0]["seen_at"] if pr else None,
                "drop_usd": opp["drop_usd"],
                "cited_text": pc[0]["cited_text"] if pc else None, "source_url": pc[0]["source_url"] if pc else None,
                "senso_source_id": pc[0]["senso_source_id"] if pc else None,
                "decision": a["decision"], "failed_rule": failed, "rule_results": results,
                "action_type": p.get("action_type", prop.get("action_type")), "kind": opp["kind"],
                "approval_id": appr[0]["id"] if appr else None,
                "pact_version": a.get("pact_version"),
                "pact_hash": (_jsonish(pk[0]["pact_text"]) or {}).get("hash") if pk else None,
                "chat_script": cs[0]["chat_script"] if cs else None,
            }
        )
    return out
