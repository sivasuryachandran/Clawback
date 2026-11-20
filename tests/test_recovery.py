"""money_recovered and friends: derived from database truth, once each (nobody else emits them)."""
import json

import httpx
import pytest

from backend.analytics import events, recovery
from backend.debug import dbaccess, sample
from tests.c_fixtures import client, sample_db  # noqa: F401


def counts():
    return events.today_counts()


def recovered():
    return sorted(e["props"]["amount_usd"] for e in events.recent("money_recovered", 50))


def test_the_demo_state_reports_34_dollars_once(sample_db):
    n = recovery.reconcile(force=True)
    c = counts()
    assert recovered() == [34.0]                                     # the earbuds claim; the follow-up and the alert are not money
    assert c["action_executed"] == 3 and c["money_recovered"] == 1   # earbuds claim, its proof follow-up, the Target alert
    assert c.get("claim_packet_ready", 0) == 0 and c["opportunity_found"] == 6
    assert n == sum(c.values())


def test_a_second_poll_reports_nothing_again(sample_db):
    first = recovery.reconcile(force=True)
    assert first > 0 and recovery.reconcile(force=True) == 0
    assert counts()["money_recovered"] == 1


def test_the_lamp_rebuy_adds_exactly_forty_when_it_runs(sample_db):
    recovery.reconcile(force=True)
    h = sample.build.__globals__["_h"]("lamp")
    sample_db.run("INSERT INTO executed_actions VALUES(%s,2,'rebuy_return',199,'done','ok','2026-11-20 12:00:00')", (h,))
    assert recovery.reconcile(force=True) == 2                       # action_executed + money_recovered
    assert recovered() == [34.0, 40.0] and sum(recovered()) == 74.0   # the same $74 /stats reports


def test_failed_and_running_actions_are_not_reported(sample_db):
    recovery.reconcile(force=True)
    for i, st in enumerate(["failed", "running"]):
        sample_db.run("INSERT INTO executed_actions VALUES(%s,1,'price_adjust_email',34,%s,'x','2026-11-20 12:00:00')", (f"sha256:x{i}", st))
    assert recovery.reconcile(force=True) == 0 and recovered() == [34.0]


def test_a_claim_packet_is_ready_but_not_recovered_money(sample_db):
    recovery.reconcile(force=True)
    h = sample.build.__globals__["_h"]("bb")
    sample_db.run("INSERT INTO executed_actions VALUES(%s,5,'claim_packet',6,'done','ok','2026-11-20 12:00:00')", (h,))
    sample_db.run("INSERT INTO claim_packets(opportunity_id,proposal_hash,store,created_at) VALUES(5,%s,'bestbuy','2026-11-20 12:00:00')", (h,))
    recovery.reconcile(force=True)
    c = counts()
    assert c["claim_packet_ready"] == 1 and c["money_recovered"] == 1 and recovered() == [34.0]


def test_a_restart_does_not_report_old_things_again(sample_db, tmp_path):
    recovery.reconcile(force=True)
    before = dict(counts())
    events.reset_for_tests(reload_from_disk=True)                     # the server restarted: memory gone, log file remains
    recovery.reset_for_tests()
    assert recovery.reconcile(force=True) == 0
    assert counts() == before


def test_the_amplitude_insert_id_is_stable_so_amplitude_itself_drops_repeats(sample_db, monkeypatch):
    sent = []
    monkeypatch.setenv("AMPLITUDE_API_KEY", "k")

    class Inline:
        def submit(self, fn, *a, **k):
            fn(*a, **k)

    monkeypatch.setattr(events, "_executor", Inline())
    monkeypatch.setattr(events, "_make_client", lambda: httpx.Client(transport=httpx.MockTransport(
        lambda r: sent.append(json.loads(r.content)["events"][0]) or httpx.Response(200, json={}))))
    recovery.reconcile(force=True)
    money = [e for e in sent if e["event_type"] == "money_recovered"]
    assert len(money) == 1 and money[0]["insert_id"].startswith("clawback:money:sha256:")
    assert money[0]["event_properties"]["amount_usd"] == 34.0 and money[0]["event_properties"]["store"] == "mockstore"
    first_ids = {e["insert_id"] for e in sent}
    import os
    events.reset_for_tests(); recovery.reset_for_tests(); sent.clear()
    os.unlink(os.environ["ANALYTICS_LOG"])                             # the log file is lost too
    recovery.reconcile(force=True)
    assert {e["insert_id"] for e in sent} == first_ids                 # same ids after a restart with no log: Amplitude dedupes


