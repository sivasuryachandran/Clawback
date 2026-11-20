"""Applies the schema, seeds store_policies + the demo user, ingests the six seed
receipts (seed/receipts/*.eml) end to end through the real parser, and seeds
listings/price_history so the section 3.8 demo scenarios are reproducible.

Mock Store listing ids are STABLE and must be used verbatim (docs/C_CONTRACTS.md
section 2) -- the price webhook and rebuy_return both speak in listing_id. When the
app (and so the mock store) is already running, this pulls the real listings/orders
from it; otherwise it falls back to the same fixed values mockstore/store.py seeds,
so running this before `uvicorn backend.main:app` is up still produces an identical
database.

Run: python -m seed.load_seed
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.agent import clock  # noqa: E402
from backend.db import execute, init_schema, query  # noqa: E402
from backend.ingest.gmail_ingest import ingest_new_emails  # noqa: E402
from backend.ingest.parser import parse_unparsed_emails  # noqa: E402
from backend.watch.price_watch import check_deadline, check_line_item_for_drop  # noqa: E402

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

# Real values confirmed against the official pages (seed/policies/*.md) -- not
# placeholders. mockstore matches section 3.8's "Mock store policy" verbatim.
STORE_POLICIES = [
    {
        "store": "mockstore", "claim_channel": "email", "price_adjust_allowed": True,
        "competitor_match_allowed": False, "adjust_window_days": 14, "adjust_window_starts": "purchase",
        "return_window_days": 30, "return_window_starts": "delivery", "final_sale_categories": ["clearance"],
        "restocking_fee_pct": 0, "return_shipping_usd": 0, "support_domains": [], "senso_doc_id": "local:mockstore",
    },
    {
        "store": "target", "claim_channel": "chat", "price_adjust_allowed": True,
        "competitor_match_allowed": False, "adjust_window_days": 14, "adjust_window_starts": "purchase",
        "return_window_days": 90, "return_window_starts": "purchase", "final_sale_categories": ["clearance"],
        "restocking_fee_pct": 0, "return_shipping_usd": 0, "support_domains": ["target.com"], "senso_doc_id": "local:target",
    },
    {
        "store": "bestbuy", "claim_channel": "chat", "price_adjust_allowed": True,
        "competitor_match_allowed": False, "adjust_window_days": 15, "adjust_window_starts": "delivery",
        "return_window_days": 15, "return_window_starts": "delivery", "final_sale_categories": ["clearance"],
        "restocking_fee_pct": 0, "return_shipping_usd": 0, "support_domains": ["bestbuy.com"], "senso_doc_id": "local:bestbuy",
    },
]

# Fallback mockstore listings (mirrors mockstore/store.py SEED_LISTINGS + its "Demo
# preset"), used only when the live GET /mock/api/listings can't be reached.
FALLBACK_MOCKSTORE_LISTINGS = [
    {"id": 1, "sku": "MS-1001", "upc": "850001000011", "model_number": "AUR-EB1", "variant": "Black",
     "title": "Aurora Wireless Earbuds", "price": 95.99},
    {"id": 2, "sku": "MS-2002", "upc": "850001000028", "model_number": "NIM-LMP2", "variant": "White",
     "title": "Nimbus Smart Lamp", "price": 199.00},
    {"id": 3, "sku": "MS-3003", "upc": "850001000035", "model_number": "TRL-BP3", "variant": "Black",
     "title": "Trailhead Backpack", "price": 89.00},
    {"id": 4, "sku": "MS-3003", "upc": "850001000042", "model_number": "TRL-BP3", "variant": "Blue",
     "title": "Trailhead Backpack", "price": 64.00},
    {"id": 5, "sku": "MS-4004", "upc": "850001000059", "model_number": "BRZ-FAN4", "variant": "Gray",
     "title": "Breeze Desk Fan", "price": 39.00},
]

# Real-store placeholders (mirrors dev/stubs/seed.py). Replace once the team picks real
# items; the matching seed/receipts/*.eml would need updating too.
REAL_STORE_LISTINGS = [
    {"store": "bestbuy", "sku": "BB-LGT-MXA3S", "upc": "850002000010", "model_number": "MXA3S",
     "variant": "Graphite", "url": "https://www.bestbuy.com/site/logitech-mx-anywhere-3s", "title": "Logitech MX Anywhere 3S Wireless Mouse", "price": 73.99},
    {"store": "target", "sku": "TG-THR-KNT1", "upc": "850002000027", "model_number": "THR-KNT1",
     "variant": "Cream", "url": "https://www.target.com/p/threshold-chunky-knit-throw", "title": "Threshold Chunky Knit Throw Blanket", "price": 39.99},
]


def demo_user_email() -> str:
    return os.environ.get("DEMO_USER_EMAIL", "demo-user@clawback.test").strip().lower()


def seed_store_policies() -> None:
    for p in STORE_POLICIES:
        if query("SELECT store FROM store_policies WHERE store=%s", (p["store"],)):
            continue
        execute(
            "INSERT INTO store_policies (store, claim_channel, price_adjust_allowed, "
            "competitor_match_allowed, adjust_window_days, adjust_window_starts, "
            "return_window_days, return_window_starts, final_sale_categories, "
            "restocking_fee_pct, return_shipping_usd, support_domains, senso_doc_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                p["store"], p["claim_channel"], p["price_adjust_allowed"], p["competitor_match_allowed"],
                p["adjust_window_days"], p["adjust_window_starts"], p["return_window_days"],
                p["return_window_starts"], json.dumps(p["final_sale_categories"]), p["restocking_fee_pct"],
                p["return_shipping_usd"], json.dumps(p["support_domains"]), p["senso_doc_id"],
            ),
        )
        print(f"  store_policies: {p['store']}")


def seed_user() -> None:
    if query("SELECT id FROM users WHERE id='u_demo'", ()):
        return
    execute(
        "INSERT INTO users (id, name, email, persona_status) VALUES (%s,%s,%s,%s)",
        ("u_demo", "Demo User", demo_user_email(), "none"),
    )
    print("  users: u_demo")


def _live_mockstore_listings() -> list[dict] | None:
    try:
        resp = httpx.get(f"{BACKEND_URL}/mock/api/listings", timeout=5)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def seed_listings() -> None:
    mockstore_listings = _live_mockstore_listings()
    source = "live mock store" if mockstore_listings else "fallback values"
    if not mockstore_listings:
        mockstore_listings = FALLBACK_MOCKSTORE_LISTINGS
    print(f"  mockstore listings from {source}")
    now = clock.ts()
    for listing in mockstore_listings:
        existing = query("SELECT id FROM listings WHERE id=%s", (listing["id"],))
        if not existing:
            execute(
                "INSERT INTO listings (id, store, sku, upc, model_number, variant, url, title) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (listing["id"], "mockstore", listing["sku"], listing.get("upc"),
                 listing.get("model_number"), listing["variant"],
                 listing.get("url", f"/mock/p/{listing['sku']}"), listing["title"]),
            )
            print(f"  listings: mockstore/{listing['sku']} ({listing['variant']}) id={listing['id']}")
        execute(
            "INSERT INTO price_history (listing_id, price, seen_at) VALUES (%s,%s,%s)",
            (listing["id"], listing["price"], now),
        )

    for listing in REAL_STORE_LISTINGS:
        existing = query(
            "SELECT id FROM listings WHERE store=%s AND sku=%s", (listing["store"], listing["sku"])
        )
        if existing:
            listing_id = existing[0]["id"]
        else:
            listing_id = execute(
                "INSERT INTO listings (store, sku, upc, model_number, variant, url, title) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (listing["store"], listing["sku"], listing["upc"], listing["model_number"],
                 listing["variant"], listing["url"], listing["title"]),
            )
            print(f"  listings: {listing['store']}/{listing['sku']}")
        execute(
            "INSERT INTO price_history (listing_id, price, seen_at) VALUES (%s,%s,%s)",
            (listing_id, listing["price"], now),
        )


def main() -> None:
    print("Applying schema...")
    init_schema()
    print("Seeding store_policies...")
    seed_store_policies()
    print("Seeding demo user...")
    seed_user()
    print("Ingesting seed receipts...")
    new_emails = ingest_new_emails()
    parsed = parse_unparsed_emails()
    print(f"  {new_emails} new emails, {parsed} parsed")
    print("Seeding listings + current prices...")
    seed_listings()
    print("Checking for opportunities...")
    for li in query("SELECT id FROM line_items WHERE status='watching'", ()):
        opp = check_line_item_for_drop(li["id"]) or check_deadline(li["id"])
        if opp:
            print(f"  line_item {li['id']} -> opportunity {opp}")
    print("Done.")


if __name__ == "__main__":
    main()
