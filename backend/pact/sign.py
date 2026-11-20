"""Pact signing, proposal hashing and revocation signatures.

PROVENANCE: canonical JSON + sha256 helpers and the HMAC sign/verify pattern are
adapted from the earlier Pact project (HCK/Pact/pact/canonical.py, tokens.py).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any


def canonical(obj: Any, drop: tuple[str, ...] = ()) -> str:
    if isinstance(obj, dict) and drop:
        obj = {k: v for k, v in obj.items() if k not in drop}
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _key() -> bytes:
    key = os.environ.get("PACT_SIGNING_KEY", "")
    if len(key) < 16:
        raise RuntimeError("PACT_SIGNING_KEY is not set (32+ random bytes required)")
    return key.encode("utf-8")


def _hmac(text: str) -> str:
    return hmac.new(_key(), text.encode("utf-8"), hashlib.sha256).hexdigest()


def sign(pact: dict) -> str:
    """Return pact_text: canonical JSON of the pact including hash + signature."""
    body = canonical(pact, drop=("hash", "signature"))
    signed = dict(json.loads(body))
    signed["hash"] = _sha(body)
    signed["signature"] = _hmac(body)
    return canonical(signed)


def verify_dict(pact: Any) -> bool:
    """True only if the hash and the HMAC both match the body."""
    try:
        if not isinstance(pact, dict):
            return False
        body = canonical(pact, drop=("hash", "signature"))
        return (
            hmac.compare_digest(str(pact.get("hash", "")), _sha(body))
            and hmac.compare_digest(str(pact.get("signature", "")), _hmac(body))
        )
    except Exception:
        return False


def verify_text(pact_text: str) -> dict | None:
    try:
        pact = json.loads(pact_text)
    except Exception:
        return None
    return pact if verify_dict(pact) else None


def proposal_hash(p: dict) -> str:
    return "sha256:" + _sha(canonical(p, drop=("reasoning",)))


def _rev_body(pact_id: str, version: int, revoked_at: Any) -> str:
    # Normalise so a datetime read back from the DB signs the same as the string we stored.
    if hasattr(revoked_at, "strftime"):
        stamp = revoked_at.strftime("%Y-%m-%d %H:%M:%S")
    else:
        stamp = str(revoked_at).replace("T", " ")[:19]
    return f"{pact_id}|{int(version)}|{stamp}"


def sign_revocation(pact_id: str, version: int, revoked_at: Any) -> str:
    return _hmac(_rev_body(pact_id, version, revoked_at))


def verify_revocation(row: dict) -> bool:
    try:
        expected = sign_revocation(row["pact_id"], row["version"], row["revoked_at"])
        return hmac.compare_digest(str(row.get("signature", "")), expected)
    except Exception:
        return False
