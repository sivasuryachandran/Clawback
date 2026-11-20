# Clawback — Team Split (4 people) · v4 (final)

Personal Agents Hackathon · tokens& · Fri Nov 20 · **Code freeze 4:10 PM PT**

**Pitch:** Clawback is a personal agent that gets your money back after you buy, and can only act inside a Pact you signed. The LLM proposes; a deterministic gate decides.

**Opening hook:** Black Friday is Nov 27, seven days after this event — the week prices drop right after people buy.

This file is the single source of truth for the split, the shared contracts, the gate rules, the UI and each person's prompt. It replaces every earlier version and the code in the team doc.

---

## 0. What v4 settles

| Topic | Decision |
|---|---|
| Names | Taken from the reference code so everyone uses one vocabulary: actions `price_adjust_email`, `claim_packet`, `claim_followup`, `rebuy_return`, `deadline_alert`, `none`; tables `executed_actions`, `pacts.pact_text`, `policy_checks.revision`; approval check `A1` |
| Kept from the reference code | Approval bound to the exact proposal hash and expiry (A1); run-once guard keyed on the proposal hash; Persona webhook signature check with replay window; date coercion inside the gate; R0 binding the proposal to the opportunity's own rows |
| Restored (lost in the reference code) | Revocation via a signed, insert-only `pact_revocations` table (never the `status` column); Persona age ≤ 30 days and a fresh re-check for approvals; citation ≤ 24 hours old; spending cap counts running and approved-but-not-run rebuys; cross-store identity via UPC/model number; no Amazon |
| Fixed | Price freshness (R8a) runs **after** net savings (R8), so the cached Best Buy price shows the $6 block; a failed action can be retried (run-once only blocks `running`/`done`) |
| Real stores | Target and Best Buy don't take claims by email. They get a `claim_packet`: shown in the app and emailed **to the user** (an allowed address), never to the store |
| UI | Calm consumer app; plain language; details behind "Why?" and a "Show details" switch (section 3.10) |
| Reference code (`clawback-v3-reference.zip`) | **Design reference only.** Read it tonight, then write the code fresh at the event. The rules say the project must be built during the event; retyping pre-written code isn't that. If the team wants to use it directly, ask an organizer at kickoff first |

---

## 1. Who does what

| Person | Area | Share | Owns (folders) |
|---|---|---|---|
| **Siva** | Pact + Persona + all UI | own track | `backend/pact/`, `backend/persona/`, `frontend/`, `tests/test_gate.py`, `tests/test_safety.py` |
| **Teammate A** | Data, ingest, matching | 35% of the rest | `backend/db.py`, `backend/schema.sql`, `backend/ingest/`, `backend/watch/`, `seed/receipts/`, `tests/test_matcher.py` |
| **Teammate B** | Agent, policy, actions | 35% of the rest | `backend/agent/`, `backend/actor/`, `seed/policies/`, Senso, Firedrill |
| **Teammate C** | Scaffold, mock store, analytics, ship | 30% of the rest | `backend/main.py`, `mockstore/`, `backend/analytics/`, `README.md`, video, submission |

**Rule:** edit only your own folders; ask the owner for anything else. Everyone may add one `include_router` line to `backend/main.py`.

**Sponsors**

| Sponsor | Prize | Owner | What judges must see |
|---|---|---|---|
| Senso | $10k / $5k / $1k credits | B | The store's policy quoted in every Why? panel; stretch: edit a policy live and watch the same claim stop, quoting the new revision |
| Persona | Keychron gift cards + swag | Siva | Verify before signing; fresh re-check when approving money; messages name the verified user |
| SingleStore | $1k / $750 / $500 gift cards | A | Vector match query and "what did the Pact stop this week" query run live |
| Airbyte | $1k / $500 / $250 Visa | A (read) + B (send) | Gmail agent connector reads receipts and sends the mock-store claim email |
| Superlinked (SIE Serverless) | $500 cash + $500 credits ×2 | A | Embeddings + rerank via SIE; vectors stored in SingleStore |
| Amplitude | $1,000 / $500 | C | Dashboard: dollars recovered, decision mix, stop reasons |
| Arcee (first to cut) | $500 credits + merch | A | Cost per receipt |
| Firedrill (stretch) | ~$2k pool | B | Adversarial scenarios with a true pass rate |
| Guild | tokens | Siva | The "Stopped by your rules" moment |

---

## 2. Build order, milestones and cuts

**Code freeze: 4:10 PM.** Nothing merges after that.

| Order | Siva | A | B | C |
|---|---|---|---|---|
| 1st | `sign.py` + `gate.py` + tests (pure code, no APIs) | `schema.sql` applied, `db.py`, `store_policies` rows | Senso: load policy docs, test 3 questions | Repo scaffold, FastAPI app, `.env.example`, push |
| 2nd | `context.py`, `record()`, `approvals.py` | Airbyte Gmail connector → `raw_emails` (or seed fallback) | `policy.py` | Mock store pages + API + price webhook |
| 3rd | Persona sandbox flow; Your Pact screen | `parser.py` → `orders`, `line_items` | `propose.py` + schema validation | `track()` + Amplitude events |
| 4th | Money back + Activity screens (3.10) | SIE embeddings + rerank; matcher; opportunities | Orchestration; actors; claim packet; follow-ups | Amplitude dashboard; README draft |
| 5th | "Show details" switch; polish copy | Saved queries; test_matcher | Firedrill (stretch); Senso live edit (stretch) | Rehearse; dry-run recording |

**Milestones**

1. Every module imports; stubs return fake data; frontend loads.
2. One seeded receipt → order row → opportunity → proposal → gate decision (no UI).
3. Full demo flow works in the UI. If Siva is behind, C builds the Activity screen.

**Cut order if running behind:** Firedrill → Senso live edit → Arcee (main model parses) → SIE rerank (keep SIE embeddings) → rebuy (keep email + claim packet). **Never cut:** the gate, the three stop moments, Senso quotes, the Why? panel, demo-mode email safety.

---

## 3. Shared contracts

### 3.1 Runtime

- Backend: FastAPI on `http://localhost:8000`; mock store under `/mock` on the same app.
- Frontend: `http://localhost:3000` (Next.js + Tailwind; Streamlit if time is tight).
- Python 3.11, one `requirements.txt`. Seed data is dated relative to **2026-11-20**.
- The gate coerces dates itself (`_date`, `_ts`); the context builder still returns Python objects where it can.

### 3.2 Environment variables

