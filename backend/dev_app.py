"""Run EVERYTHING that has been pushed, on one port, before A's database exists:

    .venv/bin/uvicorn backend.dev_app:app --port 8000

Stand-ins (backend/pact/devkit.py) are installed ONLY for modules that don't exist yet (A's db / watch),
then C's real backend/main.py mounts every router that does exist (C, B, Siva). Delete this file when A lands.
"""
import os

os.environ.setdefault("CLAWBACK_API_BASE", "http://localhost:8000")

from backend.pact import devkit  # noqa: E402

INSTALLED = devkit.install(run_pipeline=False)     # B's propose endpoint runs the pipeline

from backend.main import app  # noqa: E402,F401   (C's app: mounts mockstore, analytics, debug, voice, pact, schedules, persona, agent)
from backend.persona import api as persona_api  # noqa: E402

if not os.environ.get("PERSONA_API_KEY"):              # no Persona sandbox configured: dev only
    persona_api.fetch_inquiry_status = lambda inquiry_id: "approved"
    INSTALLED.append("persona(dev: always approved)")

if "db" in INSTALLED:                                  # A's /activity and a reset for demos
    import backend.db as _db

    @app.get("/activity", tags=["dev"])
    def activity():
        return devkit.activity(_db)

    @app.post("/dev/reset", tags=["dev"])
    def reset():
        _db.reset()
        return {"ok": True}

    @app.post("/dev/run-all", tags=["dev"])
    def run_all():
        """Run B's propose -> gate -> act for every open opportunity (what A's watcher would trigger)."""
        from fastapi.testclient import TestClient
        c = TestClient(app)
        return [c.post(f"/opportunities/{o['id']}/propose").json() for o in _db.query("SELECT id FROM opportunities ORDER BY id")]
