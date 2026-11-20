"""Plain-language sentences for gate rules (section 3.10, "Rule -> plain sentence").

Used by the voice answers and by Under the hood's "stopped" queries. Words match the team file
exactly so the orb, the cards and the Why? panel all say the same thing.
"""
from __future__ import annotations

RULE_PLAIN = {
    "R0": "Something didn't add up, so we didn't act.",
    "R1": "Please verify it's you again before Clawback can act.",
    "A1": "Please verify it's you again before Clawback can act.",
    "R2": "You haven't allowed Clawback to do this.",
    "R3": "You haven't allowed Clawback at this store.",
    "R4": "This item is final sale (or a type you excluded).",
    "R6": "We couldn't confirm the store's current policy.",
    "R7": "The window to ask has closed (or closes too soon).",
    "R8a": "The price just changed; we'll check again.",
    "R9": "That would go over your spending limit.",
    "R10": "We only write to the store's official support (or to you).",
    "R11": "We've already asked about this order.",
    "R13": "We only reply in the same conversation, up to 3 times.",
}


def usd(x) -> str:
    """$34 for whole dollars, $129.99 otherwise (matches the card copy)."""
    try:
        v = round(float(x), 2)
    except (TypeError, ValueError):
        return "$0"
    return f"${v:,.0f}" if v == int(v) else f"${v:,.2f}"


def label(title: str | None, variant: str | None) -> str | None:
    """'Trailhead Backpack (Black)': item name plus color/size, never a SKU (section 3.10)."""
    if not title:
        return None
    return f"{title} ({variant})" if variant else title


def as_rules(raw) -> list:
    """The rule results as a list, whatever shape the gate stored: a list, a JSON string of a list, or a
    Decision-shaped object {"results": [...]}."""
    if isinstance(raw, (str, bytes, bytearray)):
        import json
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return []
    if isinstance(raw, dict):
        raw = raw.get("results") or raw.get("rule_results") or raw.get("rules") or []
    return raw if isinstance(raw, list) else []


def first_failure(results) -> dict | None:
    """First rule that did not pass, from a gate Decision's results (any shape `as_rules` understands)."""
    results = as_rules(results)
    for r in results:
        if isinstance(r, dict) and r.get("pass") is False:
            return r
    return None


def _pick(values: dict, *names, default=None):
    for n in names:
        if isinstance(values, dict) and values.get(n) is not None:
            return values[n]
    return default


def plain(rule: str, *, bought: str | None = None, offered: str | None = None, store: str | None = None,
          values: dict | None = None, net=None, minimum=None, ask=None, own_prices_only: str | None = None,
          category: str | None = None) -> str:
    """The sentence for a rule. Missing facts fall back to a shorter sentence, never to a blank or a crash."""
    v = values or {}
    if rule == "R5":
        base = (f"The cheaper one isn't the same item: you bought {bought}, this is {offered}."
                if bought and offered else "The cheaper one isn't the same item.")
        # section 3.10: "({Store} only matches its own prices, when that's the reason.)"
        return base + (f" {own_prices_only} only matches its own prices." if own_prices_only else "")
    if rule == "R4" and category and category.lower() == "clearance":
        return f"Clearance items are final sale at {store or 'this store'}."
    if rule in ("R6b", "R6c"):
        return f"{store or 'This store'} doesn't take this kind of request this way."
    if rule == "R8":
        net = net if net is not None else _pick(v, "net", "net_usd", "net_savings", "net_savings_usd")
        minimum = minimum if minimum is not None else _pick(v, "min", "min_usd", "min_net_savings_usd", "minimum")
        if net is not None and minimum is not None:
            return f"Not worth it: you'd only save {usd(net)}; your minimum is {usd(minimum)}."
        return "Not worth it: the savings are below your minimum."
    if rule == "R12":
        ask = ask if ask is not None else _pick(v, "ask", "ask_first_usd", "ask_first", "limit")
        return f"This is above your {usd(ask)} ask-first limit." if ask is not None else "This is above your ask-first limit."
    return RULE_PLAIN.get(rule, "Something didn't add up, so we didn't act.")
