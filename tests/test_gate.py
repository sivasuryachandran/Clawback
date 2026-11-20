import copy
from datetime import timedelta

import pytest

from backend.pact import sign
from backend.pact.gate import run_gate, run_approved
from tests.conftest import *  # noqa
from tests.conftest import NOW, SUPPORT, USER_EMAIL, make_ctx, make_pact, make_prop, lamp, _item, _listing, TARGET_POLICY, BESTBUY_POLICY


def gate(p, ctx, pact=None, **kw):
    return run_gate(p, pact or make_pact(), ctx, **kw)


def test_earbuds_allow():
    d = gate(make_prop(), make_ctx())
    assert d.decision == "ALLOW", d.results
    assert d.proposal_hash.startswith("sha256:")


def test_followup_same_recipient_thread_allow():
    ctx = make_ctx(claim=dict(recipient=SUPPORT, thread_id="thr_1", followups=0))
    assert gate(make_prop("claim_followup"), ctx).decision == "ALLOW"


def test_fourth_followup_blocks_r13():
    ctx = make_ctx(claim=dict(recipient=SUPPORT, thread_id="thr_1", followups=3))
    assert gate(make_prop("claim_followup"), ctx).failed_rule == "R13"


def test_followup_new_address_blocks():
    ctx = make_ctx(claim=dict(recipient=SUPPORT, thread_id="thr_1", followups=0))
    p = make_prop("claim_followup"); p["email"]["to"] = USER_EMAIL     # on allowlist, but not the claim recipient
    assert gate(p, ctx).failed_rule == "R13"


def test_lamp_asks_r12():
    p, ctx = lamp()
    d = gate(p, ctx)
    assert (d.decision, d.failed_rule) == ("ASK", "R12")


def _approval(p, **o):
    a = dict(status="approved", proposal_hash=sign.proposal_hash(p), expires_at=NOW + timedelta(hours=3))
    a.update(o)
    return a


def test_lamp_approved_allows_and_own_amount_not_double_counted():
    p, ctx = lamp()
    d = run_approved(p, make_pact(), ctx, _approval(p))
    assert d.decision == "ALLOW", d.results


def test_approval_changed_proposal_blocks_a1():
    p, ctx = lamp()
    a = _approval(p)
    p2 = copy.deepcopy(p); p2["rebuy"]["listing_id"] = 99
    assert run_approved(p2, make_pact(), ctx, a).failed_rule == "A1"


def test_approval_expired_other_user_no_recheck_block_a1():
    p, ctx = lamp()
    assert run_approved(p, make_pact(), ctx, _approval(p, expires_at=NOW - timedelta(minutes=1))).failed_rule == "A1"
    p, ctx = lamp(); ctx.session_user_id = "someone_else"
    assert run_approved(p, make_pact(), ctx, _approval(p)).failed_rule == "A1"
    p, ctx = lamp(); ctx.reverified_at = None
    assert run_approved(p, make_pact(), ctx, _approval(p)).failed_rule == "A1"
    p, ctx = lamp(); ctx.reverified_at = NOW - timedelta(minutes=30)
    assert run_approved(p, make_pact(), ctx, _approval(p)).failed_rule == "A1"
    p, ctx = lamp()
    assert run_approved(p, make_pact(), ctx, _approval(p, status="pending")).failed_rule == "A1"


def test_approval_does_not_bypass_other_rules():
    p, ctx = lamp(); ctx.listing["price"] = 230   # savings drop below minimum
    assert run_approved(p, make_pact(), ctx, _approval(p)).failed_rule == "R8"


def test_backpack_blocks_r5_blue_vs_black():
    item = _item(id=3, order_no="MS-55031", title="Trailhead Backpack", sku="MS-3003", variant="Black", category="outdoor", price_paid=89.0)
    listing = _listing(id=79, sku="MS-3003", variant="Blue", title="Trailhead Backpack", price=64.0)
    d = gate(make_prop(line_item_id=3, listing_id=79), make_ctx(item=item, listing=listing))
    assert d.failed_rule == "R5"


def test_fan_blocks_r4_clearance():
    item = _item(id=4, order_no="MS-55041", title="Breeze Desk Fan", sku="MS-4004", variant="Gray", category="clearance", price_paid=59.0)
    listing = _listing(id=80, sku="MS-4004", variant="Gray", price=39.0)
    assert gate(make_prop(line_item_id=4, listing_id=80), make_ctx(item=item, listing=listing)).failed_rule == "R4"


