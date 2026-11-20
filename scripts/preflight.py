"""Preflight for Teammate C: run before the dry run and again before recording.

    python scripts/preflight.py                         # checks CLAWBACK_API_BASE / BACKEND_URL, default http://localhost:8001
    python scripts/preflight.py http://localhost:8001

Prints a checklist. It only says whether a secret is SET, never what it is. Exit code 1 if a hard
check fails (demo safety, API down, mock store wrong), 0 otherwise (warnings are for you to read).
"""
import json
import os
import sys
from pathlib import Path

import httpx

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except Exception:
    pass

API = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CLAWBACK_API_BASE") or os.environ.get("BACKEND_URL") or "http://localhost:8001").rstrip("/")
HARD, SOFT = [], []


def row(ok, label, detail="", hard=True):
    mark = "OK  " if ok else ("FAIL" if hard else "WARN")
    print(f" [{mark}] {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        (HARD if hard else SOFT).append(label)


def get(path):
    return httpx.get(API + path, timeout=8.0, trust_env=False)


print(f"\nClawback preflight  ({API})\n")

print("Environment (names only; values are never printed)")
needed = ["SINGLESTORE_URL", "SENSO_API_KEY", "AIRBYTE_CORE_DEST",
          "PERSONA_API_KEY", "PERSONA_TEMPLATE_ID", "PERSONA_WEBHOOK_SECRET", "SIE_URL", "SIE_API_KEY",
          "ARCEE_API_KEY", "LLM_API_KEY", "AMPLITUDE_API_KEY", "MOCK_WEBHOOK_SECRET", "DEMO_SUPPORT_INBOX",
          "DEMO_ALLOWED_RECIPIENTS", "PACT_SIGNING_KEY"]
for k in needed:
    v = os.environ.get(k, "").strip()
    soft_only = k in ("AMPLITUDE_API_KEY", "ARCEE_API_KEY", "SIE_API_KEY", "SIE_URL", "AIRBYTE_CORE_DEST")   # cut-list items have fallbacks
    row(bool(v), k, "set" if v else "NOT SET", hard=not soft_only)
row(len(os.environ.get("PACT_SIGNING_KEY", "")) >= 32, "PACT_SIGNING_KEY is 32+ characters", hard=True)

print("\nDemo safety (section 3.2 / 'never cut')")
dm = os.environ.get("DEMO_MODE", "true")
row(dm.strip().lower() != "false", "DEMO_MODE is on", f'DEMO_MODE="{dm}"')
allowed = [a.strip() for a in os.environ.get("DEMO_ALLOWED_RECIPIENTS", "").split(",") if a.strip()]
row(bool(allowed) and all("@" in a and "*" not in a and " " not in a for a in allowed),
    "DEMO_ALLOWED_RECIPIENTS are exact addresses", f"{len(allowed)} address(es)")
inbox = os.environ.get("DEMO_SUPPORT_INBOX", "").strip().lower()
row(bool(inbox) and inbox in [a.lower() for a in allowed], "support inbox is on the allow-list")

print("\nAPI")
try:
    h = get("/health").json()
    row(True, "API answers /health")
    row(h.get("demo_mode") is True, "API reports demo mode ON")
    mounted = h.get("routers", {})
    for name, state in mounted.items():
        row(state == "mounted", f"router {name}", state, hard=name.startswith("mockstore") or ".analytics." in name or ".debug." in name or ".voice." in name)
except Exception as e:
    row(False, "API answers /health", f"{type(e).__name__}: {e}")
    print("\nStart it with:  uvicorn backend.dev_app:app --port 8001   (no --reload: the Mock Store's prices live in memory)\n")
    sys.exit(1)

print("\nMock store")
try:
    ls = get("/mock/api/listings").json()
    prices = {l["id"]: l["price"] for l in ls}
    row(prices == {1: 129.99, 2: 239.0, 3: 89.0, 4: 89.0, 5: 59.0}, "listings are at seed prices (reset before the demo)", json.dumps(prices))
    log = get("/mock/api/webhook-log").json()
    last = (log.get("deliveries") or [None])[0]
    row(True, "webhook target", log.get("backend_url", ""))
    row(last is None or last["ok"], "last price webhook was delivered", "none sent yet" if last is None else f'status {last["status"]}', hard=False)
    order = get("/mock/api/orders/MS-55021").json()
    row(order["items"][0]["price_paid"] == 239.0, "lamp order MS-55021 exists (return target)")
    row(get("/mock/api/orders/MS-55001").status_code == 200, "earbuds order MS-55001 exists (Siva's seed number)")
    page = get("/under-the-hood/")
    row(page.status_code == 200 and "Under the hood" in page.text, "Under the hood page is served at /under-the-hood/")
except Exception as e:
    row(False, "mock store", f"{type(e).__name__}: {e}")

print("\nStats and Under the hood")
try:
    s = get("/stats").json()
    row(s.get("available") is True, "/stats reads the database", s.get("error", ""), hard=False)
    o = get("/debug/overview").json()
    for panel in ("gate_trace", "pact", "approvals_once", "policy", "matching", "ingest", "safety", "analytics", "tests"):
        p = o.get(panel, {})
        row(p.get("available") is True, f"panel: {panel}", (p.get("error") or "")[:90], hard=False)
    pact = (o.get("pact") or {}).get("pacts") or []
    if pact:
        latest = [v for v in pact[0]["versions"] if v["is_latest"]][0]
        row(latest["signature_ok"] is True, "latest Pact signature shows a check mark", hard=False)
    a = o.get("analytics", {}).get("status", {})
    row(bool(a.get("enabled")), "Amplitude is sending (AMPLITUDE_API_KEY set)", hard=False)
except Exception as e:
    row(False, "stats / overview", f"{type(e).__name__}: {e}", hard=False)

print("\nTests")
f = Path(__file__).resolve().parents[1] / ".pytest_last.json"
if f.exists():
    t = json.loads(f.read_text())
    row(t["failed"] == 0 and t["passed"] > 0, "last pytest run is green", f'{t["passed"]} passed, {t["failed"]} failed, {t["finished_at"]}')
else:
    row(False, "last pytest run is green", "no run recorded: run `pytest -q`", hard=False)

print(f"\n{len(HARD)} failing check(s), {len(SOFT)} warning(s).")
for x in HARD:
    print("  FAIL:", x)
sys.exit(1 if HARD else 0)
