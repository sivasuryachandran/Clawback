# STUB for rehearsal: replaced by Teammate A's real seed/ingest tomorrow
"""Seed rows from team spec 3.8, dated relative to CLAWBACK_TODAY (default 2026-11-20),
with prices after the mock store's "Demo preset".

Best Buy and Target items are PLACEHOLDERS until real items are picked. Their store_policies
values are PLACEHOLDERS too (marked below); confirm them from the official pages.
"""
import json
import os
from datetime import timedelta

from backend.agent import clock
from dev.stubs import db

# (key, store, order_no, title, sku, upc, variant, category, paid, ordered_days_ago, delivered_days_ago)
ROWS = [
    ("earbuds", "mockstore", "MS-55021", "Aurora Wireless Earbuds", "MS-1001", None, "Black", "electronics", 129.99, 9, 7),
    ("lamp", "mockstore", "MS-55022", "Nimbus Smart Lamp", "MS-2002", None, "White", "home", 239.00, 18, 16),
    ("backpack", "mockstore", "MS-55023", "Trailhead Backpack", "MS-3003", None, "Black", "outdoor", 89.00, 8, 6),
    ("fan", "mockstore", "MS-55024", "Breeze Desk Fan", "MS-4004", None, "Gray", "clearance", 59.00, 10, 8),
    # PLACEHOLDER real-store items: replace with the items picked before kickoff.
    ("bestbuy", "bestbuy", "BBY01-PLACEHOLDER", "PLACEHOLDER Best Buy item", "BB-PLACEHOLDER", "000000000001", "Standard", "electronics", 49.99, 6, 4),
    # Target's window is 90 days (target.com/returns), so the "closes soon" item must be
    # ordered ~88 days before demo day (Aug 24 for Nov 20), NOT ~Oct 22 as spec 3.8 says.
    ("target", "target", "TGT-PLACEHOLDER", "PLACEHOLDER Target item", "TG-PLACEHOLDER", "000000000002", "Standard", "home", 29.99, 88, 86),
]

# key -> (listing variant to match, latest price, minutes since seen)
LATEST = {
    "earbuds": ("Black", 95.99, 1),
    "lamp": ("White", 199.00, 1),
    "backpack": ("Blue", 64.00, 1),     # top match is the BLUE one (R5 stop)
    "fan": ("Gray", 39.00, 1),
    "bestbuy": ("Standard", 43.99, 120),  # $6 below paid, cached 2 hours ago (R8 stop)
    "target": ("Standard", 29.99, 60),    # no drop -> deadline opportunity
}

POLICIES = [
    # store, channel, adjust_allowed, competitor, adj_days, adj_starts, ret_days, ret_starts,
    # final_sale, restock_pct, ret_ship, support_domains
    ("mockstore", "email", 1, 0, 14, "purchase", 30, "delivery", ["clearance"], 0.0, 0.0, [], "local:mockstore"),
    # From target.com, read 2026-11-19 (seed/policies/target.md):
    #  adjust: own prices only, 14 days from purchase (standard; the holiday rule for purchases
    #    Nov 1 - Dec 24 can't be expressed as days, so the shorter standard window is used);
    #  return: 90 days for most items; start NOT stated on the page -> "purchase" (shorter, safer);
    #  clearance can't be price matched; claims go through chat.
    ("target", "chat", 1, 0, 14, "purchase", 90, "purchase", ["clearance"], 0.0, 0.0, ["target.com"], "local:target"),
    # From bestbuy.com, read 2026-11-19 (seed/policies/bestbuy.md):
    #  own price drops are matched during the return period; competitors only at time of sale;
    #  return: standard 15 days from the day you receive it (holiday: purchases Oct 30 - Dec 31
    #  run through Jan 15, 2027 - not expressible as days, standard used); clearance excluded;
    #  restocking fee only for listed categories (cameras, drones, projectors...), 0 here.
    ("bestbuy", "chat", 1, 0, 15, "delivery", 15, "delivery", ["clearance"], 0.0, 0.0, ["bestbuy.com"], "local:bestbuy"),
]


def demo_user_email() -> str:
    return os.environ.get("DEMO_USER_EMAIL", "demo-user@clawback.test").strip().lower()


def seed() -> dict:
    """Insert everything. Returns {key: opportunity_id}."""
    today = clock.today()
    now = clock.now()
    db.execute(
        "INSERT INTO users (id, name, email, persona_status, verified_at, reverified_at) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        ("u_demo", "Demo User", demo_user_email(), "approved",
         clock.ts(now - timedelta(days=1)), None))
    for (store, ch, adj, comp, ad, ads, rd, rds, fs, rp, rs, dom, doc) in POLICIES:
        db.execute(
            "INSERT INTO store_policies (store, claim_channel, price_adjust_allowed, "
            "competitor_match_allowed, adjust_window_days, adjust_window_starts, "
            "return_window_days, return_window_starts, final_sale_categories, "
            "restocking_fee_pct, return_shipping_usd, support_domains, senso_doc_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (store, ch, adj, comp, ad, ads, rd, rds, json.dumps(fs), rp, rs, json.dumps(dom), doc))

    opp_ids = {}
    listing_ids = {}
    for (key, store, order_no, title, sku, upc, variant, cat, paid, od, dd) in ROWS:
        ordered = today - timedelta(days=od)
        order_id = db.execute(
            "INSERT INTO orders (store, order_no, order_date, delivery_date, email_id) "
            "VALUES (%s, %s, %s, %s, %s)",
            (store, order_no, ordered.isoformat(), (today - timedelta(days=dd)).isoformat(), None))
        li = db.execute(
            "INSERT INTO line_items (order_id, store, title, sku, upc, model_number, variant, "
            "category, price_paid, qty, parse_confidence, status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (order_id, store, title, sku, upc, None, variant, cat, paid, 1, 0.97, "watching"))
        base = "http://localhost:8000/mock/p/" if store == "mockstore" else f"https://www.{store}.com/PLACEHOLDER/"
        for v in ([variant, "Blue"] if key == "backpack" else [variant]):
            lid = db.execute(
                "INSERT INTO listings (store, sku, upc, model_number, variant, url, title) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (store, sku, upc, None, v, f"{base}{sku}?variant={v}", f"{title} ({v})"))
            listing_ids[(key, v)] = lid
            db.execute("INSERT INTO price_history (listing_id, price, seen_at) VALUES (%s,%s,%s)",
                       (lid, paid, f"{ordered.isoformat()} 09:00:00"))
        match_variant, price, mins = LATEST[key]
        lid = listing_ids[(key, match_variant)]
        db.execute("INSERT INTO price_history (listing_id, price, seen_at) VALUES (%s,%s,%s)",
                   (lid, price, clock.ts(now - timedelta(minutes=mins))))
        kind = "price_drop" if price < paid else "deadline"
        opp_ids[key] = db.execute(
            "INSERT INTO opportunities (line_item_id, listing_id, kind, drop_usd, similarity, "
            "rerank_score, created_at, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (li, lid, kind, round(paid - price, 2), 0.93, 0.9, clock.ts(now), "open"))
    return opp_ids


def reset_and_seed() -> dict:
    db.reset()
    return seed()


if __name__ == "__main__":
    print(reset_and_seed())
