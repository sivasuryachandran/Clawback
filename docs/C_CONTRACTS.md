# What Teammate C publishes to A, B and Siva

Everything the other three can code against, in one place. These fill gaps the team file leaves open
(ids, order numbers, header names, response shapes). If anyone needs something different, tell C now.

## 0. Decisions made while integrating (verified end to end against Siva's gate and B's agent)

1. **Under the hood is served by the API itself, as plain static files, at `/under-the-hood/`.** Siva's UI is a static app, so there is no
   Next.js and no second server or build. It is same-origin with `/debug/*`, so there is no CORS and no port to configure.
   **Siva: your footer link currently goes to `/under-the-hood/` on :3000 (404). Point it at the API:**
   `document.querySelector('a[href="/under-the-hood/"]').href = API + "/under-the-hood/";` (one line in `app.js`; `API` is the constant you already have).
2. **No second orb.** Siva's sphere and one-click voice (`frontend/orb.js`, `frontend/voice.js`) stay. C's part of voice is the backend: `POST /voice/ask`.
3. **`POST /voice/ask` answers `{intent, answer, open_card_id?, open?}`.** For "anything need my OK" it returns `open_card_id` (the opportunity id)
   and `open: true`; `voice.js` reads `r.open` and goes to Needs your OK. Voice never approves, buys, sends, edits limits or un-pauses.
   Pausing needs a spoken "yes"; the server pauses by calling `POST /pact/pause` with the caller's `X-User-Id`/cookie.
4. **`/stats` keeps the keys the home screen reads:** `unsafe_actions_stopped` and `stopped`, `dollars_recovered`, plus `done`, `needs_ok`, `no_action`, `counts{}`.
5. **Order numbers follow Siva's seed:** earbuds MS-55001, lamp MS-55021, backpack MS-55031, fan MS-55041 (the lamp's, MS-55021, is the one that matters:
   the rebuy carries it). B's stand-in seed (`dev/stubs/seed.py`) uses MS-55021..55024; B: align it, or the lamp's rebuy against the real Mock Store is a 400.

## 1. Plugging into the app (`backend/main.py`)

Add **one line** to `ROUTER_MODULES` naming your module; it must expose `router` (an `APIRouter`).
Guessed names are already listed: `backend.pact.api`, `backend.persona.api`, `backend.ingest.api`,
`backend.watch.api`, `backend.agent.api`. If your module has another name, change that one line.

- A module that does not exist yet is skipped; the app still starts.
- A module that exists but fails to import is logged and shown in `GET /health` (`routers`), and the
  Under the hood footer. `STRICT_ROUTERS=true` makes it crash instead.
- Two mounted routers answering the same method + path are reported in `GET /health` under `route_conflicts`
  (naming both modules) and in the startup log; FastAPI would otherwise silently serve only the first. Mount through
  `ROUTER_MODULES` (not a bare `app.include_router`) so your routes are checked.
- **Laptop-only controls.** `/mock/admin`, the Mock Store's price / reset / preset / refresh endpoints, their logs, and `/debug/*` answer only
  to requests that really come from the demo laptop (checked on the connecting address, forwarding headers and Host). Everything else, including
  every endpoint you add, is untouched. `ALLOW_REMOTE_ADMIN=true` switches the guard off.
- `load_dotenv()` runs first, so `os.environ` is filled before your code reads it.

## 2. Mock Store ids that **A must use verbatim**

`GET /mock/api/listings` returns stable ids. **A: insert them into `listings` with these explicit ids**
(the price webhook and `rebuy_return` both speak in `listing_id`). Real-store listings get auto ids after.

