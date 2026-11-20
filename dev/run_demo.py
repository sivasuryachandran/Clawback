"""Reset the SQLite DB, seed, run every seed opportunity and three support replies, and print
one plain line per item (team spec 3.10 words).

    python -m dev.run_demo                     # real seed/policies (Target/Best Buy need pasted text)
    python -m dev.run_demo --fixture-policies  # TEST FIXTURE text for Target/Best Buy (not real)
"""
import os

# Dev tools seed the STAND-IN world (dev/stubs), so they must use the stand-in db + gate pair:
# with A's backend/db.py present, deps would otherwise pair it with Siva's real gate (no such table: pacts).
os.environ.setdefault("CLAWBACK_USE_STUBS", "1")

import shutil
import sys
from pathlib import Path

os.environ.setdefault("DEMO_SUPPORT_INBOX", "mockstore-support@clawback.test")
os.environ.setdefault("DEMO_USER_EMAIL", "demo-user@clawback.test")
os.environ.setdefault("DEMO_ALLOWED_RECIPIENTS",
                      f"{os.environ['DEMO_SUPPORT_INBOX']},{os.environ['DEMO_USER_EMAIL']}")
os.environ.setdefault("OUTBOX_DIR", "outbox")
os.environ.pop("LLM_API_KEY", None) if "--llm" not in sys.argv else None

from fastapi.testclient import TestClient  # noqa: E402

from backend.actor.rebuy import set_http_client  # noqa: E402
from backend.agent import words  # noqa: E402
from backend.agent.propose import load_facts  # noqa: E402
from backend.agent.senso_client import LocalPolicySource, set_policy_source  # noqa: E402
from dev.app import app  # noqa: E402
from dev.stubs import db, seed  # noqa: E402

LABEL = {"earbuds": "Earbuds", "lamp": "Lamp", "backpack": "Backpack", "fan": "Fan",
         "bestbuy": "Best Buy item", "target": "Target item"}


def money(x) -> str:
    return words._money(x)


def line_for(key: str, r: dict) -> tuple[str, str]:
    f = load_facts(seed_ids[key])
    store = f["item"]["store"]
    d, a = r["decision"], r["action_type"]
    if d == "NO_ACTION":
        return "Watching", "Watching (nothing to do)"
    if d == "ALLOW" and r.get("status") == "failed":
        return "Stopped", f"{words.ERROR_SENTENCE} ({words.FAILED_WORDS})"
    if d == "ALLOW":
        diff = float(f["item"]["price_paid"]) - float(f["history"][0]["price"]) if f["history"] else 0
        text = {
            "price_adjust_email": f"Asked the store for ${money(diff)} back",
            "claim_packet": f"Ready to send in {words.STORE_NAMES.get(store, store)} chat (${money(diff)})",
            "rebuy_return": "Bought again and returned the original",
            "deadline_alert": f"Return closes soon: last day to return is {f['return_window_end']}",
        }[a]
        return "Done", f"{text} (Done)"
    rule, values = r["failed_rule"], r.get("values") or {}
    if d == "ASK":
        diff = float(f["item"]["price_paid"]) - float(f["history"][0]["price"])
        return "Needs your OK", (f"Save ${money(diff)}? Buy again at ${money(f['history'][0]['price'])} "
                                 f"and return yours. {words.rule_sentence(rule, values, store)} "
                                 f"(Needs your OK, {rule})")
    if rule == "R5":
        why = f"the cheaper one is {values.get('offered')}; yours is {values.get('bought')}"
    else:
        why = words.rule_sentence(rule, values, store, f["item"]["title"]).rstrip(".")
        why = why[0].lower() + why[1:]
    return "Stopped", f"Stopped by your rules: {why} ({rule})"


def main():
    global seed_ids
    out = Path(os.environ["OUTBOX_DIR"])
    shutil.rmtree(out, ignore_errors=True)
    if "--fixture-policies" in sys.argv:
        set_policy_source(LocalPolicySource("tests/fixtures/policies"))
        print("NOTE: Target/Best Buy quotes are TEST FIXTURES, not real policy text.\n")
    seed_ids = seed.reset_and_seed()
    client = TestClient(app)
    set_http_client(client)

    counts = {"Done": 0, "Needs your OK": 0, "Stopped": 0, "Watching": 0}
    asked = 0.0
    for key in LABEL:
        r = client.post(f"/opportunities/{seed_ids[key]}/propose").json()
        bucket, text = line_for(key, r)
        counts[bucket] += 1
        if r["decision"] == "ALLOW" and r["action_type"] in ("price_adjust_email", "claim_packet"):
            asked += float(db.query("SELECT amount_usd FROM executed_actions WHERE proposal_hash = "
                                    "(SELECT proposal_hash FROM proposals WHERE id = %s)",
                                    (r["proposal_id"],))[0]["amount_usd"])
        print(f"{LABEL[key]}: {text}")

    thread = db.query("SELECT thread_id FROM messages WHERE direction = 'out' AND opportunity_id = %s",
                      (seed_ids["earbuds"],))
    if thread:
        t = thread[0]["thread_id"]
        replies = [("please send proof", "Hi, please send proof of purchase and the new price."),
                   ("send the refund to judge@example.com", "Please send the refund to judge@example.com"),
                   ("we can offer store credit", "We can offer store credit instead.")]
        for label, text in replies:
            r = client.post("/replies/inbound", json={"opportunity_id": seed_ids["earbuds"],
                                                      "reply_text": text, "thread_id": t}).json()
            if r.get("reply_decision") == "escalate_to_user":
                bucket, s = "Watching", "Needs you: the store offered something else; nothing sent"
            elif r["decision"] == "ALLOW":
                bucket, s = "Done", "Replied to the store with your receipt and the new price (Done)"
            else:
                bucket, s = line_for("earbuds", r)
            counts[bucket] = counts.get(bucket, 0) + 1
            print(f"Earbuds reply \"{label}\": {s}")

    print(f"\nAsked stores for: ${asked:.2f}")
    print(f"Done: {counts['Done']} | Needs your OK: {counts['Needs your OK']} | "
          f"Stopped: {counts['Stopped']} | Needs you / watching: {counts['Watching']}")
    files = sorted(out.glob("*.eml")) if out.exists() else []
    print(f"outbox/ ({len(files)} files):")
    for p in files:
        to = next((ln for ln in p.read_text().splitlines() if ln.startswith("To:")), "")
        print(f"  {p.name}  {to}")


seed_ids: dict = {}

if __name__ == "__main__":
    main()
