"""The Why? story and spoken answers for each item (section 3.10 + 3.11; Teammate C).

Reads only rows the app already has (audit_log, proposals, opportunities, line_items, orders,
listings, price_history, policy_checks, approvals): the same facts /activity and the Why? panel
show. Words are the plain sentences from section 3.10. Spoken answers are clipped to 25 words.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from backend.analytics import stats
from backend.debug import dbaccess as dba
from backend.debug.plain import first_failure, label, plain, usd

STORE_NAMES = {"mockstore": "Mock Store", "target": "Target", "bestbuy": "Best Buy"}
MAX_WORDS = 25
_STOP = {"smart", "wireless", "sample", "real", "item", "items", "chosen", "event", "with", "your", "the", "and"}

DECISIONS_SQL = (
    "SELECT a.id AS audit_id, a.ts, a.proposal_hash, a.decision, a.rule_results_json, "
    "p.id AS proposal_id, p.action_type, p.opportunity_id, "
    "o.drop_usd, o.kind, o.listing_id, "
    "li.id AS line_item_id, li.title, li.variant, li.store, li.category, li.price_paid, od.order_date, "
    "l.title AS offered_title, l.variant AS offered_variant, l.store AS offered_store "
    "FROM audit_log a "
    "LEFT JOIN proposals p ON p.id = a.proposal_id "
    "LEFT JOIN opportunities o ON o.id = p.opportunity_id "
    "LEFT JOIN line_items li ON li.id = o.line_item_id "
    "LEFT JOIN orders od ON od.id = li.order_id "
    "LEFT JOIN listings l ON l.id = o.listing_id "
    "ORDER BY a.id")


def store_name(s: str | None) -> str:
    return STORE_NAMES.get((s or "").lower(), (s or "the store").title())


def clip(text: str, n: int = MAX_WORDS) -> str:
    words = text.split()
    if len(words) <= n:
        return text
    return " ".join(words[:n]).rstrip(",;:") + "."


def noun(title: str | None) -> str:
    """'Trailhead Backpack' -> 'backpack'. Parentheticals and punctuation are ignored."""
    words = re.findall(r"[A-Za-z0-9]+", re.sub(r"\(.*?\)", " ", title or ""))
    return words[-1].lower() if words else "item"


def name(title: str | None) -> str:
    """Brand + noun as the card copy says it: 'Aurora Wireless Earbuds' -> 'Aurora earbuds', 'Nimbus Smart Lamp' -> 'Nimbus lamp'."""
    words = re.findall(r"[A-Za-z0-9]+", re.sub(r"\(.*?\)", " ", title or ""))
    if len(words) >= 2:
        return f"{words[0]} {words[-1].lower()}"
    return noun(title)


def nice_date(v) -> str:
    d = dba.to_dt(v)
    return f"{d.strftime('%b')} {d.day}" if d else "recently"


def ago(v, now: datetime | None = None) -> str:
    t = dba.to_dt(v)
    if not t:
        return "a while"
    secs = max(0, int(((now or datetime.now(timezone.utc)) - t).total_seconds()))
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if secs >= size:
            n = secs // size
            return f"{n} {unit}{'s' if n != 1 else ''}"
    return "less than a minute"


def _rows() -> list[dict]:
    rows = dba.query(DECISIONS_SQL)
    for r in rows:
        r["rules"] = dba.jsonish(r.pop("rule_results_json", None))
    return rows


def latest_per_opportunity(rows: list[dict]) -> list[dict]:
    """One row per opportunity: the latest decision, but a claim beats its follow-ups, so
    'why did you stop the earbuds' explains the claim and not the proof email."""
    best: dict = {}
    for r in rows:                                   # ascending, later wins
        key = r.get("opportunity_id") or f"audit:{r['audit_id']}"
        cur = best.get(key)
        if cur is None:
            best[key] = r
        elif cur.get("action_type") == "claim_followup" and r.get("action_type") != "claim_followup":
            best[key] = r
        elif cur.get("action_type") != "claim_followup" and r.get("action_type") == "claim_followup":
            continue
        else:
            best[key] = r
    return list(best.values())


def story(row: dict, now: datetime | None = None) -> dict:
    """Four Why? lines + one spoken sentence for a decision row."""
    item = label(row.get("title"), row.get("variant")) or "this item"
    found_label = label(row.get("offered_title"), row.get("offered_variant")) or item
    store = store_name(row.get("offered_store") or row.get("store"))
    price = seen = None
    if row.get("listing_id") is not None:
        ph = dba.query("SELECT price, seen_at FROM price_history WHERE listing_id = %s ORDER BY seen_at DESC LIMIT 1",
                       (row["listing_id"],))
        if ph:
            price, seen = ph[0]["price"], ph[0]["seen_at"]
    cite = dba.query("SELECT cited_text, source_url, revision FROM policy_checks WHERE opportunity_id = %s "
                     "ORDER BY id DESC LIMIT 1", (row.get("opportunity_id"),)) if row.get("opportunity_id") else []
    cited = cite[0]["cited_text"] if cite else None
    decision = (row.get("decision") or "").upper()
    fail = first_failure(row.get("rules"))
    rule = fail.get("rule") if fail else None
    cross = bool(row.get("offered_store") and row.get("store") and row["offered_store"] != row["store"])
    reason = plain(rule, values=fail.get("values"), store=store_name(row.get("store")),
                   bought=item, offered=found_label, category=row.get("category"),
                   own_prices_only=store_name(row.get("store")) if cross else None) if fail else None
    if decision == "ALLOW":
        reason = "Everything checked out."
    elif decision == "NO_ACTION":
        reason = "There was nothing to do."
    paid = row.get("price_paid")
    lines = [
        {"icon": "🧾", "text": f"You bought {item} for {usd(paid)} on {nice_date(row.get('order_date'))}."},
        {"icon": "🏷️", "text": (f"We found {found_label} for {usd(price)} at {store}, {ago(seen, now)} ago."
                                if price is not None else "We haven't found a new price yet.")},
        {"icon": "📄", "text": (f"{store}'s policy says: \"{cited}\"" if cited else "We couldn't confirm the store's policy.")},
        {"icon": "✅", "text": f"Your rules: {reason}"},
    ]
    short = noun(row.get("title"))          # "backpack": used by the R5 sentence
    nm = name(row.get("title"))             # "Aurora earbuds": used by the done / ask-first sentences
    action = row.get("action_type")
    drop = usd(row.get("drop_usd"))
    if decision == "BLOCK":
        if rule == "R5" and row.get("title") and row.get("title") == row.get("offered_title") and row.get("variant") != row.get("offered_variant"):
            spoken = f"We didn't act. The cheaper {short} is {row.get('offered_variant')}; yours is {row.get('variant')}."
        else:
            spoken = f"We didn't act. {reason}"
    elif decision == "ASK":
        spoken = f"Waiting for your OK: save {drop} on your {nm}. Approve it on screen."
    elif decision == "ALLOW" and action == "claim_followup":
        spoken = f"{store_name(row.get('store'))} asked for proof; we sent your receipt and the new price."
    elif decision == "ALLOW" and action == "rebuy_return":
        spoken = f"We bought your {nm} again and returned the first one. You save {drop}."
    elif decision == "ALLOW" and action == "deadline_alert":
        spoken = f"We'll remind you before your {nm} return window closes."
    elif decision == "ALLOW":
        spoken = (f"We asked {store_name(row.get('store'))} for {drop} back on your {nm}. "
                  f"Price dropped from {usd(paid)} to {usd(price)}." if price is not None
                  else f"We asked {store_name(row.get('store'))} for {drop} back on your {nm}.")
    else:
        spoken = f"There was nothing to do for your {nm} right now."
    return {"opportunity_id": row.get("opportunity_id"), "proposal_id": row.get("proposal_id"), "item": item,
            "short": short, "name": nm, "store": row.get("store"), "decision": decision, "failed_rule": rule,
            "action_type": action, "lines": lines, "spoken": clip(spoken), "source_url": cite[0]["source_url"] if cite else None}


def stories(now: datetime | None = None) -> list[dict]:
    return [story(r, now) for r in latest_per_opportunity(_rows())]


def find_item(question: str, rows: list[dict]) -> dict | None:
    """Which purchase is the question about? Match item words, store names and color; best score wins."""
    q = re.sub(r"[^a-z0-9 ]+", " ", question.lower())
    qw = set(q.split())
    best, best_score = None, 0
    for r in rows:
        score = 0
        for w in re.sub(r"[^a-z0-9 ]+", " ", (r.get("title") or "").lower()).split():
            if len(w) >= 3 and w not in _STOP and (w in qw or (w.endswith("s") and w[:-1] in qw) or w + "s" in qw):
                score += 2
        s = (r.get("store") or "").lower()
        if (s == "bestbuy" and ("best buy" in q or "bestbuy" in qw)) or (s == "target" and "target" in qw) \
                or (s == "mockstore" and "mock" in qw):
            score += 1
        if r.get("variant") and r["variant"].lower() in qw:
            score += 1
        if score > best_score:
            best, best_score = r, score
    return best
