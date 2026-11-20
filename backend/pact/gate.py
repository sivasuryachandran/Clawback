"""Deterministic Pact gate (rules v4, section 3.9 of the team file).

Pure Python: no database, no network, no LLM. Everything the gate compares comes
from GateContext (built from the database), never from the proposal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from . import sign

CLAIMS = {"price_adjust_email", "claim_packet"}
EMAILS = CLAIMS | {"claim_followup"}
ACTIONS = CLAIMS | {"claim_followup", "rebuy_return", "deadline_alert"}
TEMPLATES = {"price_adjust_email": "price_adjust_v1", "claim_packet": "packet_v1", "claim_followup": "proof_v1"}

PERSONA_MAX_AGE = timedelta(days=30)
CITATION_MAX_AGE = timedelta(hours=24)
PRICE_MAX_AGE = timedelta(minutes=15)
RECHECK_MAX_AGE = timedelta(minutes=15)


# ---------------------------------------------------------------- coercion
def _date(v: Any) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _ts(v: Any) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.replace(tzinfo=None)
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    return datetime.fromisoformat(str(v).replace("Z", "")[:26]).replace(tzinfo=None)


def _num(v: Any) -> float:
    return float(v)


def _norm(v: Any) -> str:
    return str(v or "").strip().lower()


# ---------------------------------------------------------------- types
@dataclass
class GateContext:
    today: Any
    now: Any
    opportunity: dict
    item: dict
    listing: dict | None
    policy: dict
    policy_check: dict | None
    pact_meta: dict
    persona_status: str = "none"
    persona_verified_at: Any = None
    reverified_at: Any = None
    session_user_id: str | None = None
    pact_owner_id: str | None = None
    user_email: str = ""
    month_committed: float = 0.0
    prior_claims: list = field(default_factory=list)
    claim: dict | None = None
    demo_mode: bool = True
    demo_recipients: frozenset = frozenset()


@dataclass
class Decision:
    decision: str                       # ALLOW | ASK | BLOCK | NO_ACTION
    results: list = field(default_factory=list)
    proposal_hash: str | None = None
    window_ends: str | None = None

    @property
    def failed_rule(self) -> str | None:
        for r in self.results:
            if not r["pass"]:
                return r["rule"]
        return None


class _Stop(Exception):
    def __init__(self, decision: str):
        self.decision = decision


class _Run:
    """Collects rule results; the first failure stops the run."""

    def __init__(self) -> None:
        self.results: list[dict] = []

    def check(self, rule: str, ok: bool, why: str, values: dict | None = None, on_fail: str = "BLOCK") -> None:
        self.results.append({"rule": rule, "pass": bool(ok), "why": why, "values": values or {}})
        if not ok:
            raise _Stop(on_fail)

    def skip(self, rule: str, why: str) -> None:
        self.results.append({"rule": rule, "pass": True, "why": "skipped: " + why, "values": {}})


# ---------------------------------------------------------------- helpers
def window_end(action: str, item: dict, policy: dict) -> date | None:
    """Adjust window for claims/follow-ups, return window for rebuys/alerts."""
    if action in CLAIMS or action == "claim_followup":
        days, starts = policy.get("adjust_window_days"), policy.get("adjust_window_starts")
    else:
        days, starts = policy.get("return_window_days"), policy.get("return_window_starts")
    if days is None:
        return None
    start = _date(item.get("order_date"))
    if starts == "delivery" and _date(item.get("delivery_date")):
        start = _date(item.get("delivery_date"))     # unknown delivery -> purchase
    return None if start is None else start + timedelta(days=int(days))


def _same_product(item: dict, listing: dict, same_store: bool) -> tuple[bool, dict]:
    variant_ok = _norm(item.get("variant")) == _norm(listing.get("variant"))
    if same_store:
        ident = bool(item.get("sku")) and _norm(item.get("sku")) == _norm(listing.get("sku"))
    else:
        upc = bool(item.get("upc")) and _norm(item.get("upc")) == _norm(listing.get("upc"))
        model = bool(item.get("model_number")) and _norm(item.get("model_number")) == _norm(listing.get("model_number"))
        ident = upc or model
    vals = {"bought": f'{item.get("title") or item.get("sku")} {item.get("variant") or ""}'.strip(),
            "offered": f'{listing.get("title") or listing.get("sku")} {listing.get("variant") or ""}'.strip(),
            "same_store": same_store, "variant_match": variant_ok}
    return ident and variant_ok, vals


def _email_to(p: dict) -> str:
    return _norm((p.get("email") or {}).get("to"))


# ---------------------------------------------------------------- the gate
def run_gate(p: dict, pact: dict | None, ctx: GateContext, human_approved: bool = False) -> Decision:
    run = _Run()
    try:
        h = sign.proposal_hash(p) if isinstance(p, dict) else None
    except Exception:
        h = None
    try:
        _evaluate(run, p, pact, ctx, human_approved)
        verdict = "ALLOW"
    except _Stop as s:
        verdict = s.decision
    except Exception as exc:                      # the gate never crashes
        run.results.append({"rule": "R0", "pass": False, "why": f"internal error: {exc}", "values": {"error": str(exc)}})
        verdict = "BLOCK"
    ends = None
    try:
        if isinstance(p, dict) and ctx.item and ctx.policy:
            e = window_end(p.get("action_type"), ctx.item, ctx.policy)
            ends = e.isoformat() if e else None
    except Exception:
        pass
    return Decision(verdict, run.results, h, ends)


def _evaluate(run: _Run, p: dict, pact: dict | None, ctx: GateContext, human_approved: bool) -> None:
    item, listing, policy, opp = ctx.item, ctx.listing, ctx.policy, ctx.opportunity
    today, now = _date(ctx.today), _ts(ctx.now)

    # R0 — proposal well formed and bound to the opportunity's own rows
    run.check("R0", isinstance(p, dict), "proposal is an object")
    a = p.get("action_type")
    run.check("R0", a in ACTIONS, f"action_type {a!r} is a gated action", {"action_type": a})
    if a in EMAILS:
        e = p.get("email") or {}
        ok = bool(e.get("to")) and e.get("template") == TEMPLATES[a]
        run.check("R0", ok, "email block has a recipient and the right template", {"template": e.get("template")})
    if a == "rebuy_return":
        run.check("R0", bool((p.get("rebuy") or {}).get("listing_id")) and bool((p.get("return") or {}).get("order_no")),
                  "rebuy block has listing_id and return.order_no")
    if a == "deadline_alert":
        run.check("R0", bool(p.get("alert_at")), "alert has alert_at")
        _date(p["alert_at"])
    bound = {
        "opportunity_id": (p.get("opportunity_id"), opp.get("id")),
        "line_item_id": (p.get("line_item_id"), item.get("id")),
        "store": (p.get("store"), item.get("store")),
    }
    if a in CLAIMS or a == "rebuy_return":
        bound["listing_id"] = (p.get("listing_id"), opp.get("listing_id"))
    if a == "rebuy_return":
        bound["rebuy.listing_id"] = ((p.get("rebuy") or {}).get("listing_id"), opp.get("listing_id"))
        bound["return.order_no"] = ((p.get("return") or {}).get("order_no"), item.get("order_no"))
    for k, (got, want) in bound.items():
        run.check("R0", got is not None and str(got) == str(want), f"{k} matches the opportunity's rows", {"field": k, "proposal": got, "database": want})

    # R1 — Pact is real, current and the user is verified
    pact_ok = pact is not None and sign.verify_dict(pact)
    run.check("R1", pact_ok, "Pact signature and hash verify")
    meta = ctx.pact_meta or {}
    run.check("R1", not meta.get("revoked"), "Pact has not been revoked", {"revoked": bool(meta.get("revoked"))})
    run.check("R1", bool(meta.get("is_latest")), "this is the latest Pact version", {"version": pact.get("version")})
    exp = _date(pact.get("expires_at"))
    run.check("R1", exp is not None and today <= exp, "Pact has not expired", {"expires_at": str(exp)})
    verified = _ts(ctx.persona_verified_at)
    run.check("R1", ctx.persona_status == "approved" and verified is not None and now - verified <= PERSONA_MAX_AGE,
              "Persona approved within 30 days", {"status": ctx.persona_status, "verified_at": str(verified)})

    # R2 — action allowed by the Pact
    allowed = set(pact.get("allowed_actions") or [])
    ok = a in allowed or (a == "claim_followup" and bool(allowed & CLAIMS))
    run.check("R2", ok, "action is allowed by the Pact", {"action": a, "allowed": sorted(allowed)})

    # R3 — store allowed (from the database, never the proposal)
    stores = {_norm(s) for s in pact.get("stores") or []}
    run.check("R3", _norm(item.get("store")) in stores, "item's store is allowed", {"store": item.get("store")})
    if listing:
        run.check("R3", _norm(listing.get("store")) in stores, "listing's store is allowed", {"store": listing.get("store")})

    # R4 — category not excluded / final sale
    cat = _norm(item.get("category"))
    excluded = {_norm(c) for c in pact.get("excluded_categories") or []}
    final_sale = {_norm(c) for c in policy.get("final_sale_categories") or []}
    run.check("R4", cat not in excluded and cat not in final_sale, "category is not excluded or final sale",
              {"category": cat, "excluded": sorted(excluded), "final_sale": sorted(final_sale)})

    # R6 — fresh Senso citation for this store and opportunity
    pc = ctx.policy_check
    fetched = _ts(pc.get("fetched_at")) if pc else None
    ok = bool(pc) and bool(pc.get("senso_source_id")) and _norm(pc.get("store")) == _norm(item.get("store")) \
        and str(pc.get("opportunity_id")) == str(opp.get("id")) and fetched is not None and now - fetched <= CITATION_MAX_AGE
    run.check("R6", ok, "policy citation for this store and opportunity, under 24 hours old",
              {"source": (pc or {}).get("senso_source_id"), "revision": (pc or {}).get("revision"), "fetched_at": str(fetched)})

    # R6b / R6c
    if a in CLAIMS or a == "claim_followup":
        run.check("R6b", bool(policy.get("price_adjust_allowed")), "store offers price adjustments")
    if a == "price_adjust_email":
        run.check("R6c", policy.get("claim_channel") == "email", "store takes claims by email", {"channel": policy.get("claim_channel")})
    if a == "claim_packet":
        run.check("R6c", policy.get("claim_channel") != "email", "store does not take email claims", {"channel": policy.get("claim_channel")})

    # R7 — window
    end = window_end(a, item, policy)
    run.check("R7", end is not None, "store policy defines a window")
    left = (end - today).days
    need = int(pact.get("buffer_days", 2)) if (a in CLAIMS or a == "rebuy_return") else 0
    run.check("R7", left >= need, "window is open with enough time left", {"window_ends": end.isoformat(), "days_left": left, "needed": need})
    if a == "deadline_alert":
        run.check("R7", _date(p["alert_at"]) <= end, "alert is set before the window closes", {"alert_at": p["alert_at"], "window_ends": end.isoformat()})

    # R5 — same product
    if a in CLAIMS or a == "rebuy_return":
        run.check("R5", listing is not None, "a listing exists to compare")
        same_store = _norm(item.get("store")) == _norm(listing.get("store"))
        ok, vals = _same_product(item, listing, same_store)
        run.check("R5", ok, "cheaper listing is the same product", vals)
        if a in CLAIMS:
            run.check("R5", same_store or bool(policy.get("competitor_match_allowed")), "store only matches its own prices", vals)

    # R8 / R8a — savings, then freshness (order matters: the cached $6 case must show R8)
    if a in CLAIMS or a == "rebuy_return":
        paid, price = _num(item["price_paid"]), _num(listing["price"])
        fees = 0.0
        if a == "rebuy_return":
            fees = _num(policy.get("restocking_fee_pct") or 0) * paid + _num(policy.get("return_shipping_usd") or 0)
        net = round(paid - price - fees, 2)
        minimum = _num(pact.get("min_net_savings", 0))
        run.check("R8", net >= minimum, "net savings meet the minimum", {"paid": paid, "price": price, "fees": fees, "net": net, "min": minimum})
        seen = _ts(listing.get("seen_at"))
        run.check("R8a", seen is not None and now - seen <= PRICE_MAX_AGE, "price seen within 15 minutes", {"seen_at": str(seen)})

    # R9 — spending caps (rebuy)
    if a == "rebuy_return":
        price = _num(listing["price"])
        run.check("R9", price <= _num(pact.get("max_rebuy_price_usd", 0)), "under the per-purchase cap", {"price": price, "cap": pact.get("max_rebuy_price_usd")})
        total = _num(ctx.month_committed) + price
        run.check("R9", total <= _num(pact.get("monthly_cap", 0)), "under the monthly cap", {"month_committed": ctx.month_committed, "this": price, "cap": pact.get("monthly_cap")})

    # R10 — recipients
    if a in EMAILS:
        to = _email_to(p)
        if ctx.demo_mode:
            run.check("R10", to in {x.lower() for x in ctx.demo_recipients}, "demo mode: recipient is on the exact allowlist", {"to": to})
        elif a == "price_adjust_email":
            domains = {_norm(d) for d in policy.get("support_domains") or []}
            run.check("R10", "@" in to and to.rsplit("@", 1)[1] in domains, "recipient is the store's support domain", {"to": to})
        elif a == "claim_packet":
            run.check("R10", to == _norm(ctx.user_email), "packet goes to the user's own email", {"to": to})
        else:
            run.check("R10", bool(ctx.claim) and to == _norm(ctx.claim.get("recipient")), "follow-up goes to the original recipient", {"to": to})

    # R11 — one claim per order
    if a in CLAIMS or a == "rebuy_return":
        n = len(ctx.prior_claims or [])
        run.check("R11", n < int(pact.get("max_claims_per_order", 1)), "no earlier claim on this order", {"prior": n})

    # R13 — follow-ups
    if a == "claim_followup":
        c = ctx.claim
        run.check("R13", bool(c), "an earlier claim exists")
        e = p["email"]
        run.check("R13", _email_to(p) == _norm(c.get("recipient")), "same recipient as the claim", {"to": _email_to(p), "claim": c.get("recipient")})
        run.check("R13", (e.get("thread_id") or None) == (c.get("thread_id") or None) and bool(e.get("thread_id")), "same thread", {"thread": e.get("thread_id")})
        run.check("R13", int(c.get("followups", 0)) < int(pact.get("max_followups", 3)), "fewer than the follow-up limit", {"followups": c.get("followups")})

    # R12 — ask first
    if human_approved:
        run.skip("R12", "human approved")
    else:
        if a == "rebuy_return":
            price = _num(listing["price"])
            run.check("R12", price <= _num(pact.get("ask_first_above", 0)), "under the ask-first limit", {"price": price, "ask": pact.get("ask_first_above")}, on_fail="ASK")
        conf = item.get("parse_confidence")
        run.check("R12", conf is None or _num(conf) >= 0.8, "receipt was read with confidence", {"confidence": conf}, on_fail="ASK")


# ---------------------------------------------------------------- approvals
def run_approved(p: dict, pact: dict | None, ctx: GateContext, approval: dict) -> Decision:
    """A1, then the full gate on fresh data with only R12 skipped."""
    h = sign.proposal_hash(p)
    run = _Run()
    try:
        now = _ts(ctx.now)
        ok = (
            approval.get("status") == "approved"
            and _ts(approval.get("expires_at")) is not None and _ts(approval["expires_at"]) > now
            and approval.get("proposal_hash") == h
            and ctx.session_user_id is not None and ctx.session_user_id == ctx.pact_owner_id
            and ctx.reverified_at is not None and now - _ts(ctx.reverified_at) <= RECHECK_MAX_AGE
        )
        run.check("A1", ok, "approval is approved, unexpired, for this exact proposal, by the owner, re-verified",
                  {"status": approval.get("status"), "expires_at": str(approval.get("expires_at")),
                   "hash_match": approval.get("proposal_hash") == h, "owner": ctx.session_user_id == ctx.pact_owner_id,
                   "reverified_at": str(ctx.reverified_at)})
    except _Stop:
        return Decision("BLOCK", run.results, h)
    except Exception as exc:
        run.results.append({"rule": "A1", "pass": False, "why": f"internal error: {exc}", "values": {}})
        return Decision("BLOCK", run.results, h)
    d = run_gate(p, pact, ctx, human_approved=True)
    d.results = run.results + d.results
    return d
