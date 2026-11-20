"""Mock Store state and rules (Teammate C).

Everything lives in memory (section 3.8: "Prices in memory"). Reset restores the seed.
Money is Decimal inside and a plain number at the edges.

Seed rows are exactly section 3.8 rows 1-4:
  1 Aurora Wireless Earbuds  MS-1001 Black  electronics  $129.99
  2 Nimbus Smart Lamp        MS-2002 White  home         $239.00
  3 Trailhead Backpack       MS-3003 Black  outdoor      $89.00   (+ the Blue one, same SKU)
  4 Breeze Desk Fan          MS-4004 Gray   clearance    $59.00

Listing ids are STABLE (1-5). Teammate A must insert them into `listings` with these explicit
ids, because the price webhook and rebuy_return both speak in listing ids.
"""
from __future__ import annotations

import copy
import os
import threading
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

STORE = "mockstore"
TWO = Decimal("0.01")
MAX_PRICE = Decimal("100000")

# --- seed ---------------------------------------------------------------------------------

SEED_LISTINGS = [
    dict(id=1, sku="MS-1001", variant="Black", title="Aurora Wireless Earbuds", category="electronics",
         price="129.99", upc="850001000011", model_number="AUR-EB1", icon="🎧",
         blurb="Bluetooth 5.3 earbuds with 28-hour battery life and a pocket-size charging case."),
    dict(id=2, sku="MS-2002", variant="White", title="Nimbus Smart Lamp", category="home",
         price="239.00", upc="850001000028", model_number="NIM-LMP2", icon="💡",
         blurb="Voice-controlled lamp with 16 million colors and a sunrise alarm."),
    dict(id=3, sku="MS-3003", variant="Black", title="Trailhead Backpack", category="outdoor",
         price="89.00", upc="850001000035", model_number="TRL-BP3", icon="🎒",
         blurb="28-liter daypack with a padded laptop sleeve and rain cover."),
    dict(id=4, sku="MS-3003", variant="Blue", title="Trailhead Backpack", category="outdoor",
         price="89.00", upc="850001000042", model_number="TRL-BP3", icon="🎒",
         blurb="28-liter daypack with a padded laptop sleeve and rain cover."),
    dict(id=5, sku="MS-4004", variant="Gray", title="Breeze Desk Fan", category="clearance",
         price="59.00", upc="850001000059", model_number="BRZ-FAN4", icon="🌀",
         blurb="Quiet three-speed desk fan. Clearance: last season's model."),
]

# "Demo preset" (section 3.8 / section 4.4): earbuds $95.99, lamp $199.00, Blue backpack $64.00, fan $39.00
DEMO_PRESET = {1: "95.99", 2: "199.00", 4: "64.00", 5: "39.00"}
EARBUDS_ID = 1   # the live drop on stage (section "90-second script", 25-45 s)

SEED_ORDERS = [
    dict(order_no="MS-55021", order_date="2026-11-02", delivery_date="2026-11-04",
         items=[dict(listing_id=2, qty=1, price_paid="239.00")]),
    dict(order_no="MS-55029", order_date="2026-11-10", delivery_date="2026-11-12",
         items=[dict(listing_id=5, qty=1, price_paid="59.00")]),
    dict(order_no="MS-55034", order_date="2026-11-11", delivery_date="2026-11-13",
         items=[dict(listing_id=1, qty=1, price_paid="129.99")]),
    dict(order_no="MS-55041", order_date="2026-11-12", delivery_date="2026-11-14",
         items=[dict(listing_id=3, qty=1, price_paid="89.00")]),
]

DEFAULT_ADJUST_DAYS = 14
RETURN_DAYS = 30
FINAL_SALE_CATEGORIES = ("clearance",)
NEW_ORDER_START = 56001
RETURN_START = 70001


