"""Pre-stage check: one OK / WARN / FAIL line per thing that can quietly break the demo.

    python -m dev.preflight                 # config, DB, transport, policy source, mock store
    python -m dev.preflight --warm          # + ask the policy source for every open opportunity
    python -m dev.preflight --live          # + one real LLM call (costs a request)

Exit code 1 if anything FAILs. Run it before the dry run and again before going on stage.
"""
import os
import sys
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv

OK, WARN, FAIL = "OK", "WARN", "FAIL"
RETAILERS = ("target.com", "bestbuy.com", "amazon.com")


def run_checks(http=None, live: bool = False, warm: bool = False) -> list[tuple[str, str, str]]:
    from backend.actor.email import allowed_recipients, demo_mode
    from backend.agent.deps import db
    from backend.agent.senso_client import STORE_NAMES, get_policy_source

    out: list[tuple[str, str, str]] = []
    add = lambda level, name, detail="": out.append((level, name, detail))  # noqa: E731

    # --- email safety config ---
    add(OK if demo_mode() else WARN, "demo mode",
        "on" if demo_mode() else "OFF: DEMO_MODE is exactly 'false'; live recipients rules apply")
    allow = allowed_recipients()
    add(OK if allow else FAIL, "allowlist", ", ".join(sorted(allow)) or "DEMO_ALLOWED_RECIPIENTS is empty")
    bad = [a for a in allow if a.rsplit("@", 1)[-1] in RETAILERS]
    if bad:
        add(FAIL, "allowlist has a retailer", ", ".join(bad))
    inbox = os.environ.get("DEMO_SUPPORT_INBOX", "").strip().lower()
    if not inbox:
        add(FAIL, "support inbox", "DEMO_SUPPORT_INBOX unset: the earbuds claim would stop on R0")
    elif inbox not in allow:
        add(FAIL, "support inbox", f"{inbox} not on the allowlist: the claim would stop on R10")
    else:
        add(OK, "support inbox", inbox)

    # --- database ---
    try:
        from backend.agent.propose import USER_ID
        users = db.query("SELECT email FROM users WHERE id = %s", (USER_ID,))
        if not users:
            add(FAIL, "demo user", f"{USER_ID} missing from users")
        else:
            email = (users[0]["email"] or "").strip().lower()
            add(OK if email in allow else FAIL, "demo user email",
                email if email in allow else f"{email or '(none)'} not on the allowlist: claim packets stop on R10")
        n = db.query("SELECT COUNT(*) AS n FROM opportunities WHERE status = 'open'")[0]["n"]
        add(OK if n else WARN, "open opportunities", str(n))
    except Exception as e:
        add(FAIL, "database", repr(e)[:200])

    # --- transport ---
    kind = os.environ.get("EMAIL_TRANSPORT", "file")
    if kind == "file":
        folder = Path(os.environ.get("OUTBOX_DIR", "outbox"))
        try:
            folder.mkdir(parents=True, exist_ok=True)
            probe = folder / f".preflight-{uuid.uuid4().hex}"
            probe.write_text("x")
            probe.unlink()
            add(OK, "email transport", f"file -> {folder}/")
        except Exception as e:
            add(FAIL, "email transport", f"outbox not writable: {e!r}")
    else:
        add(FAIL, "email transport", f"{kind}: not wired in the rehearsal build")

    # --- LLM ---
    if not os.environ.get("LLM_API_KEY"):
        add(WARN, "LLM", "LLM_API_KEY unset: offline rules decide every proposal")
    elif not live:
        add(WARN, "LLM", "key set but not pinged (run with --live)")
    else:
        try:
            from backend.agent.llm import get_llm
            from backend.agent.propose import SYSTEM, TOOL, LLMChoice, build_prompt, load_facts
            oid = db.query("SELECT id FROM opportunities ORDER BY id LIMIT 1")[0]["id"]
            f = load_facts(oid)
            t = time.time()
            raw = get_llm().call_tool(SYSTEM, build_prompt(f), TOOL, f)
            ms = int((time.time() - t) * 1000)
            try:
                c = LLMChoice.model_validate(raw)
                add(OK, "LLM", f"{os.environ.get('LLM_MODEL', 'claude-opus-5-5')} -> {c.action_type} in {ms} ms")
            except Exception as e:
                add(WARN, "LLM", f"answered in {ms} ms but invalid ({str(e)[:120]}); would retry, then do nothing")
        except Exception as e:
            add(FAIL, "LLM", f"unreachable: {e!r}"[:200] + " (offline rules will take over)")

    # --- policy source ---
    src = get_policy_source()
    from backend.agent.policy import _ask_with_timeout
    try:
        stores = [r["store"] for r in db.query("SELECT store FROM store_policies ORDER BY store")]
    except Exception:
        stores = list(STORE_NAMES)
    for store in stores:
        q = f"Does {STORE_NAMES.get(store, store)} adjust or match a lower price after purchase, and for how long?"
        t = time.time()
        try:
            r = _ask_with_timeout(store, q)
            ms = int((time.time() - t) * 1000)
            if r.get("source_id") and r.get("cited_text"):
                add(OK, f"policy {store}", f"{type(src).__name__} {ms} ms: \"{r['cited_text'][:70]}\"")
            else:
                add(FAIL, f"policy {store}", f"no citation ({ms} ms): every {store} item stops on R6. "
                                             "Paste the official text / check the Senso ingest.")
        except Exception as e:
            add(FAIL, f"policy {store}", f"{type(src).__name__} unavailable: {e!r}"[:200])

    # --- mock store ---
    try:
        import httpx
        client = http or httpx.Client(base_url=os.environ.get("CLAWBACK_BASE_URL", "http://localhost:8000"),
                                      timeout=5)
        r = client.get("/mock/api/listings")
        r.raise_for_status()
        add(OK, "mock store", f"{len(r.json())} listings")
    except Exception as e:
        add(FAIL, "mock store", f"GET /mock/api/listings failed: {e!r}"[:200] + " (is the server running?)")

    # --- warm citations ---
    if warm:
        from backend.agent.policy import warm_policies
        for w in warm_policies():
            add(OK if w["senso_source_id"] else FAIL, f"warm opp {w['opportunity_id']} ({w['store']})",
                f"{w['ms']} ms, cited" if w["senso_source_id"] else f"{w['ms']} ms, NO citation")
    return out


def main() -> int:
    load_dotenv()
    rows = run_checks(live="--live" in sys.argv, warm="--warm" in sys.argv)
    width = max(len(n) for _, n, _ in rows)
    for level, name, detail in rows:
        print(f"{level:<4}  {name:<{width}}  {detail}")
    fails = sum(1 for lv, _, _ in rows if lv == FAIL)
    warns = sum(1 for lv, _, _ in rows if lv == WARN)
    print(f"\n{fails} FAIL, {warns} WARN" + ("  -> ready" if not fails else "  -> fix the FAIL lines first"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
