# STUB for rehearsal: replaced by Siva's real module (backend/pact/) tomorrow
"""Simple but honest gate for the rehearsal, implementing contract 3.4 (Siva).

Rules in the order the rehearsal prompt gives:
  R0, R2, R3, R4, R6, R6b, R6c, R7, R5, R8, R9, R10, R11, R13, R12
First failure = BLOCK (R12 = ASK). Any exception = BLOCK on R0. NOT implemented tonight:
R1 (signing/revocation/Persona age), R8a (price freshness), A1's Persona re-check.
The Pact is hard-coded from 3.8; nothing is signed.
"""
import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from backend.agent import clock
from dev.stubs import db

CLAIMS = {"price_adjust_email", "claim_packet"}
EMAILS = CLAIMS | {"claim_followup"}
ACTIONS = EMAILS | {"rebuy_return", "deadline_alert"}
TEMPLATE_FOR = {"price_adjust_email": "price_adjust_v1", "claim_packet": "packet_v1",
                "claim_followup": "proof_v1"}

SAMPLE_PACT = {
    "pact_id": "pact_demo", "version": 1, "user_id": "u_demo",
    "actions": ["price_adjust_email", "claim_packet", "rebuy_return", "deadline_alert"],
    "stores": ["mockstore", "target", "bestbuy"],
    "excluded_categories": ["clearance", "gift_cards"],
    "min_net_savings_usd": 10, "ask_first_above_usd": 150, "max_rebuy_price_usd": 250,
    "monthly_cap_usd": 300, "buffer_days": 2, "max_claims_per_order": 1,
    "max_followups_per_thread": 3, "valid_days": 90,
}


class AlreadyRan(Exception):
    pass


@dataclass
class Decision:
    decision: str                      # ALLOW | ASK | BLOCK
    results: list = field(default_factory=list)
    proposal_hash: str | None = None
    window_ends: str | None = None

    @property
    def failed_rule(self) -> str | None:
        for r in self.results:
            if not r["pass"]:
                return r["rule"]
        return None


# ---------- helpers ----------

def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def proposal_hash(p: dict) -> str:
    body = {k: v for k, v in p.items() if k != "reasoning"}
    return "sha256:" + hashlib.sha256(_canonical(body).encode()).hexdigest()


def _date(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _ts(v) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v))


def _json(v):
    if v is None:
        return []
    return v if isinstance(v, (list, dict)) else json.loads(v)


def window_end(action: str, item: dict, policy: dict) -> date:
    if action in EMAILS:
        days, starts = policy["adjust_window_days"], policy["adjust_window_starts"]
    else:
        days, starts = policy["return_window_days"], policy["return_window_starts"]
    start = _date(item["order_date"])
    if starts == "delivery" and item.get("delivery_date"):
        start = _date(item["delivery_date"])
    return start + timedelta(days=int(days))


def demo_mode() -> bool:
    return os.environ.get("DEMO_MODE") != "false"


def demo_recipients() -> frozenset:
    raw = os.environ.get("DEMO_ALLOWED_RECIPIENTS", "")
    return frozenset(a.strip().lower() for a in raw.split(",") if a.strip())


# ---------- contract ----------

def active_pact(user_id: str):
    pact = dict(SAMPLE_PACT)
    return pact, {"version": pact["version"], "revoked": False, "is_latest": True}