```
SINGLESTORE_URL=
SENSO_API_KEY=
AIRBYTE_CLIENT_ID=
AIRBYTE_CLIENT_SECRET=
AIRBYTE_WORKSPACE_NAME=
PERSONA_API_KEY=
PERSONA_TEMPLATE_ID=       # create the template in the Persona dashboard first
PERSONA_WEBHOOK_SECRET=
SIE_URL=
SIE_API_KEY=
ARCEE_API_KEY=
LLM_API_KEY=
EMBEDDING_DIM=1024         # match the SIE model
AMPLITUDE_API_KEY=
FIREDRILL_API_KEY=
PACT_SIGNING_KEY=          # random 32+ bytes, never committed; read at call time
MOCK_WEBHOOK_SECRET=
DEMO_MODE=true             # anything but "false" = on
DEMO_SUPPORT_INBOX=        # teammate inbox posing as Mock Store support
DEMO_ALLOWED_RECIPIENTS=   # comma-separated EXACT addresses (support inbox + demo user's email)
```

`.env` goes in `.gitignore` before the first commit.

### 3.3 Database (A applies first; frozen at milestone 1)

Use one SingleStore database. Primary keys double as shard keys.

```sql
CREATE TABLE raw_emails (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, gmail_msg_id VARCHAR(128),
  from_addr VARCHAR(256), subject VARCHAR(512), received_at DATETIME,
  body LONGTEXT, parsed BOOL DEFAULT FALSE
);
CREATE TABLE orders (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, store VARCHAR(32), order_no VARCHAR(64),
  order_date DATE, delivery_date DATE NULL, email_id BIGINT
);
CREATE TABLE line_items (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, order_id BIGINT, store VARCHAR(32),
  title VARCHAR(512), sku VARCHAR(64) NULL, upc VARCHAR(32) NULL,
  model_number VARCHAR(64) NULL, variant VARCHAR(64) NULL, category VARCHAR(64),
  price_paid DECIMAL(10,2), qty INT, parse_confidence FLOAT,
  title_vec VECTOR(1024, F32) NULL,
  status VARCHAR(16) DEFAULT 'watching'
);
CREATE TABLE listings (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, store VARCHAR(32), sku VARCHAR(64),
  upc VARCHAR(32) NULL, model_number VARCHAR(64) NULL, variant VARCHAR(64),
  url VARCHAR(512), title VARCHAR(512), title_vec VECTOR(1024, F32) NULL
);
CREATE TABLE price_history (listing_id BIGINT, price DECIMAL(10,2), seen_at DATETIME);
CREATE TABLE opportunities (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, line_item_id BIGINT, listing_id BIGINT NULL,
  kind VARCHAR(16),                        -- price_drop | deadline
  drop_usd DECIMAL(10,2), similarity FLOAT, rerank_score FLOAT,
  created_at DATETIME, status VARCHAR(16) DEFAULT 'open'
);
CREATE TABLE store_policies (              -- hand-filled from official pages
  store VARCHAR(32) PRIMARY KEY,
  claim_channel VARCHAR(16),               -- 'email' (mock store) | 'chat' (Target, Best Buy)
  price_adjust_allowed BOOL, competitor_match_allowed BOOL,
  adjust_window_days INT, adjust_window_starts VARCHAR(16),   -- 'purchase' | 'delivery'
  return_window_days INT, return_window_starts VARCHAR(16),
  final_sale_categories JSON, restocking_fee_pct DECIMAL(5,4),
  return_shipping_usd DECIMAL(10,2), support_domains JSON, senso_doc_id VARCHAR(128)
);
CREATE TABLE policy_checks (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, opportunity_id BIGINT, store VARCHAR(32),
  question TEXT, answer TEXT, cited_text TEXT, source_url VARCHAR(512),
  senso_source_id VARCHAR(128), revision VARCHAR(64) NULL, fetched_at DATETIME
);
CREATE TABLE users (
  id VARCHAR(64) PRIMARY KEY, name VARCHAR(128), email VARCHAR(256),
  persona_inquiry_id VARCHAR(128) NULL, persona_status VARCHAR(16) DEFAULT 'none',
  verified_at DATETIME NULL, reverified_at DATETIME NULL
);
CREATE TABLE pacts (
  pact_id VARCHAR(64), version INT, user_id VARCHAR(64),
  pact_text TEXT,                          -- exact signed text; never a JSON column
  created_at DATETIME,
  status VARCHAR(16),                      -- display only; the gate never reads it
  PRIMARY KEY (pact_id, version)
);
CREATE TABLE pact_revocations (            -- insert-only; the gate's source of truth
  pact_id VARCHAR(64), version INT, revoked_at DATETIME, reason VARCHAR(256),
  signature VARCHAR(80)                    -- HMAC over pact_id|version|revoked_at
);
CREATE TABLE proposals (
  id VARCHAR(64) PRIMARY KEY, opportunity_id BIGINT, action_type VARCHAR(32),
  payload_json JSON, proposal_hash VARCHAR(80), reasoning TEXT, created_at DATETIME
);
CREATE TABLE approvals (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, proposal_id VARCHAR(64),
  proposal_hash VARCHAR(80), user_id VARCHAR(64), amount_usd DECIMAL(10,2),
  status VARCHAR(16) DEFAULT 'pending',    -- pending|approved|declined|expired|executed
  expires_at DATETIME, decided_at DATETIME NULL
);
CREATE TABLE executed_actions (            -- run-once guard
  proposal_hash VARCHAR(80) PRIMARY KEY, line_item_id BIGINT, action_type VARCHAR(32),
  amount_usd DECIMAL(10,2), status VARCHAR(16),   -- running|done|failed
  result TEXT, created_at DATETIME
);
CREATE TABLE audit_log (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, ts DATETIME, pact_id VARCHAR(64),
  pact_version INT, proposal_id VARCHAR(64), proposal_hash VARCHAR(80),
  rule_results_json JSON, decision VARCHAR(12),   -- ALLOW|ASK|BLOCK|NO_ACTION
  action_result JSON NULL
);
CREATE TABLE messages (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, opportunity_id BIGINT, proposal_hash VARCHAR(80),
  direction VARCHAR(8), thread_id VARCHAR(128), to_addr VARCHAR(256),
  subject VARCHAR(512), body TEXT, sent_at DATETIME
);
CREATE TABLE claim_packets (
  id BIGINT AUTO_INCREMENT PRIMARY KEY, opportunity_id BIGINT, proposal_hash VARCHAR(80),
  store VARCHAR(32), packet_json JSON, chat_script TEXT, created_at DATETIME
);
```

