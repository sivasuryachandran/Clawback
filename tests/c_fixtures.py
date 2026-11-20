"""Shared fixtures for Teammate C's tests. Import the ones you need into a test module:
    from tests.c_fixtures import client, sample_db   # noqa: F401
"""
import hashlib
import hmac
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.analytics import events
from backend.debug import dbaccess, sample
from mockstore import store, support, webhook

logging.disable(logging.CRITICAL)


@pytest.fixture
def client():
    from backend.main import app
    return TestClient(app)


@pytest.fixture
def sample_db():
    """The demo state in an in-memory SQLite, wired in as the database for the test."""
    db = sample.build()
    dbaccess._override = db.query
    yield db
    dbaccess._override = None


@pytest.fixture
def fresh_store(monkeypatch):
    """Reset the mock store; make webhook delivery synchronous and capture every request."""
    monkeypatch.setenv("MOCK_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("BACKEND_URL", "http://backend.test")
    monkeypatch.setenv("DEMO_TODAY", "2026-11-20")
    store._state.clear()
    store._fail_hook["after_buy"] = False
    webhook.clear()
    support.clear()
    monkeypatch.setattr(webhook, "SYNC", True)
    sent: list[httpx.Request] = []
    behaviour = {"status": 200, "raise": None}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if behaviour["raise"]:
            raise behaviour["raise"]
        return httpx.Response(behaviour["status"], json={"ok": True})

    monkeypatch.setattr(webhook, "_make_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(webhook.time, "sleep", lambda s: None)    # no real waiting on retries
    sent_box = type("Box", (), {})()
    sent_box.requests, sent_box.behaviour = sent, behaviour
    yield sent_box
    store._state.clear()


def hmac_hex(secret: str, body: bytes) -> str:
    """Independent of mockstore.webhook.sign, so a bug there can't hide itself."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
