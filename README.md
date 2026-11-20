# Clawback

**Clawback gets your money back after you buy, and it can only act inside a Pact you signed.**
The LLM proposes; a deterministic gate decides.

> Black Friday is November 27, one week after this hackathon. That's the week prices drop right after people buy,
> and almost nobody claims the difference.

Built for the Personal Agents Hackathon (tokens&, Fri Nov 20, 2026).

## The problem

You buy something, the price drops a few days later, and the store would have refunded the difference if you had asked
inside its window. Nobody checks, and nobody reads the policy. An agent could do it, but an agent that can spend your money
and email stores on its own is one prompt-injection away from a bad day.

## How it works

1. **Read receipts.** An Airbyte sync moves the order emails into a destination; our code reads them from there and stores them in SingleStore. (Read only: Airbyte does not send anything.)
2. **Understand them.** A small Arcee model extracts store, item, SKU/UPC, color/size, price and dates.
3. **Find the current listing.** Superlinked SIE embeds titles, SingleStore vector search finds candidates, SIE reranks them.
   The matcher only *suggests*; it never decides two items are the same.
4. **Notice a change.** The Mock Store sends a signed webhook on every price change. Any drop, or a return window closing
   within 7 days, creates an opportunity.
5. **Check the store's policy.** Senso answers a narrow question with a quoted sentence and its source.
6. **Propose.** The LLM picks one action: email the store (mock store only), prepare a claim packet (Target, Best Buy), buy
   again and return, remind the user, or do nothing. Every fact in the proposal is copied from the database.
7. **The Pact decides.** Plain code checks the proposal against the user's signed Pact and the store policy: Done, Needs your
   OK, or Stopped.
8. **Act once.** An allowed action runs exactly once; emails go only to allowed addresses.
9. **Explain.** Every decision is saved with the receipt, the price found, the quoted policy and the rule that decided it,
   and shown under **Why?**.

## The Pact

A signed permission slip the user gives Clawback after verifying with Persona. Pausing writes a signed revocation record;
editing creates a new signed version; the gate never trusts a plain status column.

Sample Pact: price adjustments, claim packets, buy again and return, deadline reminders · Mock Store, Target, Best Buy · skip
clearance and gift cards · only if I save at least $10 · ask me first above $150 · never more than $250 at once or $300 a month ·
2-day buffer before deadlines · one claim per order, up to 3 follow-ups · valid 90 days.

### Gate rules (checked in this order; the first failure returns BLOCK)

| Rule | Applies to | Check |
|---|---|---|
| R0 | all | Proposal well formed; `opportunity_id`, `line_item_id`, `store`, `listing_id`, `rebuy.listing_id`, `return.order_no` equal the opportunity's rows |
| R1 | all | Pact hash + signature verify; no valid revocation; latest version; not expired; Persona approved and verified within 30 days |
| R2 | all | Action allowed by the Pact (`claim_followup` allowed when `price_adjust_email` or `claim_packet` is) |
| R3 | all | Item's store (and listing's store) allowed, from the database |
| R4 | all | Category not excluded and not final sale per policy |
| R6 | all | Senso citation for this store and opportunity, fetched within 24 hours |
| R6b | claims, follow-ups | Store offers price adjustments |
| R6c | claims | `price_adjust_email` only if `claim_channel == "email"`; `claim_packet` only if it isn't |
| R7 | all | Adjust window (claims, follow-ups) or return window (rebuys, alerts), counted from purchase or delivery per the policy; buffer days |
| R5 | claims, rebuy | Same product: same store → SKU + variant; other store → UPC or model number + variant. Claims also need the same store unless the store matches competitors |
| R8 | claims, rebuy | Net savings ≥ the minimum (restocking + return shipping for rebuys only) |
| R8a | claims, rebuy | Price seen within 15 minutes (after R8 on purpose) |
| R9 | rebuy | Price ≤ per-purchase cap; this month's done + running + approved-not-run rebuys + this ≤ monthly cap |
| R10 | emails | Demo mode: exact address on the allow-list. Live: store support domain, or the user's own email for claim packets |
| R11 | claims, rebuy | No earlier done/running claim on this order |
| R13 | follow-ups | An earlier claim exists; same recipient and thread; fewer than 3 follow-ups |
| R12 | rebuy / low confidence | ASK if the rebuy price is above the ask-first limit or parse confidence < 0.8 (skipped once approved) |
| A1 | approvals | Approved, not expired, the stored proposal hash equals the one being run, approver is the Pact owner, Persona re-check within 15 minutes; then the full gate re-runs on fresh data |

### Threat model

| Threat | What stops it |
|---|---|
| Prompt injection in a receipt or a support reply | Recipients and facts come from the database and the Pact, never from model text; follow-ups to any other address stop on R10 / R13 |
| A "cheaper" listing that is not the same item | R5 (SKU + variant, or UPC/model number across stores) |
| Spending beyond the user's limits | R9 counts running and approved-but-not-run rebuys; R12 asks above the ask-first limit |
| Double click / retry | `executed_actions` is keyed by proposal hash; a failed action can be retried once |
| Editing the Pact in the database | R1 recomputes hash + HMAC on every decision; Under the hood shows ✓/✗ live |
| Emailing a real retailer | Three layers (agent, gate R10, sender) refuse unknown recipients; Target and Best Buy get a claim packet emailed to the user |
| A stale approval | A1 binds to the exact proposal hash, expiry, owner and a fresh Persona re-check |
| The sphere's voice approving money | `/voice/ask` is a fixed list of intents; it explains and pauses, and refuses approve / buy / send / edit limits / un-pause |

