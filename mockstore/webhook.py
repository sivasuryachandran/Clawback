"""Price-change webhook: Mock Store -> POST {BACKEND_URL}/watch/price-changed (Teammate A receives).

Contract (document of record, also in docs/C_CONTRACTS.md):
  body    canonical JSON bytes: {"event_id","listing_id","store","sku","variant","price","old_price",
          "seen_at","refresh"}
  header  X-Mockstore-Signature: sha256=<hex HMAC-SHA256(MOCK_WEBHOOK_SECRET, raw body bytes)>
  header  X-Mockstore-Timestamp: <unix seconds>   (informational; A may enforce a replay window)
  header  X-Mockstore-Event-Id: <same uuid as body.event_id>  (A can dedupe on it)
  times   UTC, ISO-8601 with a trailing Z

Delivery rules (each one fixes a real failure mode):
- Sent from one background worker, so a slow watcher never blocks the admin slider and changes
  arrive in the order they were made.
- RETRY ONLY WHEN THE REQUEST NEVER REACHED THE WATCHER (connection refused / connect timeout).
  A read timeout, a 5xx or a 4xx is NOT retried: the watcher may already have processed the change
  (matching + proposal can take seconds), and a second delivery would create duplicate work.
- The read timeout is generous (15 s) for the same reason: A's handler may do real work inline.
- Failures are logged and shown on /mock/admin; the presenter can press "Refresh prices" to resend.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx

log = logging.getLogger("clawback.mockstore.webhook")

SYNC = False                       # tests set True to deliver inline
READ_TIMEOUT_S = 15.0
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mock-webhook")
_deliveries: deque = deque(maxlen=30)


def _default_client() -> httpx.Client:
    # trust_env=False: this talks to our own backend, so never route it through a proxy from the environment.
    return httpx.Client(timeout=httpx.Timeout(READ_TIMEOUT_S, connect=3.0), trust_env=False)


_make_client = _default_client     # tests swap in a MockTransport client


def backend_url() -> str:
    return (os.environ.get("BACKEND_URL") or "http://localhost:8000").rstrip("/")


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify(body: bytes, header: str, secret: str) -> bool:
    """Reference verifier for A (and for our tests): constant-time compare."""
    return bool(secret) and bool(header) and hmac.compare_digest(sign(body, secret), header)


def build_payload(change: dict, refresh: bool = False) -> dict:
    l = change["listing"]
    return dict(event_id=str(uuid.uuid4()), listing_id=l["id"], store=l["store"], sku=l["sku"], variant=l["variant"],
                price=float(change["new"]), old_price=float(change["old"]),
                seen_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), refresh=bool(refresh))


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def deliver(payload: dict) -> dict:
    """One delivery; a single retry only if the connection itself failed. Never raises."""
    secret = os.environ.get("MOCK_WEBHOOK_SECRET", "")
    body = canonical(payload)
    headers = {"Content-Type": "application/json", "X-Mockstore-Timestamp": str(int(time.time())),
               "X-Mockstore-Event-Id": payload["event_id"]}
    note = None
    if secret:
        headers["X-Mockstore-Signature"] = sign(body, secret)
    else:
        note = "MOCK_WEBHOOK_SECRET is not set; sent unsigned (the watcher should reject it)"
        log.warning(note)
    rec = dict(at=datetime.now(timezone.utc).strftime("%H:%M:%S"), event_id=payload["event_id"],
               listing_id=payload["listing_id"], price=payload["price"], refresh=payload.get("refresh", False),
               ok=False, status=None, error=note, attempts=0)
    url = backend_url() + "/watch/price-changed"
    for attempt in (1, 2):
        rec["attempts"] = attempt
        try:
            with _make_client() as c:
                r = c.post(url, content=body, headers=headers)
            rec["status"] = r.status_code
            rec["ok"] = 200 <= r.status_code < 300
            rec["error"] = note if rec["ok"] else (f"HTTP {r.status_code}" + (f" ({note})" if note else ""))
            break                              # the watcher answered: never send it again
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            rec["error"] = f"{type(exc).__name__}: could not connect to {backend_url()}"
            if attempt == 1:
                time.sleep(0.4)                # it never arrived, so one more try is safe
                continue
        except httpx.TimeoutException:
            rec["error"] = (f"no answer within {int(READ_TIMEOUT_S)} s; the watcher may still have processed it, "
                            "so it was not sent again")
            break
        except Exception as exc:
            rec["error"] = f"{type(exc).__name__}: {exc}"[:200]
            break
    if not rec["ok"]:
        log.error("price webhook failed for listing %s: %s", payload["listing_id"], rec["error"])
    _deliveries.appendleft(rec)
    return rec


def dispatch(changes: list[dict], refresh: bool = False) -> None:
    """Queue one webhook per price change; returns immediately (unless SYNC)."""
    for ch in changes:
        payload = build_payload(ch, refresh=refresh)
        if SYNC:
            deliver(payload)
        else:
            _executor.submit(deliver, payload)


def recent() -> list[dict]:
    return list(_deliveries)


def clear() -> None:
    _deliveries.clear()
