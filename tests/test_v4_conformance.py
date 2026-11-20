"""Teammate C's slice of the v4 team file, pinned: every verbatim string, name and contract the doc fixes.
If someone edits the team file, update the expected values HERE on purpose (this is the cross-check)."""
import re
from pathlib import Path

import pytest

from backend.debug import dbaccess, sample
from backend.debug.plain import RULE_PLAIN, plain
from backend.voice import intents, stories
from mockstore import store
from tests.c_fixtures import client, fresh_store, sample_db  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


# ---- 3.2 environment variables: the exact list, in order, first ----------------------------------------------------------

V4_ENV = ["SINGLESTORE_URL", "SENSO_API_KEY", "AIRBYTE_CLIENT_ID", "AIRBYTE_CLIENT_SECRET", "AIRBYTE_WORKSPACE_NAME",
          "PERSONA_API_KEY", "PERSONA_TEMPLATE_ID", "PERSONA_WEBHOOK_SECRET", "SIE_URL", "SIE_API_KEY", "ARCEE_API_KEY",
          "LLM_API_KEY", "EMBEDDING_DIM", "AMPLITUDE_API_KEY", "FIREDRILL_API_KEY", "PACT_SIGNING_KEY", "MOCK_WEBHOOK_SECRET",
          "DEMO_MODE", "DEMO_SUPPORT_INBOX", "DEMO_ALLOWED_RECIPIENTS"]


def test_env_example_has_exactly_the_section_3_2_variables_first_and_in_order():
    keys = [l.split("=")[0] for l in (ROOT / ".env.example").read_text().splitlines() if re.match(r"^[A-Z_]+=", l)]
    assert keys[:len(V4_ENV)] == V4_ENV
    lines = {l.split("=")[0]: l for l in (ROOT / ".env.example").read_text().splitlines() if re.match(r"^[A-Z_]+=", l)}
    assert lines["EMBEDDING_DIM"].startswith("EMBEDDING_DIM=1024") and lines["DEMO_MODE"].startswith("DEMO_MODE=true")


def test_env_example_has_no_secret_values():
    for l in (ROOT / ".env.example").read_text().splitlines():
        m = re.match(r"^([A-Z_]+)=([^#\s]*)", l)
        if m and m.group(1) in V4_ENV:
            assert m.group(2) in ("", "1024", "true"), l


def test_gitignore_starts_with_env_and_ignores_the_usual():
    lines = (ROOT / ".gitignore").read_text().splitlines()
    assert lines[0] == ".env" and "__pycache__/" in lines and ".venv/" in lines and "node_modules/" in lines


def test_requirements_list_what_section_4_4_names():
    text = (ROOT / "requirements.txt").read_text().lower()
    for pkg in ["fastapi", "uvicorn", "pydantic", "singlestoredb", "httpx", "python-dotenv", "pytest", "airbyte-agent-sdk", "sie-sdk"]:
        assert re.search(rf"^{re.escape(pkg)}\b", text, re.M), pkg


# ---- 3.7: the endpoints C owns exist -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("method,path", [
    ("GET", "/health"), ("GET", "/stats"), ("GET", "/debug/overview"), ("POST", "/voice/ask"),
    ("GET", "/mock/admin"), ("GET", "/mock/policy"), ("GET", "/mock/support"), ("GET", "/mock/p/MS-1001"),
    ("GET", "/mock/api/listings"), ("POST", "/mock/api/rebuy_return"), ("GET", "/mock/api/orders/MS-55021"),
])
def test_c_owned_endpoints_exist(client, method, path):
    r = client.request(method, path, json={} if method == "POST" else None)
    assert r.status_code != 404 or path.startswith("/mock/p/"), (method, path, r.status_code)
    assert r.status_code in (200, 400, 422), (method, path, r.status_code)       # 400/422 = it exists and validated the empty body


# ---- 3.8: the mock store seed (rows 1-4) and its policy ---------------------------------------------------------------------

def test_seed_rows_1_to_4_exactly(fresh_store):
    rows = {(l["sku"], l["variant"]): l for l in store.list_listings()}
    assert {k: (r["title"], r["category"], r["price"]) for k, r in rows.items()} == {
        ("MS-1001", "Black"): ("Aurora Wireless Earbuds", "electronics", 129.99),
        ("MS-2002", "White"): ("Nimbus Smart Lamp", "home", 239.00),
        ("MS-3003", "Black"): ("Trailhead Backpack", "outdoor", 89.00),
        ("MS-3003", "Blue"): ("Trailhead Backpack", "outdoor", 89.00),
        ("MS-4004", "Gray"): ("Breeze Desk Fan", "clearance", 59.00)}


