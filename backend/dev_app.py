"""Run Siva's part on its own:  .venv/bin/uvicorn backend.dev_app:app --port 8001

Stand-ins (backend/pact/devkit.py) are used ONLY for modules that don't exist yet, so as
A's db.py and B's actor/ land they take over automatically. C's real backend/main.py
replaces this file: it only needs the two include_router lines below.
"""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.pact import devkit

INSTALLED = devkit.install()            # e.g. ['db', 'actor'] while A and B haven't landed

from backend.pact.api import router as pact_router          # noqa: E402
from backend.persona import api as persona_api              # noqa: E402

if not os.environ.get("PERSONA_API_KEY"):                    # no Persona sandbox configured: dev only
    persona_api.fetch_inquiry_status = lambda inquiry_id: "approved"
    INSTALLED.append("persona(dev: always approved)")

app = FastAPI(title="Clawback (Siva dev app)")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"], allow_methods=["*"], allow_headers=["*"])
app.include_router(pact_router)
from backend.pact.schedules_api import router as schedules_router   # noqa: E402
from backend.pact import scheduler                                    # noqa: E402
app.include_router(schedules_router)
scheduler.attach(app)
app.include_router(persona_api.router)


@app.get("/health")
def health():
    return {"ok": True, "stand_ins": INSTALLED}


if "db" in INSTALLED:                                       # A's /activity and a reset for demos
    import backend.db as _db

    @app.get("/activity")
    def activity():
        return devkit.activity(_db)

    @app.post("/dev/reset")
    def reset():
        _db.reset()
        return {"ok": True}