## Where each sponsor shows

We focus on five sponsors. A row is only a claim once that sponsor has run live in the recorded demo (see "Real vs simulated").

| Sponsor | Where a judge sees it |
|---|---|
| Amplitude | Dashboard: dollars recovered, decision mix (Done / Needs your OK / Stopped), stop reasons by rule |
| SingleStore | All data; Under the hood → Live queries ("what the Pact stopped this week") |
| Persona | Verify before signing; fresh re-check when approving money |
| Arcee | Receipt parsing; cost per receipt on Under the hood → Ingest |
| Senso | The store's policy quoted in every Why? panel; Under the hood → Policy |

## Run it

Python 3.11 or 3.12+. No Node and no build step: the UI is static files. (`sie-sdk` needs 3.12+, so `requirements.txt` installs it only there; on 3.11 Teammate A uses SIE's HTTP embeddings endpoint.)

```bash
cp .env.example .env            # fill in the keys; .env is gitignored
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Everything that exists today, on one port (until A's database lands; then use backend.main:app).
# Use any free port and keep the three URLs equal to it (8000 was taken on one laptop, so 8001 here).
export CLAWBACK_API_BASE=http://localhost:8001 CLAWBACK_BASE_URL=http://localhost:8001 BACKEND_URL=http://localhost:8001
uvicorn backend.dev_app:app --port 8001
(cd frontend && python3 serve.py)   # the app on http://localhost:3000   (second terminal; run it from INSIDE frontend/, it serves the folder it starts in)
```

- App: http://localhost:3000 · **Under the hood: http://localhost:8001/under-the-hood/** (served by the API; no second server)
- Mock Store: http://localhost:8001/mock · its control panel: http://localhost:8001/mock/admin
- Before a demo: `python scripts/preflight.py`. Demo script, stunts and Amplitude setup: [docs/RUNBOOK.md](docs/RUNBOOK.md).
- What each teammate codes against (ids, order numbers, webhook format, endpoints, decisions): [docs/C_CONTRACTS.md](docs/C_CONTRACTS.md).

### Layout

| Folder | What |
|---|---|
| `backend/pact/`, `backend/persona/`, `frontend/` | Siva: the Pact, the gate, Persona, the static UI with the sphere and voice |
| `backend/agent/`, `backend/actor/` | B: policy check, proposals, replies, claim email / packet / rebuy actors |
| `backend/main.py`, `mockstore/` | C: the app shell that mounts every router; the simulated retailer |
| `backend/analytics/`, `backend/debug/`, `backend/voice/` | C: analytics and `/stats`, Under the hood data, `POST /voice/ask` |
| `frontend/app/under-the-hood/` | C: the judges' page (static) |

### Tests

```bash
pytest -q
```

The whole suite: Pact, agent, watch, mock store, analytics, Under the hood, voice, and end-to-end runs of the real app. `573 passed, 1 skipped` on Python 3.11 and 3.12. `[Refresh this line at 4:00.]`

## Real vs simulated

| | |
|---|---|
| **Real** | The deterministic gate and its rules; Pact signing, hashing and revocation (Under the hood re-verifies the signature live); Amplitude events (`[confirm after the demo run that the dashboard shows them]`) |
| **Real only once it has run live** | SingleStore, Persona (sandbox), Arcee and Senso. `[Tick each one after it ran live in the recording; delete any that did not.]` |
| **Simulated** | The Mock Store's prices and orders; the store's support rep (a teammate inbox); cached real-store prices (Target, Best Buy); dummy receipts (`seed/receipts/05` and `06` are labelled dummy purchases); offline vector and parser fallbacks; the in-memory stand-in database when SingleStore is not connected |
| **Airbyte** | Read path only. Counts only if a real Airbyte sync ran and `python -m backend.ingest.airbyte_core` read its output into the database before the freeze. The seed receipts are dummy purchases, so the Gmail link itself is simulated. Nothing is sent through Airbyte. `[Keep only if it ran; otherwise delete this row.]` |
| **Not claimed** | Superlinked, Firedrill and Guild are not part of this submission |

The signing helpers (`backend/pact/sign.py`) and the sphere visuals (`frontend/orb.js`) were adapted from Siva's earlier Pact project.
Real stores never receive email: they get a claim packet the user submits through the store's chat.

## Honest scope

Matches each receipt to the current listing; cross-store identity by UPC or model number. Amazon is out: it generally gives no
price adjustments. Voice can explain and pause but can never approve, buy, send, change limits or un-pause.

## Team

Siva Surya Chandran · Sharan Joseph · Villyalsriprajeet · `[Teammate A]`  (`[emails]`)

## Disclosure

- **Prior ideas:** Clawback reuses the *Pact* idea from Siva's earlier projects **PACT** and **Second Look** (a gate that checks
  a final order against a signed intent), rebuilt here for refunds and returns. The signing helpers (`backend/pact/sign.py`) and the
  sphere (`frontend/orb.js`) were adapted from Siva's earlier Pact project.
- **Before the event:** planning documents, and Teammate B's rehearsal code (see `REHEARSAL_NOTES.md`). Teammate C's platform (app shell, mock
  store, analytics, Under the hood, voice backend) was first drafted on Nov 19 and reworked and integrated with the team's code on Nov 20.
  `[Confirm with an organizer that this is acceptable, and edit this line to match.]`
