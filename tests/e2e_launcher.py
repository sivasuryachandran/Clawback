"""Test-only launcher for the end-to-end tests: every REAL router (C's app, A's watch + ingest, B's agent, Siva's Pact)
on one app, over Siva's SEEDED stand-in database (the demo's six rows run through his real gate).

Why not backend.dev_app? Once A's real backend/db.py exists, dev_app installs no stand-in database (it starts empty), so
the demo rows would be missing. This forces the stand-in as `backend.db` first, so the tests stay deterministic with no
SingleStore and no files. Run as:  uvicorn tests.e2e_launcher:app
"""
import os
import sqlite3
import sys

import backend
from backend.pact import devkit

_db = devkit.FakeDB()
_db.run_pipeline = False                    # B's POST /opportunities/{id}/propose runs propose -> gate -> act
_db.IntegrityError = sqlite3.IntegrityError  # A's code imports this name from backend.db
sys.modules["backend.db"] = _db
backend.db = _db
devkit.seed(_db, False)
devkit.install(db_module=False, run_pipeline=False)     # actor / watch stand-ins only if those modules are missing

from backend.main import app  # noqa: E402,F401
from backend.persona import api as persona_api  # noqa: E402

persona_api.fetch_inquiry_status = lambda inquiry_id: "approved"      # no Persona sandbox in tests


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