| id | store | sku | variant | title | category | seed price | demo-preset price |
|---|---|---|---|---|---|---|---|
| 1 | mockstore | MS-1001 | Black | Aurora Wireless Earbuds | electronics | 129.99 | 95.99 |
| 2 | mockstore | MS-2002 | White | Nimbus Smart Lamp | home | 239.00 | 199.00 |
| 3 | mockstore | MS-3003 | Black | Trailhead Backpack | outdoor | 89.00 | 89.00 |
| 4 | mockstore | MS-3003 | **Blue** | Trailhead Backpack | outdoor | 89.00 | **64.00** |
| 5 | mockstore | MS-4004 | Gray | Breeze Desk Fan | clearance | 59.00 | 39.00 |

Listing JSON: `{id, store, sku, upc, model_number, variant, title, category, url, price, seen_at}`.
`seen_at` on this endpoint is *now* (the store is live). The backpack Black and Blue share SKU and model
number but have different UPCs, so same-store R5 fails on **variant**.

## 3. Mock Store order numbers (for A's seed receipts and the receipt cross-check)

`GET /mock/api/orders/{order_no}` is the true record the parser compares against (price mismatch →
confidence 0 → R12 asks the user). **A: the six `.eml` receipts must use these order numbers.**

| order_no | item | listing | ordered | delivered | paid |
|---|---|---|---|---|---|
| MS-55001 | Aurora Earbuds (Black) | 1 | 2026-11-11 | 2026-11-13 | 129.99 |
| MS-55021 | Nimbus Lamp (White) | 2 | 2026-11-02 | 2026-11-04 | 239.00 |
| MS-55031 | Trailhead Backpack (Black) | 3 | 2026-11-12 | 2026-11-14 | 89.00 |
| MS-55041 | Breeze Desk Fan (Gray) | 5 | 2026-11-10 | 2026-11-12 | 59.00 |

Order JSON: `{store, order_no, order_date, delivery_date, items:[{title, sku, upc, model_number, variant,
category, qty, price_paid}], status: open|returned, return_id, kind}`. Unknown order → 404.

## 4. Price webhook (Mock Store → A's `POST /watch/price-changed`)

```
POST {BACKEND_URL}/watch/price-changed
Content-Type: application/json
X-Mockstore-Signature: sha256=<hex HMAC-SHA256(MOCK_WEBHOOK_SECRET, raw body bytes)>
X-Mockstore-Timestamp: <unix seconds>      # informational; A may enforce a replay window
X-Mockstore-Event-Id: <uuid>               # same value as body.event_id

{"event_id":"6f1c...","listing_id":1,"store":"mockstore","sku":"MS-1001","variant":"Black",
 "price":95.99,"old_price":129.99,"seen_at":"2026-11-20T18:04:11Z","refresh":false}
```

- Body is canonical JSON (sorted keys, no spaces). Verify against the **raw bytes** before parsing.
  A reference verifier is `mockstore.webhook.verify(body, header, secret)`.