def build_context(opportunity_id: int, user: dict) -> dict:
    opp = db.query("SELECT * FROM opportunities WHERE id = %s", (opportunity_id,))[0]
    item = db.query(
        "SELECT li.*, o.order_no, o.order_date, o.delivery_date, o.id AS order_id "
        "FROM line_items li JOIN orders o ON o.id = li.order_id WHERE li.id = %s",
        (opp["line_item_id"],))[0]
    listing = None
    if opp["listing_id"] is not None:
        rows = db.query("SELECT * FROM listings WHERE id = %s", (opp["listing_id"],))
        if rows:
            listing = rows[0]
            ph = db.query("SELECT price, seen_at FROM price_history WHERE listing_id = %s "
                          "ORDER BY seen_at DESC LIMIT 1", (listing["id"],))
            listing["price"] = float(ph[0]["price"]) if ph else None
            listing["seen_at"] = ph[0]["seen_at"] if ph else None
    pol = db.query("SELECT * FROM store_policies WHERE store = %s", (item["store"],))
    policy = pol[0] if pol else None
    if policy:
        policy["final_sale_categories"] = _json(policy["final_sale_categories"])
        policy["support_domains"] = _json(policy["support_domains"])
    pc = db.query("SELECT * FROM policy_checks WHERE opportunity_id = %s ORDER BY id DESC LIMIT 1",
                  (opportunity_id,))
    order_items = [r["id"] for r in db.query("SELECT id FROM line_items WHERE order_id = %s",
                                             (item["order_id"],))]
    marks = ",".join(["%s"] * len(order_items))
    prior = db.query(
        f"SELECT proposal_hash, action_type FROM executed_actions WHERE line_item_id IN ({marks}) "
        "AND status IN ('running','done') AND action_type IN "
        "('price_adjust_email','claim_packet','rebuy_return')", tuple(order_items))
    claim = None
    first = db.query(
        "SELECT m.to_addr, m.thread_id FROM messages m JOIN executed_actions e "
        "ON e.proposal_hash = m.proposal_hash WHERE e.line_item_id = %s AND e.status = 'done' "
        "AND e.action_type IN ('price_adjust_email','claim_packet') AND m.direction = 'out' "
        "ORDER BY m.id LIMIT 1", (item["id"],))
    if first:
        fu = db.query("SELECT COUNT(*) AS n FROM executed_actions WHERE line_item_id = %s AND "
                      "action_type = 'claim_followup' AND status IN ('running','done')", (item["id"],))
        claim = {"recipient": first[0]["to_addr"], "thread_id": first[0]["thread_id"],
                 "followups": int(fu[0]["n"])}
    month = clock.today().strftime("%Y-%m")
    done = db.query("SELECT COALESCE(SUM(amount_usd),0) AS s FROM executed_actions WHERE "
                    "action_type = 'rebuy_return' AND status IN ('running','done') AND "
                    "created_at LIKE %s", (month + "%",))
    approved = db.query("SELECT proposal_hash, amount_usd FROM approvals WHERE status = 'approved'")
    u = db.query("SELECT * FROM users WHERE id = %s", (user["id"],))
    pact, meta = active_pact(user["id"])
    return {
        "today": clock.today(), "now": clock.now(), "opportunity": opp, "item": item,
        "listing": listing, "policy": policy, "policy_check": pc[0] if pc else None,
        "pact_meta": meta, "session_user_id": user["id"], "pact_owner_id": pact["user_id"],
        "user_email": (u[0]["email"] or "").lower() if u else "",
        "month_rebuys": float(done[0]["s"]),
        "approved_not_run": [(a["proposal_hash"], float(a["amount_usd"])) for a in approved],
        "prior_claims": prior, "claim": claim,
        "demo_mode": demo_mode(), "demo_recipients": demo_recipients(),
    }


def run_gate(p: dict, pact: dict, ctx: dict, human_approved: bool = False) -> Decision:
    results: list = []
    h = None
    try:
        h = proposal_hash(p)
        return _gate(p, pact, ctx, human_approved, results, h)
    except Exception as e:  # the gate never crashes
        results.append({"rule": "R0", "pass": False, "why": f"gate error: {e!r}", "values": {}})
        return Decision("BLOCK", results, h)


