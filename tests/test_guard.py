"""Localhost-only controls: admin and Under the hood answer the demo laptop and refuse a tunnel."""
import pytest

from backend.debug import guard
from tests.c_fixtures import client, fresh_store, sample_db  # noqa: F401

PRIVILEGED = ["/debug/overview", "/debug/query/stopped_this_week", "/debug/matching", "/mock/admin",
              "/mock/api/price", "/mock/api/drop", "/mock/api/preset", "/mock/api/prestage", "/mock/api/resend",
              "/mock/api/reset", "/mock/api/policy-window", "/mock/api/webhook-log", "/mock/api/support-log"]
PUBLIC = ["/health", "/stats", "/mock", "/mock/", "/mock/p/MS-1001", "/mock/policy", "/mock/support", "/mock/api/listings",
          "/mock/api/listings/1", "/mock/api/orders", "/mock/api/orders/MS-55021", "/mock/api/rebuy_return", "/mock/api/support",
          "/mock/api/policy", "/voice/ask", "/watch/price-changed", "/replies/inbound", "/pact", "/approvals/1", "/activity", "/opportunities/1"]


@pytest.mark.parametrize("path", PRIVILEGED)
def test_these_are_privileged(path):
    assert guard.is_privileged(path)


@pytest.mark.parametrize("path", PUBLIC)
def test_everything_else_stays_open_including_other_teammates_endpoints(path):
    assert not guard.is_privileged(path)


@pytest.mark.parametrize("headers,client,remote", [
    ({}, None, False),                                              # no information: a test client / unix socket
    ({}, "testclient", False),
    ({}, "127.0.0.1", False), ({}, "::1", False), ({}, "::ffff:127.0.0.1", False),
    ({"host": "localhost:8000"}, "127.0.0.1", False), ({"host": "127.0.0.1:8000"}, "127.0.0.1", False), ({"host": "[::1]:8000"}, "::1", False),
    # through Next.js on the laptop: forwarded for loopback, forwarded host localhost
    ({"x-forwarded-for": "::1", "x-forwarded-host": "localhost:3000", "host": "localhost:8000"}, "127.0.0.1", False),
    ({"x-forwarded-for": "127.0.0.1, ::1", "x-forwarded-host": "127.0.0.1:3000"}, "127.0.0.1", False),
    ({"forwarded": 'for="[::1]:51234";host=localhost:3000'}, "127.0.0.1", False),
    # anything that came through a tunnel or from another device
    ({}, "192.168.1.20", True), ({}, "203.0.113.9", True),
    ({"x-forwarded-for": "203.0.113.9"}, "127.0.0.1", True),
    ({"x-forwarded-for": "203.0.113.9, 127.0.0.1"}, "127.0.0.1", True),
    ({"x-forwarded-for": "127.0.0.1, 203.0.113.9"}, "127.0.0.1", True),     # a spoofed 127.0.0.1 does not hide the real hop
    ({"forwarded": "for=203.0.113.9"}, "127.0.0.1", True),
    ({"cf-connecting-ip": "203.0.113.9"}, "127.0.0.1", True),
    ({"x-real-ip": "203.0.113.9"}, "127.0.0.1", True), ({"true-client-ip": "2001:db8::1"}, "127.0.0.1", True),
    ({"host": "abc-def.trycloudflare.com"}, "127.0.0.1", True),
    ({"host": "localhost:8000", "x-forwarded-host": "abc-def.trycloudflare.com"}, "127.0.0.1", True),
    ({"host": "random.ngrok-free.app"}, None, True),
    ({"host": "192.168.1.20:8000"}, "127.0.0.1", True),
])
def test_remote_detection(headers, client, remote):
    assert guard.request_is_remote(headers, client) is remote


def test_a_remote_request_to_a_control_is_refused_with_a_useful_message(client, sample_db):
    r = client.get("/debug/overview", headers={"x-forwarded-for": "203.0.113.9", "x-forwarded-host": "abc.trycloudflare.com"})
    assert r.status_code == 403 and "demo laptop" in r.json()["detail"] and "ALLOW_REMOTE_ADMIN" in r.json()["detail"]
    for path, method in [("/mock/admin", "get"), ("/mock/api/reset", "post"), ("/mock/api/price", "post"), ("/debug/query/price_history", "get"),
                         ("/mock/api/webhook-log", "get")]:
        resp = getattr(client, method)(path, headers={"cf-connecting-ip": "203.0.113.9"})
        assert resp.status_code == 403, path


def test_a_refused_reset_changes_nothing(client, fresh_store):
    client.post("/mock/api/price", json={"listing_id": 1, "price": 95.99})
    r = client.post("/mock/api/reset", headers={"x-forwarded-for": "203.0.113.9"})
    assert r.status_code == 403
    assert client.get("/mock/api/listings/1").json()["price"] == 95.99                       # the attacker could not reset it
    r = client.post("/mock/api/price", json={"listing_id": 1, "price": 1.0}, headers={"x-forwarded-for": "203.0.113.9"})
    assert r.status_code == 403 and client.get("/mock/api/listings/1").json()["price"] == 95.99


def test_the_demo_laptop_is_never_locked_out(client, fresh_store, sample_db):
    local = {"x-forwarded-for": "::1", "x-forwarded-host": "localhost:3000", "host": "localhost:8000"}
    assert client.get("/debug/overview?mock=1", headers=local).status_code == 200
    assert client.get("/mock/admin", headers=local).status_code == 200
    assert client.post("/mock/api/price", json={"listing_id": 1, "price": 95.99}, headers=local).status_code == 200
    assert client.get("/mock/admin").status_code == 200                                      # plain, no headers at all


def test_public_endpoints_still_work_through_a_tunnel(client, fresh_store):
    tunnel = {"x-forwarded-for": "203.0.113.9", "x-forwarded-host": "abc.trycloudflare.com", "host": "abc.trycloudflare.com"}
    for path in ["/mock", "/mock/p/MS-1001", "/mock/policy", "/mock/support", "/mock/api/listings", "/mock/api/orders/MS-55021", "/health", "/stats"]:
        assert client.get(path, headers=tunnel).status_code == 200, path
    assert client.post("/voice/ask", json={"text": "hi"}, headers=tunnel).status_code == 200
    r = client.post("/mock/api/rebuy_return", json={"listing_id": 2, "original_order_no": "MS-55021"}, headers=tunnel)
    assert r.status_code == 200                                                                # B's actor path is untouched


def test_allow_remote_admin_switches_the_guard_off(client, fresh_store, monkeypatch):
    remote = {"x-forwarded-for": "203.0.113.9"}
    assert client.get("/mock/admin", headers=remote).status_code == 403
    monkeypatch.setenv("ALLOW_REMOTE_ADMIN", "true")
    assert client.get("/mock/admin", headers=remote).status_code == 200
    monkeypatch.setenv("ALLOW_REMOTE_ADMIN", "false")
    assert client.get("/mock/admin", headers=remote).status_code == 403
