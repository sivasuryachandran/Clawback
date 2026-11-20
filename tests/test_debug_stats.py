"""/stats and the Under the hood backend: numbers, panel isolation, read-only guarantee, tamper check."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.analytics import stats
from backend.debug import dbaccess, queries, sample
from backend.debug import panels as overview
from tests.c_fixtures import client, sample_db  # noqa: F401

NOW = datetime.now(timezone.utc)
FMT = sample.FMT


# ---- /stats -----------------------------------------------------------------------------------------------

def test_stats_match_the_stage_counter(sample_db):
    s = stats.compute()
    assert s["dollars_recovered"] == 34.0                       # "$34 back"
    assert s["unsafe_actions_stopped"] == 3                     # "3 unsafe actions stopped"
    assert s["counts"] == {"done": 3, "needs_your_ok": 1, "stopped": 3, "no_action": 0}
    assert s["recovered_breakdown"] == {"asked_store": 34.0, "rebought": 0.0}
    assert s["by_store"] == {"mockstore": 34.0}


def test_followups_and_alerts_are_done_but_not_money(sample_db):
    s = stats.compute()
    assert s["counts"]["done"] == 3 and s["dollars_recovered"] == 34.0


def test_approving_the_lamp_moves_it_from_needs_ok_to_done_and_adds_40(sample_db):
    h = sample.build.__globals__["_h"]("lamp")
    sample_db.run("INSERT INTO audit_log(ts,pact_id,pact_version,proposal_id,proposal_hash,rule_results_json,decision) VALUES(%s,%s,2,%s,%s,%s,'ALLOW')",
                  (NOW.strftime(FMT), "pact_demo", "prop_lamp", h, "[]"))
    sample_db.run("INSERT INTO executed_actions VALUES(%s,2,'rebuy_return',199,'done','ok',%s)", (h, NOW.strftime(FMT)))
    sample_db.run("UPDATE approvals SET status='executed'")
    s = stats.compute()
    assert s["dollars_recovered"] == 74.0 and s["recovered_breakdown"]["rebought"] == 40.0
    assert s["counts"]["done"] == 4 and s["counts"]["needs_your_ok"] == 0     # ASK then ALLOW counts once


def test_expired_and_decided_approvals_are_not_waiting(sample_db):
    sample_db.run("UPDATE approvals SET expires_at=%s", ((NOW - timedelta(hours=1)).strftime(FMT),))
    assert stats.compute()["counts"]["needs_your_ok"] == 0
    sample_db.run("UPDATE approvals SET expires_at=%s, status='declined'", ((NOW + timedelta(hours=5)).strftime(FMT),))
    assert stats.compute()["counts"]["needs_your_ok"] == 0


def test_approved_then_blocked_after_a_price_bump_counts_as_stopped(sample_db):
    h = sample.build.__globals__["_h"]("lamp")
    sample_db.run("INSERT INTO audit_log(ts,pact_id,pact_version,proposal_id,proposal_hash,rule_results_json,decision) VALUES(%s,%s,2,%s,%s,%s,'BLOCK')",
                  (NOW.strftime(FMT), "pact_demo", "prop_lamp", h, "[]"))
    sample_db.run("UPDATE approvals SET status='approved'")
    s = stats.compute()
    assert s["counts"]["stopped"] == 4 and s["counts"]["needs_your_ok"] == 0 and s["dollars_recovered"] == 34.0


def test_same_proposal_hash_is_never_counted_twice(sample_db):
    h = sample.build.__globals__["_h"]("ear")
    sample_db.run("INSERT INTO proposals(id,opportunity_id,action_type,proposal_hash) VALUES('prop_ear_again',1,'price_adjust_email',%s)", (h,))
    assert stats.compute()["dollars_recovered"] == 34.0


def test_claim_packets_and_failed_actions_are_not_recovered_money(sample_db):
    h = sample.build.__globals__["_h"]("bb")
    sample_db.run("INSERT INTO executed_actions VALUES(%s,5,'claim_packet',6,'done','ok',%s)", (h, NOW.strftime(FMT)))
    sample_db.run("INSERT INTO executed_actions VALUES('sha256:failed',1,'price_adjust_email',34,'failed','x',%s)", (NOW.strftime(FMT),))
    sample_db.run("INSERT INTO executed_actions VALUES('sha256:running',1,'price_adjust_email',34,'running','',%s)", (NOW.strftime(FMT),))
    assert stats.compute()["dollars_recovered"] == 34.0


def test_no_action_is_counted(sample_db):
    sample_db.run("INSERT INTO audit_log(ts,proposal_id,proposal_hash,rule_results_json,decision) VALUES(%s,'prop_none','sha256:none','[]','NO_ACTION')",
                  (NOW.strftime(FMT),))
    assert stats.compute()["counts"]["no_action"] == 1


def test_stats_endpoint_degrades_instead_of_failing(client):
    s = client.get("/stats").json()                              # no database in this environment
    assert s["available"] is False and s["dollars_recovered"] == 0.0 and s["counts"]["stopped"] == 0 and s["error"]


def test_stats_endpoint_sample_mode_is_labelled(client):
    s = client.get("/stats?mock=1").json()
    assert s["sample"] is True and s["dollars_recovered"] == 34.0


# ---- read-only guarantee ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "UPDATE pacts SET pact_text = 'x'", "DELETE FROM audit_log", "INSERT INTO users(id) VALUES('x')",
    "DROP TABLE pacts", "SELECT 1; DROP TABLE pacts", "  update audit_log set decision='ALLOW'", "",
])
def test_dbaccess_refuses_anything_but_a_single_select(sample_db, sql):
    with pytest.raises(ValueError):
        dbaccess.query(sql)


def test_dbaccess_allows_select_and_with(sample_db):
    assert dbaccess.query("SELECT COUNT(*) AS n FROM pacts")[0]["n"] == 2
    assert dbaccess.query("WITH x AS (SELECT 1 AS a) SELECT a FROM x")[0]["a"] == 1


def test_overview_never_changes_any_table(sample_db):
    tables = ["audit_log", "pacts", "pact_revocations", "approvals", "executed_actions", "messages", "proposals", "users", "policy_checks"]
    snap = lambda: {t: sample_db.query(f"SELECT * FROM {t}") for t in tables}
    before = snap()
    overview.build(use_sample=False)
    queries.run("stopped_this_week"); queries.run("recovered_by_store"); queries.run("price_history", 1)
    assert snap() == before


# ---- overview panels ---------------------------------------------------------------------------------------------

def test_every_panel_loads_with_sample_data(client):
    o = client.get("/debug/overview?mock=1").json()
    assert o["sample"] is True and "SAMPLE" in o["sample_note"]
    for name in ("gate_trace", "pact", "approvals_once", "policy", "matching", "live_queries", "ingest", "safety", "analytics", "tests"):
        assert o[name]["available"] is True, (name, o[name].get("error"))


def test_gate_trace_shows_every_rule_in_order_with_the_failing_one(sample_db):
    d = {x["item"]: x for x in overview.gate_trace()["decisions"]}
    bp = d["Trailhead Backpack (Black)"]
    assert bp["decision"] == "BLOCK" and bp["failed_rule"] == "R5"
    assert [r["rule"] for r in bp["rules"]] == ["R0", "R1", "R2", "R3", "R4", "R6", "R6b", "R6c", "R7", "R5"]
    assert bp["rules"][-1]["pass"] is False and bp["rules"][-1]["values"]["offered"] == "MS-3003/Blue"
    assert all(r["pass"] for r in bp["rules"][:-1])
    assert d["Breeze Desk Fan (Gray)"]["failed_rule"] == "R4" and d["Nimbus Smart Lamp (White)"]["failed_rule"] == "R12"
    assert d["Aurora Wireless Earbuds (Black)"]["short_hash"].startswith("sha256:")


def test_gate_trace_is_the_last_10_newest_first(sample_db):
    for i in range(12):
        sample_db.run("INSERT INTO audit_log(ts,proposal_hash,rule_results_json,decision) VALUES(%s,%s,'[]','BLOCK')", (NOW.strftime(FMT), f"h{i}"))
    ids = [x["audit_id"] for x in overview.gate_trace()["decisions"]]
    assert len(ids) == 10 and ids == sorted(ids, reverse=True)


def test_pact_panel_shows_valid_signature_versions_and_signed_revocation(sample_db):
    p = overview.pact_panel(use_sample=True)["pacts"][0]
    by_v = {v["version"]: v for v in p["versions"]}
    assert p["latest_version"] == 2 and by_v[2]["is_latest"] and not by_v[1]["is_latest"]
    assert by_v[2]["signature_ok"] is True and by_v[2]["revoked"] is False
    assert by_v[1]["revoked"] is True                              # v1 was replaced by an edit
    assert by_v[2]["hash"].startswith("sha256:") and '"max_rebuy_price_usd": 250' in by_v[2]["pact_text"]
    assert p["owner"]["persona_status"] == "approved" and p["owner"]["verified_at"]


def test_the_database_tamper_stunt_flips_the_check_mark(sample_db):
    """A's saved tamper SQL must match the canonical text, and the panel must then show X (stunt, section 5)."""
    assert overview.pact_panel(use_sample=True)["pacts"][0]["versions"][0]["signature_ok"] is True
    sample_db.run("UPDATE pacts SET pact_text = REPLACE(pact_text, '\"max_rebuy_price_usd\":250', "
                  "'\"max_rebuy_price_usd\":1000') WHERE pact_id = %s AND version = %s", ("pact_demo", 2))
    v2 = {v["version"]: v for v in overview.pact_panel(use_sample=True)["pacts"][0]["versions"]}[2]
    assert "1000" in v2["raw_text"] and v2["signature_ok"] is False


