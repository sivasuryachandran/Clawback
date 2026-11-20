"""GET /stats: dollars recovered and decision counts (Teammate C; section 3.7).

Definitions (so the UI and the spoken answers agree):
- dollars_recovered = the price drop on every claim or rebuy that actually RAN (executed_actions
  status 'done', action price_adjust_email or rebuy_return), counted once per proposal hash.
  For the demo: earbuds claim = $34.00 (129.99 - 95.99), lamp rebuy = $40.00 (239.00 - 199.00).
  A claim_packet is only prepared for the user, so it is NOT counted as recovered.
- counts.done / stopped / no_action = the LATEST audit_log decision per proposal (ALLOW / BLOCK /
  NO_ACTION), so an ASK that later became ALLOW counts once, as done.
- counts.needs_your_ok = approvals still pending and not expired (what the Money back screen shows).
Works on any DB shape that follows section 3.3; nothing here writes.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from backend.debug import dbaccess as dba

log = logging.getLogger("clawback.stats")
MONEY_ACTIONS = ("price_adjust_email", "rebuy_return")


def latest_decisions() -> dict[str, dict]:
    """Latest audit row per proposal (falls back to the row itself when a row has no hash)."""
    rows = dba.query("SELECT id, proposal_id, proposal_hash, decision FROM audit_log ORDER BY id")
    latest: dict[str, dict] = {}
    for r in rows:
        key = r.get("proposal_hash") or f"row:{r['id']}"
        latest[key] = r                  # rows are ascending, so the last one wins
    return latest


def pending_approvals(now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    rows = dba.query("SELECT id, proposal_id, proposal_hash, amount_usd, expires_at FROM approvals WHERE status = %s",
                     ("pending",))
    out = []
    for r in rows:
        exp = dba.to_dt(r.get("expires_at"))
        if exp is None or exp >= now:
            out.append(r)
    return out


def compute(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    latest = latest_decisions()
    counts = {"done": 0, "needs_your_ok": 0, "stopped": 0, "no_action": 0}
    for r in latest.values():
        d = (r.get("decision") or "").upper()
        if d == "ALLOW":
            counts["done"] += 1
        elif d == "BLOCK":
            counts["stopped"] += 1
        elif d == "NO_ACTION":
            counts["no_action"] += 1
    counts["needs_your_ok"] = len(pending_approvals(now))

    rows = dba.query(
        "SELECT ea.proposal_hash, ea.action_type, ea.amount_usd, o.drop_usd, li.store "
        "FROM executed_actions ea "
        "LEFT JOIN proposals p ON p.proposal_hash = ea.proposal_hash "
        "LEFT JOIN opportunities o ON o.id = p.opportunity_id "
        "LEFT JOIN line_items li ON li.id = ea.line_item_id "
        "WHERE ea.status = %s", ("done",))
    seen: set[str] = set()
    asked = rebought = 0.0
    by_store: dict[str, float] = {}
    for r in rows:
        h = r["proposal_hash"]
        if h in seen or r["action_type"] not in MONEY_ACTIONS:
            continue
        seen.add(h)
        drop = dba.num(r.get("drop_usd"))
        if r["action_type"] == "price_adjust_email":
            asked += drop
        else:
            rebought += drop
        store = r.get("store") or "unknown"
        by_store[store] = round(by_store.get(store, 0.0) + drop, 2)

    packets = dba.query("SELECT COUNT(*) AS n FROM claim_packets")
    return {
        "available": True,
        "dollars_recovered": round(asked + rebought, 2),
        "recovered_breakdown": {"asked_store": round(asked, 2), "rebought": round(rebought, 2)},
        "by_store": by_store,
        "counts": counts,
        "unsafe_actions_stopped": counts["stopped"],
        # the same numbers at the top level: the Money back screen reads stats.stopped directly
        "done": counts["done"], "needs_ok": counts["needs_your_ok"], "stopped": counts["stopped"], "no_action": counts["no_action"],
        "claim_packets_ready": int(packets[0]["n"]) if packets else 0,
        "as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def unavailable(err: Exception) -> dict:
    """/stats is public, so the response says only THAT the data is unavailable; the cause goes to the log."""
    log.warning("/stats unavailable: %s: %s", type(err).__name__, err)
    return {"available": False, "error": "The database is not reachable right now.", "dollars_recovered": 0.0,
            "recovered_breakdown": {"asked_store": 0.0, "rebought": 0.0}, "by_store": {},
            "counts": {"done": 0, "needs_your_ok": 0, "stopped": 0, "no_action": 0},
            "unsafe_actions_stopped": 0, "done": 0, "needs_ok": 0, "stopped": 0, "no_action": 0, "claim_packets_ready": 0,
            "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
