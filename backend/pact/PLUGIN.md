# Plugging into Siva's part

Run it alone: `.venv/bin/uvicorn backend.dev_app:app --port 8001` (frontend: `cd frontend && python3 -m http.server 3000`).
`backend/pact/devkit.py` supplies stand-ins ONLY for modules that don't exist yet, so each teammate's real code takes over by existing:

| Teammate | Drop in | What replaces the stand-in |
|---|---|---|
| A | `backend/db.py` with `query(sql, params)`, `execute(sql, params)`, `connection()` (`%s` placeholders, tables from 3.3) | the sqlite stand-in + seed rows. A also owns `GET /activity` (field names: see `devkit.activity`) |
| B | `backend/actor/__init__.py` exporting `send_email`, `rebuy_return`, `build_claim_packet` (3.4 signatures) | stub actors. `backend/pact/api.py::_actor` calls them on approval |
| C | `backend/main.py`: `include_router(backend.pact.api.router)` and `include_router(backend.persona.api.router)`, CORS for localhost:3000 | `backend/dev_app.py` (delete it) |

Needed env: `PACT_SIGNING_KEY`, `PERSONA_API_KEY`, `PERSONA_TEMPLATE_ID`, `PERSONA_WEBHOOK_SECRET`, `DEMO_ALLOWED_RECIPIENTS`.
Without `PERSONA_API_KEY`, `dev_app` treats every Persona check as approved (dev only; it never happens in C's main.py).
Orb: C's `frontend/components/orb/` replaces `frontend/orb.js` (keep `window.Orb.set(state)`); C's page is linked from the footer at `/under-the-hood/`.
