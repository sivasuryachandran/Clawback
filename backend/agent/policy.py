"""check_policy(opportunity_id) -> policy_checks.id

One narrow question for the item's store (from the DB). The answer and quote come from the
PolicySource; with no source, senso_source_id is NULL and the gate stops on R6.
"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from backend.agent import clock
from backend.agent.deps import db, track
from backend.agent.senso_client import STORE_NAMES, get_policy_source


def _load(opportunity_id: int) -> tuple[dict, dict, dict | None]:
    opp = db.query("SELECT * FROM opportunities WHERE id = %s", (opportunity_id,))[0]
    item = db.query(
        "SELECT li.*, o.order_date, o.delivery_date, o.order_no FROM line_items li "
        "JOIN orders o ON o.id = li.order_id WHERE li.id = %s", (opp["line_item_id"],))[0]
    pol = db.query("SELECT * FROM store_policies WHERE store = %s", (item["store"],))
    return opp, item, (pol[0] if pol else None)


def adjust_window_open(item: dict, policy: dict) -> bool:
    start = item["order_date"]
    if policy["adjust_window_starts"] == "delivery" and item.get("delivery_date"):
        start = item["delivery_date"]
    end = date.fromisoformat(str(start)[:10]) + timedelta(days=int(policy["adjust_window_days"]))
    return end >= clock.today()


ADJUST_ACTIONS = {"price_adjust_email", "claim_packet", "claim_followup"}
RETURN_ACTIONS = {"rebuy_return", "deadline_alert"}


def question_for(opp: dict, item: dict, policy: dict | None, action: str | None = None) -> str:
    """One narrow question. With `action`, the question matches what the gate will judge
    (adjust window for claims/follow-ups, return window for rebuys/alerts), so the quote in
    the Why? panel is the sentence that actually decides it. Without `action` (before the
    proposal exists) we guess from the dates."""
    name = STORE_NAMES.get(item["store"], item["store"])
    finals = json.loads(policy["final_sale_categories"]) if policy and isinstance(
        policy["final_sale_categories"], str) else (policy or {}).get("final_sale_categories") or []
    cat = item["category"]
    adjust_q = f"Does {name} adjust prices after purchase for {cat} items, and for how long?"
    return_q = f"How long is the {name} return window for {cat} items, and when does it start?"
    if cat in finals:
        return f"Is {cat} final sale at {name}?"
    if action in ADJUST_ACTIONS:
        return adjust_q
    if action in RETURN_ACTIONS:
        return return_q
    if opp["kind"] == "deadline" or (policy and not adjust_window_open(item, policy)):
        return return_q
    return adjust_q


_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="policy")


def _ask_with_timeout(store: str, question: str) -> dict:
    """Wall-clock limit on the policy source (Senso can be slow). Raises on timeout/error."""
    fut = _pool.submit(get_policy_source().ask, store, question)
    return fut.result(timeout=float(os.environ.get("POLICY_TIMEOUT_S", "10")))


def _last_real_answer(store: str, question: str) -> dict | None:
    """The most recent answer that came WITH a source for the same store + question.
    Reused only when the source is unreachable; never when it answered 'no source'."""
    rows = db.query(
        "SELECT answer, cited_text, source_url, senso_source_id, revision, fetched_at "
        "FROM policy_checks WHERE store = %s AND question = %s AND senso_source_id IS NOT NULL "
        "ORDER BY fetched_at DESC LIMIT 1", (store, question))
    return rows[0] if rows else None


def _insert(opportunity_id, store, q, r, fetched_at) -> int:
    return db.execute(
        "INSERT INTO policy_checks (opportunity_id, store, question, answer, cited_text, "
        "source_url, senso_source_id, revision, fetched_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (opportunity_id, store, q, r.get("answer"), r.get("cited_text"), r.get("source_url"),
         r.get("source_id") or None, r.get("revision"), fetched_at))


def needs_recheck(opportunity_id: int, action: str) -> bool:
    """True if the latest citation for this opportunity answers a different question than
    the one that fits `action` (the gate reads the latest row)."""
    opp, item, policy = _load(opportunity_id)
    want = question_for(opp, item, policy, action)
    last = db.query("SELECT question FROM policy_checks WHERE opportunity_id = %s "
                    "ORDER BY id DESC LIMIT 1", (opportunity_id,))
    return not last or last[0]["question"] != want


def check_policy(opportunity_id: int, action: str | None = None) -> int:
    """Contract 3.4 is check_policy(opportunity_id); `action` is an optional extra."""
    opp, item, policy = _load(opportunity_id)
    store = item["store"]
    q = question_for(opp, item, policy, action)
    try:
        r = _ask_with_timeout(store, q)
    except Exception as e:  # slow / down / not wired
        prev = _last_real_answer(store, q)
        track("policy_source_unavailable", {"store": store, "error": repr(e)[:200],
                                            "reused_citation": prev is not None})
        if prev:
            # Same real quote, and its ORIGINAL fetched_at, so R6's 24-hour check still
            # judges how old the citation really is. Nothing is made up or re-dated.
            return _insert(opportunity_id, store, q,
                           {"answer": prev["answer"], "cited_text": prev["cited_text"],
                            "source_url": prev["source_url"], "source_id": prev["senso_source_id"],
                            "revision": prev["revision"]}, prev["fetched_at"])
        r = {"answer": f"policy source unavailable: {e!r}"[:500], "cited_text": None,
             "source_url": None, "source_id": None, "revision": None}
    return _insert(opportunity_id, store, q, r, clock.ts())


def warm_policies() -> list[dict]:
    """Ask the policy source for every open opportunity before going on stage, so a citation
    exists to fall back on. Returns one row per opportunity for the preflight report."""
    out = []
    for o in db.query("SELECT id FROM opportunities WHERE status = 'open' ORDER BY id"):
        t = time.time()
        # Both questions, so a citation exists whichever action gets proposed; the
        # date-based default goes LAST so it is the latest row (what the gate reads).
        rows = [check_policy(o["id"], a) for a in ("price_adjust_email", "rebuy_return")]
        rows.append(check_policy(o["id"]))
        pcs = [db.query("SELECT store, question, cited_text, senso_source_id, fetched_at "
                        "FROM policy_checks WHERE id = %s", (i,))[0] for i in rows]
        cited = all(p["senso_source_id"] for p in pcs)
        out.append(pcs[-1] | {"opportunity_id": o["id"], "ms": int((time.time() - t) * 1000),
                              "senso_source_id": pcs[-1]["senso_source_id"] if cited else None})
    return out
