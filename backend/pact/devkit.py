"""DEV KIT: lets Siva's part run end to end on its own, so teammates can plug in theirs.

Everything here is a stand-in and yields to the real module as soon as it exists:
  backend/db.py        (A)  -> sqlite stand-in with the section 3.3 tables + seed rows from 3.8
  backend/actor/       (B)  -> stub actors that record a message instead of sending anything
  /activity            (A)  -> served here only while the stand-in db is active
Nothing here sends email, buys anything or calls an outside service.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import types
from datetime import date, datetime, timedelta

SCHEMA = """
CREATE TABLE raw_emails (id INTEGER PRIMARY KEY, gmail_msg_id TEXT, from_addr TEXT, subject TEXT, received_at TEXT, body TEXT, parsed INT DEFAULT 0);
CREATE TABLE orders (id INTEGER PRIMARY KEY, store TEXT, order_no TEXT, order_date TEXT, delivery_date TEXT, email_id INT);
CREATE TABLE line_items (id INTEGER PRIMARY KEY, order_id INT, store TEXT, title TEXT, sku TEXT, upc TEXT, model_number TEXT, variant TEXT, category TEXT, price_paid REAL, qty INT, parse_confidence REAL, status TEXT DEFAULT 'watching');
CREATE TABLE listings (id INTEGER PRIMARY KEY, store TEXT, sku TEXT, upc TEXT, model_number TEXT, variant TEXT, url TEXT, title TEXT);
CREATE TABLE price_history (listing_id INT, price REAL, seen_at TEXT);
CREATE TABLE opportunities (id INTEGER PRIMARY KEY, line_item_id INT, listing_id INT, kind TEXT, drop_usd REAL, similarity REAL, rerank_score REAL, created_at TEXT, status TEXT DEFAULT 'open');
CREATE TABLE store_policies (store TEXT PRIMARY KEY, claim_channel TEXT, price_adjust_allowed INT, competitor_match_allowed INT, adjust_window_days INT, adjust_window_starts TEXT, return_window_days INT, return_window_starts TEXT, final_sale_categories TEXT, restocking_fee_pct REAL, return_shipping_usd REAL, support_domains TEXT, senso_doc_id TEXT);
CREATE TABLE policy_checks (id INTEGER PRIMARY KEY, opportunity_id INT, store TEXT, question TEXT, answer TEXT, cited_text TEXT, source_url TEXT, senso_source_id TEXT, revision TEXT, fetched_at TEXT);
CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT, email TEXT, persona_inquiry_id TEXT, persona_status TEXT DEFAULT 'none', verified_at TEXT, reverified_at TEXT);
CREATE TABLE pacts (pact_id TEXT, version INT, user_id TEXT, pact_text TEXT, created_at TEXT, status TEXT, PRIMARY KEY (pact_id, version));
CREATE TABLE pact_revocations (pact_id TEXT, version INT, revoked_at TEXT, reason TEXT, signature TEXT);
CREATE TABLE proposals (id TEXT PRIMARY KEY, opportunity_id INT, action_type TEXT, payload_json TEXT, proposal_hash TEXT, reasoning TEXT, created_at TEXT);
CREATE TABLE approvals (id INTEGER PRIMARY KEY, proposal_id TEXT, proposal_hash TEXT, user_id TEXT, amount_usd REAL, status TEXT DEFAULT 'pending', expires_at TEXT, decided_at TEXT);
CREATE TABLE executed_actions (proposal_hash TEXT PRIMARY KEY, line_item_id INT, action_type TEXT, amount_usd REAL, status TEXT, result TEXT, created_at TEXT);
CREATE TABLE audit_log (id INTEGER PRIMARY KEY, ts TEXT, pact_id TEXT, pact_version INT, proposal_id TEXT, proposal_hash TEXT, rule_results_json TEXT, decision TEXT, action_result TEXT);
CREATE TABLE messages (id INTEGER PRIMARY KEY, opportunity_id INT, proposal_hash TEXT, direction TEXT, thread_id TEXT, to_addr TEXT, subject TEXT, body TEXT, sent_at TEXT);
CREATE TABLE scheduled_tasks (id INTEGER PRIMARY KEY, user_id TEXT, name TEXT, kind TEXT, schedule_type TEXT, interval_min INT, at_time TEXT, enabled INT DEFAULT 1, next_run TEXT, last_run TEXT, last_status TEXT, last_result TEXT, created_at TEXT);
CREATE TABLE scheduled_runs (id INTEGER PRIMARY KEY, task_id INT, ran_at TEXT, status TEXT, result TEXT);
CREATE TABLE claim_packets (id INTEGER PRIMARY KEY, opportunity_id INT, proposal_hash TEXT, store TEXT, packet_json TEXT, chat_script TEXT, created_at TEXT);
"""

FMT = "%Y-%m-%d %H:%M:%S"
_lock = threading.RLock()


def _now() -> datetime:
    return datetime.utcnow().replace(microsecond=0)


# --------------------------------------------------------------------------- db
class FakeDB(types.ModuleType):
    """Same surface as A's backend/db.py: query(), execute(), connection()."""

    def __init__(self):
        super().__init__("backend.db")
        self.IS_DEVKIT = True
        self._new()

    def _new(self):
        self._conn = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.create_function("JSON_EXTRACT_STRING", 2, lambda j, k: (json.loads(j) or {}).get(k) if j else None)
        self._conn.executescript(SCHEMA)

    @staticmethod
    def _sql(sql):
        return sql.replace("%s", "?")

    def query(self, sql, params=()):
        with _lock:
            return [dict(r) for r in self._conn.execute(self._sql(sql), tuple(params)).fetchall()]

    def execute(self, sql, params=()):
        with _lock:
            return self._conn.execute(self._sql(sql), tuple(params)).lastrowid

    def connection(self):
        return self._conn

    def reset(self):
        with _lock:
            self._conn.close()
            self._new()
            seed(self)