If `VECTOR` isn't available, A switches to `BLOB` + `JSON_ARRAY_PACK()` / `DOT_PRODUCT()`.

### 3.4 Python contracts (frozen at milestone 1)

**A — `backend/db.py`, `backend/watch/`**
```python
def query(sql: str, params: tuple = ()) -> list[dict]: ...
def execute(sql: str, params: tuple = ()) -> int: ...
def connection(): ...                                           # DB-API connection for execute_once
def tick() -> list[int]: ...
def on_price_change(listing_id: int, price: float) -> list[int]: ...
def match(line_item_id: int) -> list[dict]: ...                 # candidates with similarity, rerank_score, exact
```

**B — `backend/agent/`, `backend/actor/`**
```python
def check_policy(opportunity_id: int) -> int: ...
def propose(opportunity_id: int) -> dict: ...
def handle_reply(opportunity_id: int, reply_text: str, thread_id: str) -> dict: ...
def send_email(decision, to: str, subject: str, body: str, transport, thread_id: str | None = None) -> str: ...
def rebuy_return(decision, listing_id: int, original_order_no: str) -> str: ...
def build_claim_packet(decision, opportunity_id: int) -> int: ...
```

**Siva — `backend/pact/`**
```python
def sign(pact: dict) -> str: ...                                # returns pact_text (canonical JSON incl. hash + signature)
def verify_text(pact_text: str) -> dict | None: ...             # parsed Pact if hash + signature check out
def proposal_hash(p: dict) -> str: ...                          # sha256 of canonical JSON without "reasoning"
def active_pact(user_id: str) -> tuple[dict | None, dict]: ...  # (pact, meta: version, revoked, is_latest)
def build_context(opportunity_id: int, user: dict) -> GateContext: ...
def run_gate(p: dict, pact: dict, ctx: GateContext, human_approved: bool = False) -> Decision: ...
def run_approved(p: dict, pact: dict, ctx: GateContext, approval: dict) -> Decision: ...   # A1, then run_gate
def execute_once(conn, decision, p: dict, action_fn, amount_usd: float = 0.0) -> tuple[str, str]: ...
def record(p: dict, pact: dict | None, decision, action_result=None) -> int: ...
def verify_persona_signature(raw_body: bytes, header: str, secret: str, max_age_s: int = 300) -> bool: ...
```

**C — `backend/analytics/events.py`**
```python
def track(event: str, props: dict) -> None: ...                 # never raises
```

### 3.5 Proposal schema

```json
{
  "proposal_id": "prop_51a",
  "opportunity_id": 19,
  "line_item_id": 204,
  "store": "mockstore",
  "listing_id": 77,
  "action_type": "price_adjust_email | claim_packet | claim_followup | rebuy_return | deadline_alert | none",
  "email":  { "to": "<address>", "template": "price_adjust_v1 | packet_v1 | proof_v1", "thread_id": null },
  "rebuy":  { "listing_id": 77 },
  "return": { "order_no": "MS-55021" },
  "alert_at": "2026-12-03",
  "reasoning": "two sentences shown to the user; never used by the gate"
}
```

- B copies `opportunity_id`, `line_item_id`, `store`, `listing_id`, `rebuy.listing_id` and `return.order_no` **from the database**. The LLM only picks `action_type`, the template and `alert_at`, and writes `reasoning`.
- The gate re-checks every one of those fields against the opportunity's own rows (R0). Prices, fees and dates are never in the proposal.
- Recipients: `price_adjust_email` → `DEMO_SUPPORT_INBOX` (mock store only); `claim_packet` → the user's own email; `claim_followup` → the original claim's recipient.
- `none` is recorded as `NO_ACTION` and never reaches the gate.

### 3.6 Main loop (B owns the endpoint)

```
POST /opportunities/{id}/propose
  check_policy(id)
  p = propose(id)
  if p.action_type == "none": record(p, None, NO_ACTION); return
  pact, meta = active_pact(user)
  ctx = build_context(id, user)
  d = run_gate(p, pact, ctx)
  audit_id = record(p, pact, d)
  ALLOW -> execute_once(conn, d, p, lambda: <actor>, amount)   # AlreadyRan = it already happened
        -> record(..., action_result)
  ASK   -> INSERT approvals(proposal_id, proposal_hash, user_id, amount_usd, expires_at = now + 24h)
  BLOCK -> stop
  track("gate_decision", {...})

POST /approvals/{id}   (Siva; logged-in verified owner only)
  approval -> status approved; Persona re-check -> users.reverified_at
  d = run_approved(stored p, pact, fresh ctx, approval)
  ALLOW -> execute_once(...) -> approvals.status = executed
```

### 3.7 Endpoints and owners

| Method | Path | Owner |
|---|---|---|
| GET | `/orders` | A |
| POST | `/ingest/run`, `/watch/tick` | A |
| POST | `/watch/price-changed` (mock-store webhook, HMAC) | A |
| GET | `/activity` (audit rows + plain fields for the UI) | A |
| GET | `/opportunities/{id}` (incl. Why? data) | B |
| POST | `/opportunities/{id}/propose` | B |
| POST | `/replies/inbound` | B |
| GET | `/claim-packets/{id}` | B |
| POST | `/persona/inquiry`, `/persona/webhook`, `/persona/recheck` | Siva |
| GET/POST | `/pact`, `/pact/pause` (revoke) | Siva |
| POST | `/gate/check` | Siva |
| GET/POST | `/approvals`, `/approvals/{id}` | Siva |
| GET | `/stats` | C |
| GET/POST | `/mock/*` (pages, `/mock/api/listings`, `/mock/api/rebuy_return`, `/mock/admin`) | C |

### 3.8 Seed data

Demo date **2026-11-20**. User `u_demo` with a real email on `DEMO_ALLOWED_RECIPIENTS`.

