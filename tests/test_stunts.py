"""The judge stunts that touch Part C, checked against Siva's REAL signing and verification code."""
import json

import pytest

from backend.debug import dbaccess, panels, queries, sample
from backend.pact import sign as signer, verify_text
from tests.c_fixtures import client  # noqa: F401


def _signed_pact(**over):
    body = dict(pact_id="pact_stunt", version=1, user_id="u_demo", created_at="2026-11-20", expires_at="2027-02-18",
                actions=["price_adjust_email", "claim_packet", "rebuy_return", "deadline_alert"],
                stores=["mockstore", "target", "bestbuy"], excluded_categories=["clearance", "gift_cards"],
                min_net_savings_usd=10, ask_first_above_usd=150, max_rebuy_price_usd=250, monthly_cap_usd=300,
                buffer_days=2, max_claims_per_order=1, max_followups_per_thread=3, persona_inquiry_id="inq_1")
    body.update(over)
    return signer.sign(body)


@pytest.fixture
def pact_db():
    db = sample.build()
    db.run("DELETE FROM pacts"); db.run("DELETE FROM pact_revocations")
    db.run("INSERT INTO pacts VALUES(%s,%s,%s,%s,%s,%s)", ("pact_stunt", 1, "u_demo", _signed_pact(), "2026-11-20 10:00:00", "active"))
    dbaccess._override = db.query
    yield db
    dbaccess._override = None


def _sig(db_version=1):
    pact = panels.pact_panel(use_sample=False)["pacts"][0]
    return {v["version"]: v["signature_ok"] for v in pact["versions"]}[db_version]


def test_the_stunt_sql_matches_what_sivas_signer_actually_writes(pact_db):
    text = pact_db.query("SELECT pact_text FROM pacts")[0]["pact_text"]
    assert '"max_rebuy_price_usd":250' in text, "Siva's canonical Pact text changed: update the stunt SQL"
    assert text.count('"max_rebuy_price_usd":250') == 1


def test_untouched_pact_shows_a_check_mark_via_sivas_verify_text(pact_db):
    assert _sig() is True
    assert verify_text(pact_db.query("SELECT pact_text FROM pacts")[0]["pact_text"]) is not None


def test_the_tamper_stunt_flips_the_check_to_an_x_and_restore_flips_it_back(pact_db):
    assert _sig() is True
    pact_db.run(queries.TAMPER_STUNT_SQL, ("pact_stunt", 1))
    tampered = pact_db.query("SELECT pact_text FROM pacts")[0]["pact_text"]
    assert '"max_rebuy_price_usd":1000' in tampered
    assert verify_text(tampered) is None                      # the gate (R1) would stop on this
    assert _sig() is False                                    # and Under the hood shows the X
    pact_db.run(queries.TAMPER_RESTORE_SQL, ("pact_stunt", 1))
    assert _sig() is True


def test_the_tamper_sql_only_touches_the_named_pact_and_version(pact_db):
    pact_db.run("INSERT INTO pacts VALUES(%s,%s,%s,%s,%s,%s)", ("pact_stunt", 2, "u_demo", _signed_pact(version=2), "2026-11-20 11:00:00", "active"))
    pact_db.run(queries.TAMPER_STUNT_SQL, ("pact_stunt", 2))
    assert _sig(1) is True and _sig(2) is False


def test_the_sql_file_has_the_same_statements_as_the_constants():
    text = (__import__("pathlib").Path(__file__).resolve().parents[1] / "scripts" / "stunt_tamper_pact.sql").read_text()
    for needle in ["REPLACE(pact_text, '\"max_rebuy_price_usd\":250', '\"max_rebuy_price_usd\":1000')",
                   "REPLACE(pact_text, '\"max_rebuy_price_usd\":1000', '\"max_rebuy_price_usd\":250')"]:
        assert needle in text


def test_under_the_hood_never_runs_the_tamper_sql(client):
    assert "UPDATE" not in " ".join(sql for _, sql in queries.LIVE_QUERIES.values())
    with pytest.raises(ValueError):
        dbaccess.query(queries.TAMPER_STUNT_SQL, ("p", 1))     # the read-only guard refuses it outright


def test_a_pause_record_signed_by_siva_is_recognised(pact_db):
    stamp = "2026-11-20 12:00:00"
    pact_db.run("INSERT INTO pact_revocations VALUES(%s,%s,%s,%s,%s)",
                ("pact_stunt", 1, stamp, "paused by user", signer.sign_revocation("pact_stunt", 1, stamp)))
    panel = panels.pact_panel(use_sample=False)
    assert panel["revocations"][0]["signature_ok"] is True
    assert panel["pacts"][0]["versions"][0]["revoked"] is True
    pact_db.run("UPDATE pact_revocations SET signature = 'forged'")
    forged = panels.pact_panel(use_sample=False)
    assert forged["revocations"][0]["signature_ok"] is False and forged["pacts"][0]["versions"][0]["revoked"] is False
