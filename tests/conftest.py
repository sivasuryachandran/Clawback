"""Hand-built gate contexts matching the seed rows in section 3.8 (demo date 2026-11-20). No DB."""
import copy
import os
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("PACT_SIGNING_KEY", "test-signing-key-0123456789abcdef0123456789")

from backend.pact import sign
from backend.pact.gate import GateContext

NOW = datetime(2026, 11, 20, 12, 0, 0)
SUPPORT = "support@mockstore.test"
USER_EMAIL = "demo@user.test"

MOCK_POLICY = dict(store="mockstore", claim_channel="email", price_adjust_allowed=True, competitor_match_allowed=False,
                   adjust_window_days=14, adjust_window_starts="purchase", return_window_days=30, return_window_starts="delivery",
                   final_sale_categories=["clearance"], restocking_fee_pct=0, return_shipping_usd=0, support_domains=["mockstore.test"])
TARGET_POLICY = dict(MOCK_POLICY, store="target", claim_channel="chat", support_domains=["target.com"], return_window_days=30)
BESTBUY_POLICY = dict(MOCK_POLICY, store="bestbuy", claim_channel="chat", support_domains=["bestbuy.com"], adjust_window_starts="delivery")


def make_pact(**over):
    body = dict(pact_id="pact_1", version=1, user_id="u_demo", created_at="2026-11-20",
                expires_at="2027-02-18", actions=["price_adjust_email", "claim_packet", "rebuy_return", "deadline_alert"],
                stores=["mockstore", "target", "bestbuy"], excluded_categories=["clearance", "gift_cards"],
                min_net_savings_usd=10, ask_first_above_usd=150, max_rebuy_price_usd=250, monthly_cap_usd=300,
                buffer_days=2, max_claims_per_order=1, max_followups_per_thread=3, persona_inquiry_id="inq_1")
    body.update(over)
    return json_load(sign.sign(body))


def json_load(text):
    import json
    return json.loads(text)


def _item(**o):
    base = dict(id=1, order_no="MS-55001", store="mockstore", title="Aurora Wireless Earbuds", sku="MS-1001", upc="111", model_number="AUR-1",
                variant="Black", category="electronics", price_paid=129.99, order_date="2026-11-11", delivery_date="2026-11-13",
                parse_confidence=0.95)
    base.update(o)
    return base


def _listing(**o):
    base = dict(id=77, store="mockstore", sku="MS-1001", upc="111", model_number="AUR-1", variant="Black",
                title="Aurora Wireless Earbuds", price=95.99, seen_at=NOW - timedelta(minutes=5))
    base.update(o)
    return base


def make_ctx(**o):
    item = o.pop("item", None) or _item()
    listing = o.pop("listing", "default")
    listing = _listing() if listing == "default" else listing
    base = dict(
        today=NOW.date(), now=NOW,
        opportunity=dict(id=19, line_item_id=item["id"], listing_id=listing["id"] if listing else None, kind="price_drop"),
        item=item, listing=listing, policy=copy.deepcopy(MOCK_POLICY),
        policy_check=dict(store=item["store"], opportunity_id=19, senso_source_id="senso_1", revision="r1", fetched_at=NOW - timedelta(hours=1)),
        pact_meta=dict(version=1, revoked=False, is_latest=True),
        persona_status="approved", persona_verified_at=NOW - timedelta(days=2), reverified_at=NOW - timedelta(minutes=2),
        session_user_id="u_demo", pact_owner_id="u_demo", user_email=USER_EMAIL, month_committed=0.0,
        prior_claims=[], claim=None, demo_mode=True, demo_recipients=frozenset({SUPPORT, USER_EMAIL}),
    )
    base.update(o)
    return GateContext(**base)


def make_prop(action="price_adjust_email", **o):
    p = dict(proposal_id="prop_1", opportunity_id=19, line_item_id=1, store="mockstore", listing_id=77, action_type=action,
             email=None, rebuy=None, **{"return": None}, alert_at=None, reasoning="because")
    if action == "price_adjust_email":
        p["email"] = dict(to=SUPPORT, template="price_adjust_v1", thread_id=None)
    elif action == "claim_packet":
        p["email"] = dict(to=USER_EMAIL, template="packet_v1", thread_id=None)
    elif action == "claim_followup":
        p["email"] = dict(to=SUPPORT, template="proof_v1", thread_id="thr_1")
    elif action == "rebuy_return":
        p["rebuy"] = dict(listing_id=p["listing_id"]); p["return"] = dict(order_no="MS-55001")
    elif action == "deadline_alert":
        p["alert_at"] = "2026-12-02"
    p.update(o)
    return p


def lamp():
    item = _item(id=2, order_no="MS-55021", title="Nimbus Smart Lamp", sku="MS-2002", variant="White", category="home",
                 price_paid=239.00, order_date="2026-11-02", delivery_date="2026-11-04")
    listing = _listing(id=78, sku="MS-2002", variant="White", title="Nimbus Smart Lamp", price=199.00)
    ctx = make_ctx(item=item, listing=listing)
    p = make_prop("rebuy_return", line_item_id=2, listing_id=78)
    p["rebuy"] = dict(listing_id=78); p["return"] = dict(order_no="MS-55021")
    return p, ctx


@pytest.fixture
def pact():
    return make_pact()