| # | Store | Item | SKU | Variant | Category | Paid | Ordered | Delivered | Demo role |
|---|---|---|---|---|---|---|---|---|---|
| 1 | mockstore | Aurora Wireless Earbuds | MS-1001 | Black | electronics | $129.99 | Nov 11 | Nov 13 | Live drop to $95.99 → **Done**: email to Mock Store support ($34) |
| 1b | mockstore | (same) | | | | | | | Rep replies "send proof" → `claim_followup` → proof sent in the same thread |
| 2 | mockstore | Nimbus Smart Lamp | MS-2002 | White | home | $239.00 | Nov 2 | Nov 4 | Adjust window closed (18 > 14 days); return open → rebuy at $199 → **Needs your OK** → approve → rebuy + return together |
| 3 | mockstore | Trailhead Backpack | MS-3003 | Black | outdoor | $89.00 | Nov 12 | Nov 14 | Top match is the **Blue** one at $64 → **Stopped** (R5) |
| 4 | mockstore | Breeze Desk Fan | MS-4004 | Gray | clearance | $59.00 | Nov 10 | Nov 12 | Drop to $39, but clearance is final sale → **Stopped** (R4) |
| 5 | bestbuy | (real item, pick before kickoff) | real | real | real | real | Nov 14 | Nov 16 | Cached price $6 lower → **Stopped** (R8: not worth it) |
| 6 | target | (real item, pick before kickoff) | real | real | real | real | ~Oct 22 | ~Oct 24 | No drop; pick an item whose return window ends within 7 days of Nov 20 (e.g. a category with a 30-day window; B confirms) → **Return closes soon** alert |

If a real-store item has a real drop that passes every rule, the action is `claim_packet`: shown in the app and emailed to the user, who pastes it into the store's chat.

**Mock store policy** (C writes `/mock/policy`; B loads it into Senso; A inserts the row): claims by email to Mock Store support · price adjustments 14 days from purchase, own prices only · returns 30 days from delivery · clearance is final sale · no restocking fee · free returns.

**Real-store rows** (B confirms from official pages; A inserts): Target — claim_channel chat, own prices only, 14-day adjust window. Best Buy — claim_channel chat; confirm the price-match period and whether it starts at delivery.

**Sample Pact:** actions price_adjust_email, claim_packet, rebuy_return, deadline_alert (claim_followup comes with price_adjust_email) · stores mockstore, target, bestbuy · skip clearance and gift cards · min net savings $10 · ask first above $150 · never more than $250 at once · $300 a month · 2-day buffer · 1 claim per order · 3 follow-ups per thread · valid 90 days.

### 3.9 Gate rules v4

Checked in this order; the first failure returns BLOCK. Any exception inside the gate returns BLOCK on R0 — the gate never crashes.

| Rule | Applies to | Check |
|---|---|---|
| (pre) | `none` | Logged as NO_ACTION; gate not run |
| R0 | all | Proposal well formed for its action; `opportunity_id`, `line_item_id`, `store`, `listing_id`, `rebuy.listing_id`, `return.order_no` equal the opportunity's rows |
| R1 | all | `pact_text` hash + signature verify; no valid row in `pact_revocations`; latest version; not expired; Persona approved and verified within 30 days |
| R2 | all | Action allowed by the Pact (`claim_followup` allowed when `price_adjust_email` or `claim_packet` is) |
| R3 | all | Item's store (and listing's store) allowed — from the database |
| R4 | all | Category not excluded and not final sale per policy |
| R6 | all | Senso citation for this store and this opportunity, fetched within 24 hours |
| R6b | claims, follow-ups | Store offers price adjustments |
| R6c | claims | `price_adjust_email` only if `claim_channel == "email"`; `claim_packet` only if it isn't |
| R7 | all | Adjust window (start per `adjust_window_starts`) for claims and follow-ups; return window for rebuys and alerts. Claims/rebuys need ≥ buffer days; alerts and follow-ups ≥ 0; `alert_at` ≤ window end |
| R5 | claims, rebuy | Same product: same store → SKU + variant; other store → UPC or model number + variant. Claims also need the same store unless `competitor_match_allowed` |
| R8 | claims, rebuy | Net savings ≥ minimum (restocking + return shipping for rebuys only) |
| R8a | claims, rebuy | Price seen within 15 minutes (after R8 on purpose) |
| R9 | rebuy | Price ≤ per-purchase cap; done + running + approved-not-run rebuys this month (excluding this proposal's own approval) + this ≤ monthly cap |
| R10 | emails | Demo mode: exact address on the allowlist. Live: `price_adjust_email` → store's support domain; `claim_packet` → the user's own email |
| R11 | claims, rebuy | No earlier done/running claim on this order |
| R13 | follow-ups | An earlier claim exists; same recipient and thread; fewer than 3 follow-ups |
| R12 | rebuy / low confidence | ASK if rebuy price > ask-first limit or parse confidence < 0.8 (skipped when approved) |
| A1 | approvals | Approval status approved; not expired; stored `proposal_hash` equals the hash of the proposal being run; approver is the Pact owner; Persona re-check within 15 minutes. Then the full gate re-runs on fresh data with only R12 skipped |

"Claims" = `price_adjust_email`, `claim_packet`. "Emails" = claims + `claim_followup`.

**Run-once guard (`execute_once`):** insert into `executed_actions` with the proposal hash before the actor runs. Duplicate key with status `running` or `done` → `AlreadyRan`, nothing sent. Duplicate with status `failed` → a conditional update back to `running` allows one retry. The actor result sets `done` or `failed`.

### 3.10 UI: clean and minimal (Siva builds; everyone uses these words)

**Decision:** Clawback looks like a calm consumer app, not an admin dashboard. A normal person should understand every default screen without knowing what an agent, a rule or a database is. The technical trail still exists, because the challenge requires showing the steps and the sponsor judges need to see them, but it sits one tap away under **Why?**, and rule IDs and raw values appear only when the **Show details** switch is on (we turn it on during judging).

**Principles**

- One idea per card, one sentence per idea. Numbers in dollars and days, never IDs.
- Every outcome is phrased as something done *for* the user. A block is good news ("we stopped this"), never an error.
- Three screens, no sidebars, no tables on default views. Mobile-first column (max ~480 px) centered on desktop; large type so it reads on a projector.
- White background, one accent color, system font, generous spacing, soft rounded cards. Status colors: green = done, amber = needs your OK, gray = stopped/watching. No red alarms.
- Buttons say what happens: "Approve and buy", "Copy message for Target chat", "Pause Clawback". Never "Submit" or "Execute".

**Words: never on default views → use instead**

| Don't show | Show |
|---|---|
| Pact gate, gate decision | Your Pact / your rules |
| ALLOW / ASK / BLOCK | Done / Needs your OK / Stopped by your rules |
| Opportunity | Price drop / Return deadline |
| Proposal | What Clawback wants to do |
| R5, R8, … | The plain sentence for that rule (table below) |
| SKU, UPC, variant | Item name + color/size ("Black backpack") |
| Audit log | Activity |
| Revoke | Pause Clawback |
| Claim follow-up | Reply to the store |
| Persona inquiry | Verify it's you |
| Senso citation | "Target's policy says: …" with a link |
| Claim packet | "Ready to send in Target chat" |

**Status pills:** Watching · Price dropped · Asked the store · Got money back · Needs your OK · Stopped by your rules · Return closes soon

**Rule → plain sentence** (used in Why? and on Stopped cards)

| Rule | Plain sentence |
|---|---|
| R0 | Something didn't add up, so we didn't act. |
| R1 / A1 | Please verify it's you again before Clawback can act. |
| R2 | You haven't allowed Clawback to do this. |
| R3 | You haven't allowed Clawback at this store. |
| R4 | This item is final sale (or a type you excluded). |
| R5 | The cheaper one isn't the same item: you bought {bought}, this is {offered}. ({Store} only matches its own prices, when that's the reason.) |
| R6 | We couldn't confirm the store's current policy. |
| R6b / R6c | {Store} doesn't take this kind of request this way. |
| R7 | The window to ask has closed (or closes too soon). |
| R8 | Not worth it: you'd only save ${net}; your minimum is ${min}. |
| R8a | The price just changed; we'll check again. |
| R9 | That would go over your spending limit. |
| R10 | We only write to the store's official support (or to you). |
| R11 | We've already asked about this order. |
| R13 | We only reply in the same conversation, up to 3 times. |
| R12 | This is above your ${ask} ask-first limit. |

