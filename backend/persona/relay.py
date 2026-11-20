"""Persona Relay: a quick "is a live human here right now?" check before money moves (claim type live_human_presence).

Why a gateway: Relay needs Privacy Pass (blind RSA). Persona ships Node/Go SDKs and a Docker "Relay Gateway Service" that
does the crypto; we talk to that gateway over plain HTTP. There is no Python SDK.
    docker pull ghcr.io/persona-id/persona-relay-gateway:latest      (run it and point RELAY_GATEWAY_URL at it)
ENV (all optional; the check is OFF unless the first two are set):
    RELAY_GATEWAY_URL        e.g. http://localhost:8787
    PERSONA_RELAY_API_KEY    a Persona SANDBOX API key with Relay enabled (requested through the hackathon form)
    RELAY_SANDBOX=true       (default true)
    RELAY_MAX_AGE_S=120      how long a passed check counts before money moves
We ask for an UNENCRYPTED claim (encryption-key-jwk = null): it only carries {claim_type, claim_result}, no personal data.

ASSUMPTIONS (read from docs.withpersona.com Relay pages, never run live): gateway paths POST /relays, POST /relays/privacy-passes,
POST /relays/{relay-token}/redeem; hosted page https://relay.withpersona.com/relay?relay-session-access-token=...
Fails closed: any error means "not verified".
"""

from __future__ import annotations

import json
import os
import time
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Request

router = APIRouter()
CLAIM = "live_human_presence"
HOSTED = "https://relay.withpersona.com/relay"
_pending: dict[str, dict] = {}        # user id -> {relay_token, relay_secret, pass_token, at}
_passed_at: dict[str, float] = {}     # user id -> time of the last passed claim


def _gw() -> str | None:
    return (os.environ.get("RELAY_GATEWAY_URL") or "").rstrip("/") or None


def enabled() -> bool:
    return bool(_gw() and os.environ.get("PERSONA_RELAY_API_KEY"))


def max_age() -> float:
    return float(os.environ.get("RELAY_MAX_AGE_S", "120"))


def is_fresh(user_id: str, now: float | None = None) -> bool:
    t = _passed_at.get(user_id)
    return t is not None and (time.time() if now is None else now) - t <= max_age()


def require_fresh(user_id: str) -> None:
    """Called before money moves. A no-op while Relay isn't configured."""
    if enabled() and not is_fresh(user_id):
        raise HTTPException(403, "Please confirm you're a live human first.")


def _uid(request: Request) -> str:
    return request.headers.get("x-user-id") or "u_demo"


def _post(path: str, headers: dict, body: dict) -> dict:
    r = httpx.post(_gw() + path, headers={"Content-Type": "application/json", **headers}, json=body, timeout=20)
    r.raise_for_status()
    return r.json()


@router.get("/persona/relay/status")
def status(request: Request):
    uid = _uid(request)
    return {"enabled": enabled(), "fresh": is_fresh(uid), "max_age_s": max_age()}


@router.post("/persona/relay/start")
def start(request: Request):
    if not enabled():
        raise HTTPException(404, "The live-human check isn't turned on.")
    uid = _uid(request)
    sandbox = os.environ.get("RELAY_SANDBOX", "true").lower() != "false"
    try:
        rel = _post("/relays", {}, {"claim-type": CLAIM, "encryption-key-jwk": None, "sandbox": sandbox})
        pas = _post("/relays/privacy-passes", {"Authorization": "Bearer " + os.environ["PERSONA_RELAY_API_KEY"]}, {"claim-type": CLAIM})
        _pending[uid] = {"relay_token": rel["relay-token"], "relay_secret": rel["relay-secret"],
                         "pass_token": pas["privacy-pass-token"], "at": time.time()}
        token = rel["relay-session-access-token"]          # only this goes to the browser
    except Exception:
        raise HTTPException(502, "Couldn't start the check. Nothing was changed.")
    return {"url": f"{HOSTED}?relay-session-access-token={quote(token, safe='')}&theme=dark"}


def _claim_result(payload) -> str | None:
    data = json.loads(payload) if isinstance(payload, str) else payload
    return (data or {}).get("claim_result")


@router.post("/persona/relay/complete")
def complete(request: Request):
    """Poll this after the user finished in the Relay tab. 'waiting' until the claim exists."""
    uid = _uid(request)
    p = _pending.get(uid)
    if not p:
        raise HTTPException(409, "Start the check first.")
    if time.time() - p["at"] > 30 * 60:
        _pending.pop(uid, None)
        raise HTTPException(410, "That check expired. Start again.")
    try:
        out = _post(f"/relays/{quote(p['relay_token'], safe='')}/redeem",
                    {"Persona-Relay-Secret": p["relay_secret"]}, {"privacy-pass-token": p["pass_token"]})
        result = _claim_result(out.get("claim-payload"))
    except httpx.HTTPStatusError:
        return {"status": "waiting"}                       # the claim isn't ready yet
    except Exception:
        raise HTTPException(502, "Couldn't read the result. Nothing was changed.")
    _pending.pop(uid, None)
    if result == "passed":
        _passed_at[uid] = time.time()
        return {"status": "passed"}
    return {"status": "failed"}
