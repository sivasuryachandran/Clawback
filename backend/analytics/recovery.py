"""Events nobody else emits, derived from database truth, exactly once each (Teammate C).

The Amplitude dashboard needs `money_recovered` (dollars chart), and the team plan lists `action_executed`,
`claim_packet_ready` and `opportunity_found`. B's loop emits `gate_decision`; Siva's code emits the pact and approval
events; nothing emits those four. Rather than depend on every code path remembering to, `reconcile()` reads what
actually happened and reports each thing once:

  executed_actions status 'done'   -> action_executed (every action); money_recovered (price_adjust_email / rebuy_return)
  claim_packets row                -> claim_packet_ready
  opportunities row                -> opportunity_found

Each key includes WHEN the thing happened (created_at). B's proposals are deterministic, so after a database reset and a
re-run the same action has the same proposal hash and ids restart at 1: a key without the time would make a rehearsal
swallow the real demo's events (and Amplitude's insert_id dedupe would drop them too).
(The database stores seconds, so two runs that finish inside the SAME second are indistinguishable. A person resetting and
re-running is seconds apart; only an automated reset+rerun loop could hit this, and it would have to repeat within one second.)
"Once" is enforced three ways: an in-memory set, the analytics log file (so a restart does not re-report), and a
deterministic Amplitude `insert_id` (so even a lost log cannot double-count on the dashboard).
money_recovered.amount_usd is the SAVING (price drop), the same number /stats calls dollars_recovered:
claim $34 + lamp rebuy $40 = $74. A claim_packet is only prepared for the user, so it is not "recovered".
"""
from __future__ import annotations

import logging
import threading
import time

from backend.debug import dbaccess as dba

from . import events

log = logging.getLogger("clawback.recovery")
MONEY_ACTIONS = ("price_adjust_email", "rebuy_return")
MIN_INTERVAL_S = 2.0           # /stats is polled every few seconds: don't scan the tables on every poll

_lock = threading.Lock()
_seen: set[str] = set()
_primed = False
_last_run = 0.0


def reset_for_tests() -> None:
    global _primed, _last_run
    with _lock:
        _seen.clear()
        _primed = False
        _last_run = 0.0


def _when(v) -> str:
    """The moment as a stable string, whether the driver gives a datetime or text."""
    return "" if v is None else str(v)


def _emit(key: str, event: str, props: dict) -> bool:
    if key in _seen:
        return False
    _seen.add(key)
    events.track(event, dict(props, dedupe_key=key, derived=True), insert_id="clawback:" + key)
    return True


def reconcile(force: bool = False) -> int:
    """Report anything that happened and has not been reported. Returns how many events were emitted. Never raises."""
    global _primed, _last_run
    try:
        with _lock:
            now = time.monotonic()
            if not force and now - _last_run < MIN_INTERVAL_S:
                return 0
            _last_run = now
            if not _primed:
                _seen.update(events.logged_keys())
                _primed = True
            n = 0
            done = dba.query(
                "SELECT ea.proposal_hash, ea.action_type, ea.line_item_id, ea.created_at, p.opportunity_id, o.drop_usd, li.store "
                "FROM executed_actions ea "
                "LEFT JOIN proposals p ON p.proposal_hash = ea.proposal_hash "
                "LEFT JOIN opportunities o ON o.id = p.opportunity_id "
                "LEFT JOIN line_items li ON li.id = ea.line_item_id WHERE ea.status = %s", ("done",))
            for r in done:
                base = {"action_type": r["action_type"], "store": r.get("store"), "opportunity_id": r.get("opportunity_id")}
                when = _when(r.get("created_at"))
                n += _emit(f"action:{r['proposal_hash']}:{when}", "action_executed", base)
                if r["action_type"] in MONEY_ACTIONS:
                    n += _emit(f"money:{r['proposal_hash']}:{when}", "money_recovered", dict(base, amount_usd=dba.num(r.get("drop_usd"))))
            for r in dba.query("SELECT id, store, opportunity_id, created_at FROM claim_packets"):
                n += _emit(f"packet:{r['id']}:{_when(r.get('created_at'))}", "claim_packet_ready", {"store": r.get("store"), "opportunity_id": r.get("opportunity_id")})
            for r in dba.query("SELECT o.id, o.kind, o.drop_usd, o.created_at, li.store FROM opportunities o "
                               "LEFT JOIN line_items li ON li.id = o.line_item_id"):
                n += _emit(f"opp:{r['id']}:{_when(r.get('created_at'))}", "opportunity_found", {"kind": r.get("kind"), "drop_usd": dba.num(r.get("drop_usd")), "store": r.get("store")})
            return n
    except Exception as exc:
        log.warning("analytics reconcile skipped: %s", exc)
        return 0
