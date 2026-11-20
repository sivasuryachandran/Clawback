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


def _failed_rule(rule_results_json) -> tuple[str | None, dict | None]:
    if not rule_results_json:
        return None, None
    results = rule_results_json if isinstance(rule_results_json, list) else json.loads(rule_results_json)
    for r in results:
        if not r.get("pass"):
            return r.get("rule"), r.get("values")
    return None, None


def _latest_listing_price(listing_id: int) -> tuple[float | None, str | None]:
    rows = query(
        "SELECT (SELECT price FROM price_history ph WHERE ph.listing_id=%s ORDER BY seen_at DESC LIMIT 1) AS price, "
        "(SELECT seen_at FROM price_history ph WHERE ph.listing_id=%s ORDER BY seen_at DESC LIMIT 1) AS seen_at",
        (listing_id, listing_id),
    )
    if not rows:
        return None, None
    return rows[0].get("price"), rows[0].get("seen_at")


@router.get("/activity")
def list_activity():
    """Audit rows joined with the plain fields the UI's Activity screen and Why? panel
    need (section 3.10): item, paid, found price + seen_at, cited sentence + source, the
    decision and failed rule with values."""
    rows = query(
        """
        SELECT a.id AS audit_id, a.ts, a.decision, a.rule_results_json, a.proposal_id,
               a.proposal_hash, p.opportunity_id, p.action_type,
               li.title, li.variant, li.store AS item_store, li.price_paid,
               opp.listing_id, opp.drop_usd,
               pc.cited_text, pc.source_url, pc.fetched_at AS citation_fetched_at
        FROM audit_log a
        LEFT JOIN proposals p ON p.id = a.proposal_id
        LEFT JOIN opportunities opp ON opp.id = p.opportunity_id
        LEFT JOIN line_items li ON li.id = opp.line_item_id
        LEFT JOIN policy_checks pc ON pc.opportunity_id = opp.id
        ORDER BY a.id DESC
        """,
        (),
    )
    out = []
    for r in rows:
        failed_rule, rule_values = _failed_rule(r.get("rule_results_json"))
        found_price, seen_at = (None, None)
        if r.get("listing_id"):
            found_price, seen_at = _latest_listing_price(r["listing_id"])
        out.append(
            {
                "audit_id": r["audit_id"],
                "ts": r["ts"],
                "decision": r["decision"],
                "action_type": r.get("action_type"),
                "item_title": r.get("title"),
                "variant": r.get("variant"),
                "store": r.get("item_store"),
                "paid": r.get("price_paid"),
                "found_price": found_price,
                "seen_at": seen_at,
                "drop_usd": r.get("drop_usd"),
                "cited_text": r.get("cited_text"),
                "source_url": r.get("source_url"),
                "failed_rule": failed_rule,
                "rule_values": rule_values,
            }
        )
    return out
