"""Plain words from team spec 3.10. Rule ids never reach default views; these sentences do."""
from backend.agent.senso_client import STORE_NAMES

DECISION_WORDS = {"ALLOW": "Done", "ASK": "Needs your OK", "BLOCK": "Stopped by your rules",
                  "NO_ACTION": "Watching"}

RULE_SENTENCE = {
    "R0": "Something didn't add up, so we didn't act.",
    "R1": "Please verify it's you again before Clawback can act.",
    "A1": "Please verify it's you again before Clawback can act.",
    "R2": "You haven't allowed Clawback to do this.",
    "R3": "You haven't allowed Clawback at this store.",
    "R4": "This item is final sale (or a type you excluded).",
    "R5": "The cheaper one isn't the same item: you bought {bought}, this is {offered}.",
    "R6": "We couldn't confirm the store's current policy.",
    "R6b": "{Store} doesn't take this kind of request this way.",
    "R6c": "{Store} doesn't take this kind of request this way.",
    "R7": "The window to ask has closed (or closes too soon).",
    "R8": "Not worth it: you'd only save ${net}; your minimum is ${min}.",
    "R8a": "The price just changed; we'll check again.",
    "R9": "That would go over your spending limit.",
    "R10": "We only write to the store's official support (or to you).",
    "R11": "We've already asked about this order.",
    "R13": "We only reply in the same conversation, up to 3 times.",
    "R12": "This is above your ${ask} ask-first limit.",
}


def _money(x) -> str:
    x = float(x)
    return f"{x:.0f}" if x == int(x) else f"{x:.2f}"


def rule_sentence(rule: str | None, values: dict | None, store: str, title: str = "") -> str:
    if not rule:
        return "Everything checked out."
    v = dict(values or {})
    noun = title.split()[-1].lower() if title else "item"
    fmt = {
        "Store": STORE_NAMES.get(store, store),
        "bought": f"{v.get('bought', '?')} {noun}".strip(),
        "offered": f"{v.get('offered', '?')} {noun}".strip(),
        "net": _money(v.get("net", 0)), "min": _money(v.get("min", 0)),
        "ask": _money(v.get("ask", 0)),
    }
    s = RULE_SENTENCE.get(rule, RULE_SENTENCE["R0"]).format(**fmt)
    if rule == "R5" and v.get("item_store") != v.get("listing_store"):
        s += f" ({fmt['Store']} only matches its own prices.)"
    return s


def failed(results: list) -> tuple[str | None, dict]:
    for r in results or []:
        if not r["pass"]:
            return r["rule"], r.get("values", {})
    return None, {}