def _gate(p, pact, ctx, human_approved, results, h) -> Decision:
    def check(rule, ok, why, **values):
        results.append({"rule": rule, "pass": bool(ok), "why": why, "values": values})
        return bool(ok)

    def block():
        return Decision("BLOCK", results, h)

    a = p.get("action_type")
    opp, item, listing, policy = ctx["opportunity"], ctx["item"], ctx["listing"], ctx["policy"]
    email = p.get("email") or {}
    rebuy, ret = p.get("rebuy"), p.get("return")
    to = (email.get("to") or "").strip().lower()

    # R0 well formed + bound to the opportunity's own rows
    problems = []
    if a not in ACTIONS:
        problems.append(f"unknown action {a!r}")
    for k, want in (("opportunity_id", opp["id"]), ("line_item_id", opp["line_item_id"]),
                    ("store", item["store"]), ("listing_id", opp["listing_id"])):
        if p.get(k) != want:
            problems.append(f"{k} {p.get(k)!r} != {want!r}")
    if a in EMAILS:
        if not to:
            problems.append("email.to missing")
        if email.get("template") != TEMPLATE_FOR[a]:
            problems.append(f"template {email.get('template')!r}")
    if a == "claim_followup" and not email.get("thread_id"):
        problems.append("thread_id missing")
    if a == "rebuy_return" or rebuy is not None:
        if (rebuy or {}).get("listing_id") != opp["listing_id"]:
            problems.append("rebuy.listing_id mismatch")
    if a == "rebuy_return" or ret is not None:
        if (ret or {}).get("order_no") != item["order_no"]:
            problems.append("return.order_no mismatch")
    if a == "deadline_alert" and _date(p.get("alert_at")) is None:
        problems.append("alert_at missing")
    if policy is None:
        problems.append("no store_policies row")
    if not check("R0", not problems, "; ".join(problems) or "bound to its rows", problems=problems):
        return block()

    # R2 action allowed
    allowed = set(pact["actions"])
    ok = a in allowed or (a == "claim_followup" and allowed & CLAIMS)
    if not check("R2", ok, "action allowed by Pact", action=a, allowed=sorted(allowed)):
        return block()

    # R3 store allowed (item and listing, from the DB)
    stores = [item["store"]] + ([listing["store"]] if listing else [])
    if not check("R3", all(s in pact["stores"] for s in stores), "store allowed",
                 stores=stores, allowed=pact["stores"]):
        return block()

    # R4 category
    cat = item["category"]
    excluded = set(pact["excluded_categories"]) | set(policy["final_sale_categories"])
    if not check("R4", cat not in excluded, "category not excluded / final sale",
                 category=cat, excluded=sorted(excluded)):
        return block()

    # R6 citation for this store + opportunity, fresh
    pc = ctx["policy_check"]
    fetched = _ts(pc["fetched_at"]) if pc else None
    ok = (pc is not None and pc["senso_source_id"] and pc["store"] == item["store"]
          and pc["opportunity_id"] == opp["id"] and fetched is not None
          and ctx["now"] - fetched <= timedelta(hours=24))
    if not check("R6", ok, "fresh citation for this store",
                 source_id=pc and pc["senso_source_id"], fetched_at=pc and pc["fetched_at"]):
        return block()

    # R6b adjustments offered
    if a in EMAILS and not check("R6b", bool(policy["price_adjust_allowed"]),
                                 "store offers price adjustments",
                                 price_adjust_allowed=bool(policy["price_adjust_allowed"])):
        return block()

    # R6c channel
    if a in CLAIMS:
        ch = policy["claim_channel"]
        ok = (ch == "email") if a == "price_adjust_email" else (ch != "email")
        if not check("R6c", ok, "claim channel matches action", channel=ch, action=a):
            return block()

    # R7 window
    end = window_end(a, item, policy)
    left = (end - ctx["today"]).days
    need = pact["buffer_days"] if (a in CLAIMS or a == "rebuy_return") else 0
    ok = left >= need
    alert_at = _date(p.get("alert_at"))
    if a == "deadline_alert":
        ok = ok and alert_at is not None and alert_at <= end
    if not check("R7", ok, "window open long enough", window_end=end.isoformat(),
                 days_left=left, need_days=need, alert_at=alert_at and alert_at.isoformat()):
        return block()

    # R5 same product
    if a in CLAIMS or a == "rebuy_return":
        if listing is None:
            check("R5", False, "no listing", bought=item["variant"], offered=None)
            return block()
        same_store = listing["store"] == item["store"]
        if same_store:
            ok = listing["sku"] == item["sku"] and listing["variant"] == item["variant"]
        else:
            ident = ((item["upc"] and listing["upc"] == item["upc"]) or
                     (item["model_number"] and listing["model_number"] == item["model_number"]))
            ok = bool(ident) and listing["variant"] == item["variant"]
        if a in CLAIMS and not same_store and not policy["competitor_match_allowed"]:
            ok = False
        if not check("R5", ok, "same product", bought=item["variant"], offered=listing["variant"],
                     bought_sku=item["sku"], offered_sku=listing["sku"],
                     item_store=item["store"], listing_store=listing["store"]):
            return block()

        # R8 net savings
        paid, price = float(item["price_paid"]), float(listing["price"])
        fees = 0.0
        if a == "rebuy_return":
            fees = float(policy["restocking_fee_pct"] or 0) * paid + float(policy["return_shipping_usd"] or 0)
        net = round(paid - price - fees, 2)
        if not check("R8", net >= pact["min_net_savings_usd"], "worth it", net=net,
                     min=pact["min_net_savings_usd"], paid=paid, price=price, fees=fees):
            return block()

    # R9 spending caps
    if a == "rebuy_return":
        price = float(listing["price"])
        others = sum(amt for hh, amt in ctx["approved_not_run"] if hh != h)
        committed = ctx["month_rebuys"] + others
        ok = price <= pact["max_rebuy_price_usd"] and committed + price <= pact["monthly_cap_usd"]
        if not check("R9", ok, "within spending limits", price=price,
                     per_purchase_cap=pact["max_rebuy_price_usd"], month_committed=committed,
                     monthly_cap=pact["monthly_cap_usd"]):
            return block()

    # R10 recipients
    if a in EMAILS:
        if ctx["demo_mode"]:
            ok = to in ctx["demo_recipients"]
            mode = "demo"
        elif a == "claim_packet":
            ok = to == ctx["user_email"]
            mode = "live"
        else:
            ok = to.rsplit("@", 1)[-1] in policy["support_domains"]
            mode = "live"
        if not check("R10", ok, "recipient allowed", to=to, mode=mode,
                     allowed=sorted(ctx["demo_recipients"]) if ctx["demo_mode"] else None):
            return block()

    # R11 one claim per order
    if a in CLAIMS or a == "rebuy_return":
        if not check("R11", not ctx["prior_claims"], "no earlier claim on this order",
                     prior=len(ctx["prior_claims"])):
            return block()

    # R13 follow-ups
    if a == "claim_followup":
        c = ctx["claim"]
        ok = (c is not None and to == (c["recipient"] or "").lower()
              and email.get("thread_id") == c["thread_id"]
              and c["followups"] < pact["max_followups_per_thread"])
        if not check("R13", ok, "same thread, same recipient, under the limit",
                     to=to, claim_recipient=c and c["recipient"],
                     thread_id=email.get("thread_id"), claim_thread=c and c["thread_id"],
                     followups=c and c["followups"], max=pact["max_followups_per_thread"]):
            return block()

    # R12 ask first
    if not human_approved:
        if a == "rebuy_return" and float(listing["price"]) > pact["ask_first_above_usd"]:
            check("R12", False, "above ask-first limit", price=float(listing["price"]),
                  ask=pact["ask_first_above_usd"])
            return Decision("ASK", results, h, end.isoformat())
        conf = float(item["parse_confidence"] or 0)
        if conf < 0.8:
            check("R12", False, "low parse confidence", confidence=conf)
            return Decision("ASK", results, h, end.isoformat())
    return Decision("ALLOW", results, h, end.isoformat())