# ------------------------------------------------------------------------- actors
class _Actor(types.ModuleType):
    """Stand-in for B's backend/actor/. Records what WOULD be sent; sends nothing."""

    IS_DEVKIT = True

    def __init__(self, db):
        super().__init__("backend.actor")
        self._db = db

    def send_email(self, decision, to, subject, body, transport=None, thread_id=None):
        thread = thread_id or "thr_" + (decision.proposal_hash or "x")[-8:]
        self._db.execute("INSERT INTO messages (opportunity_id, proposal_hash, direction, thread_id, to_addr, subject, body, sent_at) VALUES (NULL,%s,'out',%s,%s,%s,%s,%s)",
                         (decision.proposal_hash, thread, to, subject, body, _now().strftime(FMT)))
        return thread

    def rebuy_return(self, decision, listing_id, original_order_no):
        return f"[devkit] rebought listing {listing_id} and returned order {original_order_no}"

    def build_claim_packet(self, decision, opportunity_id):
        return self._db.execute("INSERT INTO claim_packets (opportunity_id, proposal_hash, store, packet_json, chat_script, created_at) VALUES (%s,%s,'','{}','[devkit] paste this in chat',%s)",
                                (opportunity_id, decision.proposal_hash, _now().strftime(FMT)))


def install(db_module: bool = True, actor_module: bool = True):
    """Install stand-ins only for modules that don't exist yet. Returns what was installed."""
    import importlib
    import backend
    installed = []
    db = None
    if db_module and not _exists("backend.db"):
        db = FakeDB()
        sys.modules["backend.db"] = db
        backend.db = db
        seed(db)
        installed.append("db")
    if not _exists("backend.watch"):          # A's watch module: stand-in that finds nothing new
        w = types.ModuleType("backend.watch"); w.tick = lambda: []; w.IS_DEVKIT = True
        sys.modules["backend.watch"] = w; backend.watch = w; installed.append("watch")
    if actor_module and not _exists("backend.actor"):
        db = db or importlib.import_module("backend.db")
        act = _Actor(db)
        sys.modules["backend.actor"] = act
        backend.actor = act
        installed.append("actor")
    return installed


def _exists(name: str) -> bool:
    import importlib
    try:
        importlib.import_module(name)
        return True
    except ModuleNotFoundError as e:
        if e.name == name or name.startswith(str(e.name) + "."):
            return False
        raise


