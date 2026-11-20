"""GET /debug/overview: everything the Under the hood page shows (section 3.10; Teammate C).

READ-ONLY. Every panel is built independently: if one query fails, that panel becomes
{"available": false, "error": ...} and the rest still load. Nothing here writes to any table.

Panels: gate_trace, pact, approvals_once, policy, matching, live_queries, ingest, safety,
analytics, tests.
"""
from __future__ import annotations

import importlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from backend.analytics import events

from . import dbaccess as dba
from . import queries, sample
from .plain import as_rules, first_failure

ROOT = Path(__file__).resolve().parents[2]
SAMPLE_NOTE = "SAMPLE DATA: nothing below is from the live system."

MATCH_SQL_DOCUMENTED = (
    "SELECT id, store, sku, upc, model_number, variant, title,\n"
    "       DOT_PRODUCT(title_vec, :vec) AS similarity\n"
    "FROM listings\n"
    "ORDER BY similarity DESC\n"
    "LIMIT 10;\n"
    "-- then Superlinked SIE reranks these 10 and the top 5 are kept")


def _panel(fn: Callable[[], dict]) -> dict:
    t0 = time.perf_counter()
    try:
        out = fn()
        out.setdefault("available", True)
    except Exception as exc:                       # any failure = this panel only
        out = {"available": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
    out["took_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return out


def _short(h) -> str | None:
    if not h:
        return None
    s = str(h)
    return s[:14] + "…" if len(s) > 14 else s


def _lookup(paths: tuple[str, ...], name: str):
    """First importable `name` among the module paths, or None (teammate code may not exist yet)."""
    for p in paths:
        try:
            fn = getattr(importlib.import_module(p), name, None)
            if fn:
                return fn
        except Exception:
            continue
    return None


def pact_verifiers(use_sample: bool):
    """(verify_text, verify_revocation, source label). Siva's functions in real mode."""
    if use_sample:
        return sample.verify_sample, sample.verify_revocation_sample, "sample (throwaway key)"
    vt = _lookup(("backend.pact", "backend.pact.sign"), "verify_text")
    vr = _lookup(("backend.pact", "backend.pact.sign"), "verify_revocation")
    return vt, vr, "backend.pact (Siva)"


def check_revocation(verify_rev, row: dict) -> bool:
    """Verify a pact_revocations row. The database hands back `revoked_at` as a datetime, but the signature was made
    over whatever text the signer used, so a correct pause record could show a false X. Try the usual renderings of
    the same instant; the HMAC must still match one of them, so a forged or edited record still fails."""
    candidates = [dict(row)]
    t = dba.to_dt(row.get("revoked_at"))
    if t is not None:
        naive = t.replace(tzinfo=None)
        for text in (naive.strftime("%Y-%m-%d %H:%M:%S"), naive.isoformat(), naive.isoformat(sep=" "),
                     t.isoformat(), naive.isoformat() + "Z", naive.strftime("%Y-%m-%dT%H:%M:%S"),
                     naive.strftime("%Y-%m-%dT%H:%M:%SZ")):
            candidates.append(dict(row, revoked_at=text))
    for c in candidates:
        try:
            if verify_rev(c):
                return True
        except Exception:
            continue
    return False


def _norm_rules(raw) -> list[dict]:
    out = []
    for r in as_rules(raw):
        if isinstance(r, dict):
            out.append({"rule": r.get("rule"), "pass": r.get("pass"), "why": r.get("why"),
                        "values": r.get("values") or {}})
    return out


# ---- panels -----------------------------------------------------------------------------------

def gate_trace() -> dict:
    rows = dba.query(
        "SELECT a.id, a.ts, a.pact_id, a.pact_version, a.proposal_id, a.proposal_hash, a.decision, "
        "a.rule_results_json, a.action_result, p.action_type, p.opportunity_id, li.title, li.variant, li.store "
        "FROM audit_log a "
        "LEFT JOIN proposals p ON p.id = a.proposal_id "
        "LEFT JOIN opportunities o ON o.id = p.opportunity_id "
        "LEFT JOIN line_items li ON li.id = o.line_item_id "
        "ORDER BY a.id DESC LIMIT 10")
    out = []
    for r in rows:
        rules = _norm_rules(r.get("rule_results_json"))
        fail = first_failure(rules)
        out.append({
            "audit_id": r["id"], "ts": dba.clean(r["ts"]), "decision": r["decision"],
            "action_type": r.get("action_type"), "proposal_id": r.get("proposal_id"),
            "opportunity_id": r.get("opportunity_id"),
            "item": " ".join(x for x in (r.get("title"), f"({r['variant']})" if r.get("variant") else None) if x) or None,
            "store": r.get("store"), "pact_id": r.get("pact_id"), "pact_version": r.get("pact_version"),
            "proposal_hash": r.get("proposal_hash"), "short_hash": _short(r.get("proposal_hash")),
            "failed_rule": fail.get("rule") if fail else None, "rules": rules,
            "action_result": dba.jsonish(r.get("action_result")),
        })
    return {"decisions": out}


def pact_panel(use_sample: bool) -> dict:
    verify_text, verify_rev, source = pact_verifiers(use_sample)
    rows = dba.query("SELECT pact_id, version, user_id, pact_text, created_at, status FROM pacts "
                     "ORDER BY pact_id, version DESC")
    revs = dba.query("SELECT pact_id, version, revoked_at, reason, signature FROM pact_revocations "
                     "ORDER BY revoked_at DESC")
    users = {u["id"]: u for u in dba.query(
        "SELECT id, name, persona_status, persona_inquiry_id, verified_at, reverified_at FROM users")}
    rev_out = []
    for r in revs:
        ok = check_revocation(verify_rev, r) if verify_rev else None
        rev_out.append({**dba.clean_row(r), "signature_ok": ok})
    valid_rev = {(r["pact_id"], int(r["version"])) for r in rev_out if r["signature_ok"]}

    pacts: dict[str, dict] = {}
    for r in rows:
        pid = r["pact_id"]
        p = pacts.setdefault(pid, {"pact_id": pid, "user_id": r["user_id"], "versions": []})
        parsed = verified = None
        text = r["pact_text"] or ""
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        if verify_text:
            try:
                verified = verify_text(text)
            except Exception:
                verified = None
        p["versions"].append({
            "version": r["version"], "created_at": dba.clean(r["created_at"]),
            "status_column": r["status"],                       # display only; the gate never reads it
            "signature_ok": (verified is not None) if verify_text else None,
            "hash": (parsed or {}).get("hash") if isinstance(parsed, dict) else None,
            "revoked": (pid, int(r["version"])) in valid_rev,
            "pact_text": json.dumps(parsed, indent=2, sort_keys=True) if isinstance(parsed, dict) else text,
            "raw_text": text,
        })
    for p in pacts.values():
        p["latest_version"] = max(v["version"] for v in p["versions"])
        for v in p["versions"]:
            v["is_latest"] = v["version"] == p["latest_version"]
        u = users.get(p["user_id"]) or {}
        p["owner"] = {"id": p["user_id"], "name": u.get("name"), "persona_status": u.get("persona_status"),
                      "persona_inquiry_id": u.get("persona_inquiry_id"),
                      "verified_at": dba.clean(u.get("verified_at")), "reverified_at": dba.clean(u.get("reverified_at"))}
    return {"pacts": list(pacts.values()), "revocations": rev_out, "verifier": source,
            "verifier_found": verify_text is not None}


def approvals_once() -> dict:
    appr = dba.query("SELECT id, proposal_id, proposal_hash, user_id, amount_usd, status, expires_at, decided_at "
                     "FROM approvals ORDER BY id DESC LIMIT 20")
    ex = dba.query("SELECT proposal_hash, line_item_id, action_type, amount_usd, status, result, created_at "
                   "FROM executed_actions ORDER BY created_at DESC LIMIT 20")
    return {"approvals": [dba.clean_row(r) for r in appr],
            "executed": [dba.clean_row(r) for r in ex],
            "note": "executed_actions is keyed by proposal hash: a second click on the same action hits the key and does nothing."}


def policy_panel() -> dict:
    rows = dba.query("SELECT pc.id, pc.opportunity_id, pc.store, pc.question, pc.cited_text, pc.source_url, "
                     "pc.senso_source_id, pc.revision, pc.fetched_at FROM policy_checks pc ORDER BY pc.id DESC LIMIT 30")
    return {"checks": [dba.clean_row(r) for r in rows]}


def _sample_candidates(line_item_id: int) -> list[dict]:
    db = sample.build()
    item = db.query("SELECT store, title, sku, variant FROM line_items WHERE id=%s", (line_item_id,))
    if not item:
        return []
    it = item[0]
    out = []
    for l in db.query("SELECT id, store, sku, variant, title FROM listings ORDER BY id"):
        exact = l["store"] == it["store"] and l["sku"] == it["sku"] and l["variant"] == it["variant"]
        same_title = l["title"].split()[:2] == it["title"].split()[:2]
        if not (exact or same_title):
            continue
        out.append({"listing_id": l["id"], "store": l["store"], "sku": l["sku"], "variant": l["variant"],
                    "title": l["title"], "similarity": 0.96 if exact else 0.97,
                    "rerank_score": 0.93 if exact else 0.95, "exact": exact})
    return sorted(out, key=lambda c: -c["similarity"])[:5]


MATCH_CACHE_TTL_S = 30.0
_match_cache: dict[tuple, tuple[float, list]] = {}


def _items() -> list[dict]:
    return [dba.clean_row(r) for r in dba.query(
        "SELECT li.id, li.title, li.variant, li.store, li.price_paid FROM line_items li ORDER BY li.id")]


def matching_panel(line_item_id: int | None, use_sample: bool) -> dict:
    """The cheap half of the Matching panel, built on every refresh: the purchases and the SQL.
    It deliberately does NOT call the matcher: that is an embedding + rerank request to SIE and this
    runs every few seconds. Candidates come from matching_candidates(), on demand."""
    items = _items()
    chosen = line_item_id if line_item_id is not None else (items[0]["id"] if items else None)
    sql, source = MATCH_SQL_DOCUMENTED, "documented matcher query (the matcher's own MATCH_SQL is shown when it exposes one)"
    if not use_sample:
        own_sql = _lookup(("backend.watch", "backend.watch.matcher"), "MATCH_SQL")
        if own_sql:
            sql, source = str(own_sql), "backend.watch (Teammate A)"
    return {"items": items, "line_item_id": chosen, "sql": sql, "sql_source": source, "candidates": []}


def matching_candidates(line_item_id: int | None, use_sample: bool, now: float | None = None) -> dict:
    """Run the matcher for one purchase (on demand; cached for 30 s so a double click or a reload
    does not spend two SIE requests)."""
    out = matching_panel(line_item_id, use_sample)
    chosen = out["line_item_id"]
    if chosen is None:
        return out
    if use_sample:
        out["candidates"] = _sample_candidates(chosen)
        out["note"] = "SAMPLE: similarity numbers are stand-ins."
        return out
    match = _lookup(("backend.watch", "backend.watch.matcher"), "match")
    if not match:
        raise RuntimeError("the matcher (backend.watch.match) is not available yet")
    t = time.monotonic() if now is None else now
    hit = _match_cache.get((chosen,))
    if hit and t - hit[0] < MATCH_CACHE_TTL_S:
        cands = hit[1]
        out["note"] = f"cached {int(t - hit[0])} s ago"
    else:
        cands = [dba.clean_row(c) if isinstance(c, dict) else c for c in match(int(chosen))]
        _match_cache[(chosen,)] = (t, cands)
    out["candidates"] = cands
    return out


def ingest_panel() -> dict:
    emails = dba.query("SELECT id, gmail_msg_id, from_addr, subject, received_at, parsed FROM raw_emails "
                       "ORDER BY id DESC LIMIT 10")
    items = dba.query("SELECT li.id, li.store, o.order_no, li.title, li.variant, li.price_paid, li.parse_confidence "
                      "FROM line_items li LEFT JOIN orders o ON o.id = li.order_id ORDER BY li.id DESC LIMIT 10")
    parse_events = events.recent("receipt_parsed", 10)
    costs = [e["props"].get("cost_usd") for e in parse_events if isinstance(e["props"].get("cost_usd"), (int, float))]
    return {"emails": [dba.clean_row(r) for r in emails], "line_items": [dba.clean_row(r) for r in items],
            "parse_events": parse_events,
            "avg_cost_per_receipt_usd": round(sum(costs) / len(costs), 6) if costs else None,
            "note": "model, tokens and latency come from receipt_parsed events (the schema has no table for them)."}


def safety_panel() -> dict:
    raw = os.environ.get("DEMO_MODE", "true")
    allowed = [a.strip() for a in os.environ.get("DEMO_ALLOWED_RECIPIENTS", "").split(",") if a.strip()]
    out = {"demo_mode": raw.strip().lower() != "false", "demo_mode_raw": raw, "allowed_recipients": allowed,
           "support_inbox": os.environ.get("DEMO_SUPPORT_INBOX", "") or None, "messages": [], "messages_error": None}
    try:
        out["messages"] = [dba.clean_row(r) for r in dba.query(
            "SELECT m.id, m.opportunity_id, m.direction, m.thread_id, m.to_addr, m.subject, m.sent_at, m.proposal_hash "
            "FROM messages m WHERE (m.direction IS NULL OR LOWER(m.direction) NOT IN ('in', 'inbound', 'incoming', 'received')) ORDER BY m.id DESC LIMIT 20")]
    except Exception as exc:      # the env half of this panel is still useful
        out["messages_error"] = f"{type(exc).__name__}: {exc}"[:200]
    return out


def analytics_panel(use_sample: bool) -> dict:
    recent = sample.sample_events() if use_sample else events.recent(limit=10)
    return {"status": events.status(), "dashboard_url": os.environ.get("AMPLITUDE_DASHBOARD_URL") or None,
            "today_counts": events.today_counts(), "recent": recent}


def tests_panel() -> dict:
    p = ROOT / ".pytest_last.json"
    if not p.exists():
        return {"available": True, "ran": False, "note": "No test run recorded yet. Run `pytest -q` from the repo root."}
    return {"ran": True, **json.loads(p.read_text())}


# ---- assembly -----------------------------------------------------------------------------------

def build(line_item_id: int | None = None, use_sample: bool = False, routers: dict | None = None) -> dict:
    def go():
        return {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sample": use_sample, "sample_note": SAMPLE_NOTE if use_sample else None,
            "routers": routers or {},
            "gate_trace": _panel(gate_trace),
            "pact": _panel(lambda: pact_panel(use_sample)),
            "approvals_once": _panel(approvals_once),
            "policy": _panel(policy_panel),
            "matching": _panel(lambda: matching_panel(line_item_id, use_sample)),    # no matcher call here
            "live_queries": {"available": True, "queries": queries.catalog()},
            "ingest": _panel(ingest_panel),
            "safety": _panel(safety_panel),
            "analytics": _panel(lambda: analytics_panel(use_sample)),
            "tests": _panel(tests_panel),
        }
    if use_sample:
        with dba.use_override(sample.build().query):
            return go()
    return go()
