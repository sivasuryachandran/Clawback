"""Whole pipeline on the devkit stand-ins: seed rows -> context from the database -> real gate -> approvals API."""
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.pact import api as pact_api, devkit
from backend.persona import api as persona_api


@pytest.fixture
def env(monkeypatch):
    import backend
    db = devkit.FakeDB()
    monkeypatch.setitem(sys.modules, "backend.db", db)
    monkeypatch.setattr(backend, "db", db, raising=False)
    act = devkit._Actor(db)
    monkeypatch.setitem(sys.modules, "backend.actor", act)
    monkeypatch.setattr(backend, "actor", act, raising=False)
    monkeypatch.setattr(persona_api, "fetch_inquiry_status", lambda _id: "approved")
    devkit.seed(db)
    app = FastAPI(); app.include_router(pact_api.router); app.include_router(persona_api.router)
    return db, TestClient(app)


def _latest(db):
    seen = {}
    for a in devkit.activity(db):
        seen.setdefault(a["item_title"], a)
    return seen


def test_seed_rows_match_section_3_8(env):
    db, _ = env
    s = _latest(db)
    assert s["Aurora Wireless Earbuds"]["decision"] == "ALLOW" and s["Aurora Wireless Earbuds"]["action_type"] == "price_adjust_email"
    assert (s["Nimbus Smart Lamp"]["decision"], s["Nimbus Smart Lamp"]["failed_rule"]) == ("ASK", "R12")
    assert (s["Trailhead Backpack"]["decision"], s["Trailhead Backpack"]["failed_rule"]) == ("BLOCK", "R5")
    assert (s["Breeze Desk Fan"]["decision"], s["Breeze Desk Fan"]["failed_rule"]) == ("BLOCK", "R4")
    assert (s["Wireless Mouse (cached real price)"]["decision"], s["Wireless Mouse (cached real price)"]["failed_rule"]) == ("BLOCK", "R8")
    assert s["Cotton Throw Blanket"]["action_type"] == "deadline_alert" and s["Cotton Throw Blanket"]["decision"] == "ALLOW"


def test_earbuds_email_went_to_allowlisted_inbox_only(env):
    db, _ = env
    sent = db.query("SELECT to_addr FROM messages WHERE direction='out'")
    assert [m["to_addr"] for m in sent] == ["support@mockstore.test"]


def test_approve_lamp_once_and_double_click_is_harmless(env):
    db, c = env
    aid = db.query("SELECT id FROM approvals WHERE status='pending'")[0]["id"]
    r = c.post(f"/approvals/{aid}", json={"decision": "approve"})
    assert r.status_code == 200 and r.json()["status"] == "done", r.text
    assert c.post(f"/approvals/{aid}", json={"decision": "approve"}).status_code == 409
    assert db.query("SELECT COUNT(*) AS n FROM executed_actions WHERE action_type='rebuy_return'")[0]["n"] == 1
    assert db.query("SELECT status FROM approvals WHERE id=%s", (aid,))[0]["status"] == "executed"


def test_paused_pact_stops_new_actions(env):
    db, c = env
    c.post("/pact/pause")
    aid = db.query("SELECT id FROM approvals WHERE status='pending'")[0]["id"]
    r = c.post(f"/approvals/{aid}", json={"decision": "approve"}).json()
    assert r["status"] == "stopped" and r["failed_rule"] == "R1"
    assert db.query("SELECT COUNT(*) AS n FROM executed_actions WHERE action_type='rebuy_return'")[0]["n"] == 0


def test_decline_and_stale_recheck(env, monkeypatch):
    db, c = env
    aid = db.query("SELECT id FROM approvals WHERE status='pending'")[0]["id"]
    monkeypatch.setattr(persona_api, "fetch_inquiry_status", lambda _id: "declined")
    assert c.post(f"/approvals/{aid}", json={"decision": "approve"}).status_code == 403      # failed re-check: nothing runs
    assert c.post(f"/approvals/{aid}", json={"decision": "decline"}).json()["status"] == "declined"


def test_gate_check_endpoint_blocks_tampered_proposal(env):
    db, c = env
    import json
    p = json.loads(db.query("SELECT payload_json FROM proposals WHERE id='prop_1'")[0]["payload_json"])
    ok = c.post("/gate/check", json={"opportunity_id": 1, "proposal": p}).json()
    assert ok["decision"] == "ALLOW" or ok["failed_rule"] == "R11"      # already done once by the seed run
    p["store"] = "target"
    assert c.post("/gate/check", json={"opportunity_id": 1, "proposal": p}).json()["failed_rule"] == "R0"
