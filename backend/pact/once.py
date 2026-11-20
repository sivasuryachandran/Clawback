"""Run-once guard. PROVENANCE: idea adapted from Pact's claim_idempotency (HCK/Pact/pact/state.py),
rewritten for a DB-API connection and the executed_actions table."""

from __future__ import annotations

from datetime import datetime
from typing import Callable


class AlreadyRan(Exception):
    pass


def _ph(conn) -> str:
    return "?" if type(conn).__module__.startswith("sqlite3") else "%s"


def execute_once(conn, decision, p: dict, action_fn: Callable[[], object], amount_usd: float = 0.0) -> tuple[str, str]:
    """Insert 'running' before the actor. running/done -> AlreadyRan; failed -> one retry."""
    if getattr(decision, "decision", None) != "ALLOW" or not getattr(decision, "proposal_hash", None):
        raise ValueError("execute_once needs an ALLOW decision with a proposal hash")
    ph, h = _ph(conn), decision.proposal_hash
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.cursor()
    try:
        cur.execute(
            f"INSERT INTO executed_actions (proposal_hash, line_item_id, action_type, amount_usd, status, result, created_at) "
            f"VALUES ({ph},{ph},{ph},{ph},'running',NULL,{ph})",
            (h, p.get("line_item_id"), p.get("action_type"), amount_usd, now),
        )
    except Exception as exc:                      # duplicate key
        if "duplicate" not in str(exc).lower() and "unique" not in str(exc).lower() and "1062" not in str(exc):
            raise
        cur.execute(f"UPDATE executed_actions SET status='running' WHERE proposal_hash={ph} AND status='failed'", (h,))
        if cur.rowcount != 1:
            conn.commit()
            raise AlreadyRan(h)
    conn.commit()
    try:
        result = action_fn()
        status, text = "done", str(result)[:1000]
    except Exception as exc:
        status, text = "failed", f"{type(exc).__name__}: {exc}"[:1000]
    cur.execute(f"UPDATE executed_actions SET status={ph}, result={ph} WHERE proposal_hash={ph}", (status, text, h))
    conn.commit()
    return status, text
