"""Pact lifecycle against a throwaway sqlite stand-in for backend.db (A's SingleStore module)."""
import sqlite3
import sys
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.pact import api as pact_api


@pytest.fixture
def client(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT, email TEXT, persona_inquiry_id TEXT, persona_status TEXT, verified_at TEXT, reverified_at TEXT);
    CREATE TABLE pacts (pact_id TEXT, version INT, user_id TEXT, pact_text TEXT, created_at TEXT, status TEXT, PRIMARY KEY (pact_id, version));
    CREATE TABLE pact_revocations (pact_id TEXT, version INT, revoked_at TEXT, reason TEXT, signature TEXT);
    INSERT INTO users VALUES ('u_demo','Demo','d@x.test','inq_1','approved','2026-11-19 00:00:00',NULL);
    INSERT INTO users VALUES ('u_new','New','n@x.test',NULL,'none',NULL,NULL);
    """)
    fake = types.ModuleType("backend.db")
    fake.query = lambda sql, params=(): [dict(r) for r in conn.execute(sql.replace("%s", "?"), params).fetchall()]
    def execute(sql, params=()):
        cur = conn.execute(sql.replace("%s", "?"), params); conn.commit(); return cur.lastrowid
    fake.execute = execute
    monkeypatch.setitem(sys.modules, "backend.db", fake)
    import backend
    monkeypatch.setattr(backend, "db", fake, raising=False)
    app = FastAPI(); app.include_router(pact_api.router)
    return TestClient(app)


def test_sign_pause_and_new_version(client):
    assert client.get("/pact").json() == {"signed": False}
    r = client.post("/pact", json={}).json()
    assert r["signed"] and r["version"] == 1 and not r["paused"]
    assert client.post("/pact/pause").json()["paused"] is True          # revocation row, status column untouched by the gate
    r2 = client.post("/pact", json={"min_net_savings": 20}).json()
    assert r2["version"] == 2 and not r2["paused"] and r2["current"]
    assert r2["limits"]["min_net_savings"] == 20


def test_cannot_sign_without_persona(client):
    r = client.post("/pact", json={}, headers={"x-user-id": "u_new"})
    assert r.status_code == 403


def test_tampered_pact_text_is_not_active(client):
    client.post("/pact", json={})
    import backend.db as db
    text = db.query("SELECT pact_text FROM pacts")[0]["pact_text"].replace('"monthly_cap":300', '"monthly_cap":999999')
    db.execute("UPDATE pacts SET pact_text=%s", (text,))
    assert client.get("/pact").json() == {"signed": False}