def _bb(price_seen_minutes_ago=6000):
    item = _item(id=5, order_no="BB-1", store="bestbuy", sku="BB123", upc="999", variant="", category="electronics", price_paid=100.0,
                 order_date="2026-11-14", delivery_date="2026-11-16")
    listing = _listing(id=81, store="bestbuy", sku="BB123", upc="999", variant="", price=94.0, seen_at=NOW - timedelta(minutes=price_seen_minutes_ago))
    return make_ctx(item=item, listing=listing, policy=copy.deepcopy(BESTBUY_POLICY)), item


def test_bestbuy_six_dollars_blocks_r8_even_when_price_is_stale():
    ctx, _ = _bb()
    p = make_prop("claim_packet", store="bestbuy", line_item_id=5, listing_id=81)
    d = gate(p, ctx)
    assert d.failed_rule == "R8"        # R8a runs after R8


def test_bestbuy_window_counts_from_delivery():
    ctx, item = _bb(); ctx.policy["adjust_window_days"] = 14
    p = make_prop("claim_packet", store="bestbuy", line_item_id=5, listing_id=81)
    assert gate(p, ctx).window_ends == "2026-11-30"      # Nov 16 + 14


def _target(**o):
    item = _item(id=6, order_no="T-1", store="target", sku="T1", upc="555", variant="", category="home", price_paid=50.0,
                 order_date="2026-10-22", delivery_date="2026-10-24")
    listing = o.pop("listing", None)
    return make_ctx(item=item, listing=listing, policy=copy.deepcopy(TARGET_POLICY), **o)


def test_target_alert_allows_return_closing_soon():
    ctx = _target()     # return window 30d from delivery Oct 24 -> Nov 23
    ctx.opportunity["kind"] = "deadline"
    p = make_prop("deadline_alert", store="target", line_item_id=6, listing_id=None, alert_at="2026-11-22")
    assert gate(p, ctx).decision == "ALLOW"


def test_alert_after_window_end_blocks_r7():
    ctx = _target()
    p = make_prop("deadline_alert", store="target", line_item_id=6, listing_id=None, alert_at="2026-12-30")
    assert gate(p, ctx).failed_rule == "R7"


def test_price_adjust_email_to_chat_store_blocks_r6c_but_packet_allows():
    item = _item(id=6, order_no="T-1", store="target", sku="T1", upc="555", variant="", category="home", price_paid=50.0,
                 order_date="2026-11-10", delivery_date="2026-11-12")
    listing = _listing(id=82, store="target", sku="T1", upc="555", variant="", price=30.0)
    ctx = make_ctx(item=item, listing=listing, policy=copy.deepcopy(TARGET_POLICY))
    p_bad = make_prop("price_adjust_email", store="target", line_item_id=6, listing_id=82)
    assert gate(p_bad, ctx).failed_rule == "R6c"
    p_ok = make_prop("claim_packet", store="target", line_item_id=6, listing_id=82)
    assert gate(p_ok, ctx).decision == "ALLOW"


def test_target_claim_against_cheaper_bestbuy_blocks_r5():
    item = _item(id=6, order_no="T-1", store="target", sku="T1", upc="555", variant="", category="home", price_paid=50.0,
                 order_date="2026-11-10", delivery_date="2026-11-12")
    listing = _listing(id=83, store="bestbuy", sku="BB1", upc="555", variant="", price=30.0)
    ctx = make_ctx(item=item, listing=listing, policy=copy.deepcopy(TARGET_POLICY))
    assert gate(make_prop("claim_packet", store="target", line_item_id=6, listing_id=83), ctx).failed_rule == "R5"


def test_cross_store_same_upc_passes_r5_for_rebuy():
    p, ctx = lamp()
    ctx.listing.update(store="bestbuy", sku="OTHER", upc="111", model_number=None); ctx.item["upc"] = "111"
    ctx.item.update(sku="MS-2002", model_number=None, variant="White"); ctx.listing["variant"] = "White"
    d = run_gate(p, make_pact(), ctx, human_approved=True)
    assert d.decision == "ALLOW", d.results


def test_stale_price_blocks_r8a():
    ctx = make_ctx(listing=_listing(seen_at=NOW - timedelta(minutes=20)))
    assert gate(make_prop(), ctx).failed_rule == "R8a"