# --------------------------------------------------------------------- seed data
def propose(db, opp: dict) -> dict:
    """Stand-in for B's propose(): deterministic choice, every fact copied from the database."""
    from .gate import window_end
    from .context import _json
    item = db.query("SELECT li.*, o.order_no, o.order_date, o.delivery_date FROM line_items li JOIN orders o ON o.id=li.order_id WHERE li.id=%s", (opp["line_item_id"],))[0]
    pol = db.query("SELECT * FROM store_policies WHERE store=%s", (item["store"],))[0]
    support = os.environ.get("DEMO_SUPPORT_INBOX") or "support@mockstore.test"
    user = db.query("SELECT email FROM users LIMIT 1")[0]["email"]
    p = {"proposal_id": f"prop_{opp['id']}", "opportunity_id": opp["id"], "line_item_id": item["id"], "store": item["store"],
         "listing_id": opp["listing_id"], "action_type": "none", "email": None, "rebuy": None, "return": None, "alert_at": None,
         "reasoning": "Prepared by the devkit stand-in."}
    today = date.today()
    if opp["kind"] == "deadline":
        end = window_end("deadline_alert", item, pol)
        p.update(action_type="deadline_alert", listing_id=None, alert_at=(end - timedelta(days=1)).isoformat())
        return p
    adj_end = window_end("price_adjust_email", item, pol)
    if adj_end and adj_end >= today:
        if pol["claim_channel"] == "email":
            p.update(action_type="price_adjust_email", email={"to": support, "template": "price_adjust_v1", "thread_id": None})
        else:
            p.update(action_type="claim_packet", email={"to": user, "template": "packet_v1", "thread_id": None})
    else:
        p.update(action_type="rebuy_return", rebuy={"listing_id": opp["listing_id"]}, **{"return": {"order_no": item["order_no"]}})
    return p


