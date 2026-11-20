"""Persona sandbox flow. Stores only ids, status and timestamps. Fails closed.

ASSUMPTIONS (check against Persona's current docs at the event):
- Create inquiry: POST https://withpersona.com/api/v1/inquiries, Bearer PERSONA_API_KEY,
  body {"data":{"attributes":{"inquiry-template-id":..., "reference-id":<user id>}}}.
- Resume/session token: POST /inquiries/{id}/resume -> meta["session-token"] for the embedded client.
- Webhook payload: data.attributes.payload.data.id is the inquiry id; we ignore the claimed
  status and re-fetch GET /inquiries/{id}; data.attributes.status in {"approved","completed"} counts as verified.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

import httpx
from fastapi import APIRouter, HTTPException, Request

from .webhook import verify_persona_signature

router = APIRouter()
BASE = "https://withpersona.com/api/v1"
OK_STATUSES = {"approved", "completed"}


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _headers() -> dict:
    return {"Authorization": f"Bearer {os.environ['PERSONA_API_KEY']}", "Persona-Version": "2023-01-05", "Content-Type": "application/json"}


def fetch_inquiry_status(inquiry_id: str) -> str:
    r = httpx.get(f"{BASE}/inquiries/{inquiry_id}", headers=_headers(), timeout=15)
    r.raise_for_status()
    return str(r.json()["data"]["attributes"]["status"]).lower()


def _db():
    from backend import db
    return db


def user_id_from(request: Request) -> str:
    """Demo login: the signed-in user comes from X-User-Id (default u_demo)."""
    return request.headers.get("x-user-id") or "u_demo"


@router.post("/persona/inquiry")
def create_inquiry(request: Request):
    uid = user_id_from(request)
    body = {"data": {"attributes": {"inquiry-template-id": os.environ["PERSONA_TEMPLATE_ID"], "reference-id": uid}}}
    r = httpx.post(f"{BASE}/inquiries", headers=_headers(), json=body, timeout=15)
    if r.status_code >= 300:
        raise HTTPException(502, "Could not start verification. Nothing was changed.")
    data = r.json()
    inquiry_id = data["data"]["id"]
    _db().execute("UPDATE users SET persona_inquiry_id=%s, persona_status='pending' WHERE id=%s", (inquiry_id, uid))
    return {"inquiry_id": inquiry_id, "template_id": os.environ["PERSONA_TEMPLATE_ID"], "reference_id": uid,
            "session_token": (data.get("meta") or {}).get("session-token")}


@router.post("/persona/webhook")
async def webhook(request: Request):
    raw = await request.body()
    if not verify_persona_signature(raw, request.headers.get("persona-signature"), os.environ.get("PERSONA_WEBHOOK_SECRET", "")):
        raise HTTPException(401, "bad signature")
    try:
        inquiry_id = json.loads(raw)["data"]["attributes"]["payload"]["data"]["id"]
    except Exception:
        raise HTTPException(400, "unrecognised payload")
    rows = _db().query("SELECT id FROM users WHERE persona_inquiry_id=%s", (inquiry_id,))
    if not rows:
        return {"ok": True, "ignored": True}
    status = fetch_inquiry_status(inquiry_id)          # trust the server-side fetch, not the webhook body
    if status in OK_STATUSES:
        _db().execute("UPDATE users SET persona_status='approved', verified_at=%s, reverified_at=%s WHERE id=%s", (_now(), _now(), rows[0]["id"]))
    else:
        _db().execute("UPDATE users SET persona_status=%s WHERE id=%s", ("declined" if status in {"declined", "failed"} else "pending", rows[0]["id"]))
    return {"ok": True, "status": status}


@router.post("/persona/recheck")
def recheck(request: Request):
    """Fresh server-side check of the user's inquiry; sets reverified_at only if still approved."""
    uid = user_id_from(request)
    rows = _db().query("SELECT persona_inquiry_id FROM users WHERE id=%s", (uid,))
    if not rows or not rows[0]["persona_inquiry_id"]:
        raise HTTPException(403, "Please verify it's you first.")
    if fetch_inquiry_status(rows[0]["persona_inquiry_id"]) not in OK_STATUSES:
        raise HTTPException(403, "Please verify it's you again before Clawback can act.")
    _db().execute("UPDATE users SET reverified_at=%s WHERE id=%s", (_now(), uid))
    return {"ok": True}


@router.get("/persona/status")
def status(request: Request):
    rows = _db().query("SELECT name, persona_status, verified_at FROM users WHERE id=%s", (user_id_from(request),))
    return rows[0] if rows else {"persona_status": "none"}