def test_seed_orders_match_the_dates_in_the_seed_table(fresh_store):
    by = {o["order_no"]: o for o in store.list_orders()}
    got = {o["items"][0]["title"]: (o["order_date"], o["delivery_date"], o["items"][0]["price_paid"]) for o in by.values()}
    assert got == {"Aurora Wireless Earbuds": ("2026-11-11", "2026-11-13", 129.99), "Nimbus Smart Lamp": ("2026-11-02", "2026-11-04", 239.0),
                   "Trailhead Backpack": ("2026-11-12", "2026-11-14", 89.0), "Breeze Desk Fan": ("2026-11-10", "2026-11-12", 59.0)}
    assert "MS-55021" in by                                                   # the order_no used in the 3.5 proposal example


def test_the_lamps_adjust_window_is_closed_and_its_return_window_open_on_demo_day(fresh_store):
    """3.8 row 2: 'adjust window closed (18 > 14 days); return open'."""
    from datetime import date
    lamp = store.get_order("MS-55021")
    purchased, delivered, today = date.fromisoformat(lamp["order_date"]), date.fromisoformat(lamp["delivery_date"]), date(2026, 11, 20)
    assert (today - purchased).days == 18 > store.policy()["adjust_window_days"] == 14
    assert (today - delivered).days < store.policy()["return_window_days"] == 30


def test_demo_preset_matches_the_plan_exactly(fresh_store):
    assert store.DEMO_PRESET == {1: "95.99", 2: "199.00", 4: "64.00", 5: "39.00"}      # earbuds, lamp, BLUE backpack, fan


def test_policy_covers_every_clause_of_the_section_3_8_mock_policy(fresh_store):
    p = store.policy()
    text = p["text"].lower()
    assert p["claim_channel"] == "email" and "email mock store support" in text
    assert "14 days of purchase" in text and "own prices" in text and p["competitor_match_allowed"] is False
    assert "30 days of delivery" in text and "final sale" in text and "clearance" in text
    assert "no restocking fee" in text and "free" in text
    assert (p["restocking_fee_pct"], p["return_shipping_usd"]) == (0.0, 0.0)


# ---- 3.10: the plain words, verbatim -----------------------------------------------------------------------------------------------

def test_rule_sentences_are_the_doc_s_words():
    assert plain("R0") == "Something didn't add up, so we didn't act."
    assert plain("R1") == plain("A1") == "Please verify it's you again before Clawback can act."
    assert plain("R2") == "You haven't allowed Clawback to do this."
    assert plain("R3") == "You haven't allowed Clawback at this store."
    assert plain("R4") == "This item is final sale (or a type you excluded)."
    assert plain("R5", bought="Black backpack", offered="Blue backpack") == "The cheaper one isn't the same item: you bought Black backpack, this is Blue backpack."
    assert plain("R6") == "We couldn't confirm the store's current policy."
    assert plain("R6b", store="Target") == plain("R6c", store="Target") == "Target doesn't take this kind of request this way."
    assert plain("R7") == "The window to ask has closed (or closes too soon)."
    assert plain("R8", net=6, minimum=10) == "Not worth it: you'd only save $6; your minimum is $10."
    assert plain("R8a") == "The price just changed; we'll check again."
    assert plain("R9") == "That would go over your spending limit."
    assert plain("R10") == "We only write to the store's official support (or to you)."
    assert plain("R11") == "We've already asked about this order."
    assert plain("R13") == "We only reply in the same conversation, up to 3 times."
    assert plain("R12", ask=150) == "This is above your $150 ask-first limit."


def test_every_gate_rule_has_a_plain_sentence():
    for rule in ["R0", "R1", "R2", "R3", "R4", "R5", "R6", "R6b", "R6c", "R7", "R8", "R8a", "R9", "R10", "R11", "R12", "R13", "A1"]:
        assert plain(rule, store="X", bought="a", offered="b", net=1, minimum=2, ask=3)


def test_card_copy_for_the_demo_rows_through_the_voice(sample_db):
    """3.10 'Card copy for the demo (seed rows)': the orb says what the cards say."""
    assert intents.ask("why did you act on the earbuds")["answer"] == \
        "We asked Mock Store for $34 back on your Aurora earbuds. Price dropped from $129.99 to $95.99."
    assert intents.ask("why did you stop the backpack")["answer"] == "We didn't act. The cheaper backpack is Blue; yours is Black."
    assert intents.ask("why did you stop the fan")["answer"] == "We didn't act. Clearance items are final sale at Mock Store."
    assert intents.ask("why did you stop the best buy item")["answer"] == "We didn't act. Not worth it: you'd only save $6; your minimum is $10."
    assert intents.ask("anything need my ok")["answer"].startswith("Yes: save $40 on your Nimbus lamp?")