**Card copy for the demo (seed rows)**

- Earbuds — Asked the store: "We asked Mock Store for **$34 back** on your Aurora earbuds. Price dropped from $129.99 to $95.99." After the follow-up: "Mock Store asked for proof; we sent your receipt and the new price."
- Lamp — Needs your OK: "Save **$40** on your Nimbus lamp? We'd buy it again at $199 and return the one you have. This is above your $150 ask-first limit." [Approve and buy] [Not now]
- Backpack — Stopped: "**We didn't act.** The cheaper backpack is Blue; yours is Black."
- Fan — Stopped: "**We didn't act.** Clearance items are final sale at Mock Store."
- Best Buy item — Stopped: "**Not worth it.** You'd only save $6; your minimum is $10."
- Target item — Return closes soon: "Last day to return your {item} is {date}." [Remind me]
- Real-store claim (if one passes): "Target doesn't take requests by email. We've put everything together and emailed it to you, ready to paste into Target's chat." [Copy message] [Open Target chat]

**Why? panel (4 lines, plain)**

1. 🧾 You bought **{item}** for **${paid}** on {date}.
2. 🏷️ We found it for **${price}** at {store} ({time} ago).
3. 📄 {Store}'s policy says: "{cited sentence}" [source]
4. ✅ Your rules: {plain sentence for the deciding rule, or "Everything checked out."}

With **Show details** on, the panel adds: rule-by-rule list with values, Pact version + short hash, Senso source id, opportunity and proposal ids.

**Your Pact setup (3 steps)**

1. **Verify it's you** — "Clawback will only act for a verified person." [Verify with Persona]
2. **Set your limits** — toggles and dollar fields written as sentences:
   - "Ask the store for money back when prices drop" (on)
   - "Buy again and return when that saves more" (on)
   - "Only act if I save at least **$10**"
   - "Ask me first above **$150**" · "Never spend more than **$250** at once" · "**$300** a month max"
   - "Skip clearance and gift cards" (on)
3. **Review and sign** — one paragraph: "Clawback may ask Mock Store, Target and Best Buy for price adjustments and may buy again and return, only when you save at least $10, never over $250 at once or $300 a month, and it asks you first above $150. Valid until Feb 18." [Sign my Pact]

**Never shown to normal users:** JSON, hashes (except short hash with details on), database ids, model names, token counts, raw emails, error stack traces. Errors read: "Something went wrong on our side. Nothing was sent or bought."

---

## 4. Prompts for each person's coding agent

Paste your prompt into your coding agent at kickoff, together with section 3 of this file. No code before kickoff. The reference zip is for reading only.

### 4.1 Siva — Pact, Persona, UI

