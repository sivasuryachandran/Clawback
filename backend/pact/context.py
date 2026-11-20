"""Builds GateContext from the database (A's backend/db.py) and writes the audit log."""

from __future__ import annotations

import json
import os
from datetime import datetime

from . import sign
from .gate import GateContext


def clock_now() -> datetime:
    """ONE CLOCK with B's backend/agent/clock.py: CLAWBACK_NOW (ISO, naive UTC) if set, else real UTC."""
    raw = os.environ.get("CLAWBACK_NOW")
    return datetime.fromisoformat(raw).replace(tzinfo=None) if raw else datetime.utcnow().replace(microsecond=0)


def clock_today():
    raw = os.environ.get("CLAWBACK_TODAY")
    return datetime.fromisoformat(raw).date() if raw else clock_now().date()


def demo_mode() -> bool:
    return os.environ.get("DEMO_MODE", "true").strip().lower() != "false"      # anything but "false" = on


def demo_recipients() -> frozenset:
    raw = os.environ.get("DEMO_ALLOWED_RECIPIENTS", "")
    return frozenset(x.strip().lower() for x in raw.split(",") if x.strip())


def _db():
    from backend import db
    return db


def _json(v):
    if isinstance(v, (str, bytes)):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


def active_pact(user_id: str) -> tuple[dict | None, dict]:
    """(pact, meta). Never reads pacts.status; revocation = a valid signed row in pact_revocations."""
    db = _db()
    rows = db.query("SELECT * FROM pacts WHERE user_id=%s ORDER BY version DESC LIMIT 1", (user_id,))
    if not rows:
        return None, {"version": None, "revoked": False, "is_latest": False}
    row = rows[0]
    pact = sign.verify_text(row["pact_text"])
    revs = db.query("SELECT * FROM pact_revocations WHERE pact_id=%s AND version=%s", (row["pact_id"], row["version"]))
    revoked = any(sign.verify_revocation(r) for r in revs)
    newer = db.query("SELECT 1 FROM pacts WHERE user_id=%s AND version>%s LIMIT 1", (user_id, row["version"]))
    return pact, {"version": row["version"], "revoked": revoked, "is_latest": not newer}


def build_context(opportunity_id: int, user: dict) -> GateContext:
    db = _db()
    now = clock_now()
    opp = db.query("SELECT * FROM opportunities WHERE id=%s", (opportunity_id,))[0]
    item = db.query(
        "SELECT li.*, o.order_no, o.order_date, o.delivery_date FROM line_items li JOIN orders o ON o.id=li.order_id WHERE li.id=%s",
        (opp["line_item_id"],))[0]
    listing = None
    if opp.get("listing_id"):
        listing = db.query(
            "SELECT l.*, (SELECT price FROM price_history WHERE listing_id=l.id ORDER BY seen_at DESC LIMIT 1) AS price, "
            "(SELECT seen_at FROM price_history WHERE listing_id=l.id ORDER BY seen_at DESC LIMIT 1) AS seen_at "
            "FROM listings l WHERE l.id=%s", (opp["listing_id"],))[0]
    policy = db.query("SELECT * FROM store_policies WHERE store=%s", (item["store"],))[0]
    policy["final_sale_categories"] = _json(policy.get("final_sale_categories")) or []
    policy["support_domains"] = _json(policy.get("support_domains")) or []
    pcs = db.query("SELECT * FROM policy_checks WHERE opportunity_id=%s ORDER BY fetched_at DESC LIMIT 1", (opportunity_id,))
    pact, meta = active_pact(user["id"])
    owner = db.query("SELECT user_id FROM pacts WHERE user_id=%s LIMIT 1", (user["id"],))
    u = db.query("SELECT * FROM users WHERE id=%s", (user["id"],))[0]
    month_start = now.replace(day=1, hour=0, minute=0, second=0).strftime("%Y-%m-%d %H:%M:%S")
    done = db.query("SELECT COALESCE(SUM(amount_usd),0) AS s FROM executed_actions WHERE action_type='rebuy_return' "
                    "AND status IN ('done','running') AND created_at>=%s", (month_start,))[0]["s"]
    pending = db.query(
        "SELECT COALESCE(SUM(a.amount_usd),0) AS s FROM approvals a JOIN proposals p ON p.id=a.proposal_id "
        "WHERE a.status='approved' AND p.action_type='rebuy_return' AND p.opportunity_id<>%s "
        "AND a.proposal_hash NOT IN (SELECT proposal_hash FROM executed_actions)", (opportunity_id,))[0]["s"]
    prior = db.query(
        "SELECT ea.* FROM executed_actions ea JOIN line_items li ON li.id=ea.line_item_id JOIN line_items me ON me.order_id=li.order_id "
        "WHERE me.id=%s AND ea.status IN ('done','running') AND ea.action_type IN ('price_adjust_email','claim_packet','rebuy_return')",
        (item["id"],))
    claim = None
    if prior:
        m = db.query("SELECT * FROM messages WHERE opportunity_id=%s AND direction='out' ORDER BY sent_at ASC LIMIT 1", (opportunity_id,))
        if m:
            n = db.query("SELECT COUNT(*) AS n FROM executed_actions WHERE action_type='claim_followup' AND line_item_id=%s "
                         "AND status IN ('done','running')", (item["id"],))[0]["n"]
            claim = {"recipient": m[0]["to_addr"], "thread_id": m[0]["thread_id"], "followups": n}
    return GateContext(
        today=clock_today(), now=now, opportunity=opp, item=item, listing=listing, policy=policy,
        policy_check=pcs[0] if pcs else None, pact_meta=meta,
        persona_status=u["persona_status"], persona_verified_at=u["verified_at"], reverified_at=u["reverified_at"],
        session_user_id=user["id"], pact_owner_id=(pact or {}).get("user_id") or (owner[0]["user_id"] if owner else None),
        user_email=u["email"], month_committed=float(done) + float(pending), prior_claims=prior, claim=claim,
        demo_mode=demo_mode(), demo_recipients=demo_recipients(),
    )


def record(p, pact, decision, action_result=None) -> int:
    db = _db()
    p = p if isinstance(p, dict) else {}
    return db.execute(
        "INSERT INTO audit_log (ts, pact_id, pact_version, proposal_id, proposal_hash, rule_results_json, decision, action_result) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), (pact or {}).get("pact_id"), (pact or {}).get("version"),
         p.get("proposal_id"), getattr(decision, "proposal_hash", None),
         json.dumps(getattr(decision, "results", []), default=str), getattr(decision, "decision", decision),
         json.dumps(action_result, default=str) if action_result is not None else None))
