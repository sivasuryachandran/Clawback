"""Regression tests for the problems found in the v4 audit. Each test pins one real bug that was reproduced first."""
import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI, APIRouter

from backend.analytics import events
from backend.debug import dbaccess, panels, sample
from backend.debug.plain import as_rules, first_failure, plain
from backend.voice import intents
from mockstore import store, support, webhook
from tests.c_fixtures import client, fresh_store, hmac_hex, sample_db  # noqa: F401


@pytest.fixture(autouse=True)
def _voice_reset():
    intents.reset()
    panels._match_cache.clear()
    support.clear()
    yield


# ---- 1. one price change must never reach the watcher twice -------------------------------------------------------

def test_a_slow_watcher_is_not_sent_the_same_change_twice(client, fresh_store):
    """Reproduced: a watcher that answered in 4 s received the change twice (read timeout, then a retry)."""
    fresh_store.behaviour["raise"] = httpx.ReadTimeout("slow")
    client.post("/mock/api/price", json={"listing_id": 1, "price": 95.99})
    assert len(fresh_store.requests) == 1
    rec = client.get("/mock/api/webhook-log").json()["deliveries"][0]
    assert rec["attempts"] == 1 and rec["ok"] is False and "not sent again" in rec["error"]


def test_connect_failure_is_retried_once_with_the_same_event_id(client, fresh_store):
    fresh_store.behaviour["raise"] = httpx.ConnectError("refused")
    client.post("/mock/api/price", json={"listing_id": 1, "price": 95.99})
    assert len(fresh_store.requests) == 2
    ids = {r.headers["X-Mockstore-Event-Id"] for r in fresh_store.requests}
    assert len(ids) == 1                                                 # A can dedupe on it
    assert json.loads(fresh_store.requests[0].content)["event_id"] == ids.pop()


def test_event_ids_differ_per_change_and_are_inside_the_signed_body(client, fresh_store):
    client.post("/mock/api/price", json={"listing_id": 1, "price": 95.99})
    client.post("/mock/api/price", json={"listing_id": 2, "price": 199})
    a, b = fresh_store.requests
    assert a.headers["X-Mockstore-Event-Id"] != b.headers["X-Mockstore-Event-Id"]
    assert a.headers["X-Mockstore-Signature"] == "sha256=" + hmac_hex("test-secret", a.content)   # event_id is signed


def test_the_watcher_is_given_a_generous_read_timeout():
    assert webhook.READ_TIMEOUT_S >= 10
    assert webhook._default_client().timeout.read == webhook.READ_TIMEOUT_S


# ---- 2. refresh keeps prices "seen within 15 minutes" (R8a) -----------------------------------------------------------

def test_refresh_prices_resends_every_current_price_flagged_as_refresh(client, fresh_store):
    client.post("/mock/api/prestage")
    fresh_store.requests.clear()
    assert client.post("/mock/api/resend").json() == {"sent": 5}
    bodies = [json.loads(r.content) for r in fresh_store.requests]
    assert [b["listing_id"] for b in bodies] == [1, 2, 3, 4, 5]
    assert all(b["refresh"] is True and b["price"] == b["old_price"] for b in bodies)
    assert {b["listing_id"]: b["price"] for b in bodies} == {1: 129.99, 2: 199.0, 3: 89.0, 4: 64.0, 5: 39.0}
    assert len({b["event_id"] for b in bodies}) == 5


def test_real_changes_are_not_flagged_as_refresh(client, fresh_store):
    client.post("/mock/api/price", json={"listing_id": 1, "price": 95.99})
    assert json.loads(fresh_store.requests[0].content)["refresh"] is False


def test_admin_page_explains_the_fifteen_minute_rule_and_has_the_button(client, fresh_store):
    html = client.get("/mock/admin").text
    assert "Refresh prices (before an approval)" in html and "R8a" in html and "15 minutes" in html
    assert "Already at that price" in html                      # the live-drop button says so when nothing changed


# ---- 3. the support box is the one public input: rate-limit it ---------------------------------------------------------

