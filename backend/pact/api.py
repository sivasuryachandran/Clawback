"""Pact, gate check and approvals endpoints (Siva). Mount with one include_router line in backend/main.py."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import sign as signer
from .context import active_pact, build_context, record
from .gate import run_approved, run_gate
from .once import AlreadyRan, execute_once

router = APIRouter()
FMT = "%Y-%m-%d %H:%M:%S"


def _db():
    from backend import db
    return db


def _now() -> datetime:
    return datetime.utcnow().replace(microsecond=0)


def _uid(request: Request) -> str:
    return request.headers.get("x-user-id") or "u_demo"


class Limits(BaseModel):
    price_adjust: bool = True
    rebuy: bool = True
    min_net_savings_usd: float = Field(10, ge=0, le=10000)
    ask_first_above_usd: float = Field(150, ge=0, le=10000)
    max_rebuy_price_usd: float = Field(250, ge=0, le=10000)
    monthly_cap_usd: float = Field(300, ge=0, le=100000)
    skip_clearance_and_gift_cards: bool = True
    stores: list[str] = ["mockstore", "target", "bestbuy"]
    valid_days: int = Field(90, ge=1, le=365)


def summary(pact: dict | None, meta: dict) -> dict:
    if not pact:
        return {"signed": False}
    return {"signed": True, "paused": meta["revoked"], "current": meta["is_latest"], "version": pact["version"],
            "valid_until": pact["expires_at"], "short_hash": pact["hash"][:8], "limits": {
                k: pact.get(k) for k in ("min_net_savings_usd", "ask_first_above_usd", "max_rebuy_price_usd", "monthly_cap_usd", "stores", "actions", "excluded_categories")}}


@router.get("/pact")
def get_pact(request: Request):
    pact, meta = active_pact(_uid(request))
    return summary(pact, meta)


@router.post("/pact")
def post_pact(limits: Limits, request: Request):
    db, uid = _db(), _uid(request)
    u = db.query("SELECT * FROM users WHERE id=%s", (uid,))
    if not u or u[0]["persona_status"] != "approved":
        raise HTTPException(403, "Please verify it's you before signing your Pact.")
    prev = db.query("SELECT pact_id, version FROM pacts WHERE user_id=%s ORDER BY version DESC LIMIT 1", (uid,))
    pact_id, version = (prev[0]["pact_id"], prev[0]["version"] + 1) if prev else ("pact_" + uuid.uuid4().hex[:8], 1)
    actions = ["deadline_alert"] + (["price_adjust_email", "claim_packet"] if limits.price_adjust else []) + (["rebuy_return"] if limits.rebuy else [])
    now = _now()
    body = {"pact_id": pact_id, "version": version, "user_id": uid, "persona_inquiry_id": u[0]["persona_inquiry_id"],
            "created_at": now.strftime("%Y-%m-%d"), "expires_at": (now + timedelta(days=limits.valid_days)).strftime("%Y-%m-%d"),
            "actions": actions, "stores": [s.lower() for s in limits.stores],
            "excluded_categories": ["clearance", "gift_cards"] if limits.skip_clearance_and_gift_cards else [],
            "min_net_savings_usd": limits.min_net_savings_usd, "ask_first_above_usd": limits.ask_first_above_usd,
            "max_rebuy_price_usd": limits.max_rebuy_price_usd, "monthly_cap_usd": limits.monthly_cap_usd,
            "buffer_days": 2, "max_claims_per_order": 1, "max_followups_per_thread": 3}
    text = signer.sign(body)
    db.execute("INSERT INTO pacts (pact_id, version, user_id, pact_text, created_at, status) VALUES (%s,%s,%s,%s,%s,'active')",
               (pact_id, version, uid, text, now.strftime(FMT)))
    if prev:   # the old version is retired with a signed revocation
        stamp = now.strftime(FMT)
        db.execute("INSERT INTO pact_revocations (pact_id, version, revoked_at, reason, signature) VALUES (%s,%s,%s,%s,%s)",
                   (pact_id, prev[0]["version"], stamp, "replaced by a newer version", signer.sign_revocation(pact_id, prev[0]["version"], stamp)))
        db.execute("UPDATE pacts SET status='replaced' WHERE pact_id=%s AND version=%s", (pact_id, prev[0]["version"]))
    _track("pact_signed", {"version": version})
    return summary(*active_pact(uid))


@router.post("/pact/pause")
def pause(request: Request):
    db, uid = _db(), _uid(request)
    pact, meta = active_pact(uid)
    if not pact:
        raise HTTPException(404, "No Pact to pause.")
    stamp = _now().strftime(FMT)
    db.execute("INSERT INTO pact_revocations (pact_id, version, revoked_at, reason, signature) VALUES (%s,%s,%s,%s,%s)",
               (pact["pact_id"], pact["version"], stamp, "paused by user", signer.sign_revocation(pact["pact_id"], pact["version"], stamp)))
    db.execute("UPDATE pacts SET status='paused' WHERE pact_id=%s AND version=%s", (pact["pact_id"], pact["version"]))
    _track("pact_paused", {"version": pact["version"]})
    return summary(*active_pact(uid))


class GateCheck(BaseModel):
    opportunity_id: int
    proposal: dict


@router.post("/gate/check")
def gate_check(body: GateCheck, request: Request):
    uid = _uid(request)
    pact, _ = active_pact(uid)
    d = run_gate(body.proposal, pact, build_context(body.opportunity_id, {"id": uid}))
    return {"decision": d.decision, "failed_rule": d.failed_rule, "results": d.results, "proposal_hash": d.proposal_hash}


# ------------------------------------------------------------------ approvals
@router.get("/approvals")
def list_approvals(request: Request):
    rows = _db().query(
        "SELECT a.id, a.status, a.amount_usd, a.expires_at, p.opportunity_id, p.payload_json, p.reasoning, li.title, li.variant, li.price_paid, li.store "
        "FROM approvals a JOIN proposals p ON p.id=a.proposal_id JOIN line_items li ON li.id=JSON_EXTRACT_STRING(p.payload_json, 'line_item_id') "
        "WHERE a.user_id=%s ORDER BY a.id DESC", (_uid(request),))
    return rows


def _actor(p: dict, decision):
    """B's actions. Prefers B's run_action (backend.agent.api) so every action type behaves exactly as in the
    propose loop; falls back to backend.actor.<name> (the 3.4 signatures) while B's module isn't there."""
    try:
        from backend.agent.api import run_action
        return lambda: run_action(decision, p)
    except ImportError:
        pass
    from backend import actor
    t = p["action_type"]
    if t == "rebuy_return":
        return lambda: actor.rebuy_return(decision, p["rebuy"]["listing_id"], p["return"]["order_no"])
    if t == "claim_packet":
        return lambda: actor.build_claim_packet(decision, p["opportunity_id"])
    raise HTTPException(501, f"approval flow for {t} is not wired")


class Decide(BaseModel):
    decision: str   # approve | decline


@router.post("/approvals/{approval_id}")
def decide(approval_id: int, body: Decide, request: Request):
    db, uid = _db(), _uid(request)
    rows = db.query("SELECT * FROM approvals WHERE id=%s", (approval_id,))
    if not rows:
        raise HTTPException(404, "Not found.")
    a = rows[0]
    if a["user_id"] != uid:
        raise HTTPException(403, "Only the Pact owner can approve this.")
    if a["status"] != "pending":
        raise HTTPException(409, "This was already handled.")
    if datetime.fromisoformat(str(a["expires_at"])) <= _now():
        db.execute("UPDATE approvals SET status='expired', decided_at=%s WHERE id=%s", (_now().strftime(FMT), approval_id))
        raise HTTPException(410, "This request expired.")
    if body.decision == "decline":
        db.execute("UPDATE approvals SET status='declined', decided_at=%s WHERE id=%s", (_now().strftime(FMT), approval_id))
        _track("approval_resolved", {"status": "declined"})
        return {"status": "declined"}
    # Approve: fresh Persona re-check first (raises 403 if not still verified), then run A1 + full gate.
    from backend.persona.api import recheck
    recheck(request)
    db.execute("UPDATE approvals SET status='approved', decided_at=%s WHERE id=%s", (_now().strftime(FMT), approval_id))
    a = db.query("SELECT * FROM approvals WHERE id=%s", (approval_id,))[0]
    prop = db.query("SELECT * FROM proposals WHERE id=%s", (a["proposal_id"],))[0]
    p = json.loads(prop["payload_json"]) if isinstance(prop["payload_json"], (str, bytes)) else prop["payload_json"]
    pact, _ = active_pact(uid)
    ctx = build_context(p["opportunity_id"], {"id": uid})
    d = run_approved(p, pact, ctx, a)
    if d.decision != "ALLOW":
        record(p, pact, d)
        return {"status": "stopped", "failed_rule": d.failed_rule, "results": d.results}
    try:
        status, result = execute_once(db.connection(), d, p, _actor(p, d), float(a["amount_usd"] or 0))
    except AlreadyRan:
        return {"status": "already_done"}        # a double click lands here: one action only
    db.execute("UPDATE approvals SET status=%s WHERE id=%s", ("executed" if status == "done" else "approved", approval_id))
    record(p, pact, d, {"status": status, "result": result})
    _track("approval_resolved", {"status": status})
    return {"status": status, "result": result}


def _track(event: str, props: dict) -> None:
    try:
        from backend.analytics.events import track
        track(event, props)
    except Exception:
        pass
