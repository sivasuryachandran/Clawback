# What Teammate C publishes to A, B and Siva

Everything the other three can code against, in one place. These fill gaps the team file leaves open
(ids, order numbers, header names, response shapes). If anyone needs something different, tell C now.

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
| MS-55034 | Aurora Earbuds (Black) | 1 | 2026-11-11 | 2026-11-13 | 129.99 |
| MS-55021 | Nimbus Lamp (White) | 2 | 2026-11-02 | 2026-11-04 | 239.00 |
| MS-55041 | Trailhead Backpack (Black) | 3 | 2026-11-12 | 2026-11-14 | 89.00 |
| MS-55029 | Breeze Desk Fan (Gray) | 5 | 2026-11-10 | 2026-11-12 | 59.00 |

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

`/mock/support` posts the typed text (verbatim, as data) to `/replies/inbound` inside this app:

```
{"opportunity_id": <int|null>, "thread_id": <str|null>, "reply_text": "...", "text": "...",
 "from_addr": "support@mockstore.invalid", "source": "mockstore_support"}
```

The thread is the most recent outgoing message on an earbuds opportunity (read from `messages`; any direction that says incoming, such as `in`, `inbound`, `incoming` or `received`, is ignored).
**B: please confirm these field names, or tell C which to send.** Answer immediately; the page never waits on B's LLM.

## 8. Analytics: `track(event, props)`

```python
from backend.analytics.events import track      # never raises; no-op toward Amplitude without a key
track("gate_decision", {"decision": "BLOCK", "failed_rule": "R5", "action_type": "price_adjust_email", "pact_version": 2})
```

| Event | Emit from | Useful props |
|---|---|---|
| `receipt_parsed` | A | `model, tokens, latency_ms, confidence, cost_usd` (Under the hood's Ingest panel reads these) |
| `opportunity_found` | A | `kind, drop_usd, store` |
| `gate_decision` | B (main loop) | `decision, failed_rule, action_type, pact_version` |
| `action_executed` | B | `action_type, store, amount_usd` |
| `claim_packet_ready` | B | `store` |
| `money_recovered` | B | `amount_usd, store` |
| `approval_resolved` | Siva | `status` |
| `pact_signed`, `pact_paused` | Siva | `pact_version` |

Keys named `email, to, body, text, subject, pact_text, token, api_key, secret, signature, password` are dropped
before anything leaves the machine. Pass `user_id` if you have it (default `u_demo`).

## 9. Endpoints C owns

| Endpoint | Returns |
|---|---|
| `GET /stats` | `{available, dollars_recovered, recovered_breakdown{asked_store, rebought}, by_store{}, counts{done, needs_your_ok, stopped, no_action}, unsafe_actions_stopped, claim_packets_ready, as_of}` |
| `GET /debug/overview?line_item_id=&mock=` | every Under the hood panel (read-only) |
| `GET /debug/matching?line_item_id=&mock=` | matcher candidates for ONE purchase, on demand and cached 30 s (each call is a SIE embed + rerank request, so it is not part of the polled overview) |
| `GET /debug/query/{key}?item=&mock=` | `stopped_this_week`, `recovered_by_store`, `price_history` (fixed whitelist) |
| `POST /voice/ask` `{text, session_id?}` | `{intent, answer, open_card_id, lines?, approval_id?}` |
| `GET /health` | `{status, demo_mode, routers}` |

**`/stats` definitions (Siva: this is your "You've got $X back" and "N unsafe actions stopped"):**
`dollars_recovered` = price drop on every `price_adjust_email` / `rebuy_return` that ran (`executed_actions.status='done'`),
once per proposal hash (earbuds claim $34 + lamp rebuy $40). A `claim_packet` is *prepared for the user*, so it does not
count. `counts.done|stopped|no_action` use the **latest** audit decision per proposal hash (an ASK that became ALLOW counts
once, as done). `counts.needs_your_ok` = pending, unexpired approvals. With no database it answers 200 with
`available:false` and zeros.

`?mock=1` on `/stats`, `/debug/*` and `/voice/ask` answers from labelled **sample data** (an in-memory SQLite loaded with
the section 3.8 demo state). It never touches SingleStore. Handy while the real pieces are missing; never use it on stage.

## 10. What Under the hood and voice need from the others (all optional; panels degrade to "Unavailable")

- **A:** `backend.db.query(sql, params) -> list[dict]` with `%s` placeholders. For the Matching panel, `backend.watch.match(line_item_id)`
  (or `backend.watch.matcher.match`) returning candidates with `similarity, rerank_score, exact`, and optionally a `MATCH_SQL` string.
- **Siva:** mark each Money back card with its opportunity id in any one of these ways: `data-card-id="2"`, `data-opportunity-id="2"`, or `id="card-2"` /
  `id="opportunity-2"` / `id="opp-2"`. "Anything need my OK?" then scrolls to and flashes that card (`open_card_id` is the opportunity id). `<Orb onOpenCard={(id) => ...}>` overrides it.
- **Siva:** `verify_text(pact_text)` and `verify_revocation(row)` importable from `backend.pact` or `backend.pact.sign`, and
  `POST /pact/pause` (voice calls it with the caller's own cookie / `Authorization`). `verify_revocation(row)` receives
  a raw DB row, so `revoked_at` is a `datetime`, not the string you signed; C tries the common renderings of that instant
  (the HMAC must still match), but make `verify_revocation` itself tolerant too, since the gate reads the same rows. Canonical Pact text must have **no spaces**
  (`"max_rebuy_price_usd":250`), or A's tamper SQL will not match.
- **B:** `proposals.proposal_hash`, `audit_log.rule_results_json` as a list of `{rule, pass, why, values}`, and a `policy_checks`
  row per opportunity. The voice "why" story and the stopped-this-week query read those tables directly.