```
You are my pair programmer at a one-day hackathon (code freeze 4:10 PM).
Project: Clawback — a personal agent that recovers money after purchases and can ONLY act
inside a "Pact": a signed, versioned mandate the user creates after verifying identity with
Persona. The LLM proposes; a deterministic gate in plain Python decides ALLOW / ASK / BLOCK.
The attached team file (section 3) has the contracts, the gate rules table (3.9) and the UI
spec (3.10). Follow them exactly. I may only edit backend/pact/, backend/persona/,
frontend/, tests/test_gate.py, tests/test_safety.py, plus one include_router line in
backend/main.py. Write all code fresh; do not paste from any pre-written file.

BUILD, in this order. Steps 1–4 are pure code with no external APIs; do them first so the
team plugs into a tested core.

1. backend/pact/sign.py
   - canonical(obj, drop): json.dumps(sort_keys=True, separators=(",", ":"), default=str).
   - sign(pact) -> pact_text: add "hash" (sha256 of canonical without hash/signature) and
     "signature" (HMAC-SHA256 with PACT_SIGNING_KEY read at call time), return the
     canonical text of the whole object. That exact text is stored in pacts.pact_text.
   - verify_text(pact_text) -> dict | None: parse, recompute hash and HMAC over the body
     without hash/signature, compare with hmac.compare_digest; None if anything fails.
   - proposal_hash(p): "sha256:" + sha256 of canonical(p without "reasoning").
   - sign_revocation(pact_id, version, revoked_at) / verify_revocation(row).

2. backend/pact/gate.py
   - GateContext (built from opportunity_id only): today, now; opportunity {id,
     line_item_id, listing_id, kind}; item (line_items + orders: id, order_no, store, sku,
     upc, model_number, variant, category, price_paid, order_date, delivery_date,
     parse_confidence); listing or None (id, store, sku, upc, model_number, variant, price,
     seen_at); policy (store_policies row, JSON parsed); policy_check (store,
     opportunity_id, senso_source_id, revision, fetched_at) or None; pact_meta (version,
     revoked, is_latest); persona_status, persona_verified_at, reverified_at;
     session_user_id, pact_owner_id; user_email; month_committed (done + running +
     approved-not-run rebuys this month, EXCLUDING the approval for the proposal being
     checked, so approving the lamp doesn't count its own $199 twice); prior_claims (done/running claims on this
     order); claim (first claim: recipient, thread_id, followups) or None; demo_mode;
     demo_recipients (frozenset of exact lowercase addresses).
   - Decision: decision, results [{rule, pass, why, values}], proposal_hash, window_ends,
     property failed_rule.
   - _date(v) and _ts(v) coerce strings/datetimes; window_end(action, item, policy) picks
     the adjust window (claims, follow-ups) or the return window (rebuys, alerts), each
     starting at purchase or delivery per the policy; unknown delivery -> purchase.
   - run_gate(p, pact, ctx, human_approved=False): table 3.9 in exactly that order. Wrap
     everything so any exception returns BLOCK on R0 with the error text. Record the
     values compared for every rule. Never read a price, fee or date from the proposal.
   - run_approved(p, pact, ctx, approval): A1 (status approved, not expired, stored
     proposal_hash == proposal_hash(p), session_user_id == pact_owner_id, reverified_at
     within 15 minutes) else BLOCK on A1; then run_gate(..., human_approved=True).

3. backend/pact/once.py — execute_once(conn, decision, p, action_fn, amount_usd):
   only for ALLOW with a proposal_hash. INSERT executed_actions(status 'running') first;
   on duplicate key: if the existing status is 'failed', UPDATE ... SET status='running'
   WHERE proposal_hash=%s AND status='failed' and continue only if exactly one row
   changed; otherwise raise AlreadyRan. Run action_fn; set 'done' or 'failed' with the
   result (truncated to 1000 chars).

4. tests/test_gate.py and tests/test_safety.py (pytest, hand-built contexts, no DB):
   one test per rule plus every seed-row outcome in 3.8:
   earbuds ALLOW; follow-up ALLOW to same recipient/thread; 4th follow-up BLOCK R13;
   follow-up to a new address BLOCK R13; lamp ASK R12; lamp approved -> ALLOW; approval
   with changed proposal / expired / other user / no re-check -> BLOCK A1; backpack BLOCK
   R5; fan BLOCK R4; Best Buy $6 BLOCK R8 even though its price is stale (R8a runs after);
   Target alert ALLOW; price_adjust_email to Target BLOCK R6c while claim_packet ALLOWs;
   Target claim against a cheaper Best Buy listing BLOCK R5; cross-store same UPC passes R5
   for a rebuy; price seen 20 minutes ago BLOCK R8a; Best Buy window counted from delivery;
   proposal listing_id or store differing from the DB BLOCK R0; missing action_type BLOCK
   R0 without raising; revoked (pacts.status still 'active') BLOCK R1; old version BLOCK
   R1; edited pact_text BLOCK R1; Persona 45 days old BLOCK R1; citation for another store
   or 2 days old BLOCK R6; $200 done + $90 approved-not-run + $40 new with $300 cap BLOCK
   R9; approving the $199 lamp with nothing else this month ALLOWs (own approval not
   double-counted); second claim on the same order BLOCK R11; demo mode blocks support@target.com;
   DEMO_MODE unset -> on; random@gmail.com blocked when only a teammate's Gmail is
   allowed; execute_once twice -> AlreadyRan; execute_once after 'failed' -> retries once;
   Persona webhook: good signature passes, wrong secret / old timestamp / missing header
   fail.

5. backend/pact/context.py — build_context(opportunity_id, user): load everything in
   GateContext from SingleStore, starting from the opportunity. active_pact(user_id):
   newest version, verify_text(pact_text), revoked = any valid pact_revocations row for
   that pact_id+version, is_latest = no newer version. Never read pacts.status.
   record(p, pact, decision, action_result): audit_log row (decision may be NO_ACTION).

6. backend/pact/api.py — POST /pact (validate the form fields, attach persona_inquiry_id,
   sign, store pact_text as a new version, write a signed revocation for the previous
   version), GET /pact (plain summary for the UI), POST /pact/pause (signed revocation),
   POST /gate/check, GET /approvals, POST /approvals/{id} (logged-in verified owner only;
   approve -> Persona re-check -> run_approved with a freshly built context -> on ALLOW
   execute_once with B's actor -> status executed; decline -> declined; past expires_at
   -> expired).

7. backend/persona/ (sandbox; template created in the Persona dashboard beforehand).
   POST /persona/inquiry (reference_id = user id). POST /persona/webhook: read the RAW
   body; verify the Persona-Signature header (t=<unix>,v1=<hex>; HMAC-SHA256 of
   "{t}.{raw_body}" with PERSONA_WEBHOOK_SECRET; accept any of several space-separated
   pairs; reject if older than 5 minutes); then confirm the status with a server-side GET
   of the inquiry before updating users. POST /persona/recheck sets reverified_at.
   Store only ids, status and timestamps. Fail closed. Check Persona's current docs for
   exact names and tell me what you assumed.

8. frontend/ — follow section 3.10 exactly: a calm consumer app, plain words, details
   only behind "Why?" and the "Show details" switch. Three screens:
   (1) Money back (home): big "You've got $X back"; "Needs your OK" cards on top with
       "Approve and buy" / "Not now"; one card per purchase with one plain sentence and a
       status pill; "Why?" opens the 4-line story.
   (2) Your Pact: first run = Verify it's you -> Set your limits (sentences with toggles
       and dollar fields) -> Review one-paragraph summary -> Sign my Pact. After that, a
       summary card with "Edit limits" and "Pause Clawback".
   (3) Activity: timeline, newest first, filter chips All / Done / Needs your OK /
       Stopped, each item with "Why?".
   "Show details" switch in the header (off by default; remembered per browser): adds
   rule IDs with values, Pact version and short hash, Senso source id and ids.
   Approve -> Persona re-check -> "Done" with what happened. Lists refresh every few
   seconds so the webhook-driven price drop appears on stage without a reload.

Small commits. After each step run the tests and tell me what's done and what's next.
If a contract blocks you, stop and tell me which teammate to ask.
```

### 4.2 Teammate A — Data, ingest, matching (35%)