def test_a_forged_revocation_does_not_count(sample_db):
    sample_db.run("UPDATE pact_revocations SET signature = 'forged'")
    panel = overview.pact_panel(use_sample=True)
    assert panel["revocations"][0]["signature_ok"] is False
    assert {v["version"]: v for v in panel["pacts"][0]["versions"]}[1]["revoked"] is False


def test_without_sivas_module_the_panel_says_unknown_not_valid(sample_db, monkeypatch):
    monkeypatch.setattr(overview, "_lookup", lambda paths, name: None)      # as if backend.pact had no verifier
    panel = overview.pact_panel(use_sample=False)
    assert panel["verifier_found"] is False
    assert all(v["signature_ok"] is None for v in panel["pacts"][0]["versions"])


def test_matching_panel_in_sample_mode_ranks_blue_first_but_not_exact(sample_db):
    m = overview.matching_candidates(3, use_sample=True)            # the Black backpack receipt
    top = m["candidates"][0]
    assert (top["variant"], top["exact"]) == ("Blue", False)
    assert any(c["variant"] == "Black" and c["exact"] for c in m["candidates"])
    assert "DOT_PRODUCT" in m["sql"] and len(m["items"]) == 6


def test_matching_panel_reports_a_missing_matcher_instead_of_inventing_results(sample_db, monkeypatch):
    monkeypatch.setattr(overview, "_lookup", lambda paths, name: None)          # as if backend.watch had no matcher
    out = overview._panel(lambda: overview.matching_candidates(1, use_sample=False))
    assert out["available"] is False and "matcher" in out["error"]


