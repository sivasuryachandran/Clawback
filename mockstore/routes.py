"""Mock Store routes, mounted at /mock on the main app (section 3.7: GET/POST /mock/*)."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from . import pages, store, support, webhook

router = APIRouter(prefix="/mock", tags=["mockstore"])


def _fail(e: store.StoreError):
    raise HTTPException(status_code=e.status, detail={"code": e.code, "message": e.message})


def _html(fn, *a):
    with store._lock:
        store._ensure()
        out = fn(*a)
    if out is None:
        raise HTTPException(404, "Not found")
    return HTMLResponse(out)


# ---- pages ---------------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse, include_in_schema=False)
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def home():
    return _html(pages.home)


@router.get("/p/{sku}", response_class=HTMLResponse, include_in_schema=False)
def product(sku: str, variant: str | None = None):
    return _html(pages.product, sku, variant)


@router.get("/policy", response_class=HTMLResponse, include_in_schema=False)
def policy_page():
    return _html(pages.policy)


@router.get("/admin", response_class=HTMLResponse, include_in_schema=False)
def admin_page():
    return _html(pages.admin)


@router.get("/support", response_class=HTMLResponse, include_in_schema=False)
def support_page():
    return _html(pages.support)


# ---- API: listings and prices ---------------------------------------------------------------

class PriceIn(BaseModel):
    listing_id: int
    price: float = Field(..., description="New price in dollars")


class DropIn(BaseModel):
    listing_id: int
    pct: float = Field(-25, description="-25 drops 25%, 25 raises 25%")


class WindowIn(BaseModel):
    days: int


class RebuyIn(BaseModel):
    listing_id: int
    original_order_no: str
    idempotency_key: str | None = None


class SupportIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)


@router.get("/api/listings")
def api_listings():
    return store.list_listings()


@router.get("/api/listings/{listing_id}")
def api_listing(listing_id: int):
    try:
        l = store.get_listing(listing_id)
        return store.public_listing(l)
    except store.StoreError as e:
        _fail(e)


def _changed(change, bg: BackgroundTasks | None = None):
    if change:
        webhook.dispatch([change])
    return {"changed": bool(change), "listing": (change or {}).get("listing")}


@router.post("/api/price")
def api_price(body: PriceIn):
    try:
        return _changed(store.set_price(body.listing_id, body.price))
    except store.StoreError as e:
        _fail(e)


@router.post("/api/drop")
def api_drop(body: DropIn):
    try:
        return _changed(store.scale_price(body.listing_id, body.pct))
    except store.StoreError as e:
        _fail(e)


@router.post("/api/preset")
def api_preset():
    changes = store.apply_preset()
    webhook.dispatch(changes)
    return {"changed": len(changes), "listings": [c["listing"] for c in changes]}


@router.post("/api/prestage")
def api_prestage():
    """Stage lamp, Blue backpack and fan; leave the earbuds at their seed price for the live drop."""
    changes = store.apply_preset(exclude=(store.EARBUDS_ID,))
    webhook.dispatch(changes)
    return {"changed": len(changes), "listings": [c["listing"] for c in changes]}


@router.post("/api/resend")
def api_resend():
    """Re-announce every current price (nothing changed). Keeps price_history fresh before an approval:
    the gate's R8a stops a price not seen in the last 15 minutes."""
    changes = store.current_prices()
    webhook.dispatch(changes, refresh=True)
    return {"sent": len(changes)}


@router.post("/api/reset")
def api_reset():
    changes = store.reset_state()
    webhook.dispatch(changes)
    return {"reset": True, "changed": len(changes)}


# ---- API: policy -----------------------------------------------------------------------------

@router.get("/api/policy")
def api_policy():
    return store.policy()


@router.post("/api/policy-window")
def api_policy_window(body: WindowIn):
    try:
        return store.set_adjust_window(body.days)
    except store.StoreError as e:
        _fail(e)


# ---- API: orders, rebuy + return ----------------------------------------------------------------

@router.get("/api/orders")
def api_orders():
    return store.list_orders()


@router.get("/api/orders/{order_no}")
def api_order(order_no: str):
    try:
        return store.get_order(order_no)
    except store.StoreError as e:
        _fail(e)


@router.post("/api/rebuy_return")
def api_rebuy_return(body: RebuyIn):
    """Buy again and return the original in one step. Both happen or neither (section 3.7)."""
    try:
        return store.rebuy_return(body.listing_id, body.original_order_no, body.idempotency_key)
    except store.StoreError as e:
        _fail(e)


# ---- API: webhook log, support box ------------------------------------------------------------------

@router.get("/api/webhook-log")
def api_webhook_log():
    return {"backend_url": webhook.backend_url(), "deliveries": webhook.recent()}


@router.post("/api/support")
async def api_support(body: SupportIn, request: Request, bg: BackgroundTasks):
    text = body.text.strip()
    if not text:
        raise HTTPException(422, "Message is empty")
    if not support.allow_message():     # every message costs an LLM call and maybe an email: don't let a loop run away
        raise HTTPException(429, "You're sending messages too fast. Please wait a moment.")
    conv = support.resolve_conversation()
    bg.add_task(support.forward, request.app, text, conv)   # the reply handler may be slow (LLM + email)
    return {"accepted": True}


@router.get("/api/support-log")
def api_support_log():
    return {"messages": support.recent()}
