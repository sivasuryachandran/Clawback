# Integration notes (Siva)

## Run everything that exists today (no database needed)

    export CLAWBACK_API_BASE=http://localhost:8001 CLAWBACK_BASE_URL=http://localhost:8001 BACKEND_URL=http://localhost:8001
    .venv/bin/uvicorn backend.dev_app:app --port 8001      # C's main.py + B's agent + Siva's Pact, with a sqlite stand-in for A
    python3 frontend/serve.py                              # UI on :3000   (POST /dev/run-all runs propose -> gate -> act for all 6 seed rows)

`backend/pact/devkit.py` installs stand-ins ONLY for modules that don't exist yet (A's `backend/db.py` and `backend/watch`).
When A lands: delete `backend/dev_app.py`, run `uvicorn backend.main:app`. Nothing else changes.

## What A still needs to provide
`backend/db.py` (`query`, `execute`, `connection`, `%s` placeholders) and the 3.3 tables **plus** `scheduled_tasks` and
`scheduled_runs` (see SCHEMA in devkit.py); `GET /activity` (field names: `devkit.activity`); `backend.watch.tick()`.

## Agreements made while integrating
- **Pact field names** follow B's/the team doc: `actions`, `stores`, `excluded_categories`, `min_net_savings_usd`,
  `ask_first_above_usd`, `max_rebuy_price_usd`, `monthly_cap_usd`, `buffer_days`, `max_claims_per_order`, `max_followups_per_thread`, `expires_at`.
- `backend/pact/__init__.py` re-exports the 3.4 contract (`active_pact`, `build_context`, `run_gate`, `run_approved`, `execute_once`, `record`, `proposal_hash`, `AlreadyRan`).
- `POST /approvals/{id}` runs B's `run_action` (backend/agent/api.py) so approving behaves exactly like the propose loop.
- Two small edits in B's files: `backend/agent/deps.py` uses the real gate only when a real `backend.db` exists (else B's stub pair),
  and `tests/test_agent.py` points B's modules at the stub `track` so its event assertions still work next to C's real one.

## Open items
- **One clock.** B's code uses `backend/agent/clock.py` (fixed 12:00 on CLAWBACK_TODAY); the gate uses real UTC time. Fine on event day; decide one.
- Scheduler calls `POST /opportunities/{id}/propose` at `CLAWBACK_API_BASE`.
- Persona: `dev_app` treats every check as approved when no `PERSONA_API_KEY` is set. `main.py` never does.
