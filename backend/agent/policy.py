"""check_policy(opportunity_id) -> policy_checks.id

One narrow question for the item's store (from the DB). The answer and quote come from the
PolicySource; with no source, senso_source_id is NULL and the gate stops on R6.
"""
import json
from datetime import date, timedelta

from backend.agent import clock
from backend.agent.deps import db
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


def question_for(opp: dict, item: dict, policy: dict | None) -> str:
    name = STORE_NAMES.get(item["store"], item["store"])
    finals = json.loads(policy["final_sale_categories"]) if policy and isinstance(
        policy["final_sale_categories"], str) else (policy or {}).get("final_sale_categories") or []
    cat = item["category"]
    if cat in finals:
        return f"Is {cat} final sale at {name}?"
    if opp["kind"] == "deadline" or (policy and not adjust_window_open(item, policy)):
        return f"How long is the {name} return window for {cat} items, and when does it start?"
    return f"Does {name} adjust prices after purchase for {cat} items, and for how long?"


def check_policy(opportunity_id: int) -> int:
    opp, item, policy = _load(opportunity_id)
    q = question_for(opp, item, policy)
    try:
        r = get_policy_source().ask(item["store"], q)
    except Exception as e:  # source down -> no citation -> R6 stops it
        r = {"answer": f"policy source error: {e}", "cited_text": None, "source_url": None,
             "source_id": None, "revision": None}
    return db.execute(
        "INSERT INTO policy_checks (opportunity_id, store, question, answer, cited_text, "
        "source_url, senso_source_id, revision, fetched_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (opportunity_id, item["store"], q, r.get("answer"), r.get("cited_text"),
         r.get("source_url"), r.get("source_id") or None, r.get("revision"), clock.ts()))
