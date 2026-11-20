# STUB for rehearsal: replaced by Teammate A's real module (backend/db.py) tomorrow
"""Contract 3.4 (A): query(sql, params) -> list[dict]; execute(sql, params) -> int; connection().

SQLite file by default; SingleStore if SINGLESTORE_URL is set. Callers write portable SQL with
%s placeholders; only this stub translates them to SQLite's ?.

execute() returns lastrowid for INSERT and rowcount otherwise. The 3.4 contract just says
"-> int", so confirm this with A tomorrow.
"""
import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT, store TEXT, order_no TEXT,
  order_date TEXT, delivery_date TEXT NULL, email_id INTEGER);
CREATE TABLE IF NOT EXISTS line_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER, store TEXT, title TEXT,
  sku TEXT, upc TEXT, model_number TEXT, variant TEXT, category TEXT,
  price_paid REAL, qty INTEGER, parse_confidence REAL, title_vec BLOB NULL,
  status TEXT DEFAULT 'watching');
CREATE TABLE IF NOT EXISTS listings (
  id INTEGER PRIMARY KEY AUTOINCREMENT, store TEXT, sku TEXT, upc TEXT, model_number TEXT,
  variant TEXT, url TEXT, title TEXT, title_vec BLOB NULL);
CREATE TABLE IF NOT EXISTS price_history (listing_id INTEGER, price REAL, seen_at TEXT);
CREATE TABLE IF NOT EXISTS opportunities (
  id INTEGER PRIMARY KEY AUTOINCREMENT, line_item_id INTEGER, listing_id INTEGER NULL,
  kind TEXT, drop_usd REAL, similarity REAL, rerank_score REAL, created_at TEXT,
  status TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS store_policies (
  store TEXT PRIMARY KEY, claim_channel TEXT, price_adjust_allowed INTEGER,
  competitor_match_allowed INTEGER, adjust_window_days INTEGER, adjust_window_starts TEXT,
  return_window_days INTEGER, return_window_starts TEXT, final_sale_categories TEXT,
  restocking_fee_pct REAL, return_shipping_usd REAL, support_domains TEXT, senso_doc_id TEXT);
CREATE TABLE IF NOT EXISTS policy_checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, opportunity_id INTEGER, store TEXT, question TEXT,
  answer TEXT, cited_text TEXT, source_url TEXT, senso_source_id TEXT, revision TEXT NULL,
  fetched_at TEXT);
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, name TEXT, email TEXT, persona_inquiry_id TEXT NULL,
  persona_status TEXT DEFAULT 'none', verified_at TEXT NULL, reverified_at TEXT NULL);
CREATE TABLE IF NOT EXISTS proposals (
  id TEXT PRIMARY KEY, opportunity_id INTEGER, action_type TEXT, payload_json TEXT,
  proposal_hash TEXT, reasoning TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id TEXT, proposal_hash TEXT, user_id TEXT,
  amount_usd REAL, status TEXT DEFAULT 'pending', expires_at TEXT, decided_at TEXT NULL);
CREATE TABLE IF NOT EXISTS executed_actions (
  proposal_hash TEXT PRIMARY KEY, line_item_id INTEGER, action_type TEXT, amount_usd REAL,
  status TEXT, result TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, pact_id TEXT, pact_version INTEGER,
  proposal_id TEXT, proposal_hash TEXT, rule_results_json TEXT, decision TEXT,
  action_result TEXT NULL);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, opportunity_id INTEGER, proposal_hash TEXT,
  direction TEXT, thread_id TEXT, to_addr TEXT, subject TEXT, body TEXT, sent_at TEXT);
CREATE TABLE IF NOT EXISTS claim_packets (
  id INTEGER PRIMARY KEY AUTOINCREMENT, opportunity_id INTEGER, proposal_hash TEXT,
  store TEXT, packet_json TEXT, chat_script TEXT, created_at TEXT);
"""

TABLES = ["orders", "line_items", "listings", "price_history", "opportunities",
          "store_policies", "policy_checks", "users", "proposals", "approvals",
          "executed_actions", "audit_log", "messages", "claim_packets"]

# Same exception type the real DB-API raises on a duplicate primary key.
IntegrityError = sqlite3.IntegrityError


def _singlestore() -> bool:
    return bool(os.environ.get("SINGLESTORE_URL"))


def _path() -> str:
    return os.environ.get("CLAWBACK_DB", os.path.join("dev", "clawback.db"))


def _sqlite(sql: str) -> str:
    return sql.replace("%s", "?")


class _SqliteConn:
    """Thin DB-API wrapper so callers can always use %s placeholders."""

    def __init__(self):
        self.raw = sqlite3.connect(_path(), timeout=10, check_same_thread=False)
        self.raw.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        return self.raw.execute(_sqlite(sql), tuple(params))

    def commit(self):
        self.raw.commit()

    def close(self):
        self.raw.close()


def connection():
    if _singlestore():
        import singlestoredb  # lazy: not needed offline
        return singlestoredb.connect(os.environ["SINGLESTORE_URL"])
    return _SqliteConn()


def query(sql: str, params: tuple = ()) -> list[dict]:
    if _singlestore():
        conn = connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                cols = [c[0] for c in cur.description]
                return [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            conn.close()
    conn = _SqliteConn()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def execute(sql: str, params: tuple = ()) -> int:
    is_insert = sql.lstrip().upper().startswith("INSERT")
    if _singlestore():
        conn = connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                conn.commit()
                return cur.lastrowid if is_insert else cur.rowcount
        finally:
            conn.close()
    conn = _SqliteConn()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid if is_insert else cur.rowcount
    finally:
        conn.close()


def reset() -> None:
    """Drop and recreate every table (SQLite only; rehearsal helper)."""
    if _singlestore():
        raise RuntimeError("reset() refuses to touch SingleStore")
    os.makedirs(os.path.dirname(_path()) or ".", exist_ok=True)
    conn = _SqliteConn()
    try:
        for t in TABLES:
            conn.raw.execute(f"DROP TABLE IF EXISTS {t}")
        conn.raw.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
