"""rebuy_return(decision, listing_id, original_order_no) -> result text

Exactly one call to the MOCK store's POST /mock/api/rebuy_return (buy + return, both or
neither). Never a real store.
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


def rebuy_return(decision, listing_id: int, original_order_no: str) -> str:
    if getattr(decision, "decision", decision) != "ALLOW":
        raise RebuyRefused("rebuy needs an ALLOW decision")
    rows = db.query("SELECT store FROM listings WHERE id = %s", (listing_id,))
    if not rows or rows[0]["store"] != "mockstore":
        raise RebuyRefused("rebuy only ever runs on the mock store")
    r = _http().post("/mock/api/rebuy_return",
                     json={"listing_id": listing_id, "original_order_no": original_order_no})
    r.raise_for_status()
    return json.dumps(r.json())
