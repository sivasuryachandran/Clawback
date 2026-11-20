"""tests/test_matcher.py (A owns this per section 1). Runs against the local SQLite
fallback (no SINGLESTORE_URL needed) with the offline hashed embedding fallback (no
SIE_API_KEY needed), so it runs anywhere with just `pytest`.

Covers section 2's milestone-5 cases:
  - earbuds: same store + same SKU + same variant -> exact
  - different SKUs, same UPC + variant, across stores -> exact
  - Blue backpack ranks as the top vector candidate for the Black receipt, but isn't
    exact (variant differs) -- the matcher only suggests; identity is the gate's job.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import db  # noqa: E402
from backend.watch.matcher import is_exact_match, match  # noqa: E402
from backend.watch.price_watch import check_line_item_for_drop  # noqa: E402


@pytest.fixture(autouse=True)
def sqlite_db(tmp_path, monkeypatch):
    monkeypatch.delenv("SINGLESTORE_URL", raising=False)
    monkeypatch.setenv("CLAWBACK_DB", str(tmp_path / "test.db"))
    db.init_schema()
    yield db


def _insert_line_item(store, title, sku, upc, model_number, variant, category, price_paid, order_id=1):
    return db.execute(
        "INSERT INTO line_items (order_id, store, title, sku, upc, model_number, variant, "
        "category, price_paid, qty, parse_confidence, status) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,1,0.95,'watching')",
        (order_id, store, title, sku, upc, model_number, variant, category, price_paid),
    )


def _insert_listing(store, title, sku, upc, model_number, variant, price):
    listing_id = db.execute(
        "INSERT INTO listings (store, sku, upc, model_number, variant, url, title) "
        "VALUES (%s,%s,%s,%s,%s,'/x',%s)",
        (store, sku, upc, model_number, variant, title),
    )
    db.execute(
        "INSERT INTO price_history (listing_id, price, seen_at) VALUES (%s,%s,'2026-11-20 10:00:00')",
        (listing_id, price),
    )
    return listing_id


def test_exact_match_same_store_sku_variant():
    item = {"store": "mockstore", "sku": "MS-1001", "upc": "U1", "model_number": None, "variant": "Black"}
    candidate = {"store": "mockstore", "sku": "MS-1001", "upc": "U1", "model_number": None, "variant": "Black"}
    assert is_exact_match(item, candidate) is True


def test_exact_match_cross_store_same_upc_and_variant_different_sku():
    item = {"store": "mockstore", "sku": "MS-9", "upc": "UPC-123", "model_number": None, "variant": "Red"}
    candidate = {"store": "target", "sku": "TGT-9", "upc": "UPC-123", "model_number": None, "variant": "Red"}
    assert is_exact_match(item, candidate) is True


def test_not_exact_when_variant_differs():
    item = {"store": "mockstore", "sku": "MS-3003", "upc": "U3", "model_number": None, "variant": "Black"}
    candidate = {"store": "mockstore", "sku": "MS-3003-BLU", "upc": "U3B", "model_number": None, "variant": "Blue"}
    assert is_exact_match(item, candidate) is False


def test_match_top_candidate_can_be_non_exact():
    # Item title matches the Blue listing's title exactly; the Black (exact-SKU)
    # listing's title carries extra decorative words, diluting its similarity.
    # The matcher must still rank Blue first: it only suggests, it doesn't filter by
    # identity.
    li_id = _insert_line_item(
        "mockstore", "Trailhead Backpack", "MS-3003", "U3", None, "Black", "outdoor", 89.00
    )
    black_id = _insert_listing(
        "mockstore", "Trailhead Backpack Black Edition New", "MS-3003", "U3", None, "Black", 89.00
    )
    blue_id = _insert_listing("mockstore", "Trailhead Backpack", "MS-3003-BLU", "U3B", None, "Blue", 64.00)

    candidates = match(li_id)
    assert candidates[0]["listing_id"] == blue_id
    assert candidates[0]["exact"] is False
    assert any(c["listing_id"] == black_id and c["exact"] for c in candidates)


def test_opportunity_uses_the_cheaper_non_exact_candidate_not_the_exact_no_drop_one():
    li_id = _insert_line_item(
        "mockstore", "Trailhead Backpack", "MS-3003", "U3", None, "Black", "outdoor", 89.00
    )
    _insert_listing("mockstore", "Trailhead Backpack Black Edition New", "MS-3003", "U3", None, "Black", 89.00)
    blue_id = _insert_listing("mockstore", "Trailhead Backpack", "MS-3003-BLU", "U3B", None, "Blue", 64.00)

    opp_id = check_line_item_for_drop(li_id)
    assert opp_id is not None
    opp = db.query("SELECT * FROM opportunities WHERE id=%s", (opp_id,))[0]
    assert opp["listing_id"] == blue_id
    assert opp["kind"] == "price_drop"
    assert round(float(opp["drop_usd"]), 2) == 25.00


def test_no_opportunity_when_no_listing_is_cheaper():
    li_id = _insert_line_item(
        "mockstore", "Breeze Desk Fan", "MS-4004", "U4", None, "Gray", "clearance", 59.00
    )
    _insert_listing("mockstore", "Breeze Desk Fan", "MS-4004", "U4", None, "Gray", 59.00)
    assert check_line_item_for_drop(li_id) is None
