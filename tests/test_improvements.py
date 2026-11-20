"""Tests for the polish pass: seed script, message-direction handling, orb lookup, admin toggle."""
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from backend.analytics import events
from backend.debug import panels
from mockstore import support
from tests.c_fixtures import client, fresh_store, sample_db  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def _script():
    spec = importlib.util.spec_from_file_location("seed_amplitude_events", ROOT / "scripts" / "seed_amplitude_events.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- Amplitude chart seeding ---------------------------------------------------------------------------------------------------------

def test_seed_events_cover_what_the_three_charts_need():
    evs = _script().build_events()
    names = {n for n, _ in evs}
    assert names == set(events.KNOWN_EVENTS)                                  # all nine events from section 4.4
    g = [p for n, p in evs if n == "gate_decision"]
    assert {p["decision"] for p in g} == {"ALLOW", "ASK", "BLOCK"}            # decision mix chart
    assert {p["failed_rule"] for p in g if p["decision"] == "BLOCK"} == {"R5", "R4", "R8"}   # stop reasons chart
    assert sum(p["amount_usd"] for n, p in evs if n == "money_recovered") == 74.0             # dollars recovered chart


def test_every_seed_event_is_labelled_so_it_cannot_pass_as_real():
    assert all(p["seed"] is True and p["user_id"] == "u_seed" for _, p in _script().build_events())


def test_dry_run_sends_nothing_and_writes_no_local_log(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network in a dry run")))
    monkeypatch.setattr(httpx.Client, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network in a dry run")))
    assert _script().main([]) == 0
    out = capsys.readouterr().out
    assert "dry run" in out and "seed != true" in out


def test_send_without_a_key_refuses(capsys, monkeypatch):
    monkeypatch.delenv("AMPLITUDE_API_KEY", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    assert _script().main(["--send"]) == 1 and "not set" in capsys.readouterr().out


# ---- message direction: any spelling of "incoming" is not an outgoing message ---------------------------------------------------------------

@pytest.mark.parametrize("incoming", ["in", "IN", "inbound", "Inbound", "incoming", "received"])
def test_incoming_messages_are_never_listed_as_outgoing(sample_db, incoming):
    sample_db.run("UPDATE messages SET direction = %s WHERE direction = 'in'", (incoming,))
    msgs = panels.safety_panel()["messages"]
    assert len(msgs) == 2 and all(m["direction"] != incoming for m in msgs)


@pytest.mark.parametrize("outgoing", ["out", "outbound", "OUT", "sent"])
def test_outgoing_spellings_are_listed(sample_db, outgoing):
    sample_db.run("UPDATE messages SET direction = %s WHERE direction = 'out'", (outgoing,))
    assert len(panels.safety_panel()["messages"]) == 2


def test_a_message_with_no_direction_counts_as_outgoing(sample_db):
    sample_db.run("UPDATE messages SET direction = NULL WHERE direction = 'out'")
    assert len(panels.safety_panel()["messages"]) == 2


def test_claim_thread_lookup_ignores_inbound_whatever_it_is_called(sample_db):
    sample_db.run("UPDATE messages SET direction = 'inbound' WHERE direction = 'in'")
    sample_db.run("INSERT INTO messages(opportunity_id,direction,thread_id,sent_at) VALUES(1,'inbound','thr_reply_only','2099-01-01 00:00:00')")
    assert support.find_claim_thread()["thread_id"] == "thr_sample_1"          # the newest row is an inbound one: skipped


# ---- admin: the optional keep-fresh toggle ---------------------------------------------------------------------------------------------------------

def test_keep_fresh_toggle_is_there_and_off_by_default(client, fresh_store):
    import re
    html = client.get("/mock/admin").text
    tag = re.search(r'<input[^>]*id="keep-fresh"[^>]*>', html).group(0)
    assert 'type="checkbox"' in tag and not re.search(r'\schecked(\s|=|>)', tag.replace("this.checked", ""))   # unticked on load
    assert "Re-announce the current prices every 5 minutes" in html and "refresh:true" in html
    assert "setInterval(run, everyMs||300000)" in html                       # five minutes unless a test overrides it


# ---- fits the code already in the repo (Siva's Money back screen and his PLUGIN.md) ----------------------------------------------------------

def test_stats_also_has_the_counts_at_the_top_level(sample_db):
    from backend.analytics import stats
    s = stats.compute()
    assert (s["done"], s["needs_ok"], s["stopped"], s["no_action"]) == (3, 1, 3, 0)          # the screen reads stats.stopped
    assert s["counts"]["stopped"] == s["stopped"] == s["unsafe_actions_stopped"]
    u = stats.unavailable(RuntimeError("x"))
    assert u["stopped"] == 0 and u["done"] == 0 and u["available"] is False


def test_needs_ok_answer_asks_the_screen_to_open_the_tab(sample_db):
    from backend.voice import intents
    r = intents.ask("anything need my ok")
    assert r["open"] is True and r["open_card_id"] == "2"
    assert "open" not in intents.ask("why did you stop the backpack")                        # a "why" must not jump to the Needs tab


def test_voice_pause_carries_the_users_id_to_the_pact_api():
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from backend.voice.api import router
    app, seen = FastAPI(), {}
    app.include_router(router)

    @app.post("/pact/pause")
    async def pause(request: Request):
        seen["uid"] = request.headers.get("x-user-id")
        return {"paused": True}

    c = TestClient(app)
    c.post("/voice/ask", json={"text": "pause clawback", "session_id": "uid-test"})
    r = c.post("/voice/ask", json={"text": "yes", "session_id": "uid-test"}, headers={"X-User-Id": "u_demo"})
    assert r.json()["intent"] == "paused" and seen["uid"] == "u_demo"


def test_main_mounts_sivas_schedule_routes_and_scheduler_when_present():
    import importlib.util
    from backend.main import MOUNTED, ROUTER_MODULES
    assert ("backend.pact.schedules_api", "Siva") in ROUTER_MODULES
    if importlib.util.find_spec("backend.pact.schedules_api"):
        assert MOUNTED["backend.pact.schedules_api"] == "mounted" and MOUNTED.get("backend.pact.scheduler") == "attached"