def test_one_broken_panel_does_not_take_down_the_others(sample_db):
    real = sample_db.query

    def flaky(sql, params=()):
        if "FROM approvals" in sql:
            raise RuntimeError("table locked")
        return real(sql, params)

    dbaccess._override = flaky
    o = overview.build(use_sample=False)
    assert o["approvals_once"]["available"] is False and "table locked" in o["approvals_once"]["error"]
    assert o["gate_trace"]["available"] is True and o["policy"]["available"] is True and o["safety"]["available"] is True


def test_all_panels_fail_softly_without_a_database(client):
    o = client.get("/debug/overview").json()
    assert o["gate_trace"]["available"] is False and o["pact"]["available"] is False
    assert o["safety"]["available"] is True and o["live_queries"]["available"] is True and o["tests"]["available"] is True


@pytest.mark.parametrize("raw,on", [(None, True), ("true", True), ("false", False), ("", True), ("yes", True), ("False", False)])
def test_safety_panel_demo_mode(monkeypatch, raw, on):
    monkeypatch.delenv("DEMO_MODE", raising=False)
    if raw is not None:
        monkeypatch.setenv("DEMO_MODE", raw)
    assert overview.safety_panel()["demo_mode"] is on


def test_safety_panel_lists_recipients_and_only_outgoing_messages(sample_db, monkeypatch):
    monkeypatch.setenv("DEMO_ALLOWED_RECIPIENTS", "support@x.com, me@x.com ,")
    monkeypatch.setenv("DEMO_SUPPORT_INBOX", "support@x.com")
    s = overview.safety_panel()
    assert s["allowed_recipients"] == ["support@x.com", "me@x.com"] and s["support_inbox"] == "support@x.com"
    assert len(s["messages"]) == 2 and all(m["direction"] == "out" for m in s["messages"])
    assert "body" not in s["messages"][0]