def test_why_panel_is_four_lines_in_the_docs_order_with_its_emoji(sample_db):
    lines = intents.ask("why did you stop the backpack")["lines"]
    assert [l["icon"] for l in lines] == ["🧾", "🏷️", "📄", "✅"]
    assert lines[0]["text"] == "You bought Trailhead Backpack (Black) for $89 on Nov 12."
    assert re.fullmatch(r"We found Trailhead Backpack \(Blue\) for \$64 at Mock Store, \d+ minutes? ago\.|"
                        r"We found Trailhead Backpack \(Blue\) for \$64 at Mock Store, less than a minute ago\.", lines[1]["text"])
    assert lines[2]["text"] == "Mock Store's policy says: \"Price adjustments apply to the identical item within 14 days of purchase.\""
    assert lines[3]["text"].startswith("Your rules: ")


def test_everything_ok_wording_for_an_allowed_decision(sample_db):
    assert intents.ask("why did you act on the earbuds")["lines"][3]["text"] == "Your rules: Everything checked out."


# ---- 3.11: the orb, exactly ------------------------------------------------------------------------------------------------------------

def test_the_help_sentence_is_the_docs_sentence(sample_db):
    assert intents.HELP == "I can tell you what I did, why, or pause. Approvals happen on screen."
    assert intents.ask("sing me a song")["answer"] == intents.HELP


def test_pause_asks_say_yes(sample_db):
    assert intents.ask("Pause Clawback")["answer"].startswith("Say yes to pause")


def test_the_five_listed_utterances_all_work(sample_db):
    assert intents.ask("What did you do today?")["intent"] == "summary"
    assert intents.ask("How much did I get back?")["intent"] == "summary"
    assert intents.ask("Why did you stop the backpack?")["intent"] == "why"
    assert intents.ask("Anything need my OK?")["intent"] == "needs_ok"
    assert intents.ask("Pause Clawback")["intent"] == "pause_confirm"


def test_definition_of_done_orb_lines(sample_db):
    """5: 'asking the orb "why did you stop the backpack?" answers correctly; "approve the lamp" is refused'."""
    assert "cheaper backpack is Blue" in intents.ask("why did you stop the backpack?")["answer"]
    assert intents.ask("approve the lamp")["intent"] == "refused"


def test_the_spoken_summary_matches_the_scripts_closing_line(sample_db):
    """90-second script step 6: '$34 back · 3 unsafe actions stopped.'"""
    a = intents.ask("how much did I get back")["answer"]
    assert "$34 back" in a and "3 unsafe actions stopped" in a


# ---- 3.4: C's contracts ---------------------------------------------------------------------------------------------------------------------

def test_track_signature_and_never_raises():
    import inspect
    from backend.analytics.events import track
    assert list(inspect.signature(track).parameters)[:2] == ["event", "props"]
    track("gate_decision", object())          # nonsense in, no exception out


def test_the_nine_analytics_events_from_section_4_4_are_known():
    from backend.analytics.events import KNOWN_EVENTS
    assert set(KNOWN_EVENTS) == {"receipt_parsed", "opportunity_found", "gate_decision", "action_executed", "claim_packet_ready",
                                 "money_recovered", "approval_resolved", "pact_signed", "pact_paused"}


# ---- 3.10 Under the hood: the ten panels -----------------------------------------------------------------------------------------------------

def test_under_the_hood_has_exactly_the_ten_panels_of_the_table(client):
    o = client.get("/debug/overview?mock=1").json()
    panels = {k for k, v in o.items() if isinstance(v, dict) and "available" in v}
    assert panels == {"gate_trace", "pact", "approvals_once", "policy", "matching", "live_queries", "ingest", "safety", "analytics", "tests"}


def test_the_three_saved_queries_are_the_ones_named_in_the_doc(client):
    labels = {q["label"] for q in client.get("/debug/overview?mock=1").json()["live_queries"]["queries"]}
    assert labels == {"What the Pact stopped this week, and why", "Dollars recovered by store", "Price history for an item"}


def test_the_frontend_panel_captions_name_their_sponsors():
    src = (ROOT / "frontend/app/under-the-hood/panels.tsx").read_text()
    for needle in ["Guild", "Persona", "Senso", "SingleStore + Superlinked", "SingleStore:", "Airbyte + Arcee", "Amplitude"]:
        assert needle in src, needle
    for title in ["Live gate trace", "Your Pact", "Approvals and run-once", "Policy", "Matching", "Live queries", "Ingest", "Safety", "Analytics", "Tests"]:
        assert f'title="{title}"' in src, title


def test_the_orb_has_the_documented_props_and_states():
    src = (ROOT / "frontend/components/orb/Orb.tsx").read_text()
    assert 'export type OrbState = "idle" | "listening" | "thinking" | "speaking"' in src
    assert "onAsk" in src and "SpeechRecognition" in src and "speechSynthesis" in src and "Space" in src
    assert (ROOT / "frontend/components/orb/index.ts").exists() and (ROOT / "frontend/app/under-the-hood/page.tsx").exists()
