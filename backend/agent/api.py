"""B's endpoints (team spec 3.6 / 3.7):

POST /opportunities/{id}/propose   main loop
GET  /opportunities/{id}           everything the Why? panel needs
GET  /claim-packets/{id}
POST /replies/inbound              store replies (see replies.py)
"""
import json
from datetime import timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.actor.email import send_template
from backend.actor.packet import build_claim_packet
from backend.actor.rebuy import rebuy_return
from backend.agent import clock, words
from backend.agent.deps import db, pact, track
from backend.agent.policy import check_policy, needs_recheck
from backend.agent.propose import USER_ID, propose

router = APIRouter()

STATUS_ON_ALLOW = {"price_adjust_email": "asked_store", "claim_packet": "packet_ready",
                   "rebuy_return": "rebought", "deadline_alert": "return_soon"}


# ---------- shared pieces (also used by replies.py and dev/dev_routes.py) ----------

def amount_usd(p: dict) -> float:
    """Dollars at stake, from the DB: rebuy -> new price; claims -> paid - price."""
    if p["listing_id"] is None:
        return 0.0
    paid = float(db.query("SELECT price_paid FROM line_items WHERE id = %s",
                          (p["line_item_id"],))[0]["price_paid"])
    ph = db.query("SELECT price FROM price_history WHERE listing_id = %s ORDER BY seen_at DESC "
                  "LIMIT 1", (p["listing_id"],))
    price = float(ph[0]["price"]) if ph else paid
    if p["action_type"] == "rebuy_return":
        return price
    if p["action_type"] in ("price_adjust_email", "claim_packet"):
        return round(paid - price, 2)
    return 0.0


def run_action(d, p: dict) -> str:
    a = p["action_type"]
    if a in ("price_adjust_email", "claim_followup"):
        return "message " + send_template(d, p)
    if a == "claim_packet":
        return f"claim_packet {build_claim_packet(d, p['opportunity_id'])}"
    if a == "rebuy_return":
        return rebuy_return(d, p["rebuy"]["listing_id"], p["return"]["order_no"])
    if a == "deadline_alert":
        return f"alert on {p['alert_at']}"  # in-app only; nothing leaves the building
    raise ValueError(f"no actor for {a}")


def _set_status(p: dict, status: str) -> None:
    db.execute("UPDATE line_items SET status = %s WHERE id = %s", (status, p["line_item_id"]))


def act_on(p: dict, the_pact: dict, d, *, source: str = "propose") -> dict:
    """Record the decision, then ALLOW -> run once / ASK -> approval / BLOCK -> stop."""
    audit_id = pact.record(p, the_pact, d)
    rule, values = words.failed(d.results)
    out = {"proposal_id": p["proposal_id"], "action_type": p["action_type"],
           "decision": d.decision, "failed_rule": rule, "values": values, "audit_id": audit_id}
    amount = amount_usd(p)
    if d.decision == "ALLOW":
        conn = db.connection()
        try:
            status, result = pact.execute_once(conn, d, p, lambda: run_action(d, p), amount)
        except pact.AlreadyRan:
            status, result = "already_ran", "it already happened; nothing sent again"
        finally:
            conn.close()
        out.update(status=status, result=result)
        db.execute("UPDATE audit_log SET action_result = %s WHERE id = %s",
                   (json.dumps({"status": status, "result": result}), audit_id))
        if status == "done" and p["action_type"] in STATUS_ON_ALLOW:
            _set_status(p, STATUS_ON_ALLOW[p["action_type"]])
    elif d.decision == "ASK":
        aid = db.execute(
            "INSERT INTO approvals (proposal_id, proposal_hash, user_id, amount_usd, status, "
            "expires_at) VALUES (%s,%s,%s,%s,%s,%s)",
            (p["proposal_id"], d.proposal_hash, USER_ID, amount, "pending",
             clock.ts(clock.now() + timedelta(hours=24))))
        out["approval_id"] = aid
        _set_status(p, "needs_ok")
    elif p["action_type"] != "claim_followup":  # a stopped reply doesn't undo the claim
        _set_status(p, "stopped")
    track("gate_decision", {"source": source, "opportunity_id": p["opportunity_id"],
                            "action_type": p["action_type"], "decision": d.decision,
                            "failed_rule": rule, "amount_usd": amount})
    return out


def gate_and_act(p: dict, source: str = "propose") -> dict:
    if p["action_type"] == "none":
        pact.record(p, None, "NO_ACTION")
        track("gate_decision", {"source": source, "opportunity_id": p["opportunity_id"],
                                "action_type": "none", "decision": "NO_ACTION"})
        return {"proposal_id": p["proposal_id"], "action_type": "none", "decision": "NO_ACTION"}
    the_pact, _meta = pact.active_pact(USER_ID)
    ctx = pact.build_context(p["opportunity_id"], {"id": USER_ID})
    d = pact.run_gate(p, the_pact, ctx)
    return act_on(p, the_pact, d, source=source)


