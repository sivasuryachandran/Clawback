"""rebuy_return(decision, listing_id, original_order_no, max_price_usd=None) -> result text

Exactly one call to the MOCK store's POST /mock/api/rebuy_return (buy + return, both or
neither). Never a real store. (3.4 contract: the first three arguments; max_price_usd is an
optional extra.)

Price guard (found on event day): the gate approves using the DB's latest price, but the store
charges ITS current price. If they disagree (e.g. the store's "Demo preset" was never pressed, so
the lamp is still $239 while the DB says $199), we refuse BEFORE buying: nothing is bought,
execute_once marks the action failed, and the card says it didn't go through.
The proposal hash is sent as idempotency_key, so a retry can never buy twice.
"""
import json
import os

import httpx

from backend.agent.deps import db

_client: httpx.Client | None = None


def set_http_client(c: httpx.Client | None) -> None:
    """Tests and run_demo pass a FastAPI TestClient (an httpx.Client) here."""
    global _client
    _client = c


def _http() -> httpx.Client:
    return _client or httpx.Client(base_url=os.environ.get("CLAWBACK_BASE_URL", "http://localhost:8000"),
                                   timeout=10)


class RebuyRefused(Exception):
    pass


def _store_price(http: httpx.Client, listing_id: int) -> float | None:
    r = http.get("/mock/api/listings")
    r.raise_for_status()
    for l in r.json():
        if (l.get("id") if l.get("id") is not None else l.get("listing_id")) == listing_id:
            return float(l["price"]) if l.get("price") is not None else None
    return None


def rebuy_return(decision, listing_id: int, original_order_no: str,
                 max_price_usd: float | None = None) -> str:
    if getattr(decision, "decision", decision) != "ALLOW":
        raise RebuyRefused("rebuy needs an ALLOW decision")
    rows = db.query("SELECT store FROM listings WHERE id = %s", (listing_id,))
    if not rows or rows[0]["store"] != "mockstore":
        raise RebuyRefused("rebuy only ever runs on the mock store")
    http = _http()
    if max_price_usd is not None:
        live = _store_price(http, listing_id)
        if live is None:
            raise RebuyRefused(f"the store has no price for listing {listing_id}; nothing was bought")
        if live > float(max_price_usd) + 0.005:
            raise RebuyRefused(f"the store now charges ${live:.2f}, but ${float(max_price_usd):.2f} was "
                               "approved; nothing was bought")
    body = {"listing_id": listing_id, "original_order_no": original_order_no}
    key = getattr(decision, "proposal_hash", None)
    if key:
        body["idempotency_key"] = key
    r = http.post("/mock/api/rebuy_return", json=body)
    r.raise_for_status()
    return json.dumps(r.json())