def test_derived_events_are_marked_so_they_can_be_told_apart(sample_db):
    recovery.reconcile(force=True)
    p = events.recent("money_recovered", 1)[0]["props"]
    assert p["derived"] is True and p["dedupe_key"].startswith("money:") and p["action_type"] == "price_adjust_email"


def test_it_is_throttled_and_never_raises(sample_db, monkeypatch):
    assert recovery.reconcile() > 0
    assert recovery.reconcile() == 0                                   # second call inside the throttle window: skipped
    recovery.reset_for_tests()
    monkeypatch.setattr(dbaccess, "_override", lambda sql, params=(): (_ for _ in ()).throw(RuntimeError("db down")))
    assert recovery.reconcile(force=True) == 0                         # a dead database is swallowed, not raised


def test_stats_endpoint_triggers_it_and_still_answers_instantly(client, sample_db):
    r = client.get("/stats")
    assert r.status_code == 200 and r.json()["dollars_recovered"] == 34.0
    assert counts().get("money_recovered") == 1


def test_stats_numbers_and_the_dashboard_number_agree_after_an_approval(client, sample_db):
    client.get("/stats")
    h = sample.build.__globals__["_h"]("lamp")
    sample_db.run("INSERT INTO audit_log(ts,proposal_id,proposal_hash,rule_results_json,decision) VALUES('2026-11-20 12:00:00','prop_lamp',%s,'[]','ALLOW')", (h,))
    sample_db.run("INSERT INTO executed_actions VALUES(%s,2,'rebuy_return',199,'done','ok','2026-11-20 12:00:00')", (h,))
    recovery.reset_for_tests()                                         # let the throttle window pass
    stats = client.get("/stats").json()
    assert stats["dollars_recovered"] == sum(recovered()) == 74.0


# ---- a database reset and a re-run must count again (rehearsals must not swallow the real demo) ---------------------------------------------------

def _rerun_after_reset(db):
    """What /dev/reset + the pipeline does: same deterministic proposal hashes, ids restarting, a NEW created_at."""
    db.run("DELETE FROM executed_actions")
    for h, line, action, amount in [("ear", 1, "price_adjust_email", 34.0), ("ear2", 1, "claim_followup", 0.0), ("tgt", 6, "deadline_alert", 0.0)]:
        db.run("INSERT INTO executed_actions VALUES(%s,%s,%s,%s,'done','ok',%s)", (sample.build.__globals__["_h"](h), line, action, amount, "2026-11-20 18:30:00"))


def test_a_rerun_after_a_database_reset_is_reported_again(sample_db):
    recovery.reconcile(force=True)
    assert recovered() == [34.0]
    _rerun_after_reset(sample_db)                                     # same hashes as before, later created_at
    recovery.reconcile(force=True)
    assert recovered() == [34.0, 34.0]                                # the second run's $34 counts, not swallowed
    recovery.reconcile(force=True)
    assert recovered() == [34.0, 34.0]                                # ...and still only once


def test_the_amplitude_insert_id_differs_between_a_rehearsal_and_the_real_run(sample_db, monkeypatch):
    sent = []
    monkeypatch.setenv("AMPLITUDE_API_KEY", "k")

    class Inline:
        def submit(self, fn, *a, **k):
            fn(*a, **k)

    monkeypatch.setattr(events, "_executor", Inline())
    monkeypatch.setattr(events, "_make_client", lambda: httpx.Client(transport=httpx.MockTransport(
        lambda r: sent.append(json.loads(r.content)["events"][0]) or httpx.Response(200, json={}))))
    recovery.reconcile(force=True)
    _rerun_after_reset(sample_db)
    recovery.reconcile(force=True)
    ids = [e["insert_id"] for e in sent if e["event_type"] == "money_recovered"]
    assert len(ids) == 2 and len(set(ids)) == 2                       # Amplitude would otherwise drop the real run's event


def test_the_same_run_after_a_restart_still_does_not_double_count(sample_db):
    recovery.reconcile(force=True)
    _rerun_after_reset(sample_db)
    recovery.reconcile(force=True)
    before = dict(counts())
    events.reset_for_tests(reload_from_disk=True); recovery.reset_for_tests()
    assert recovery.reconcile(force=True) == 0 and counts() == before