def test_support_box_is_rate_limited(client, fresh_store, monkeypatch):
    async def fake_forward(app, text, thread, transport=None):
        return {}
    monkeypatch.setattr(support, "forward", fake_forward)
    codes = [client.post("/mock/api/support", json={"text": f"message {i}"}).status_code for i in range(12)]
    assert codes[:10] == [200] * 10 and codes[10:] == [429, 429]


def test_rate_limit_window_slides():
    assert all(support.allow_message(now=100.0) for _ in range(10))
    assert support.allow_message(now=101.0) is False
    assert support.allow_message(now=100.0 + support.RATE_WINDOW_S + 1) is True


# ---- 4. the matcher costs a SIE request: never on the polled overview ----------------------------------------------------

def test_overview_never_calls_the_matcher(sample_db, monkeypatch):
    import sys, types
    calls = []
    mod = types.ModuleType("backend.watch")
    mod.match = lambda i: calls.append(i) or [{"listing_id": 1}]
    monkeypatch.setitem(sys.modules, "backend.watch", mod)
    for _ in range(3):
        o = panels.build(use_sample=False)
    assert calls == [] and o["matching"]["available"] is True and o["matching"]["candidates"] == []
    assert len(o["matching"]["items"]) == 6 and "DOT_PRODUCT" in o["matching"]["sql"]


def test_candidates_come_from_the_matcher_once_then_the_cache(sample_db, monkeypatch):
    import sys, types
    calls = []
    mod = types.ModuleType("backend.watch")
    mod.match = lambda i: calls.append(i) or [{"listing_id": 4, "similarity": 0.97, "rerank_score": 0.95, "exact": False}]
    monkeypatch.setitem(sys.modules, "backend.watch", mod)
    a = panels.matching_candidates(3, use_sample=False, now=1000.0)
    b = panels.matching_candidates(3, use_sample=False, now=1010.0)
    assert calls == [3] and b["candidates"] == a["candidates"] and "cached" in b["note"]
    panels.matching_candidates(3, use_sample=False, now=1000.0 + panels.MATCH_CACHE_TTL_S + 1)
    assert calls == [3, 3]
    panels.matching_candidates(1, use_sample=False, now=1010.0)           # another purchase is its own cache entry
    assert calls == [3, 3, 1]


def test_matching_endpoint_returns_candidates_on_demand(client):
    m = client.get("/debug/matching", params={"line_item_id": 3, "mock": 1}).json()
    assert m["available"] is True and m["candidates"][0]["variant"] == "Blue" and m["candidates"][0]["exact"] is False
    soft = client.get("/debug/matching").json()                           # no database here: soft failure, not a 500
    assert soft["available"] is False and soft["error"]


# ---- 5. rule results can arrive in more than one shape --------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    [{"rule": "R0", "pass": True}, {"rule": "R5", "pass": False, "values": {}}],
    {"results": [{"rule": "R0", "pass": True}, {"rule": "R5", "pass": False}]},
    json.dumps([{"rule": "R0", "pass": True}, {"rule": "R5", "pass": False}]),
    json.dumps({"results": [{"rule": "R0", "pass": True}, {"rule": "R5", "pass": False}]}),
])
def test_first_failure_understands_every_shape(raw):
    assert first_failure(raw)["rule"] == "R5" and len(as_rules(raw)) == 2


@pytest.mark.parametrize("raw", [None, "", "not json", 42, {}, {"results": None}, []])
def test_unparseable_rule_results_mean_no_failure_not_a_crash(raw):
    assert first_failure(raw) is None and as_rules(raw) == []


def test_decision_shaped_results_still_drive_the_trace_and_the_stopped_query(sample_db):
    sample_db.run("UPDATE audit_log SET rule_results_json = %s WHERE proposal_id = 'prop_bp'",
                  (json.dumps({"results": [{"rule": "R0", "pass": True, "why": "ok", "values": {}},
                                           {"rule": "R5", "pass": False, "why": "x", "values": {}}]}),))
    d = {x["item"]: x for x in panels.gate_trace()["decisions"]}["Trailhead Backpack (Black)"]
    assert d["failed_rule"] == "R5" and len(d["rules"]) == 2


