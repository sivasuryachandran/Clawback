"""match(line_item_id) -> candidate listings, ranked (section 3.4 contract).

Design rule from the team doc: this module only SUGGESTS. It returns the best vector
candidate even when SKU/variant don't match, and for ANY price drop. Identity (R5) and
minimum savings (R8) are enforced later, in Siva's gate -- never here. Filtering early
kills the demo's "Stopped" moments.
"""

from __future__ import annotations

from backend.db import execute, query, using_singlestore
from backend.ingest.embed import (
    cosine_similarity,
    embedding_backend,
    get_embedding,
    rerank,
    vec_from_json,
    vec_to_json,
)


MATCH_SQL = """
SELECT l.*,
       (SELECT price FROM price_history ph WHERE ph.listing_id = l.id ORDER BY seen_at DESC LIMIT 1) AS price,
       (SELECT seen_at FROM price_history ph WHERE ph.listing_id = l.id ORDER BY seen_at DESC LIMIT 1) AS seen_at
FROM listings l
""".strip()  # shown on Under the hood's Matching panel (docs/C_CONTRACTS.md section 10)

# Real vector search (SingleStore only): title_vec is a VECTOR(1024, F32) column; a plain
# JSON array string binds directly as %s, no JSON_ARRAY_PACK() needed (confirmed against
# docs.singlestore.com's vector-type page). DOT_PRODUCT ranks the top 10 in the database,
# not in Python -- this is the "vector match query... run live" the SingleStore prize wants.
SINGLESTORE_MATCH_SQL = """
SELECT l.*,
       (SELECT price FROM price_history ph WHERE ph.listing_id = l.id ORDER BY seen_at DESC LIMIT 1) AS price,
       (SELECT seen_at FROM price_history ph WHERE ph.listing_id = l.id ORDER BY seen_at DESC LIMIT 1) AS seen_at,
       DOT_PRODUCT(l.title_vec, %s) AS similarity
FROM listings l
WHERE l.title_vec IS NOT NULL
ORDER BY similarity DESC
LIMIT 10
""".strip()


def is_exact_match(item: dict, candidate: dict) -> bool:
    """item/candidate: dicts with store, sku, upc, model_number, variant.
    Same store -> SKU + variant. Other store -> UPC or model_number + variant."""
    if not item or not candidate:
        return False
    if (item.get("variant") or None) != (candidate.get("variant") or None):
        return False
    if item.get("store") == candidate.get("store"):
        return bool(item.get("sku")) and item.get("sku") == candidate.get("sku")
    upc_match = bool(item.get("upc")) and item.get("upc") == candidate.get("upc")
    model_match = bool(item.get("model_number")) and item.get("model_number") == candidate.get("model_number")
    return upc_match or model_match


def _ensure_vec(table: str, row_id: int, title: str, existing_vec_json) -> list[float]:
    vec = vec_from_json(existing_vec_json)
    if vec:
        return vec
    vec = get_embedding(title)
    execute(f"UPDATE {table} SET title_vec = %s WHERE id = %s", (vec_to_json(vec), row_id))
    return vec


def _listings_with_latest_price() -> list[dict]:
    return query(MATCH_SQL, ())


def _embed_listings_missing_vectors() -> None:
    """DOT_PRODUCT needs every candidate's vector populated first; the SQLite path does
    this lazily per-listing in the Python loop instead (_ensure_vec below)."""
    for listing in query("SELECT id, title FROM listings WHERE title_vec IS NULL", ()):
        vec = get_embedding(listing["title"])
        execute("UPDATE listings SET title_vec = %s WHERE id = %s", (vec_to_json(vec), listing["id"]))


def _top10_singlestore(item: dict, item_vec: list[float]) -> list[dict]:
    _embed_listings_missing_vectors()
    rows = query(SINGLESTORE_MATCH_SQL, (vec_to_json(item_vec),))
    return [
        {
            "listing_id": r["id"], "store": r["store"], "sku": r.get("sku"), "title": r["title"],
            "variant": r.get("variant"), "price": r.get("price"), "seen_at": r.get("seen_at"),
            "similarity": r["similarity"], "exact": is_exact_match(item, r),
        }
        for r in rows
    ]


def _top10_python(item: dict, item_vec: list[float]) -> list[dict]:
    scored = []
    for listing in _listings_with_latest_price():
        vec = _ensure_vec("listings", listing["id"], listing["title"], listing.get("title_vec"))
        sim = cosine_similarity(item_vec, vec)
        scored.append(
            {
                "listing_id": listing["id"],
                "store": listing["store"],
                "sku": listing.get("sku"),
                "title": listing["title"],
                "variant": listing.get("variant"),
                "price": listing.get("price"),
                "seen_at": listing.get("seen_at"),
                "similarity": sim,
                "exact": is_exact_match(item, listing),
            }
        )
    scored.sort(key=lambda c: c["similarity"], reverse=True)
    return scored[:10]


def match(line_item_id: int) -> list[dict]:
    rows = query("SELECT * FROM line_items WHERE id = %s", (line_item_id,))
    if not rows:
        return []
    item = rows[0]
    item_vec = _ensure_vec("line_items", item["id"], item["title"], item.get("title_vec"))

    top10 = _top10_singlestore(item, item_vec) if using_singlestore() else _top10_python(item, item_vec)
    reranked = rerank(item["title"], top10)
    reranked.sort(key=lambda c: c["rerank_score"], reverse=True)
    backend_label = embedding_backend()  # honest labeling for Under the hood -- never implies SIE ran
    for c in reranked:
        c["embedding_backend"] = backend_label
    return reranked[:5]