class StoreError(Exception):
    """A business-rule refusal. `status` is the HTTP code, `code` a stable machine string."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def money(v) -> Decimal:
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        raise StoreError(422, "bad_price", "Price must be a number.")
    if not d.is_finite():
        raise StoreError(422, "bad_price", "Price must be a number.")
    d = d.quantize(TWO, rounding=ROUND_HALF_UP)
    if d <= 0 or d > MAX_PRICE:
        raise StoreError(422, "bad_price", "Price must be above $0 and at most $100,000.")
    return d


def num(d: Decimal) -> float:
    return float(d)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def demo_today() -> date:
    raw = os.environ.get("DEMO_TODAY", "").strip()
    try:
        return date.fromisoformat(raw) if raw else date(2026, 11, 20)
    except ValueError:
        return date(2026, 11, 20)


# --- state --------------------------------------------------------------------------------

_lock = threading.RLock()
_state: dict = {}
_fail_hook = {"after_buy": False}   # test-only: make the next rebuy fail between buy and return


def _fresh_state() -> dict:
    listings = {}
    for row in SEED_LISTINGS:
        r = copy.deepcopy(row)
        r["price"] = Decimal(r["price"])
        r["seed_price"] = r["price"]
        r["changed_at"] = now_utc()
        listings[r["id"]] = r
    orders = {}
    for o in SEED_ORDERS:
        orders[o["order_no"]] = dict(
            order_no=o["order_no"], order_date=o["order_date"], delivery_date=o["delivery_date"],
            items=[dict(i, price_paid=Decimal(i["price_paid"])) for i in o["items"]],
            returned=None, kind="seed")
    return dict(listings=listings, orders=orders, next_order=NEW_ORDER_START, next_return=RETURN_START,
                idem={}, adjust_days=DEFAULT_ADJUST_DAYS, policy_revision=1,
                policy_updated_at=now_utc())


def _ensure():
    if not _state:
        _state.update(_fresh_state())


def reset_state() -> list[dict]:
    """Restore the seed. Returns [{listing, old, new}] for every listing whose price moved,
    so the caller can tell the watcher (otherwise a stale low price stays in price_history)."""
    with _lock:
        _ensure()
        old = {i: l["price"] for i, l in _state["listings"].items()}
        _state.clear()
        _state.update(_fresh_state())
        return [dict(listing=public_listing(l), old=old[i], new=l["price"])
                for i, l in _state["listings"].items() if old[i] != l["price"]]


# --- listings ------------------------------------------------------------------------------

def public_listing(l: dict, base: str = "/mock") -> dict:
    """Shape of GET /mock/api/listings rows (section 3.8 / section 4.4)."""
    qs = f"?variant={l['variant']}"
    return dict(
        id=l["id"], store=STORE, sku=l["sku"], upc=l["upc"], model_number=l["model_number"],
        variant=l["variant"], title=l["title"], category=l["category"],
        url=f"{base}/p/{l['sku']}{qs}", price=num(l["price"]),
        seen_at=iso(now_utc()),   # the store is live: every read is "seen now"
    )


def list_listings() -> list[dict]:
    with _lock:
        _ensure()
        return [public_listing(l) for l in _state["listings"].values()]


def get_listing(listing_id: int) -> dict:
    with _lock:
        _ensure()
        l = _state["listings"].get(listing_id)
        if not l:
            raise StoreError(404, "unknown_listing", f"No listing {listing_id}.")
        return l


def listings_for_sku(sku: str) -> list[dict]:
    with _lock:
        _ensure()
        return [l for l in _state["listings"].values() if l["sku"].lower() == sku.lower()]


def set_price(listing_id: int, price) -> dict | None:
    """Set a price. Returns the change record, or None when the price did not move."""
    new = money(price)
    with _lock:
        _ensure()
        l = get_listing(listing_id)
        old = l["price"]
        if new == old:
            return None
        l["price"] = new
        l["changed_at"] = now_utc()
        return dict(listing=public_listing(l), old=old, new=new)


def scale_price(listing_id: int, pct: float) -> dict | None:
    """pct=-25 drops the price 25%; pct=25 raises it 25%."""
    if not (-90 <= float(pct) <= 300):
        raise StoreError(422, "bad_pct", "Percent must be between -90 and 300.")
    with _lock:
        l = get_listing(listing_id)
        new = (l["price"] * (Decimal(100) + Decimal(str(pct))) / Decimal(100))
        return set_price(listing_id, new)


def current_prices() -> list[dict]:
    """Every listing at its CURRENT price as a change record with old == new. Used to re-announce prices
    so the watcher's price_history stays fresh (the gate's R8a wants a price seen in the last 15 minutes)."""
    with _lock:
        _ensure()
        return [dict(listing=public_listing(l), old=l["price"], new=l["price"]) for l in _state["listings"].values()]


def apply_preset(exclude: tuple[int, ...] = ()) -> list[dict]:
    """The demo preset. `exclude` lets the presenter stage everything except the one drop they do live."""
    changes = []
    with _lock:
        for lid, p in DEMO_PRESET.items():
            if lid in exclude:
                continue
            c = set_price(lid, p)
            if c:
                changes.append(c)
    return changes


# --- policy --------------------------------------------------------------------------------

def policy() -> dict:
    with _lock:
        _ensure()
        d = _state["adjust_days"]
        sentences = [
            f"Price adjustments apply to the identical item within {d} days of purchase.",
            "Mock Store only adjusts to its own prices; it does not match competitors.",
            f"Returns are accepted within {RETURN_DAYS} days of delivery.",
            "Returns are free and there is no restocking fee.",
            "Items in the Clearance category are final sale and cannot be returned or adjusted.",
            "To request a price adjustment, email Mock Store support with your order number. "
            "If we ask for proof, reply in the same conversation with your receipt and the new price.",
        ]
        return dict(store=STORE, revision=_state["policy_revision"], adjust_window_days=d,
                    adjust_window_starts="purchase", return_window_days=RETURN_DAYS,
                    return_window_starts="delivery", final_sale_categories=list(FINAL_SALE_CATEGORIES),
                    restocking_fee_pct=0.0, return_shipping_usd=0.0, competitor_match_allowed=False,
                    claim_channel="email", sentences=sentences, text=" ".join(sentences),
                    updated_at=iso(_state["policy_updated_at"]))


def set_adjust_window(days: int) -> dict:
    if not (1 <= int(days) <= 90):
        raise StoreError(422, "bad_days", "Window must be between 1 and 90 days.")
    with _lock:
        _ensure()
        if _state["adjust_days"] != int(days):
            _state["adjust_days"] = int(days)
            _state["policy_revision"] += 1
            _state["policy_updated_at"] = now_utc()
        return policy()


# --- orders --------------------------------------------------------------------------------

def _order_json(o: dict) -> dict:
    items = []
    for it in o["items"]:
        l = _state["listings"][it["listing_id"]]
        items.append(dict(title=l["title"], sku=l["sku"], upc=l["upc"], model_number=l["model_number"],
                          variant=l["variant"], category=l["category"], qty=it["qty"],
                          price_paid=num(it["price_paid"])))
    return dict(store=STORE, order_no=o["order_no"], order_date=o["order_date"],
                delivery_date=o["delivery_date"], items=items, status=("returned" if o["returned"] else "open"),
                return_id=(o["returned"] or {}).get("return_id"), kind=o["kind"])


def get_order(order_no: str) -> dict:
    with _lock:
        _ensure()
        o = _state["orders"].get(order_no.strip().upper())
        if not o:
            raise StoreError(404, "unknown_order", f"No order {order_no}.")
        return _order_json(o)


def list_orders() -> list[dict]:
    with _lock:
        _ensure()
        return [_order_json(o) for o in _state["orders"].values()]


# --- rebuy + return, all or nothing -------------------------------------------------------------

def _returnable(o: dict) -> tuple[bool, str]:
    for it in o["items"]:
        l = _state["listings"][it["listing_id"]]
        if l["category"] in FINAL_SALE_CATEGORIES:
            return False, "final_sale"
    if not o["delivery_date"]:
        return False, "not_delivered"
    ends = date.fromisoformat(o["delivery_date"]) + timedelta(days=RETURN_DAYS)
    if demo_today() > ends:
        return False, "window_closed"
    return True, ""


def _result(new_order: dict, o: dict, listing: dict, replayed: bool) -> dict:
    refund = sum((i["price_paid"] * i["qty"] for i in o["items"]), Decimal(0))
    price = new_order["items"][0]["price_paid"]
    return dict(ok=True, replayed=replayed, listing_id=listing["id"], original_order_no=o["order_no"],
                new_order_no=new_order["order_no"], return_id=o["returned"]["return_id"],
                price_paid=num(price), refund_usd=num(refund), net_savings_usd=num(refund - price))


def rebuy_return(listing_id: int, original_order_no: str, idempotency_key: str | None = None) -> dict:
    """Buy the new item AND start the return of the old one in one step.
    If either half fails, neither happens. The same call twice returns the same result."""
    with _lock:
        _ensure()
        key = (idempotency_key or "").strip() or None
        if key and key in _state["idem"]:
            return dict(_state["idem"][key], replayed=True)

        order_no = (original_order_no or "").strip().upper()
        o = _state["orders"].get(order_no)
        if not o:
            raise StoreError(400, "unknown_order", f"No order {original_order_no}. Nothing was bought.")
        listing = _state["listings"].get(listing_id)
        if not listing:
            raise StoreError(400, "unknown_listing", f"No listing {listing_id}. Nothing was bought.")

        if o["returned"]:
            prior = _state["orders"].get(o["returned"]["rebuy_order_no"])
            if prior and o["returned"]["rebuy_listing_id"] == listing_id:
                return _result(prior, o, listing, replayed=True)   # same call again: same answer
            raise StoreError(409, "already_returned", f"Order {order_no} was already returned.")

        ok, why = _returnable(o)
        if not ok:
            msg = {"final_sale": "Clearance items are final sale.",
                   "window_closed": f"The {RETURN_DAYS}-day return window has closed.",
                   "not_delivered": "That order has not been delivered yet."}[why]
            raise StoreError(409, why, f"{msg} Nothing was bought.")

        # Transaction: apply the purchase, then the return; undo the purchase if the return fails.
        new_no = f"MS-{_state['next_order']}"
        ret_id = f"RMA-{_state['next_return']}"
        bought = False
        try:
            _state["orders"][new_no] = dict(
                order_no=new_no, order_date=demo_today().isoformat(), delivery_date=None,
                items=[dict(listing_id=listing_id, qty=1, price_paid=listing["price"])],
                returned=None, kind="rebuy")
            _state["next_order"] += 1
            bought = True
            if _fail_hook["after_buy"]:
                _fail_hook["after_buy"] = False
                raise RuntimeError("simulated failure after the purchase")
            o["returned"] = dict(return_id=ret_id, rebuy_order_no=new_no, rebuy_listing_id=listing_id,
                                 started_at=iso(now_utc()))
            _state["next_return"] += 1
        except Exception as exc:
            if bought:
                _state["orders"].pop(new_no, None)
                _state["next_order"] -= 1
            o["returned"] = None
            raise StoreError(500, "rolled_back", f"Something went wrong, so nothing was bought or returned ({exc}).")

        res = _result(_state["orders"][new_no], o, listing, replayed=False)
        if key:
            _state["idem"][key] = res
        return res
