-- Clawback schema (SingleStore). Section 3.3 of the team split doc, frozen at milestone 1.
-- Applied by Teammate A. Primary keys double as shard keys.

CREATE TABLE IF NOT EXISTS raw_emails (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, gmail_msg_id VARCHAR(128),
  from_addr VARCHAR(256), subject VARCHAR(512), received_at DATETIME,
  body LONGTEXT, parsed BOOL DEFAULT FALSE
);
CREATE TABLE IF NOT EXISTS orders (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, store VARCHAR(32), order_no VARCHAR(64),
  order_date DATE, delivery_date DATE NULL, email_id BIGINT
);
CREATE TABLE IF NOT EXISTS line_items (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, order_id BIGINT, store VARCHAR(32),
  title VARCHAR(512), sku VARCHAR(64) NULL, upc VARCHAR(32) NULL,
  model_number VARCHAR(64) NULL, variant VARCHAR(64) NULL, category VARCHAR(64),
  price_paid DECIMAL(10,2), qty INT, parse_confidence FLOAT,
  title_vec VECTOR(1024, F32) NULL,
  status VARCHAR(16) DEFAULT 'watching'
);
CREATE TABLE IF NOT EXISTS listings (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, store VARCHAR(32), sku VARCHAR(64),
  upc VARCHAR(32) NULL, model_number VARCHAR(64) NULL, variant VARCHAR(64),
  url VARCHAR(512), title VARCHAR(512), title_vec VECTOR(1024, F32) NULL
);
CREATE TABLE IF NOT EXISTS price_history (listing_id BIGINT, price DECIMAL(10,2), seen_at DATETIME);
CREATE TABLE IF NOT EXISTS opportunities (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, line_item_id BIGINT, listing_id BIGINT NULL,
  kind VARCHAR(16),                        -- price_drop | deadline
  drop_usd DECIMAL(10,2), similarity FLOAT, rerank_score FLOAT,
  created_at DATETIME, status VARCHAR(16) DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS store_policies (              -- hand-filled from official pages
  store VARCHAR(32) PRIMARY KEY,
  claim_channel VARCHAR(16),               -- 'email' (mock store) | 'chat' (Target, Best Buy)
  price_adjust_allowed BOOL, competitor_match_allowed BOOL,
  adjust_window_days INT, adjust_window_starts VARCHAR(16),   -- 'purchase' | 'delivery'
  return_window_days INT, return_window_starts VARCHAR(16),
  final_sale_categories JSON, restocking_fee_pct DECIMAL(5,4),
  return_shipping_usd DECIMAL(10,2), support_domains JSON, senso_doc_id VARCHAR(128)
);
CREATE TABLE IF NOT EXISTS policy_checks (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, opportunity_id BIGINT, store VARCHAR(32),
  question TEXT, answer TEXT, cited_text TEXT, source_url VARCHAR(512),
  senso_source_id VARCHAR(128), revision VARCHAR(64) NULL, fetched_at DATETIME
);
CREATE TABLE IF NOT EXISTS users (
  id VARCHAR(64) PRIMARY KEY, name VARCHAR(128), email VARCHAR(256),
  persona_inquiry_id VARCHAR(128) NULL, persona_status VARCHAR(16) DEFAULT 'none',
  verified_at DATETIME NULL, reverified_at DATETIME NULL
);
CREATE TABLE IF NOT EXISTS pacts (
  pact_id VARCHAR(64), version INT, user_id VARCHAR(64),
  pact_text TEXT,                          -- exact signed text; never a JSON column
  created_at DATETIME,
  status VARCHAR(16),                      -- display only; the gate never reads it
  PRIMARY KEY (pact_id, version)
);
CREATE TABLE IF NOT EXISTS pact_revocations (            -- insert-only; the gate's source of truth
  pact_id VARCHAR(64), version INT, revoked_at DATETIME, reason VARCHAR(256),
  signature VARCHAR(80)                    -- HMAC over pact_id|version|revoked_at
);
CREATE TABLE IF NOT EXISTS proposals (
  id VARCHAR(64) PRIMARY KEY, opportunity_id BIGINT, action_type VARCHAR(32),
  payload_json JSON, proposal_hash VARCHAR(80), reasoning TEXT, created_at DATETIME
);
CREATE TABLE IF NOT EXISTS approvals (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, proposal_id VARCHAR(64),
  proposal_hash VARCHAR(80), user_id VARCHAR(64), amount_usd DECIMAL(10,2),
  status VARCHAR(16) DEFAULT 'pending',    -- pending|approved|declined|expired|executed
  expires_at DATETIME, decided_at DATETIME NULL
);
CREATE TABLE IF NOT EXISTS executed_actions (            -- run-once guard
  proposal_hash VARCHAR(80) PRIMARY KEY, line_item_id BIGINT, action_type VARCHAR(32),
  amount_usd DECIMAL(10,2), status VARCHAR(16),   -- running|done|failed
  result TEXT, created_at DATETIME
);
CREATE TABLE IF NOT EXISTS audit_log (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, ts DATETIME, pact_id VARCHAR(64),
  pact_version INT, proposal_id VARCHAR(64), proposal_hash VARCHAR(80),
  rule_results_json JSON, decision VARCHAR(12),   -- ALLOW|ASK|BLOCK|NO_ACTION
  action_result JSON NULL
);
CREATE TABLE IF NOT EXISTS messages (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, opportunity_id BIGINT, proposal_hash VARCHAR(80),
  direction VARCHAR(8), thread_id VARCHAR(128), to_addr VARCHAR(256),
  subject VARCHAR(512), body TEXT, sent_at DATETIME
);
CREATE TABLE IF NOT EXISTS claim_packets (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, opportunity_id BIGINT, proposal_hash VARCHAR(80),
  store VARCHAR(32), packet_json JSON, chat_script TEXT, created_at DATETIME
);
-- Added for Siva's scheduler (backend/pact/scheduler.py); DDL matches backend/pact/devkit.py's SCHEMA.
CREATE TABLE IF NOT EXISTS scheduled_tasks (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id VARCHAR(64), name VARCHAR(128), kind VARCHAR(32),
  schedule_type VARCHAR(16), interval_min INT, at_time VARCHAR(16), enabled BOOL DEFAULT TRUE,
  next_run DATETIME, last_run DATETIME NULL, last_status VARCHAR(16) NULL, last_result TEXT NULL,
  created_at DATETIME
);
CREATE TABLE IF NOT EXISTS scheduled_runs (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, task_id BIGINT, ran_at DATETIME, status VARCHAR(16), result TEXT
);
