"""Embeddings + rerank for item/listing titles (Superlinked SIE Serverless prize).

get_embedding() calls SIE's OpenAI-compatible /v1/embeddings endpoint when SIE_URL/
SIE_API_KEY are set. With no SIE credentials, falls back to a small deterministic
hashed bag-of-words vector so matching still works end to end for local dev and tests
(no network, no API key needed). Swap in real creds and nothing else in the matcher
changes: both paths return a list[float] of length EMBEDDING_DIM.
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


def vec_from_json(text: str | None) -> list[float] | None:
    if not text:
        return None
    return json.loads(text)


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
