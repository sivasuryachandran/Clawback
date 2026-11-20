# STUB for rehearsal: replaced by Teammate C's real module (mockstore/) tomorrow
"""Just the parts of C's mock store that B's code touches:
GET  /mock/api/listings
POST /mock/api/rebuy_return {listing_id, original_order_no}  (both or neither)
GET  /mock/support  (text box that posts to /replies/inbound for the earbuds claim thread)
"""
import itertools

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from backend.agent import clock
from dev.stubs import db

router = APIRouter(prefix="/mock")
CALLS: list[dict] = []          # every successful rebuy_return, for tests
_counter = itertools.count(1)


@router.get("/api/listings")
def listings():
    out = []
    for l in db.query("SELECT * FROM listings WHERE store = 'mockstore'"):
        ph = db.query("SELECT price, seen_at FROM price_history WHERE listing_id = %s "
                      "ORDER BY seen_at DESC LIMIT 1", (l["id"],))
        out.append({k: l[k] for k in ("id", "store", "sku", "upc", "variant", "title", "url")}
                   | {"price": ph[0]["price"] if ph else None, "seen_at": ph[0]["seen_at"] if ph else None})
    return out


class RebuyIn(BaseModel):
    listing_id: int
    original_order_no: str
    idempotency_key: str | None = None   # same as C's real store: same key -> same result


_IDEM: dict[str, dict] = {}


@router.post("/api/rebuy_return")
def rebuy_return(body: RebuyIn):
    if body.idempotency_key and body.idempotency_key in _IDEM:
        return dict(_IDEM[body.idempotency_key], replayed=True)
    # Validate everything before doing anything, so it's both or neither.
    l = db.query("SELECT * FROM listings WHERE id = %s AND store = 'mockstore'", (body.listing_id,))
    o = db.query("SELECT * FROM orders WHERE order_no = %s AND store = 'mockstore'",
                 (body.original_order_no,))
    if not l or not o:
        raise HTTPException(400, "unknown listing or order: nothing bought, nothing returned")
    n = next(_counter)
    res = {"new_order_no": f"MS-RB-{n:04d}", "return_no": f"MS-RT-{n:04d}",
           "listing_id": body.listing_id, "original_order_no": body.original_order_no,
           "at": clock.ts()}
    CALLS.append(res)
    if body.idempotency_key:
        _IDEM[body.idempotency_key] = res
    return res


@router.get("/support", response_class=HTMLResponse)
def support_page():
    rows = db.query(
        "SELECT m.opportunity_id, m.thread_id FROM messages m "
        "JOIN opportunities o ON o.id = m.opportunity_id "
        "JOIN line_items li ON li.id = o.line_item_id WHERE li.sku = 'MS-1001' AND m.direction = 'out' ORDER BY m.id LIMIT 1")
    if not rows:
        return "<p>No earbuds claim thread yet. Run the earbuds claim first.</p>"
    opp, thread = rows[0]["opportunity_id"], rows[0]["thread_id"]
    return f"""<!doctype html><title>Mock Store support</title>
<h2>Mock Store support</h2><p>Reply on thread {thread}</p>
<textarea id=t rows=6 cols=60></textarea><br><button onclick="go()">Send reply</button>
<pre id=out></pre>
<script>
async function go(){{
  const r = await fetch('/replies/inbound', {{method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{opportunity_id:{opp}, thread_id:{thread!r}, reply_text:document.getElementById('t').value}})}});
  document.getElementById('out').textContent = JSON.stringify(await r.json(), null, 2);
}}
</script>"""
