"""Embeddings + rerank for item/listing titles.

NOT claiming the Superlinked SIE Serverless prize -- SIE isn't a focus sponsor this
round. What's actually running is the OFFLINE fallback below: a deterministic hashed
bag-of-words vector, no network call, no API key. get_embedding() would call SIE's
OpenAI-compatible /v1/embeddings endpoint if SIE_URL/SIE_API_KEY were ever set (both
are empty in .env), but that path is unused and unverified live -- don't present it as
working. Either path returns a list[float] of length EMBEDDING_DIM, so the matcher
doesn't care which one ran; match()'s result only reports plain similarity scores, not
which embedding backend produced them, so label this clearly wherever it's surfaced to
judges (e.g. the Matching panel) rather than implying SIE.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re

import httpx

SIE_URL = os.getenv("SIE_URL")
SIE_API_KEY = os.getenv("SIE_API_KEY")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _fallback_embedding(text: str) -> list[float]:
    """Deterministic, offline, hashed bag-of-words embedding. Not a real embedding
    model: titles that share more words score higher cosine similarity, which is
    enough to exercise the matcher (e.g. same product in a different color/variant)
    without any external service."""
    vec = [0.0] * EMBEDDING_DIM
    tokens = _TOKEN_RE.findall((text or "").lower())
    if not tokens:
        return vec
    for tok in tokens:
        h = int(hashlib.sha256(tok.encode()).hexdigest(), 16)
        idx = h % EMBEDDING_DIM
        sign = 1.0 if (h >> 1) % 2 == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def embedding_backend() -> str:
    """For honest labeling wherever match() results reach a UI (e.g. Under the hood's
    Matching panel) -- never imply SIE/Superlinked ran when it didn't."""
    return "SIE" if (SIE_URL and SIE_API_KEY) else "offline-hashed (not SIE)"


def get_embedding(text: str) -> list[float]:
    if SIE_URL and SIE_API_KEY:
        resp = httpx.post(
            f"{SIE_URL.rstrip('/')}/v1/embeddings",
            headers={"Authorization": f"Bearer {SIE_API_KEY}"},
            json={"input": text},
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]
    return _fallback_embedding(text)


def vec_to_json(vec: list[float]) -> str:
    return json.dumps(vec)


def vec_from_json(text) -> list[float] | None:
    """Accepts a JSON string (SQLite's TEXT column) or an already-decoded array-like
    value -- SingleStore's driver returns VECTOR columns as numpy arrays, not JSON
    text, and `if not <numpy array with >1 element>` raises ValueError, not False."""
    if text is None:
        return None
    if isinstance(text, str):
        if not text:
            return None
        return json.loads(text)
    if hasattr(text, "tolist"):
        return text.tolist()
    return list(text) if len(text) else None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


def rerank(query_title: str, candidates: list[dict]) -> list[dict]:
    """candidates: [{listing_id, title, similarity, ...}]. Returns the same list with
    a 'rerank_score' added, via SIE's rerank endpoint when configured, else falls back
    to similarity as the rerank score (keeps the pipeline shape identical either way)."""
    if not candidates:
        return []
    if SIE_URL and SIE_API_KEY:
        resp = httpx.post(
            f"{SIE_URL.rstrip('/')}/v1/rerank",
            headers={"Authorization": f"Bearer {SIE_API_KEY}"},
            json={
                "query": query_title,
                "documents": [c["title"] for c in candidates],
            },
            timeout=20,
        )
        resp.raise_for_status()
        scores = resp.json()["scores"]
        for c, s in zip(candidates, scores):
            c["rerank_score"] = s
        return candidates
    for c in candidates:
        c["rerank_score"] = c.get("similarity", 0.0)
    return candidates