# ---- 6. words match section 3.10 exactly ----------------------------------------------------------------------------------

def test_fan_uses_the_card_copy_for_final_sale(sample_db):
    assert intents.ask("why did you stop the fan")["answer"] == "We didn't act. Clearance items are final sale at Mock Store."


def test_r5_names_the_store_that_only_matches_its_own_prices():
    s = plain("R5", bought="A (Black)", offered="B (Blue)", own_prices_only="Target")
    assert s == "The cheaper one isn't the same item: you bought A (Black), this is B (Blue). Target only matches its own prices."
    assert "only matches" not in plain("R5", bought="A", offered="B")


def test_r5_cross_store_clause_in_the_voice_story(sample_db):
    sample_db.run("UPDATE listings SET store = 'bestbuy' WHERE id = 4")
    a = intents.ask("why did you stop the backpack")
    assert a["lines"][3]["text"].endswith("Mock Store only matches its own prices.")


# ---- 7. voice must not answer questions it wasn't asked ---------------------------------------------------------------------

@pytest.mark.parametrize("q", ["what's the weather today", "did you do anything for me", "why is the sky blue",
                               "tell me my status", "how much does a lamp cost", "what time is it today", "any good movies"])
def test_unrelated_questions_get_the_help_sentence(sample_db, q):
    r = intents.ask(q)
    assert r["intent"] == "help" and r["answer"] == "I can tell you what I did, why, or pause. Approvals happen on screen."


@pytest.mark.parametrize("q,intent", [
    ("What did you do today?", "summary"), ("how much money did you recover", "summary"), ("what did you block", "summary"),
    ("Anything need my OK?", "needs_ok"), ("is anything waiting for me", "needs_ok"), ("what needs my approval", "needs_ok"),
    ("why did you stop the backpack", "why"), ("why did you stop it", "why"), ("explain the lamp", "why"),
])
def test_the_real_questions_still_work(sample_db, q, intent):
    assert intents.ask(q)["intent"] == intent


# ---- 8. analytics counts survive a restart --------------------------------------------------------------------------------------

def test_todays_counts_and_parser_details_survive_a_restart(tmp_path):
    log = tmp_path / "analytics_events.jsonl"
    events.track("gate_decision", {"decision": "BLOCK", "failed_rule": "R5"})
    events.track("gate_decision", {"decision": "ALLOW"})
    events.track("receipt_parsed", {"model": "arcee-x", "tokens": 800, "email": "me@x.com"})
    events.reset_for_tests(reload_from_disk=True)                         # == the server restarted
    assert events.today_counts() == {"gate_decision": 2, "receipt_parsed": 1}
    assert events.recent("receipt_parsed")[0]["props"] == {"model": "arcee-x", "tokens": 800}   # personal data never hit disk
    assert "me@x.com" not in log.read_text()


def test_only_todays_events_count_and_corrupt_lines_are_skipped(tmp_path):
    log = tmp_path / "analytics_events.jsonl"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log.write_text("\n".join([
        json.dumps({"event": "gate_decision", "at": "01:00:00", "day": "2020-01-01", "props": {}}),
        "{this is torn",
        json.dumps({"event": "gate_decision", "at": "02:00:00", "day": today, "props": {}}),
        "", json.dumps({"no_event_key": True, "day": today}),
    ]))
    events.reset_for_tests(reload_from_disk=True)
    assert events.today_counts() == {"gate_decision": 1}


def test_analytics_log_can_be_switched_off(tmp_path, monkeypatch):
    monkeypatch.setenv("ANALYTICS_LOG", "off")
    events.track("pact_signed", {})
    assert not (tmp_path / "analytics_events.jsonl").exists() and events.today_counts() == {"pact_signed": 1}


def test_an_unwritable_log_never_breaks_track(tmp_path, monkeypatch):
    monkeypatch.setenv("ANALYTICS_LOG", str(tmp_path / "no" / "such" / "dir" / "events.jsonl"))
    events.track("pact_signed", {})
    assert events.today_counts() == {"pact_signed": 1}