def test_ingest_panel_uses_receipt_events_for_model_tokens_and_cost(sample_db):
    from backend.analytics import events
    events.track("receipt_parsed", {"model": "arcee-x", "tokens": 800, "latency_ms": 600, "cost_usd": 0.0004})
    events.track("receipt_parsed", {"model": "arcee-x", "tokens": 700, "latency_ms": 500, "cost_usd": 0.0006})
    p = overview.ingest_panel()
    assert len(p["emails"]) == 6 and "body" not in p["emails"][0]
    assert p["avg_cost_per_receipt_usd"] == 0.0005 and p["parse_events"][0]["props"]["model"] == "arcee-x"
    assert p["line_items"][0]["parse_confidence"] is not None


def test_tests_panel_reads_the_pytest_result_file(tmp_path, monkeypatch):
    monkeypatch.setattr(overview, "ROOT", tmp_path)
    assert overview.tests_panel()["ran"] is False
    (tmp_path / ".pytest_last.json").write_text(json.dumps({"passed": 72, "failed": 0, "duration_s": 3.2}))
    t = overview.tests_panel()
    assert t["ran"] is True and t["passed"] == 72


def test_policy_panel_lists_quotes_with_source_and_revision(sample_db):
    c = overview.policy_panel()["checks"]
    assert len(c) == 6 and {"cited_text", "source_url", "senso_source_id", "revision", "fetched_at"} <= set(c[0])


def test_approvals_panel_explains_run_once(sample_db):
    a = overview.approvals_once()
    assert a["approvals"][0]["status"] == "pending" and a["executed"][0]["proposal_hash"].startswith("sha256:")


# ---- saved live queries --------------------------------------------------------------------------------------------

def test_stopped_this_week_returns_the_three_demo_stops_with_plain_reasons(sample_db):
    q = queries.run("stopped_this_week")
    assert {r["failed_rule"] for r in q["rows"]} == {"R5", "R4", "R8"} and "BLOCK" in q["sql"]
    why = {r["failed_rule"]: r["why"] for r in q["rows"]}
    assert why["R8"] == "Not worth it: you'd only save $6; your minimum is $10."
    assert "Trailhead Backpack (Black)" in why["R5"] and "Trailhead Backpack (Blue)" in why["R5"]


def test_stopped_this_week_ignores_older_stops(sample_db):
    sample_db.run("INSERT INTO audit_log(ts,proposal_hash,rule_results_json,decision) VALUES(%s,'old','[]','BLOCK')",
                  ((NOW - timedelta(days=10)).strftime(FMT),))
    assert len(queries.run("stopped_this_week")["rows"]) == 3


def test_recovered_by_store_and_price_history(sample_db):
    assert queries.run("recovered_by_store")["rows"] == [{"store": "mockstore", "dollars": 34.0, "actions": 1}]
    ph = queries.run("price_history", 1)
    assert ph["params"] == [1] and ph["rows"][0]["price"] == 95.99
    assert queries.run("price_history")["params"] == [1]          # defaults to the first listing


def test_live_query_endpoint_whitelist(client):
    assert client.get("/debug/query/DROP TABLE users").status_code == 404
    assert client.get("/debug/query/stopped_this_week", params={"mock": 1}).json()["rows"]
    soft = client.get("/debug/query/stopped_this_week").json()      # no DB: soft error, still shows the SQL
    assert soft["rows"] == [] and soft["error"] and "SELECT" in soft["sql"]