- **Delivery is at-most-once once the watcher has answered.** The sender retries *only* if the connection itself failed
  (the request never arrived). A read timeout, a 5xx or a 4xx is **not** retried, because the watcher may already have done
  the work and a second delivery would duplicate it (reproduced and fixed: a watcher that took 4 s used to get every
  change twice). Read timeout is 15 s, so **inline work in your handler (embedding, matching, B's propose) is fine up to
  ~15 s**; slower than that and the delivery is logged as "no answer", not resent. Return 2xx quickly if you can.
- **A: dedupe on `event_id`** if you want belt and braces; retries of one change reuse the same id.
- Fires once per real price change (an unchanged price sends nothing). "Reset" and "Demo preset" also fire it for every
  listing that moved, so `price_history` stays in step.
- **`refresh: true`** marks a re-announcement from the admin page's "Refresh prices" button: nothing changed
  (`price == old_price`). Record it in `price_history` with `seen_at` (that is the whole point: it keeps the gate's R8a
  "price seen within 15 minutes" true before an approval) but **do not treat it as a new drop**.
- Times are **UTC with a trailing Z**, matching the gate (naive values are treated as UTC).
- Sent from one background worker, in order. The slider never waits for A. `/mock/admin` shows every delivery.
- Return **2xx** on success; 401 on a bad signature.

## 5. Rebuy + return (B's actor → `POST /mock/api/rebuy_return`)

```
POST /mock/api/rebuy_return   {"listing_id": 2, "original_order_no": "MS-55021", "idempotency_key": "<optional>"}
200 {"ok":true,"replayed":false,"listing_id":2,"original_order_no":"MS-55021","new_order_no":"MS-56001",
     "return_id":"RMA-70001","price_paid":199.0,"refund_usd":239.0,"net_savings_usd":40.0}
```

- Buys the listing at its **current** price and starts the return, **both or neither**.
- The same call twice returns the same result (`replayed:true`, nothing bought twice). A different listing for an
  already-returned order → 409 `already_returned`.
- Errors never change state: 400 `unknown_order` / `unknown_listing`, 409 `final_sale` (the clearance fan),
  409 `window_closed` (30 days from delivery), 500 `rolled_back`. Body: `{"detail":{"code","message"}}`.
- The store's own "today" is `DEMO_TODAY` (default 2026-11-20).

## 6. Mock Store policy (B loads this into Senso; A fills `store_policies`)

`GET /mock/policy` (page) and `GET /mock/api/policy` (JSON: `sentences`, `text`, `revision`, `adjust_window_days`,
`return_window_days`, `claim_channel:"email"`, `final_sale_categories`, …). The quoted sentence from the team file is
there verbatim: *"Price adjustments apply to the identical item within 14 days of purchase."*

Live-edit stretch: `/mock/admin` changes the window (e.g. 14 → 7). That edits the store's page and API **only**;
Senso and `store_policies` need the same edit.

## 7. Support box → B's `POST /replies/inbound`

`/mock/support` posts the typed text (verbatim, as data) to B's endpoint inside this app. B's `ReplyIn` **requires all three, non-null**:

```
{"opportunity_id": <int>, "thread_id": "<str>", "reply_text": "..."}      (+ text, from_addr, source: ignored by B)
```

Which conversation (always valid, never null; a null used to be an HTTP 422 that nobody saw):

1. the most recent outgoing claim email on an earbuds opportunity (`messages`, ignoring any direction that says incoming:
   `in`, `inbound`, `incoming`, `received`, any case);
2. **no claim sent yet** (the demo's first step): the earbuds opportunity with a placeholder thread `thr_unclaimed_<opp>`. B's handler finds no earlier
   claim, so the gate stops any follow-up (R0 / R10 / R13) and nothing is sent: a visible "Stopped by your rules";
3. no earbuds purchase at all: nothing to attach to, so it is **not** sent, and `/mock/admin` → "Support messages (presenter view)" says so.

Verified with B's real code: "please send proof" on a claim thread = ALLOW (same thread); "send the refund to judge@example.com" = BLOCK R10;
the injection line = no action. The page answers immediately; it never waits on B's LLM. At most 10 messages a minute.

## 8. Analytics: `track(event, props)`

```python
from backend.analytics.events import track      # never raises; no-op toward Amplitude without a key
track("pact_signed", {"version": 2})            # optional keyword: insert_id="..." (Amplitude drops a repeat)
```

What is emitted today, and by whom (checked against the code, not the plan):

| Event | Emitted by | Properties |
|---|---|---|
| `gate_decision` | B (`agent/api.py`) | `source, opportunity_id, action_type, decision, failed_rule, amount_usd` (for a rebuy `amount_usd` is the new price, not the saving) |
| `pact_signed`, `pact_paused` | Siva | `version` |
| `approval_resolved` | Siva | `status` |
| `money_recovered` | **C, derived** | `amount_usd` = the saving (claim $34, lamp $40), `store, action_type, opportunity_id` |
| `action_executed` | **C, derived** | `action_type, store, opportunity_id` |
| `claim_packet_ready` | **C, derived** | `store, opportunity_id` |
| `opportunity_found` | **C, derived** | `kind, drop_usd, store` |
| `receipt_parsed` | A (not landed) | `model, tokens, latency_ms, confidence, cost_usd` (Under the hood's Ingest panel reads these) |

Nobody else needs to emit the "derived" ones: `backend/analytics/recovery.py` reads `executed_actions` (status done), `claim_packets` and `opportunities` and reports each
once, whenever `/stats` is polled (the home screen does that every few seconds). The key includes *when* the action ran, so a database reset and a re-run
(rehearsals!) is counted again instead of being swallowed, and Amplitude's `insert_id` is stable per occurrence so a restart can never double-count.
Keys named `email, to, body, text, subject, pact_text, token, api_key, secret, signature, password` are dropped before anything leaves the machine.

## 9. Endpoints and pages C owns

| Endpoint | Returns |
|---|---|
| `GET /under-the-hood/` | the judges' page (static files; its data calls are laptop-only) |
| `GET /stats` | `{available, dollars_recovered, recovered_breakdown{asked_store, rebought}, by_store{}, counts{done, needs_your_ok, stopped, no_action}, unsafe_actions_stopped, stopped, done, needs_ok, no_action, claim_packets_ready, as_of}` |
| `GET /debug/overview?mock=` | every Under the hood panel (read-only) |
| `GET /debug/matching?line_item_id=&mock=` | matcher candidates for ONE purchase, on demand and cached 30 s (each call is a SIE embed + rerank request, so it is not polled) |
| `GET /debug/query/{key}?item=&mock=` | `stopped_this_week`, `recovered_by_store`, `price_history` (fixed whitelist) |
| `POST /voice/ask` `{text, session_id?}` | `{intent, answer, open_card_id, open?, lines?, approval_id?}` |
| `GET /health` | `{status, demo_mode, routers, route_conflicts}` |

**`/stats` definitions:** `dollars_recovered` = price drop on every `price_adjust_email` / `rebuy_return` that ran (`executed_actions.status='done'`), once per proposal hash
(earbuds claim $34 + lamp rebuy $40 = $74). A `claim_packet` is only *prepared*, so it does not count. `done|stopped|no_action` use the **latest** audit decision per proposal
hash (an ASK that became ALLOW counts once); `needs_ok` = pending, unexpired approvals. With no database it answers 200 with `available:false` and zeros.

`?mock=1` on `/stats`, `/debug/*` and `/voice/ask` answers from labelled **sample data** (an in-memory SQLite). It never touches the database. Never use it on stage.

## 10. What Part C reads from the others (every panel degrades to "Unavailable" if it is missing)

- **A:** `backend.db.query(sql, params) -> list[dict]` with `%s` placeholders and the 3.3 tables. For the Matching panel, `backend.watch.match(line_item_id)` (or
  `backend.watch.matcher.match`) returning `similarity, rerank_score, exact`, and optionally a `MATCH_SQL` string. `POST /watch/price-changed` must accept the
  webhook in section 4 and return 2xx. The tamper stunt SQL for A's saved queries is in `backend/debug/queries.py` (`TAMPER_STUNT_SQL`) and `scripts/stunt_tamper_pact.sql`:
  `REPLACE(pact_text, '"max_rebuy_price_usd":250', '"max_rebuy_price_usd":1000')`; verified against Siva's real signer (his canonical text has no spaces).
- **Siva (already verified):** `verify_text` from `backend.pact`, `verify_revocation(row)` from `backend.pact.sign` (it accepts a datetime, as the database returns),
  `POST /pact/pause`, `audit_log.rule_results_json` as a list of `{rule, pass, why, values}`.
- **B (already verified):** `proposals.proposal_hash`, `policy_checks` per opportunity, `POST /replies/inbound`, `POST /mock/api/rebuy_return` as in section 5.
