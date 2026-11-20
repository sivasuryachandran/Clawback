import json
import sys
import types

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.persona import api as persona_api, relay
from backend.pact import api as pact_api, devkit


@pytest.fixture(autouse=True)
def clean():
    relay._pending.clear(); relay._passed_at.clear()
    yield


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("RELAY_GATEWAY_URL", "http://gw.test")
    monkeypatch.setenv("PERSONA_RELAY_API_KEY", "persona_sandbox_x")


class FakeGateway:
    def __init__(self, claim="passed", ready=True):
        self.claim, self.ready, self.calls = claim, ready, []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append((url, headers, json))
        req = httpx.Request("POST", url)
        if url.endswith("/relays"):
            return httpx.Response(200, json={"relay-token": "RTOKEN123", "relay-secret": "RSECRET456", "relay-session-access-token": "sess/abc"}, request=req)
        if url.endswith("/privacy-passes"):
            return httpx.Response(200, json={"privacy-pass-token": "PPASS789"}, request=req)
        if "/redeem" in url:
            if not self.ready:
                return httpx.Response(409, json={}, request=req)
            return httpx.Response(200, json={"claim-payload": json_dumps({"claim_type": "live_human_presence", "claim_result": self.claim}), "token-consumed": True}, request=req)
        return httpx.Response(404, request=req)


def json_dumps(o):
    return json.dumps(o)


@pytest.fixture
def client():
    app = FastAPI(); app.include_router(persona_api.router)
    return TestClient(app)


def test_off_by_default_and_gate_is_a_noop(client, monkeypatch):
    monkeypatch.delenv("RELAY_GATEWAY_URL", raising=False)
    assert client.get("/persona/relay/status").json()["enabled"] is False
    relay.require_fresh("u_demo")                       # no error while Relay is off
    assert client.post("/persona/relay/start").status_code == 404


def test_full_flow_passes_and_only_session_token_reaches_the_browser(client, on, monkeypatch):
    gw = FakeGateway(); monkeypatch.setattr(httpx, "post", gw)
    r = client.post("/persona/relay/start").json()
    assert r["url"].startswith("https://relay.withpersona.com/relay?relay-session-access-token=sess%2Fabc")
    assert not any(x in r["url"] for x in ("RTOKEN123", "RSECRET456", "PPASS789"))
    assert client.post("/persona/relay/complete").json() == {"status": "passed"}
    assert client.get("/persona/relay/status").json()["fresh"] is True
    relay.require_fresh("u_demo")                       # now allowed


def test_waiting_failed_and_expiry(client, on, monkeypatch):
    gw = FakeGateway(ready=False); monkeypatch.setattr(httpx, "post", gw)
    client.post("/persona/relay/start")
    assert client.post("/persona/relay/complete").json() == {"status": "waiting"}
    gw.ready, gw.claim = True, "failed"
    assert client.post("/persona/relay/complete").json() == {"status": "failed"}
    assert client.get("/persona/relay/status").json()["fresh"] is False
    with pytest.raises(Exception):
        relay.require_fresh("u_demo")


def test_stale_pass_is_rejected(on):
    relay._passed_at["u_demo"] = 1000.0
    assert relay.is_fresh("u_demo", now=1100.0) is True
    assert relay.is_fresh("u_demo", now=1200.0) is False


def test_gateway_error_fails_closed(client, on, monkeypatch):
    def boom(*a, **k): raise httpx.ConnectError("down")
    monkeypatch.setattr(httpx, "post", boom)
    assert client.post("/persona/relay/start").status_code == 502
    assert client.get("/persona/relay/status").json()["fresh"] is False


def test_approval_is_blocked_without_a_fresh_check(on, monkeypatch):
    import backend
    db = devkit.FakeDB()
    monkeypatch.setitem(sys.modules, "backend.db", db); monkeypatch.setattr(backend, "db", db, raising=False)
    monkeypatch.setattr(persona_api, "fetch_inquiry_status", lambda _id: "approved")
    devkit.seed(db)
    app = FastAPI(); app.include_router(pact_api.router); app.include_router(persona_api.router)
    c = TestClient(app)
    aid = db.query("SELECT id FROM approvals WHERE status='pending'")[0]["id"]
    r = c.post(f"/approvals/{aid}", json={"decision": "approve"})
    assert r.status_code == 403 and "live human" in r.json()["detail"]
    assert db.query("SELECT COUNT(*) AS n FROM executed_actions WHERE action_type='rebuy_return'")[0]["n"] == 0