def run_approved(p: dict, pact: dict, ctx: dict, approval: dict) -> Decision:
    h = proposal_hash(p)
    problems = []
    if approval.get("status") != "approved":
        problems.append(f"status {approval.get('status')!r}")
    if _ts(approval.get("expires_at")) is None or _ts(approval["expires_at"]) <= ctx["now"]:
        problems.append("expired")
    if approval.get("proposal_hash") != h:
        problems.append("proposal changed")
    if approval.get("user_id") != ctx["pact_owner_id"] or ctx["session_user_id"] != ctx["pact_owner_id"]:
        problems.append("not the Pact owner")
    # Persona re-check within 15 minutes: NOT implemented in the stub.
    if problems:
        return Decision("BLOCK", [{"rule": "A1", "pass": False, "why": "; ".join(problems),
                                   "values": {"problems": problems}}], h)
    d = run_gate(p, pact, ctx, human_approved=True)
    d.results.insert(0, {"rule": "A1", "pass": True, "why": "approval valid", "values": {}})
    return d


def _exec(conn, sql, params):
    if isinstance(conn, db._SqliteConn):
        cur = conn.execute(sql, params)
        conn.commit()
        return cur
    cur = conn.cursor()
    cur.execute(sql, params)
    conn.commit()
    return cur


def execute_once(conn, decision, p: dict, action_fn, amount_usd: float = 0.0):
    if decision.decision != "ALLOW" or not decision.proposal_hash:
        raise ValueError("execute_once needs an ALLOW decision with a proposal hash")
    h = decision.proposal_hash
    try:
        _exec(conn, "INSERT INTO executed_actions (proposal_hash, line_item_id, action_type, "
                    "amount_usd, status, result, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
              (h, p.get("line_item_id"), p.get("action_type"), amount_usd, "running", None, clock.ts()))
    except Exception as e:
        if "UNIQUE" not in str(e).upper() and "DUPLICATE" not in str(e).upper():
            raise
        cur = _exec(conn, "UPDATE executed_actions SET status = 'running' "
                          "WHERE proposal_hash = %s AND status = 'failed'", (h,))
        if cur.rowcount != 1:
            raise AlreadyRan(h)
    try:
        result = str(action_fn())
        status = "done"
    except Exception as e:
        result, status = f"error: {e!r}", "failed"
    _exec(conn, "UPDATE executed_actions SET status = %s, result = %s WHERE proposal_hash = %s",
          (status, result[:1000], h))
    return status, result


def record(p: dict, pact: dict | None, decision, action_result=None) -> int:
    if isinstance(decision, str):
        name, results, h = decision, [], proposal_hash(p)
    else:
        name, results, h = decision.decision, decision.results, decision.proposal_hash
    return db.execute(
        "INSERT INTO audit_log (ts, pact_id, pact_version, proposal_id, proposal_hash, "
        "rule_results_json, decision, action_result) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (clock.ts(), pact and pact["pact_id"], pact and pact["version"], p.get("proposal_id"), h,
         json.dumps(results, default=str), name,
         None if action_result is None else json.dumps(action_result, default=str)))
