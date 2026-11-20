"""Amplitude track(): never raises, no-op without a key, sends clean payloads, drops personal fields."""
import json

import httpx
import pytest

from backend.analytics import events


class SyncExecutor:
    """Run submitted work inline so tests can inspect the request."""
    def submit(self, fn, *a, **k):
        fn(*a, **k)


@pytest.fixture
def amp(monkeypatch):
    monkeypatch.setenv("AMPLITUDE_API_KEY", "test-key")
    monkeypatch.setattr(events, "_executor", SyncExecutor())
    box = type("Box", (), {"requests": [], "status": 200})()

    def handler(req):
        box.requests.append(req)
        return httpx.Response(box.status, json={"code": box.status})

    monkeypatch.setattr(events, "_make_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    return box


def test_no_key_means_no_network_but_the_dashboard_counts_still_work(monkeypatch):
    monkeypatch.delenv("AMPLITUDE_API_KEY", raising=False)
    monkeypatch.setattr(events, "_make_client", lambda: (_ for _ in ()).throw(AssertionError("must not call out")))
    events.track("gate_decision", {"decision": "BLOCK", "failed_rule": "R5"})
    assert events.today_counts() == {"gate_decision": 1}
    assert events.status()["enabled"] is False and events.status()["queued"] == 0


def test_payload_shape_for_amplitude_http_api_v2(amp):
    events.track("gate_decision", {"decision": "BLOCK", "failed_rule": "R5", "action_type": "price_adjust_email", "pact_version": 2})
    (req,) = amp.requests
    assert str(req.url) == "https://api2.amplitude.com/2/httpapi"
    body = json.loads(req.content)
    ev = body["events"][0]
    assert body["api_key"] == "test-key" and ev["event_type"] == "gate_decision"
    assert ev["event_properties"] == {"decision": "BLOCK", "failed_rule": "R5", "action_type": "price_adjust_email", "pact_version": 2}
    assert len(ev["user_id"]) >= 5 and ev["insert_id"] and isinstance(ev["time"], int)


def test_insert_ids_are_unique(amp):
    events.track("money_recovered", {"amount_usd": 34})
    events.track("money_recovered", {"amount_usd": 34})
    ids = {json.loads(r.content)["events"][0]["insert_id"] for r in amp.requests}
    assert len(ids) == 2


def test_personal_data_never_leaves_the_machine(amp):
    events.track("action_executed", {"to": "judge@example.com", "email": "me@x.com", "body": "secret", "pact_text": "{}",
                                     "subject": "hi", "api_key": "k", "amount_usd": 34, "store": "mockstore"})
    props = json.loads(amp.requests[0].content)["events"][0]["event_properties"]
    assert props == {"amount_usd": 34, "store": "mockstore"}
    assert "judge@example.com" not in amp.requests[0].content.decode()
    assert "email" not in events.recent()[0]["props"]                # also kept out of the local buffer


def test_user_id_from_props_and_short_ids_are_padded(amp):
    events.track("pact_signed", {"user_id": "u_demo"})
    events.track("pact_signed", {"user_id": "ab"})
    ids = [json.loads(r.content)["events"][0]["user_id"] for r in amp.requests]
    assert ids[0] == "u_demo" and len(ids[1]) >= 5


@pytest.mark.parametrize("props", [None, {}, {"x": object()}, {"d": {1, 2}}, {"n": float("inf")}, "not a dict", 42])
def test_track_never_raises(amp, props):
    events.track("gate_decision", props)


def test_amplitude_errors_are_swallowed_and_counted(amp):
    amp.status = 500
    events.track("gate_decision", {"decision": "ALLOW"})
    s = events.status()
    assert s["failed"] == 1 and s["sent"] == 0 and "500" in s["last_error"]


def test_network_failure_is_swallowed(amp, monkeypatch):
    def boom():
        raise httpx.ConnectError("offline")
    monkeypatch.setattr(events, "_make_client", boom)
    events.track("gate_decision", {"decision": "ALLOW"})
    assert events.status()["failed"] == 1


def test_eu_region(amp, monkeypatch):
    monkeypatch.setenv("AMPLITUDE_REGION", "eu")
    events.track("pact_paused", {})
    assert str(amp.requests[0].url) == "https://api.eu.amplitude.com/2/httpapi"


def test_buffer_keeps_receipt_parse_details_for_under_the_hood():
    events.track("receipt_parsed", {"model": "arcee-x", "tokens": 800, "latency_ms": 600, "confidence": 0.97, "cost_usd": 0.0004})
    events.track("gate_decision", {"decision": "ALLOW"})
    rows = events.recent("receipt_parsed")
    assert len(rows) == 1 and rows[0]["props"]["tokens"] == 800
    assert events.recent(limit=1)[0]["event"] == "gate_decision"      # newest first


def test_buffer_is_bounded():
    for i in range(500):
        events.track("opportunity_found", {"i": i})
    assert len(events.recent(limit=1000)) == 200