```
You are my pair programmer at a one-day hackathon (code freeze 4:10 PM).
Project: Clawback — a personal agent that reads purchase receipts from Gmail, watches prices
and recovers money only within a signed "Pact"; a deterministic gate (Siva's code) decides
every action. My part is DATA: SingleStore, receipt ingest with the Airbyte Gmail agent
connector, parsing (Arcee), embeddings and reranking with Superlinked SIE Serverless,
vector matching in SingleStore, price updates and opportunities. The attached team file
(section 3) has the contracts; follow them exactly. I may only edit backend/db.py,
backend/schema.sql, backend/ingest/, backend/watch/, seed/receipts/, tests/test_matcher.py,
plus one include_router line in backend/main.py. I target the SingleStore, Airbyte,
Superlinked and Arcee prizes, so their use must be real and visible.

KEY DESIGN RULE: the matcher only SUGGESTS. Create opportunities from the best vector
candidate even when SKU/variant don't match, and for ANY price drop. The gate enforces
identity (R5) and minimum savings (R8). Filtering early kills the demo's "Stopped" moments.

BUILD, in this order:

1. backend/schema.sql exactly as 3.3 in one database. backend/db.py: query(), execute(),
   connection() (singlestoredb, SINGLESTORE_URL). Insert store_policies: mockstore
   (claim_channel email, price_adjust_allowed true, competitor_match_allowed false, adjust
   14 days from purchase, return 30 days from delivery, final_sale ["clearance"], 0 fees,
   support_domains []); target and bestbuy with the values B confirms (claim_channel chat).
   Insert user u_demo with the demo email. Commit this first — everyone depends on it.

2. Ingest with the Airbyte Gmail agent connector (airbyte-agent-sdk / airbyte-agent-gmail,
   hosted mode; AIRBYTE_CLIENT_ID/SECRET, AIRBYTE_WORKSPACE_NAME). The Airbyte SingleStore
   destination is archived: don't use it. Search the demo inbox for order confirmations,
   fetch each, insert into raw_emails from our code (skip ids already present). Check the
   connector docs for entity/action names. POST /ingest/run = ingest + parse.
   Fallback: seed/load_seed.py inserts the .eml files; return to Airbyte after milestone 3.

3. backend/ingest/parser.py: Arcee small model; email is untrusted data in <email> tags;
   JSON with store, order_no, order_date, delivery_date, items[title, sku, upc,
   model_number, variant, category, price_paid, qty, confidence]; null instead of guessing.
   Validate; retry once; else confidence 0. Insert orders and line_items (store copied onto
   each item). Log model, tokens, latency; track("receipt_parsed", ...).

4. backend/ingest/embed.py: SIE Serverless embeddings (SIE_URL/SIE_API_KEY; sie-sdk or the
   OpenAI-compatible /v1/embeddings) for line_items.title and listings.title, stored in
   title_vec. Storage and search stay in SingleStore.

5. backend/watch/:
   - Listings from C's GET /mock/api/listings, plus one cached real price each for the Best
     Buy and Target seed items (fetched once, stored with its real seen_at).
   - POST /watch/price-changed: verify HMAC (MOCK_WEBHOOK_SECRET), write price_history,
     call on_price_change(). tick() does the same for all listings (button / fallback).
   - match(line_item_id): ORDER BY DOT_PRODUCT(title_vec, :vec) DESC LIMIT 10, rerank with
     SIE, keep top 5 with similarity and rerank_score; exact = same store -> SKU + variant,
     other store -> UPC or model_number + variant. Prefer exact; else keep the best anyway.
   - Opportunities: kind price_drop when the chosen candidate's latest price < price_paid
     (any amount); kind deadline when the return window ends within 7 days with no drop.
     One open opportunity per line item.

6. GET /orders; GET /activity (audit_log joined with proposals, line_items and
   policy_checks, returning the fields the UI needs: item title, variant, store, paid,
   found price, seen_at, cited_text, source_url, decision, failed_rule, rule values);
   saved SQL "everything the Pact stopped this week, and why" to run live for judges.

7. tests/test_matcher.py: Blue backpack is the top candidate for the Black receipt but not
   exact; earbuds exact; different SKUs with the same UPC and variant across stores exact.

Seed: six .eml receipts in seed/receipts/ matching 3.8, dated relative to 2026-11-20.
Small commits; report after each step. Schema changes need Siva's and B's OK.
```

### 4.3 Teammate B — Agent, policy, actions (35%)

```
You are my pair programmer at a one-day hackathon (code freeze 4:10 PM).
Project: Clawback — a personal agent that recovers money after purchases and can only act
inside a signed "Pact". The LLM PROPOSES; a deterministic gate (Siva's code) DECIDES
ALLOW / ASK / BLOCK. My part is the AGENT: Senso policy checks, the proposal LLM, the
orchestration endpoint, the actors (mock-store claim email, claim packet, rebuy+return,
follow-ups) and Firedrill (stretch). The attached team file (section 3) has the contracts
and gate rules (3.9); follow them exactly. I may only edit backend/agent/, backend/actor/,
seed/policies/, plus one include_router line in backend/main.py. I target Senso (largest
prize) and Firedrill, so the store's policy must be quoted in every decision.

HARD SAFETY RULES
- No email ever reaches a real retailer. Target and Best Buy don't take claims by email:
  for them the action is claim_packet, emailed to the USER (their own address) and shown in
  the app. Automatic store email is only for the mock store.
- DEMO_MODE defaults to true; send_email() refuses any recipient not exactly in
  DEMO_ALLOWED_RECIPIENTS, even with an ALLOW decision.
- The LLM never calls actors and never supplies facts the gate uses.
- Every action runs through Siva's execute_once, so a double click or retry can't send twice.

BUILD, in this order:

1. Senso: CLI (npm install -g @senso-ai/cli, senso login). One document per store in
   seed/policies/: Target and Best Buy official price-adjustment/price-match and return text
   with source URLs (Target: own prices only since July 2025; Best Buy: confirm the period
   and whether it starts at delivery), plus the mock store policy. Indexing may be slow or
   rate-limited: load first thing and wait until search returns them. Give A the structured
   values for store_policies and each senso_doc_id.

2. backend/agent/policy.py — check_policy(opportunity_id): narrow Senso question for the
   item's store (from the DB); save question, answer, cited sentence, source_url,
   senso_source_id, revision (if Senso exposes one), store, opportunity_id, fetched_at.
   No source -> NULL (the gate stops it on R6). Never invent a citation.

3. backend/agent/propose.py — propose(opportunity_id): decision prompt with trusted DB
   facts (item, listing, price history, store_policies row, Senso answer + source, Pact
   summary). One tool, propose_action: the LLM chooses only action_type, template, alert_at
   and reasoning. Fill opportunity_id, line_item_id, store, listing_id, rebuy.listing_id,
   return.order_no and the recipient from the DB (3.5). Choice logic for the prompt:
   price_adjust_email if claim_channel is email, adjustments are offered and the adjust
   window is open; claim_packet for the same case at a chat store; rebuy_return if
   adjustments aren't possible but the return window is open; deadline_alert for kind
   deadline; none otherwise. Validate with pydantic; save with proposal_hash.

4. POST /opportunities/{id}/propose — the main loop in 3.6. none -> NO_ACTION. ALLOW ->
   execute_once(conn, decision, p, actor, amount) -> record the result. ASK -> approvals
   row (proposal_hash, user_id, amount_usd, expires_at now + 24 h). BLOCK -> stop. Update
   statuses; track() every decision. GET /opportunities/{id} returns everything the Why?
   panel needs: item, paid, found price + seen_at, cited sentence + source_url, the
   decision and failed rule with values, messages and any claim packet.

5. Actors (backend/actor/):
   - send_email(decision, to, subject, body, transport, thread_id): raise unless ALLOW;
     raise in demo mode unless `to` is exactly allowed; send through the Airbyte Gmail agent
     connector (hosted mode; check the docs for the send action) passed in as transport;
     store the message with proposal_hash and thread_id. Templates only: price_adjust_v1,
     packet_v1, proof_v1; footer "Sent by Clawback on behalf of <verified user name>".
   - build_claim_packet(decision, opportunity_id): receipt details, price + URL + seen
     time, the quoted policy sentence and a short chat script; save to claim_packets and
     email it to the user with packet_v1.
   - rebuy_return(decision, listing_id, original_order_no): one call to C's
     POST /mock/api/rebuy_return (both happen or neither). Never a real store.

6. Follow-ups (backend/agent/replies.py + POST /replies/inbound): reply text is untrusted
   data in <reply> tags. Choose answer_with_proof, accept_offer or escalate_to_user (store
   credit, partial refund, policy dispute -> escalate). An answer is a claim_followup
   proposal (proof_v1, to = the original claim's recipient, same thread_id) that goes
   through the gate (R13). Never send payment details or write to a new address.

7. Stretch, once everything works end to end:
   - Senso live edit: change the mock store's adjust window from 14 to 7 days in Senso and
     in store_policies, re-run a claim, and show it stopped on R7 quoting the new text. Only
     live if indexing is fast enough; otherwise pre-record it.
   - Firedrill scenarios F1–F10 from the team doc; report a pass rate only if true.

Small commits; report after each step. If Siva's gate isn't ready, use a clearly marked
stub that returns ALLOW and remove it as soon as the real gate lands.
```

