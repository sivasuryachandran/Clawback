"""tick() / on_price_change() (section 3.4 contract) and opportunity creation.

Pulls current prices from C's mock store (GET /mock/api/listings) plus whatever cached
real-store prices A seeded, writes price_history, and turns a drop or a closing return
window into an `opportunities` row. One open opportunity per line item.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta

import httpx

from backend.agent import clock
from backend.analytics.events import track
from backend.db import execute, query
from backend.watch.matcher import match

MOCK_STORE_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
DEADLINE_WINDOW_DAYS = 7


def _now_iso() -> str:
    return clock.ts()


def _date(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()


def _open_opportunity(line_item_id: int) -> dict | None:
    rows = query(
        "SELECT * FROM opportunities WHERE line_item_id=%s AND status='open'",
        (line_item_id,),
    )
    return rows[0] if rows else None


def _upsert_price_drop(line_item_id: int, candidate: dict) -> int | None:
    item = query("SELECT * FROM line_items WHERE id=%s", (line_item_id,))[0]
    price = candidate.get("price")
    price_paid = float(item["price_paid"])
    if price is None or float(price) >= price_paid:
        return None
    drop = round(price_paid - float(price), 2)
    existing = _open_opportunity(line_item_id)
    if existing:
        execute(
            "UPDATE opportunities SET listing_id=%s, kind='price_drop', drop_usd=%s, "
            "similarity=%s, rerank_score=%s WHERE id=%s",
            (candidate["listing_id"], drop, candidate["similarity"], candidate["rerank_score"], existing["id"]),
        )
        return existing["id"]
    opp_id = execute(
        "INSERT INTO opportunities (line_item_id, listing_id, kind, drop_usd, similarity, "
        "rerank_score, created_at, status) VALUES (%s,%s,'price_drop',%s,%s,%s,%s,'open')",
        (line_item_id, candidate["listing_id"], drop, candidate["similarity"], candidate["rerank_score"], _now_iso()),
    )
    track("opportunity_found", {"kind": "price_drop", "drop_usd": drop, "store": candidate.get("store")})
    return opp_id


def check_line_item_for_drop(line_item_id: int) -> int | None:
    """Walks the ranked candidates (best vector match first) and opens/updates the
    opportunity on the first one that's actually cheaper than what was paid -- a
    same-SKU candidate with no price change isn't an opportunity, even if it outranks
    a cheaper, non-exact candidate on similarity. Identity (R5) is still the gate's
    call, not ours: a non-exact candidate is used exactly the same as an exact one."""
    item = query("SELECT price_paid FROM line_items WHERE id=%s", (line_item_id,))[0]
    price_paid = float(item["price_paid"])
    for candidate in match(line_item_id):
        if candidate.get("price") is not None and float(candidate["price"]) < price_paid:
            return _upsert_price_drop(line_item_id, candidate)
    return None


def check_deadline(line_item_id: int) -> int | None:
    """kind=deadline when the return window ends within DEADLINE_WINDOW_DAYS and no
    price-drop opportunity already covers this line item."""
    if _open_opportunity(line_item_id):
        return None
    item = query("SELECT * FROM line_items WHERE id=%s", (line_item_id,))[0]
    orders = query("SELECT * FROM orders WHERE id=%s", (item["order_id"],))
    policies = query("SELECT * FROM store_policies WHERE store=%s", (item["store"],))
    if not orders or not policies:
        return None
    order, policy = orders[0], policies[0]
    starts_at_delivery = policy.get("return_window_starts") == "delivery" and order.get("delivery_date")
    start = _date(order["delivery_date"]) if starts_at_delivery else _date(order["order_date"])
    if not start or policy.get("return_window_days") is None:
        return None
    end = start + timedelta(days=int(policy["return_window_days"]))
    days_left = (end - clock.today()).days
    if 0 <= days_left <= DEADLINE_WINDOW_DAYS:
        opp_id = execute(
            "INSERT INTO opportunities (line_item_id, listing_id, kind, drop_usd, "
            "similarity, rerank_score, created_at, status) "
            "VALUES (%s,NULL,'deadline',NULL,NULL,NULL,%s,'open')",
            (line_item_id, _now_iso()),
        )
        track("opportunity_found", {"kind": "deadline", "store": item.get("store")})
        return opp_id
    return None


def on_price_change(listing_id: int, price: float, refresh: bool = False) -> list[int]:
    """`refresh=True` (the mock store's "Refresh prices" / reset re-announcement, section
    4 of docs/C_CONTRACTS.md) means the price didn't actually move -- record it so R8a's
    15-minute freshness check stays true, but don't re-run opportunity creation for it."""
    execute(
        "INSERT INTO price_history (listing_id, price, seen_at) VALUES (%s,%s,%s)",
        (listing_id, price, _now_iso()),
    )
    if refresh:
        return []
    touched: list[int] = []
    watching = query("SELECT id FROM line_items WHERE status='watching'", ())
    for li in watching:
        candidates = match(li["id"])
        if any(c["listing_id"] == listing_id for c in candidates):
            opp_id = check_line_item_for_drop(li["id"])
            if opp_id:
                touched.append(opp_id)
    return touched


def _pull_mock_store_prices() -> list[int]:
    touched: list[int] = []
    try:
        resp = httpx.get(f"{MOCK_STORE_URL}/mock/api/listings", timeout=10)
        resp.raise_for_status()
    except Exception:
        return touched  # mock store not up yet; tick() still runs the deadline scan
    for row in resp.json():
        # Mock Store listing ids are stable and must be used verbatim (docs/C_CONTRACTS.md
        # section 2); fall back to a store/sku/variant lookup for anything else (real-store
        # listings A seeded under their own auto ids).
        listing_rows = query("SELECT id FROM listings WHERE id=%s", (row["id"],)) or query(
            "SELECT id FROM listings WHERE store=%s AND sku=%s AND variant=%s",
            (row["store"], row["sku"], row.get("variant")),
        )
        if not listing_rows:
            continue
        touched += on_price_change(listing_rows[0]["id"], row["price"])
    return touched


def tick() -> list[int]:
    touched = _pull_mock_store_prices()
    watching = query("SELECT id FROM line_items WHERE status='watching'", ())
    for li in watching:
        opp_id = check_deadline(li["id"])
        if opp_id:
            touched.append(opp_id)
    return touched