def test_proposal_not_matching_db_blocks_r0():
    assert gate(make_prop(listing_id=999), make_ctx()).failed_rule == "R0"
    assert gate(make_prop(store="target"), make_ctx()).failed_rule == "R0"
    p = make_prop(); p["email"]["to"] = None
    assert gate(p, make_ctx()).failed_rule == "R0"


def test_missing_action_type_blocks_r0_without_raising():
    p = make_prop(); del p["action_type"]
    assert gate(p, make_ctx()).failed_rule == "R0"
    assert gate({}, make_ctx()).failed_rule == "R0"
    assert gate(None, make_ctx()).failed_rule == "R0"


def test_revoked_blocks_r1_even_though_status_is_active():
    ctx = make_ctx(pact_meta=dict(version=1, revoked=True, is_latest=True))
    pact = make_pact(); pact["status"] = "active"      # irrelevant extra; gate never reads status
    d = gate(make_prop(), ctx, pact=make_pact())
    assert d.failed_rule == "R1"


def test_old_version_blocks_r1():
    assert gate(make_prop(), make_ctx(pact_meta=dict(version=1, revoked=False, is_latest=False))).failed_rule == "R1"


def test_edited_pact_blocks_r1():
    pact = make_pact(); pact["monthly_cap_usd"] = 99999
    assert gate(make_prop(), make_ctx(), pact=pact).failed_rule == "R1"


def test_expired_pact_blocks_r1():
    assert gate(make_prop(), make_ctx(), pact=make_pact(expires_at="2026-11-01")).failed_rule == "R1"


def test_persona_45_days_old_blocks_r1():
    assert gate(make_prop(), make_ctx(persona_verified_at=NOW - timedelta(days=45))).failed_rule == "R1"
    assert gate(make_prop(), make_ctx(persona_status="none")).failed_rule == "R1"


def test_citation_wrong_store_or_stale_blocks_r6():
    c = make_ctx(); c.policy_check["store"] = "target"
    assert gate(make_prop(), c).failed_rule == "R6"
    c = make_ctx(); c.policy_check["fetched_at"] = NOW - timedelta(days=2)
    assert gate(make_prop(), c).failed_rule == "R6"
    c = make_ctx(); c.policy_check["senso_source_id"] = None
    assert gate(make_prop(), c).failed_rule == "R6"
    assert gate(make_prop(), make_ctx(policy_check=None)).failed_rule == "R6"


def test_spending_cap_counts_approved_not_run_r9():
    p, ctx = lamp(); ctx.month_committed = 290.0          # $200 done + $90 approved-not-run
    ctx.listing["price"] = 40.0; ctx.item["price_paid"] = 239.0
    d = run_gate(p, make_pact(), ctx, human_approved=True)
    assert d.failed_rule == "R9"


def test_per_purchase_cap_r9():
    p, ctx = lamp()
    d = run_gate(p, make_pact(max_rebuy_price_usd=150, ask_first_above_usd=100), ctx, human_approved=True)
    assert d.failed_rule == "R9"


def test_second_claim_same_order_blocks_r11():
    assert gate(make_prop(), make_ctx(prior_claims=[{"status": "done"}])).failed_rule == "R11"


def test_window_closed_and_buffer_r7():
    ctx = make_ctx(item=_item(order_date="2026-11-07"))     # ends Nov 21 -> 1 day left < buffer 2
    assert gate(make_prop(), ctx).failed_rule == "R7"


def test_min_savings_r8():
    assert gate(make_prop(), make_ctx(listing=_listing(price=125.0))).failed_rule == "R8"


def test_action_and_store_permissions_r2_r3():
    assert gate(make_prop(), make_ctx(), pact=make_pact(actions=["deadline_alert"])).failed_rule == "R2"
    assert gate(make_prop(), make_ctx(), pact=make_pact(stores=["target"])).failed_rule == "R3"


def test_excluded_category_r4():
    assert gate(make_prop(), make_ctx(item=_item(category="gift_cards"))).failed_rule == "R4"


def test_low_parse_confidence_asks():
    assert gate(make_prop(), make_ctx(item=_item(parse_confidence=0.5))).decision == "ASK"


def test_store_without_price_adjust_r6b():
    c = make_ctx(); c.policy["price_adjust_allowed"] = False
    assert gate(make_prop(), c).failed_rule == "R6b"
