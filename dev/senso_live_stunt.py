"""Rehearse the Senso live-edit stunt end to end with the REAL Senso API (stand-in db + gate).

    python -m dev.senso_live_stunt

Needs SENSO_API_KEY in .env and the Senso CLI (see dev/senso_live_edit.py). Steps:
  1. earbuds claim is ALLOWed (Mock Store window 14 days, quote from Senso).
  2. Senso text edited 14 -> 7 days; table still says 14 -> the policy step says "needs review"
     and the gate stops on R6 (the two disagree; nothing is sent).
  3. Table row also set to 7 -> the SAME stored claim stops on R7, quoting the new Senso sentence.
  4. Always reverts BOTH (Senso text and table row) at the end.
"""
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv(".env")
os.environ.setdefault("CLAWBACK_USE_STUBS", "1")
KEY = os.environ.get("SENSO_API_KEY")


def main() -> int:
    if not KEY:
        print("SENSO_API_KEY is not set.")
        return 2
    import json

    from backend.agent.policy import check_policy
    from backend.agent.senso_client import SensoPolicySource, set_policy_source
    from dev import senso_live_edit
    from dev.firedrill_local import fresh_world
    from dev.stubs import db
    from dev.stubs import pact as gate

    ok = True

    def step(name, cond, detail=""):
        nonlocal ok
        ok &= bool(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail}")

    with fresh_world("seed/policies") as w:
        oid = w.ids["earbuds"]
        set_policy_source(SensoPolicySource(KEY))
        first = w.client.post(f"/opportunities/{oid}/propose").json()
        step("1. claim allowed with the Senso quote", first["decision"] == "ALLOW", f"({first['decision']})")
        stored = json.loads(db.query("SELECT payload_json FROM proposals WHERE id = %s",
                                     (first["proposal_id"],))[0]["payload_json"])
        try:
            t = time.time()
            step("2a. Senso edited 14 -> 7", senso_live_edit.main("edit") == 0, f"({time.time() - t:.0f}s to searchable)")
            db.execute("DELETE FROM executed_actions")
            row = db.query("SELECT * FROM policy_checks WHERE id = %s", (check_policy(oid, "price_adjust_email"),))[0]
            step("2b. Senso says 7, table says 14: needs review, no source id",
                 row["senso_source_id"] is None and "needs review" in (row["answer"] or ""), f"-> {row['answer']}")
            the_pact, _ = gate.active_pact("u_demo")
            d = gate.run_gate(stored, the_pact, gate.build_context(oid, {"id": "u_demo"}))
            step("2c. gate stops on R6", (d.decision, d.failed_rule) == ("BLOCK", "R6"), f"-> {d.decision} {d.failed_rule}")
            db.execute("UPDATE store_policies SET adjust_window_days = 7 WHERE store = 'mockstore'")
            row = db.query("SELECT * FROM policy_checks WHERE id = %s", (check_policy(oid, "price_adjust_email"),))[0]
            step("3a. both say 7: normal citation", bool(row["senso_source_id"]) and "7 days" in row["cited_text"],
                 f"-> \"{row['cited_text']}\" (rev {row['revision']})")
            d = gate.run_gate(stored, the_pact, gate.build_context(oid, {"id": "u_demo"}))
            step("3b. same claim stops on R7", (d.decision, d.failed_rule) == ("BLOCK", "R7"), f"-> {d.decision} {d.failed_rule}")
        finally:
            db.execute("UPDATE store_policies SET adjust_window_days = 14 WHERE store = 'mockstore'")
            rc = senso_live_edit.main("revert")
            step("4. reverted Senso text and table row", rc == 0)
    print("ALL PASS" if ok else "SOME STEPS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
