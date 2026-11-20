"""Persona webhook signature check: header `Persona-Signature: t=<unix>,v1=<hex>[ t=..,v1=..]`,
HMAC-SHA256 over "{t}.{raw_body}". Several space-separated pairs are accepted (secret rotation)."""

from __future__ import annotations

import hashlib
import hmac
import time


def verify_persona_signature(raw_body: bytes, header: str | None, secret: str, max_age_s: int = 300, now: float | None = None) -> bool:
    if not header or not secret:
        return False
    now = time.time() if now is None else now
    for pair in header.split():
        try:
            parts = dict(kv.split("=", 1) for kv in pair.split(","))
            t, sig = parts["t"], parts["v1"]
            if abs(now - int(t)) > max_age_s:
                continue
            expected = hmac.new(secret.encode(), t.encode() + b"." + raw_body, hashlib.sha256).hexdigest()
            if hmac.compare_digest(sig, expected):
                return True
        except Exception:
            continue
    return False
