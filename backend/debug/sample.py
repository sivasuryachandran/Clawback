"""SAMPLE DATA for Under the hood (?mock=1) and for tests (Teammate C).

Builds an in-memory SQLite database with the section 3.3 tables and fills it with the demo state
from section 3.8, so the real panel code can be developed and rehearsed before A, B and Siva have
pushed anything. Everything it shows is labelled SAMPLE in the UI. It is never used unless the
request says ?mock=1 (or a test asks for it), and it never touches SingleStore.

The sample Pact is signed with a throwaway key using the documented algorithm (section 4.1 step 1):
  hash      = sha256(canonical JSON of the body without hash/signature)
  signature = HMAC-SHA256(key, same canonical bytes)
That lets the signature panel show a real recomputed check mark without Siva's module.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

SAMPLE_KEY = b"sample-key-not-a-secret"
SCHEMA = Path(__file__).with_name("sample_schema.sql")
FMT = "%Y-%m-%d %H:%M:%S"


def _canon(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()


def sign_sample(body: dict) -> str:
    body = {k: v for k, v in body.items() if k not in ("hash", "signature")}
    raw = _canon(body)
    full = dict(body, hash="sha256:" + hashlib.sha256(raw).hexdigest(),
                signature=hmac.new(SAMPLE_KEY, raw, hashlib.sha256).hexdigest())
    return _canon(full).decode()


def verify_sample(pact_text: str) -> dict | None:
    try:
        obj = json.loads(pact_text)
        body = {k: v for k, v in obj.items() if k not in ("hash", "signature")}
        raw = _canon(body)
        ok_hash = hmac.compare_digest(obj.get("hash", ""), "sha256:" + hashlib.sha256(raw).hexdigest())
        ok_sig = hmac.compare_digest(obj.get("signature", ""), hmac.new(SAMPLE_KEY, raw, hashlib.sha256).hexdigest())
        return obj if (ok_hash and ok_sig) else None
    except Exception:
        return None


def revocation_sig(pact_id: str, version: int, revoked_at: str) -> str:
    return hmac.new(SAMPLE_KEY, f"{pact_id}|{version}|{revoked_at}".encode(), hashlib.sha256).hexdigest()


def verify_revocation_sample(row: dict) -> bool:
    try:
        return hmac.compare_digest(str(row.get("signature") or ""),
                                   revocation_sig(row["pact_id"], int(row["version"]), str(row["revoked_at"])))
    except Exception:
        return False


# ---- SQLite stand-in for SingleStore -------------------------------------------------------------

def _translate_ddl(sql: str) -> str:
    sql = sql.replace("BIGINT AUTO_INCREMENT PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
    return re.sub(r"VECTOR\(\s*\d+\s*,\s*F32\s*\)", "BLOB", sql)


class SampleDB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self.conn.executescript(_translate_ddl(SCHEMA.read_text()))

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.lock:
            cur = self.conn.execute(sql.replace("%s", "?"), tuple(params))
            return [dict(r) for r in cur.fetchall()]

    def run(self, sql: str, params: tuple = ()) -> int:
        with self.lock:
            cur = self.conn.execute(sql.replace("%s", "?"), tuple(params))
            self.conn.commit()
            return cur.lastrowid


_RULES = {
    "claim": ["R0", "R1", "R2", "R3", "R4", "R6", "R6b", "R6c", "R7", "R5", "R8", "R8a", "R10", "R11", "R12"],
    "rebuy": ["R0", "R1", "R2", "R3", "R4", "R6", "R7", "R5", "R8", "R8a", "R9", "R11", "R12"],
    "followup": ["R0", "R1", "R2", "R3", "R4", "R6", "R6b", "R7", "R10", "R13", "R12"],
    "alert": ["R0", "R1", "R2", "R3", "R4", "R6", "R7", "R12"],
}


def rule_results(kind: str, fail: str | None = None, why: str = "", values: dict | None = None,
                 ask: bool = False) -> list[dict]:
    """Results up to and including the first failing rule, as the gate records them."""
    out = []
    for r in _RULES[kind]:
        if r == fail:
            out.append({"rule": r, "pass": False, "why": why, "values": values or {}})
            return out
        if r == "R12" and ask:
            out.append({"rule": r, "pass": False, "why": why, "values": values or {}})
            return out
        out.append({"rule": r, "pass": True, "why": "ok", "values": {}})
    return out


def _h(name: str) -> str:
    return "sha256:" + hashlib.sha256(name.encode()).hexdigest()


def build(now: datetime | None = None) -> SampleDB:
    """The demo state: earbuds claimed + follow-up, lamp waiting for OK, backpack/fan/Best Buy stopped,
    Target return-deadline alert."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    ago = lambda **kw: (now - timedelta(**kw)).strftime(FMT)
    ahead = lambda **kw: (now + timedelta(**kw)).strftime(FMT)
    db = SampleDB()
    R = db.run
    J = json.dumps

    R("INSERT INTO users(id,name,email,persona_inquiry_id,persona_status,verified_at,reverified_at) VALUES(%s,%s,%s,%s,%s,%s,%s)",
      ("u_demo", "Demo Shopper", "demo@example.com", "inq_sample_123", "approved", ago(days=1), ago(minutes=2)))

    for store, chan, adj, comp in (("mockstore", "email", 14, 0), ("target", "chat", 14, 0), ("bestbuy", "chat", 15, 0)):
        R("INSERT INTO store_policies VALUES(%s,%s,1,%s,%s,%s,30,%s,%s,0,0,%s,%s)",
          (store, chan, comp, adj, "purchase" if store != "bestbuy" else "delivery", "delivery",
           J(["clearance"]), J([]), "doc_" + store))

    # orders + line items (ids 1..6), listings (1..5 mock store with the real stable ids, 6 Best Buy, 7 Target)
    items = [
        ("mockstore", "MS-55001", "2026-11-11", "2026-11-13", "Aurora Wireless Earbuds", "MS-1001", "Black", "electronics", 129.99, 0.97),
        ("mockstore", "MS-55021", "2026-11-02", "2026-11-04", "Nimbus Smart Lamp", "MS-2002", "White", "home", 239.00, 0.96),
        ("mockstore", "MS-55031", "2026-11-12", "2026-11-14", "Trailhead Backpack", "MS-3003", "Black", "outdoor", 89.00, 0.95),
        ("mockstore", "MS-55041", "2026-11-10", "2026-11-12", "Breeze Desk Fan", "MS-4004", "Gray", "clearance", 59.00, 0.94),
        ("bestbuy", "BBY01-SAMPLE", "2026-11-14", "2026-11-16", "Sample Best Buy keyboard", "BB-SAMPLE", "Black", "electronics", 109.99, 0.9),
        ("target", "TGT-SAMPLE", "2026-10-22", "2026-10-24", "Sample Target air fryer", "TG-SAMPLE", "Silver", "home", 89.99, 0.9),
    ]
    for i, (store, order_no, od, dd, title, sku, var, cat, paid, conf) in enumerate(items, start=1):
        R("INSERT INTO raw_emails(gmail_msg_id,from_addr,subject,received_at,body,parsed) VALUES(%s,%s,%s,%s,%s,1)",
          (f"gmail-sample-{i}", f"orders@{store}.example", f"Your order {order_no}", ago(days=9 - i), "(sample receipt body)"))
        R("INSERT INTO orders(store,order_no,order_date,delivery_date,email_id) VALUES(%s,%s,%s,%s,%s)", (store, order_no, od, dd, i))
        R("INSERT INTO line_items(order_id,store,title,sku,variant,category,price_paid,qty,parse_confidence,status) VALUES(%s,%s,%s,%s,%s,%s,%s,1,%s,'watching')",
          (i, store, title, sku, var, cat, paid, conf))

    listings = [
        (1, "mockstore", "MS-1001", "Black", "Aurora Wireless Earbuds", 95.99),
        (2, "mockstore", "MS-2002", "White", "Nimbus Smart Lamp", 199.00),
        (3, "mockstore", "MS-3003", "Black", "Trailhead Backpack", 89.00),
        (4, "mockstore", "MS-3003", "Blue", "Trailhead Backpack", 64.00),
        (5, "mockstore", "MS-4004", "Gray", "Breeze Desk Fan", 39.00),
        (6, "bestbuy", "BB-SAMPLE", "Black", "Sample Best Buy keyboard", 103.99),
        (7, "target", "TG-SAMPLE", "Silver", "Sample Target air fryer", 89.99),
    ]
    for lid, store, sku, var, title, price in listings:
        R("INSERT INTO listings(id,store,sku,variant,url,title) VALUES(%s,%s,%s,%s,%s,%s)", (lid, store, sku, var, f"/mock/p/{sku}", title))
        R("INSERT INTO price_history VALUES(%s,%s,%s)", (lid, price, ago(minutes=6 if lid == 1 else 3)))

    # opportunities: line_item -> listing
    opps = [(1, 1, 1, "price_drop", 34.00, 0.99, 0.98), (2, 2, 2, "price_drop", 40.00, 0.99, 0.97),
            (3, 3, 4, "price_drop", 25.00, 0.93, 0.90), (4, 4, 5, "price_drop", 20.00, 0.99, 0.97),
            (5, 5, 6, "price_drop", 6.00, 0.98, 0.96), (6, 6, 7, "deadline", 0.00, 0.95, 0.93)]
    for oid, li, lid, kind, drop, sim, rr in opps:
        R("INSERT INTO opportunities(id,line_item_id,listing_id,kind,drop_usd,similarity,rerank_score,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
          (oid, li, lid, kind, drop, sim, rr, ago(minutes=15)))

    cite = {
        "mockstore": ("Price adjustments apply to the identical item within 14 days of purchase.", "/mock/policy"),
        "target": ("[sample] Target will adjust the price of an item within 14 days of purchase to its own current price.", "https://www.target.com/c/price-match-guarantee/-/N-4mr5a"),
        "bestbuy": ("[sample] Best Buy matches its own lower price during the return period.", "https://www.bestbuy.com/site/help-topics/price-match-guarantee/pcmcat290300050002.c"),
    }
    for oid, store in ((1, "mockstore"), (2, "mockstore"), (3, "mockstore"), (4, "mockstore"), (5, "bestbuy"), (6, "target")):
        text, url = cite[store]
        R("INSERT INTO policy_checks(opportunity_id,store,question,answer,cited_text,source_url,senso_source_id,revision,fetched_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
          (oid, store, f"Does {store} adjust prices within its window?", "Yes, within the window.", text, url, f"senso_src_{store}", "rev-1", ago(minutes=14)))

    # Pact: v1 (revoked by the edit) and v2 (active)
    base = {"pact_id": "pact_demo", "user_id": "u_demo", "persona_inquiry_id": "inq_sample_123",
            "actions": ["price_adjust_email", "claim_packet", "rebuy_return", "deadline_alert"],
            "stores": ["mockstore", "target", "bestbuy"], "excluded_categories": ["clearance", "gift_cards"],
            "min_net_savings_usd": 10, "ask_first_usd": 150, "max_rebuy_price_usd": 250, "monthly_cap_usd": 300,
            "buffer_days": 2, "max_claims_per_order": 1, "max_followups": 3, "valid_until": "2027-02-18"}
    revoked_at = ago(minutes=40)
    for v, created, status in ((1, ago(minutes=50), "revoked"), (2, ago(minutes=40), "active")):
        R("INSERT INTO pacts VALUES(%s,%s,%s,%s,%s,%s)", ("pact_demo", v, "u_demo", sign_sample(dict(base, version=v)), created, status))
    R("INSERT INTO pact_revocations VALUES(%s,%s,%s,%s,%s)", ("pact_demo", 1, revoked_at, "replaced by v2", revocation_sig("pact_demo", 1, revoked_at)))

    # proposals + audit rows (decision per demo row)
    def prop(pid, opp, action, h):
        R("INSERT INTO proposals(id,opportunity_id,action_type,payload_json,proposal_hash,reasoning,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s)",
          (pid, opp, action, J({"proposal_id": pid, "opportunity_id": opp, "action_type": action}), h, "(sample reasoning)", ago(minutes=12)))

    def audit(minutes, pid, h, decision, results, result=None):
        R("INSERT INTO audit_log(ts,pact_id,pact_version,proposal_id,proposal_hash,rule_results_json,decision,action_result) VALUES(%s,%s,2,%s,%s,%s,%s,%s)",
          (ago(minutes=minutes), "pact_demo", pid, h, J(results), decision, J(result) if result else None))

    prop("prop_bp", 3, "price_adjust_email", _h("bp"))
    audit(12, "prop_bp", _h("bp"), "BLOCK", rule_results("claim", "R5", "different variant", {"bought": "MS-3003/Black", "offered": "MS-3003/Blue"}))
    prop("prop_fan", 4, "price_adjust_email", _h("fan"))
    audit(11, "prop_fan", _h("fan"), "BLOCK", rule_results("claim", "R4", "final sale", {"category": "clearance"}))
    prop("prop_bb", 5, "claim_packet", _h("bb"))
    audit(9, "prop_bb", _h("bb"), "BLOCK", rule_results("claim", "R8", "net savings below minimum",
          {"price_paid": 109.99, "price_found": 103.99, "net": 6.0, "min_net_savings_usd": 10}))
    prop("prop_lamp", 2, "rebuy_return", _h("lamp"))
    audit(10, "prop_lamp", _h("lamp"), "ASK", rule_results("rebuy", "R12", "above ask-first limit",
          {"price": 199.0, "ask_first_usd": 150}, ask=True))
    R("INSERT INTO approvals(proposal_id,proposal_hash,user_id,amount_usd,status,expires_at) VALUES(%s,%s,%s,%s,'pending',%s)",
      ("prop_lamp", _h("lamp"), "u_demo", 199.00, ahead(hours=23)))
    prop("prop_ear", 1, "price_adjust_email", _h("ear"))
    audit(6, "prop_ear", _h("ear"), "ALLOW", rule_results("claim"), {"sent": "ok"})
    R("INSERT INTO executed_actions VALUES(%s,%s,%s,%s,%s,%s,%s)", (_h("ear"), 1, "price_adjust_email", 34.00, "done", "sent", ago(minutes=6)))
    prop("prop_ear2", 1, "claim_followup", _h("ear2"))
    audit(4, "prop_ear2", _h("ear2"), "ALLOW", rule_results("followup"), {"sent": "ok"})
    R("INSERT INTO executed_actions VALUES(%s,%s,%s,%s,%s,%s,%s)", (_h("ear2"), 1, "claim_followup", 0, "done", "sent", ago(minutes=4)))
    prop("prop_tgt", 6, "deadline_alert", _h("tgt"))
    audit(5, "prop_tgt", _h("tgt"), "ALLOW", rule_results("alert"), {"alert_at": "2026-11-25"})
    R("INSERT INTO executed_actions VALUES(%s,%s,%s,%s,%s,%s,%s)", (_h("tgt"), 6, "deadline_alert", 0, "done", "alert set", ago(minutes=5)))

    # messages: claim out, support reply in, proof out
    R("INSERT INTO messages(opportunity_id,proposal_hash,direction,thread_id,to_addr,subject,body,sent_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
      (1, _h("ear"), "out", "thr_sample_1", "support@mockstore.example", "Price adjustment request: order MS-55001", "(sample)", ago(minutes=6)))
    R("INSERT INTO messages(opportunity_id,proposal_hash,direction,thread_id,to_addr,subject,body,sent_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
      (1, None, "in", "thr_sample_1", "demo@example.com", "Re: Price adjustment request", "Please send proof.", ago(minutes=5)))
    R("INSERT INTO messages(opportunity_id,proposal_hash,direction,thread_id,to_addr,subject,body,sent_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
      (1, _h("ear2"), "out", "thr_sample_1", "support@mockstore.example", "Re: Price adjustment request", "(sample)", ago(minutes=4)))
    return db


def sample_events() -> list[dict]:
    """What the in-memory analytics buffer would hold, for the sample Ingest/Analytics panels."""
    t = datetime.now(timezone.utc).strftime("%H:%M:%S")
    return [
        {"event": "receipt_parsed", "at": t, "props": {"model": "arcee-sample", "tokens": 812, "latency_ms": 640, "confidence": 0.97, "cost_usd": 0.0004}},
        {"event": "receipt_parsed", "at": t, "props": {"model": "arcee-sample", "tokens": 790, "latency_ms": 580, "confidence": 0.96, "cost_usd": 0.0004}},
    ]
