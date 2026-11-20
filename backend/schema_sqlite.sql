-- Local-dev fallback schema (SQLite). Not part of the team spec: section 3.3 is the
-- source of truth and is frozen; this file is a type-translation of it so the app and
-- tests run on a laptop with no SINGLESTORE_URL. backend/db.py picks this file
-- automatically when SINGLESTORE_URL is unset. Same tables, same columns, same order.
-- VECTOR(1024, F32) -> TEXT (JSON-encoded float array); JSON -> TEXT; DECIMAL -> REAL;
-- DATE/DATETIME -> TEXT (ISO8601); BOOL -> INTEGER; BIGINT AUTO_INCREMENT PK -> INTEGER PK.

CREATE TABLE IF NOT EXISTS raw_emails (
  id INTEGER PRIMARY KEY AUTOINCREMENT, gmail_msg_id TEXT,
  from_addr TEXT, subject TEXT, received_at TEXT,
  body TEXT, parsed INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT, store TEXT, order_no TEXT,
  order_date TEXT, delivery_date TEXT NULL, email_id INTEGER
);
CREATE TABLE IF NOT EXISTS line_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER, store TEXT,
  title TEXT, sku TEXT NULL, upc TEXT NULL,
  model_number TEXT NULL, variant TEXT NULL, category TEXT,
  price_paid REAL, qty INTEGER, parse_confidence REAL,
  title_vec TEXT NULL,
  status TEXT DEFAULT 'watching'
);
CREATE TABLE IF NOT EXISTS listings (
  id INTEGER PRIMARY KEY AUTOINCREMENT, store TEXT, sku TEXT,
  upc TEXT NULL, model_number TEXT NULL, variant TEXT,
  url TEXT, title TEXT, title_vec TEXT NULL
);
CREATE TABLE IF NOT EXISTS price_history (listing_id INTEGER, price REAL, seen_at TEXT);
CREATE TABLE IF NOT EXISTS opportunities (
  id INTEGER PRIMARY KEY AUTOINCREMENT, line_item_id INTEGER, listing_id INTEGER NULL,
  kind TEXT,
  drop_usd REAL, similarity REAL, rerank_score REAL,
  created_at TEXT, status TEXT DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS store_policies (
  store TEXT PRIMARY KEY,
  claim_channel TEXT, price_adjust_allowed INTEGER, competitor_match_allowed INTEGER,
  adjust_window_days INTEGER, adjust_window_starts TEXT,
  return_window_days INTEGER, return_window_starts TEXT,
  final_sale_categories TEXT, restocking_fee_pct REAL,
  return_shipping_usd REAL, support_domains TEXT, senso_doc_id TEXT
);
CREATE TABLE IF NOT EXISTS policy_checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, opportunity_id INTEGER, store TEXT,
  question TEXT, answer TEXT, cited_text TEXT, source_url TEXT,
  senso_source_id TEXT, revision TEXT NULL, fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, name TEXT, email TEXT,
  persona_inquiry_id TEXT NULL, persona_status TEXT DEFAULT 'none',
  verified_at TEXT NULL, reverified_at TEXT NULL
);
CREATE TABLE IF NOT EXISTS pacts (
  pact_id TEXT, version INTEGER, user_id TEXT,
  pact_text TEXT,
  created_at TEXT,
  status TEXT,
  PRIMARY KEY (pact_id, version)
);
CREATE TABLE IF NOT EXISTS pact_revocations (
  pact_id TEXT, version INTEGER, revoked_at TEXT, reason TEXT,
  signature TEXT
);
CREATE TABLE IF NOT EXISTS proposals (
  id TEXT PRIMARY KEY, opportunity_id INTEGER, action_type TEXT,
  payload_json TEXT, proposal_hash TEXT, reasoning TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id TEXT,
  proposal_hash TEXT, user_id TEXT, amount_usd REAL,
  status TEXT DEFAULT 'pending',
  expires_at TEXT, decided_at TEXT NULL
);
CREATE TABLE IF NOT EXISTS executed_actions (
  proposal_hash TEXT PRIMARY KEY, line_item_id INTEGER, action_type TEXT,
  amount_usd REAL, status TEXT,
  result TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, pact_id TEXT,
  pact_version INTEGER, proposal_id TEXT, proposal_hash TEXT,
  rule_results_json TEXT, decision TEXT,
  action_result TEXT NULL
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, opportunity_id INTEGER, proposal_hash TEXT,
  direction TEXT, thread_id TEXT, to_addr TEXT,
  subject TEXT, body TEXT, sent_at TEXT
);
CREATE TABLE IF NOT EXISTS claim_packets (
  id INTEGER PRIMARY KEY AUTOINCREMENT, opportunity_id INTEGER, proposal_hash TEXT,
  store TEXT, packet_json TEXT, chat_script TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS scheduled_tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, name TEXT, kind TEXT,
  schedule_type TEXT, interval_min INTEGER NULL, at_time TEXT NULL, enabled INTEGER DEFAULT 1,
  next_run TEXT NULL, last_run TEXT NULL, last_status TEXT NULL, last_result TEXT NULL, created_at TEXT
);
CREATE TABLE IF NOT EXISTS scheduled_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER, ran_at TEXT, status TEXT, result TEXT
);