# ---- 9. section 3.4 contract functions exist where the contract says ------------------------------------------------------------

def test_contract_functions_are_importable_by_their_contract_names(sample_db):
    from backend.analytics import track
    from backend.debug import overview
    from backend.voice import ask
    assert callable(track) and callable(overview) and callable(ask)
    track("pact_signed", {"x": 1})
    o = overview()
    assert o["gate_trace"]["available"] is True and "pact" in o
    assert ask("how much did I get back")["intent"] == "summary"


# ---- 10. two routers answering the same path must be visible -------------------------------------------------------------------------

def _router(prefix="", **routes):
    r = APIRouter(prefix=prefix)
    for method, path in routes.values():
        getattr(r, method)(path)(lambda: {})
    return r


def test_duplicate_routes_are_detected_and_name_both_owners():
    from backend.main import find_route_conflicts
    a = _router(x=("get", "/stats"))
    b = _router(x=("get", "/stats"), y=("post", "/stats"))
    assert find_route_conflicts({"backend.watch.api": a, "backend.agent.api": b}) == [
        "GET /stats: backend.watch.api and backend.agent.api"]


def test_router_prefixes_are_part_of_the_path():
    from backend.main import find_route_conflicts
    a = _router(prefix="/mock", x=("get", "/api/listings"))
    b = _router(x=("get", "/mock/api/listings"))
    c = _router(prefix="/other", x=("get", "/api/listings"))
    assert find_route_conflicts({"a": a, "b": b, "c": c}) == ["GET /mock/api/listings: a and b"]


def test_the_detector_really_sees_routes():
    """Guard against the detector silently seeing nothing (FastAPI wraps included routers in lazy objects)."""
    from backend.main import ROUTERS, _iter_routes
    seen = {(p, m) for name in ROUTERS for p, ms in _iter_routes(ROUTERS[name]) for m in ms}
    assert ("/mock/api/listings", "GET") in seen and ("/stats", "GET") in seen and ("/voice/ask", "POST") in seen
    assert ("/debug/overview", "GET") in seen and len(seen) >= 25


def test_the_real_app_has_no_duplicate_routes(client):
    h = client.get("/health").json()
    assert h["route_conflicts"] == []
    assert {"mockstore.routes", "backend.analytics.api", "backend.debug.api", "backend.voice.api"} <= set(h["routers"])


# ---- 11. a correct pause record must not show a false X because the DB returns datetimes ------------------------------------------

def test_revocation_check_accepts_the_same_instant_in_other_renderings():
    from datetime import datetime
    sig_text = "2026-11-20 07:00:00"
    row = {"pact_id": "p1", "version": 1, "revoked_at": datetime(2026, 11, 20, 7, 0, 0),
           "signature": sample.revocation_sig("p1", 1, sig_text)}
    assert panels.check_revocation(sample.verify_revocation_sample, row) is True          # signed over "YYYY-MM-DD HH:MM:SS"
    iso = dict(row, signature=sample.revocation_sig("p1", 1, "2026-11-20T07:00:00"))
    assert panels.check_revocation(sample.verify_revocation_sample, iso) is True           # signed over ISO "T" text


def test_revocation_check_still_rejects_forgeries_and_other_instants():
    from datetime import datetime
    good = sample.revocation_sig("p1", 1, "2026-11-20 07:00:00")
    base = {"pact_id": "p1", "version": 1, "revoked_at": datetime(2026, 11, 20, 7, 0, 0), "signature": good}
    assert panels.check_revocation(sample.verify_revocation_sample, dict(base, signature="forged")) is False
    assert panels.check_revocation(sample.verify_revocation_sample, dict(base, version=2)) is False
    assert panels.check_revocation(sample.verify_revocation_sample, dict(base, pact_id="other")) is False
    assert panels.check_revocation(sample.verify_revocation_sample, dict(base, revoked_at=datetime(2026, 11, 20, 7, 0, 1))) is False
    assert panels.check_revocation(lambda r: 1 / 0, base) is False                           # a crashing verifier is a failure, not an error