def seed(db) -> None:
    """The six rows from section 3.8, dated relative to today, then run the real gate on each."""
    from . import sign as signer
    from .context import active_pact, build_context, record
    from .gate import run_gate
    from .once import execute_once

    os.environ.setdefault("PACT_SIGNING_KEY", "dev-only-signing-key-do-not-use-in-prod-0123456789")
    os.environ.setdefault("DEMO_MODE", "true")
    os.environ.setdefault("DEMO_SUPPORT_INBOX", "support@mockstore.test")
    os.environ.setdefault("DEMO_ALLOWED_RECIPIENTS", "support@mockstore.test,demo@user.test")

    now, today = _now(), date.today()
    d = lambda n: (today - timedelta(days=n)).isoformat()      # noqa: E731
    stamp = lambda dt: dt.strftime(FMT)                        # noqa: E731

    db.execute("INSERT INTO users VALUES ('u_demo','Demo User','demo@user.test','inq_dev','approved',%s,NULL)", (stamp(now - timedelta(days=2)),))
    pol = lambda *a: db.execute("INSERT INTO store_policies VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", a)   # noqa: E731
    pol("mockstore", "email", 1, 0, 14, "purchase", 30, "delivery", '["clearance"]', 0, 0, '["mockstore.test"]', "dev-doc-mock")
    pol("target", "chat", 1, 0, 14, "purchase", 30, "delivery", '["gift_cards"]', 0, 0, '["target.com"]', "dev-doc-target")
    pol("bestbuy", "chat", 1, 0, 15, "delivery", 15, "delivery", '[]', 0, 0, '["bestbuy.com"]', "dev-doc-bestbuy")

    # (store, title, sku, upc, variant, category, paid, order_age, delivery_age, order_no, listing price, listing variant, listing store, price age (min))
    rows = [
        ("mockstore", "Aurora Wireless Earbuds", "MS-1001", "111", "Black", "electronics", 129.99, 9, 7, "MS-55001", 95.99, "Black", "mockstore", 3),
        ("mockstore", "Nimbus Smart Lamp", "MS-2002", "222", "White", "home", 239.00, 18, 16, "MS-55021", 199.00, "White", "mockstore", 3),
        ("mockstore", "Trailhead Backpack", "MS-3003", "333", "Black", "outdoor", 89.00, 8, 6, "MS-55031", 64.00, "Blue", "mockstore", 3),
        ("mockstore", "Breeze Desk Fan", "MS-4004", "444", "Gray", "clearance", 59.00, 10, 8, "MS-55041", 39.00, "Gray", "mockstore", 3),
        ("bestbuy", "Wireless Mouse (cached real price)", "BB-6543", "555", "", "electronics", 60.00, 6, 4, "BB-1", 54.00, "", "bestbuy", 60 * 24 * 2),
        ("target", "Cotton Throw Blanket", "T-7788", "666", "", "home", 40.00, 29, 27, "T-1", 40.00, "", "target", 60 * 24 * 2),
    ]
    opps = []
    for i, (store, title, sku, upc, var, cat, paid, oa, da, ono, lp, lvar, lstore, age) in enumerate(rows, 1):
        oid = db.execute("INSERT INTO orders (store, order_no, order_date, delivery_date) VALUES (%s,%s,%s,%s)", (store, ono, d(oa), d(da)))
        li = db.execute("INSERT INTO line_items (order_id, store, title, sku, upc, model_number, variant, category, price_paid, qty, parse_confidence) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,1,0.95)",
                        (oid, store, title, sku, upc, "M-" + sku, var, cat, paid))
        lsku = sku if lvar == var else sku                     # backpack: same SKU, different colour
        ls = db.execute("INSERT INTO listings (store, sku, upc, model_number, variant, url, title) VALUES (%s,%s,%s,%s,%s,%s,%s)", (lstore, lsku, upc, "M-" + sku, lvar, f"https://example.test/{sku}", title))
        db.execute("INSERT INTO price_history VALUES (%s,%s,%s)", (ls, lp, stamp(now - timedelta(minutes=age))))
        kind = "deadline" if store == "target" else "price_drop"
        opp = db.execute("INSERT INTO opportunities (line_item_id, listing_id, kind, drop_usd, created_at) VALUES (%s,%s,%s,%s,%s)",
                         (li, None if kind == "deadline" else ls, kind, round(paid - lp, 2), stamp(now)))
        opps.append(opp)
        cited = {"mockstore": "Price adjustments are available within 14 days of purchase, on our own prices only.",
                 "target": "Target adjusts its own price within 14 days of purchase.",
                 "bestbuy": "Best Buy price-matches within 15 days of delivery."}[store]
        db.execute("INSERT INTO policy_checks (opportunity_id, store, question, answer, cited_text, source_url, senso_source_id, revision, fetched_at) VALUES (%s,%s,'dev',%s,%s,%s,%s,'dev-r1',%s)",
                   (opp, store, cited, cited, f"https://example.test/{store}/policy", f"dev-senso-{store}", stamp(now - timedelta(hours=1))))

    from . import scheduler as sch
    for name, kind, st, mins, at in [("Check prices", "price_watch", "interval", 5, None), ("Daily summary", "daily_summary", "daily", None, "08:00"),
                                      ("Return deadline check", "deadline_check", "daily", None, "09:00")]:
        t = {"schedule_type": st, "interval_min": mins, "at_time": at}
        db.execute("INSERT INTO scheduled_tasks (user_id, name, kind, schedule_type, interval_min, at_time, enabled, next_run, created_at) VALUES ('u_demo',%s,%s,%s,%s,%s,1,%s,%s)",
                   (name, kind, st, mins, at, sch.next_after(t, sch._now()).strftime(FMT), sch._now().strftime(FMT)))

    # a signed Pact for the demo user (the real one is created through POST /pact)
    pact = {"pact_id": "pact_dev", "version": 1, "user_id": "u_demo", "persona_inquiry_id": "inq_dev", "created_at": today.isoformat(),
            "expires_at": (today + timedelta(days=90)).isoformat(), "allowed_actions": ["price_adjust_email", "claim_packet", "rebuy_return", "deadline_alert"],
            "stores": ["mockstore", "target", "bestbuy"], "excluded_categories": ["clearance", "gift_cards"], "min_net_savings": 10,
            "ask_first_above": 150, "max_rebuy_price_usd": 250, "monthly_cap": 300, "buffer_days": 2, "max_claims_per_order": 1, "max_followups": 3}
    db.execute("INSERT INTO pacts VALUES ('pact_dev',1,'u_demo',%s,%s,'active')", (signer.sign(pact), stamp(now)))

    # run the real pipeline once per opportunity: propose -> gate -> record -> act / ask
    user = {"id": "u_demo"}
    for opp_id in opps:
        opp = db.query("SELECT * FROM opportunities WHERE id=%s", (opp_id,))[0]
        p = propose(db, opp)
        pact_dict, _ = active_pact("u_demo")
        ctx = build_context(opp_id, user)
        dec = run_gate(p, pact_dict, ctx)
        db.execute("INSERT INTO proposals VALUES (%s,%s,%s,%s,%s,%s,%s)", (p["proposal_id"], opp_id, p["action_type"], json.dumps(p), dec.proposal_hash, p["reasoning"], stamp(now)))
        record(p, pact_dict, dec)
        if dec.decision == "ALLOW":
            amount = 0.0
            actor = sys.modules.get("backend.actor") or _Actor(db)
            fn = (lambda: actor.send_email(dec, p["email"]["to"], "Price adjustment request", "(devkit)", None, None)) if p["action_type"] == "price_adjust_email" else (lambda: "[devkit] done")
            execute_once(db.connection(), dec, p, fn, amount)
        elif dec.decision == "ASK":
            price = db.query("SELECT price FROM price_history WHERE listing_id=%s ORDER BY seen_at DESC LIMIT 1", (opp["listing_id"],))[0]["price"]
            db.execute("INSERT INTO approvals (proposal_id, proposal_hash, user_id, amount_usd, status, expires_at) VALUES (%s,%s,'u_demo',%s,'pending',%s)",
                       (p["proposal_id"], dec.proposal_hash, price, stamp(now + timedelta(hours=24))))


