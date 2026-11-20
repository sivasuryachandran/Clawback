"""Saved read-only queries for the "Live queries" panel (Under the hood; SingleStore).

A fixed whitelist: the page names a key, never SQL. Each query is a single SELECT, run through
dbaccess.query (which refuses anything else). Standard SQL only, so it runs on SingleStore and on
the SQLite stand-in used for sample data and tests.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from . import dbaccess as dba
from .plain import first_failure, label as item_label, plain

STORE_NAMES = {"mockstore": "Mock Store", "target": "Target", "bestbuy": "Best Buy"}


def store_display(s):
    return STORE_NAMES.get((s or "").lower(), (s or "This store").title())

STOPPED_THIS_WEEK = """\
SELECT a.id, a.ts, a.decision, a.rule_results_json, p.action_type,
       li.title, li.variant, li.store, li.price_paid,
       li.category, l.title AS offered_title, l.variant AS offered_variant, l.store AS offered_store
FROM audit_log a
LEFT JOIN proposals p ON p.id = a.proposal_id
LEFT JOIN opportunities o ON o.id = p.opportunity_id
LEFT JOIN line_items li ON li.id = o.line_item_id
LEFT JOIN listings l ON l.id = o.listing_id
WHERE a.decision = 'BLOCK' AND a.ts >= %s
ORDER BY a.ts DESC"""

RECOVERED_BY_STORE = """\
SELECT li.store, SUM(o.drop_usd) AS dollars, COUNT(*) AS actions
FROM executed_actions ea
JOIN (SELECT proposal_hash, MIN(opportunity_id) AS opportunity_id
      FROM proposals GROUP BY proposal_hash) p ON p.proposal_hash = ea.proposal_hash
JOIN opportunities o ON o.id = p.opportunity_id
JOIN line_items li ON li.id = ea.line_item_id
WHERE ea.status = 'done' AND ea.action_type IN ('price_adjust_email', 'rebuy_return')
GROUP BY li.store
ORDER BY dollars DESC"""

PRICE_HISTORY = """\
SELECT l.store, l.sku, l.variant, ph.price, ph.seen_at
FROM price_history ph
JOIN listings l ON l.id = ph.listing_id
WHERE l.id = %s
ORDER BY ph.seen_at DESC
LIMIT 20"""

LIVE_QUERIES = {
    "stopped_this_week": ("What the Pact stopped this week, and why", STOPPED_THIS_WEEK),
    "recovered_by_store": ("Dollars recovered by store", RECOVERED_BY_STORE),
    "price_history": ("Price history for an item", PRICE_HISTORY),
}


def catalog() -> list[dict]:
    return [{"key": k, "label": label, "sql": sql, "needs_item": k == "price_history"}
            for k, (label, sql) in LIVE_QUERIES.items()]


def run(key: str, item: int | None = None, now: datetime | None = None) -> dict:
    """Run one saved query. Raises KeyError for an unknown key, so a caller can answer 404."""
    label, sql = LIVE_QUERIES[key]
    now = now or datetime.now(timezone.utc)
    if key == "stopped_this_week":
        params: tuple = ((now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S"),)
    elif key == "price_history":
        if item is None:
            first = dba.query("SELECT id FROM listings ORDER BY id LIMIT 1")
            item = first[0]["id"] if first else 0
        params = (int(item),)
    else:
        params = ()
    t0 = time.perf_counter()
    rows = [dba.clean_row(r) for r in dba.query(sql, params)]
    if key == "stopped_this_week":
        for r in rows:
            fail = first_failure(r.pop("rule_results_json", None))
            r["failed_rule"] = fail.get("rule") if fail else None
            r["why"] = plain(fail["rule"], values=fail.get("values"), store=store_display(r.get("store")),
                             bought=item_label(r.get("title"), r.get("variant")),
                             offered=item_label(r.get("offered_title"), r.get("offered_variant")),
                             category=r.get("category"),
                             own_prices_only=(store_display(r.get("store")) if r.get("offered_store") and r.get("store")
                                              and r["offered_store"] != r["store"] else None)) if fail else None
    return {"key": key, "label": label, "sql": sql, "params": list(params), "rows": rows,
            "took_ms": round((time.perf_counter() - t0) * 1000, 1)}