# ---------- endpoints ----------

@router.post("/opportunities/{opportunity_id}/propose")
def propose_endpoint(opportunity_id: int):
    if not db.query("SELECT id FROM opportunities WHERE id = %s", (opportunity_id,)):
        raise HTTPException(404, "no such opportunity")
    check_policy(opportunity_id)          # first guess from the dates: gives the LLM a quote
    p = propose(opportunity_id)
    if p["action_type"] != "none" and needs_recheck(opportunity_id, p["action_type"]):
        check_policy(opportunity_id, p["action_type"])  # the quote that decides THIS action
    return gate_and_act(p) | {"reasoning": p["reasoning"]}


@router.get("/opportunities/{opportunity_id}")
def opportunity_view(opportunity_id: int):
    rows = db.query(
        "SELECT o.*, li.title, li.variant, li.store, li.price_paid, li.status AS item_status, "
        "li.category, od.order_no, od.order_date FROM opportunities o "
        "JOIN line_items li ON li.id = o.line_item_id JOIN orders od ON od.id = li.order_id "
        "WHERE o.id = %s", (opportunity_id,))
    if not rows:
        raise HTTPException(404, "no such opportunity")
    o = rows[0]
    found = db.query("SELECT price, seen_at FROM price_history WHERE listing_id = %s "
                     "ORDER BY seen_at DESC LIMIT 1", (o["listing_id"],)) if o["listing_id"] else []
    listing = (db.query("SELECT variant, store, url FROM listings WHERE id = %s", (o["listing_id"],))
               or [None])[0] if o["listing_id"] else None
    pc = (db.query("SELECT * FROM policy_checks WHERE opportunity_id = %s ORDER BY id DESC LIMIT 1",
                   (opportunity_id,)) or [None])[0]
    trail = db.query(
        "SELECT a.id, a.decision, a.rule_results_json, a.action_result, a.ts, p.action_type, "
        "p.id AS proposal_id, p.reasoning, a.proposal_hash FROM audit_log a "
        "JOIN proposals p ON p.id = a.proposal_id WHERE p.opportunity_id = %s ORDER BY a.id",
        (opportunity_id,))
    for t in trail:
        t["rules"] = json.loads(t.pop("rule_results_json") or "[]")
        t["failed_rule"], t["failed_values"] = words.failed(t["rules"])
        t["plain"] = words.rule_sentence(t["failed_rule"], t["failed_values"], o["store"], o["title"])
        t["status_words"] = words.DECISION_WORDS.get(t["decision"], t["decision"])
    main = [t for t in trail if t["action_type"] != "claim_followup"]
    latest = main[-1] if main else None
    msgs = db.query("SELECT direction, thread_id, to_addr, subject, body, sent_at FROM messages "
                    "WHERE opportunity_id = %s ORDER BY id", (opportunity_id,))
    packets = db.query("SELECT id, chat_script, packet_json, created_at FROM claim_packets "
                       "WHERE opportunity_id = %s ORDER BY id", (opportunity_id,))
    return {
        "opportunity_id": opportunity_id, "kind": o["kind"], "item": o["title"],
        "variant": o["variant"], "store": o["store"], "paid": float(o["price_paid"]),
        "order_date": o["order_date"], "order_no": o["order_no"], "status": o["item_status"],
        "found_price": float(found[0]["price"]) if found else None,
        "found_variant": listing and listing["variant"], "found_url": listing and listing["url"],
        "seen_at": found[0]["seen_at"] if found else None,
        "policy_question": pc and pc["question"], "cited_text": pc and pc["cited_text"],
        "source_url": pc and pc["source_url"], "senso_source_id": pc and pc["senso_source_id"],
        "revision": pc and pc["revision"],
        "decision": latest and latest["decision"],
        "decision_words": latest and latest["status_words"],
        "failed_rule": latest and latest["failed_rule"],
        "failed_values": latest and latest["failed_values"],
        "plain_sentence": latest and latest["plain"],
        "reasoning": latest and latest["reasoning"],
        "trail": trail, "messages": msgs, "claim_packets": packets,
    }


@router.get("/claim-packets/{packet_id}")
def claim_packet_view(packet_id: int):
    rows = db.query("SELECT * FROM claim_packets WHERE id = %s", (packet_id,))
    if not rows:
        raise HTTPException(404, "no such claim packet")
    r = rows[0]
    r["packet"] = json.loads(r.pop("packet_json"))
    return r


class ReplyIn(BaseModel):
    opportunity_id: int
    reply_text: str
    thread_id: str


@router.post("/replies/inbound")
def replies_inbound(body: ReplyIn):
    from backend.agent.replies import handle_reply
    return handle_reply(body.opportunity_id, body.reply_text, body.thread_id)


@router.post("/replies/poll")
def replies_poll():
    """Read new real-email replies in our claim threads (inbox.py) and handle each one."""
    from backend.agent.inbox import poll_replies
    return poll_replies()