# --------------------------------------------------------------------- /activity
def activity(db) -> list[dict]:
    """Stand-in for A's GET /activity (field names are what frontend/app.js reads)."""
    out = []
    for a in db.query("SELECT * FROM audit_log ORDER BY id DESC"):
        prop = db.query("SELECT * FROM proposals WHERE id=%s", (a["proposal_id"],))
        if not prop:
            continue
        p = json.loads(prop[0]["payload_json"])
        opp = db.query("SELECT * FROM opportunities WHERE id=%s", (p["opportunity_id"],))[0]
        it = db.query("SELECT li.*, o.order_date, o.order_no FROM line_items li JOIN orders o ON o.id=li.order_id WHERE li.id=%s", (opp["line_item_id"],))[0]
        pr = db.query("SELECT price, seen_at FROM price_history WHERE listing_id=%s ORDER BY seen_at DESC LIMIT 1", (opp["listing_id"],)) if opp["listing_id"] else []
        pc = db.query("SELECT * FROM policy_checks WHERE opportunity_id=%s ORDER BY id DESC LIMIT 1", (opp["id"],))
        results = json.loads(a["rule_results_json"] or "[]")
        failed = next((r["rule"] for r in results if not r["pass"]), None)
        appr = db.query("SELECT id FROM approvals WHERE proposal_hash=%s AND status='pending'", (a["proposal_hash"],)) if a["decision"] == "ASK" else []
        pk = db.query("SELECT pact_text FROM pacts WHERE pact_id=%s AND version=%s", (a["pact_id"], a["pact_version"])) if a["pact_id"] else []
        cs = db.query("SELECT chat_script FROM claim_packets WHERE proposal_hash=%s", (a["proposal_hash"],))
        out.append({
            "audit_id": a["id"], "ts": a["ts"], "opportunity_id": opp["id"], "line_item_id": it["id"], "proposal_id": p["proposal_id"],
            "item_title": it["title"], "variant": it["variant"], "store": it["store"], "price_paid": it["price_paid"], "order_date": it["order_date"],
            "found_price": pr[0]["price"] if pr else None, "seen_at": pr[0]["seen_at"] if pr else None, "drop_usd": opp["drop_usd"],
            "cited_text": pc[0]["cited_text"] if pc else None, "source_url": pc[0]["source_url"] if pc else None,
            "senso_source_id": pc[0]["senso_source_id"] if pc else None, "decision": a["decision"], "failed_rule": failed,
            "rule_results": results, "action_type": p["action_type"], "kind": opp["kind"], "approval_id": appr[0]["id"] if appr else None,
            "pact_version": a["pact_version"], "pact_hash": (json.loads(pk[0]["pact_text"]).get("hash") if pk else None),
            "chat_script": cs[0]["chat_script"] if cs else None,
        })
    return out