### 4.4 Teammate C — Scaffold, mock store, analytics, ship (30%)

```
You are my pair programmer at a one-day hackathon (code freeze 4:10 PM).
Project: Clawback — a personal agent that recovers money after purchases and can only act
inside a signed "Pact"; a deterministic gate decides every action. My part is the PLATFORM
and SHIPPING: repo scaffold, FastAPI shell, the mock store (our controllable retailer),
Amplitude analytics, README, demo video and submission. The attached team file (section 3,
including the UI words in 3.10) has the contracts; follow them exactly. I may only edit
backend/main.py, mockstore/, backend/analytics/, README.md, requirements.txt, .env.example,
.gitignore.

BUILD, in this order:

1. First: GitHub repo with the folder structure, .gitignore (.env first), .env.example (3.2),
   requirements.txt (fastapi, uvicorn, pydantic, singlestoredb, httpx, python-dotenv,
   pytest, airbyte-agent-sdk, sie-sdk), backend/main.py with CORS for localhost:3000,
   /health and the router-mounting pattern. Push and tell the team.

2. mockstore/ under /mock on the same app, products exactly as seed rows 1–4 (3.8):
   earbuds MS-1001 Black $129.99; lamp MS-2002 White $239.00; backpack MS-3003 Black $89.00
   AND Blue $89.00; fan MS-4004 Gray, category clearance, $59.00.
   - Pages: /mock/p/{sku}; /mock/policy (claims by email to Mock Store support; price
     adjustments 14 days from purchase, own prices only; returns 30 days from delivery;
     clearance is final sale; no restocking fee; free returns); /mock/admin with price
     fields, "Drop 25%" buttons, "Demo preset" (earbuds $95.99, lamp $199.00, Blue
     backpack $64.00, fan $39.00) and Reset.
   - API: GET /mock/api/listings (store, sku, upc, variant, title, url, price, seen_at);
     POST /mock/api/rebuy_return {listing_id, original_order_no}: buys the new item AND
     creates the return in one step; if either fails, neither happens; returns both numbers.
   - On every price change, POST to /watch/price-changed with listing_id and price, signed
     with HMAC (MOCK_WEBHOOK_SECRET), so the drop appears instantly.
   Prices in memory. Make it look like a plain, believable store.

3. backend/analytics/events.py — track() to Amplitude's HTTP API, never raises. Events:
   receipt_parsed, opportunity_found, gate_decision (with failed_rule), action_executed,
   claim_packet_ready, money_recovered, approval_resolved, pact_signed, pact_paused.
   GET /stats: dollars recovered and Done / Needs your OK / Stopped / No action counts.
   Amplitude dashboard: dollars recovered, decision mix, stop reasons by rule.
   (Amplitude alone qualifies for the "Amplitude or Statsig" prize; no Statsig.)

4. Takeover rule: if Siva's Activity screen isn't working at milestone 3, build it with the
   words and card copy in 3.10 (GET /activity + GET /opportunities/{id}); plain language,
   no rule IDs unless "Show details" is on.

5. README.md: hook (Black Friday is a week away); pitch; problem; how it works; the Pact
   (rules table 3.9, threat model); each sponsor and exactly where it shows; how to run;
   pytest output; real vs simulated (simulated: mock store prices, the support rep, cached
   real-store prices; real: Airbyte Gmail ingest and send, SingleStore, SIE, Senso, Persona
   sandbox, the gate); honest scope: "matches each receipt to the current listing;
   cross-store identity by UPC/model number"; team names.

6. Demo + submission: own the live script and the 2–2.5 minute video. Dry run first;
   final recording right after the 4:10 code freeze. Submit repo link, video link,
   description (team doc), team names and emails, and a screenshot of a "Stopped by your
   rules" card with its Why? panel open.
```

---

## 5. Definition of done (by 4:10 code freeze)

- [ ] Mock-store price drop arrives via webhook → email lands in the Mock Store support inbox, with the policy quoted in Why?
- [ ] Support replies "send proof" → follow-up passes R13 → proof sent in the same thread
- [ ] Lamp → Needs your OK → approve with Persona re-check → rebuy + return together on the mock store
- [ ] Backpack → Stopped (R5); fan → Stopped (R4); Best Buy $6 → Stopped (R8); Target → Return closes soon
- [ ] Double-clicking Approve sends one action only
- [ ] `pytest -q` green
- [ ] "What the Pact stopped this week" SQL query runs live
- [ ] Amplitude dashboard shows today's events
- [ ] Gmail Sent folder has nothing outside DEMO_ALLOWED_RECIPIENTS
- [ ] A non-technical teammate can sign a Pact and approve the lamp without help; no rule IDs visible until "Show details" is on
