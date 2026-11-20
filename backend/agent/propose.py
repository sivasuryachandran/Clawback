"""propose(opportunity_id) -> proposal dict (schema 3.5), saved to `proposals`.

The LLM picks ONLY action_type, template, alert_at and reasoning. Every fact the gate reads
(ids, store, listing, order number, recipient) is filled here from the database.
"""
import hashlib
import json
import os
from datetime import date, timedelta
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from backend.agent import clock
from backend.agent.deps import db, pact, track
from backend.agent.llm import RuleBasedLLM, get_llm

USER_ID = os.environ.get("CLAWBACK_USER_ID", "u_demo")  # schema has no orders.user_id: one user
ActionType = Literal["price_adjust_email", "claim_packet", "claim_followup", "rebuy_return",
                     "deadline_alert", "none"]
Template = Literal["price_adjust_v1", "packet_v1", "proof_v1"]
TEMPLATE_FOR = {"price_adjust_email": "price_adjust_v1", "claim_packet": "packet_v1",
                "claim_followup": "proof_v1"}


# ---------- schemas ----------

class LLMChoice(BaseModel):
    """What the LLM is allowed to decide. Extra keys (e.g. a listing_id or price) are DROPPED."""
    model_config = ConfigDict(extra="ignore")
    action_type: ActionType
    template: Optional[Template] = None
    alert_at: Optional[date] = None
    reasoning: str = Field(min_length=1, max_length=600)

    @field_validator("template", "alert_at", mode="before")
    @classmethod
    def _null_strings(cls, v):
        """Open-weight models often write the STRING 'None'/'null' for an unused optional field. That means 'no value'."""
        return None if isinstance(v, str) and v.strip().lower() in ("", "none", "null", "n/a") else v

    @model_validator(mode="after")
    def _consistent(self):
        want = TEMPLATE_FOR.get(self.action_type)
        if want and self.template != want:
            raise ValueError(f"{self.action_type} needs template {want}")
        if not want:
            self.template = None
        if self.action_type == "deadline_alert" and self.alert_at is None:
            raise ValueError("deadline_alert needs alert_at")
        if self.action_type != "deadline_alert":
            self.alert_at = None
        return self


class EmailSpec(BaseModel):
    to: str
    template: Template
    thread_id: Optional[str] = None


class RebuySpec(BaseModel):
    listing_id: int


class ReturnSpec(BaseModel):
    order_no: str


class Proposal(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    proposal_id: str
    opportunity_id: int
    line_item_id: int
    store: str
    listing_id: Optional[int]
    action_type: ActionType
    email: Optional[EmailSpec] = None
    rebuy: Optional[RebuySpec] = None
    return_: Optional[ReturnSpec] = Field(None, alias="return")
    alert_at: Optional[str] = None
    reasoning: str


TOOL = {
    "name": "propose_action",
    "description": "Propose exactly one action for this purchase. Facts and recipients are "
                   "filled in by the system; you only choose the action, template, alert date "
                   "and a two-sentence reason for the user.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action_type": {"type": "string", "enum": list(ActionType.__args__)},
            "template": {"type": ["string", "null"], "enum": list(Template.__args__) + [None]},
            "alert_at": {"type": ["string", "null"], "description": "YYYY-MM-DD, deadline_alert only"},
            "reasoning": {"type": "string", "description": "Two plain sentences for the user."},
        },
        "required": ["action_type", "template", "alert_at", "reasoning"],
        "additionalProperties": False,
    },
}

SYSTEM = """You are Clawback's proposal step. You PROPOSE one action; a separate deterministic
gate decides whether it runs, so never try to argue around rules.
Use only the trusted facts given. You never supply prices, fees, ids, recipients or dates
(except alert_at). Always answer by calling the propose_action tool exactly once.

Choice guidance:
- price_adjust_email (template price_adjust_v1) if claim_channel is "email", the store offers
  price adjustments, and the adjust window is open.
- claim_packet (template packet_v1) for the same case at a store whose claim_channel is "chat".
- rebuy_return (template null) if adjustments aren't possible (not offered or window closed)
  but the return window is open.
- deadline_alert (template null, alert_at = a date on or before the return window end, not
  before today) when the opportunity kind is "deadline".
- none otherwise.
reasoning: two short plain sentences a normal person understands; no ids."""


# ---------- facts ----------

def _window_end(item: dict, days: int, starts: str) -> date:
    start = item["order_date"]
    if starts == "delivery" and item.get("delivery_date"):
        start = item["delivery_date"]
    return date.fromisoformat(str(start)[:10]) + timedelta(days=int(days))


def load_facts(opportunity_id: int) -> dict:
    opp = db.query("SELECT * FROM opportunities WHERE id = %s", (opportunity_id,))[0]
    item = db.query(
        "SELECT li.*, o.order_no, o.order_date, o.delivery_date FROM line_items li "
        "JOIN orders o ON o.id = li.order_id WHERE li.id = %s", (opp["line_item_id"],))[0]
    listing, history = None, []
    if opp["listing_id"] is not None:
        rows = db.query("SELECT * FROM listings WHERE id = %s", (opp["listing_id"],))
        listing = rows[0] if rows else None
        history = db.query("SELECT price, seen_at FROM price_history WHERE listing_id = %s "
                           "ORDER BY seen_at DESC LIMIT 10", (opp["listing_id"],))
    policy = db.query("SELECT * FROM store_policies WHERE store = %s", (item["store"],))[0]
    pc = db.query("SELECT * FROM policy_checks WHERE opportunity_id = %s ORDER BY id DESC LIMIT 1",
                  (opportunity_id,))
    user = db.query("SELECT * FROM users WHERE id = %s", (USER_ID,))[0]
    the_pact, _ = pact.active_pact(USER_ID)
    today = clock.today()
    adj_end = _window_end(item, policy["adjust_window_days"], policy["adjust_window_starts"])
    ret_end = _window_end(item, policy["return_window_days"], policy["return_window_starts"])
    alert = max(today, ret_end - timedelta(days=2))
    return {
        "opp": opp, "item": item, "listing": listing, "history": history, "policy": policy,
        "policy_check": pc[0] if pc else None, "user": user, "pact": the_pact,
        # derived, for the prompt and the offline fallback
        "kind": opp["kind"], "claim_channel": policy["claim_channel"],
        "price_adjust_allowed": bool(policy["price_adjust_allowed"]),
        "adjust_window_end": adj_end.isoformat(), "adjust_window_open": adj_end >= today,
        "return_window_end": ret_end.isoformat(), "return_window_open": ret_end >= today,
        "alert_at": alert.isoformat(), "today": today.isoformat(),
    }


def pact_summary(p: dict) -> str:
    return (f"Allowed actions: {', '.join(p['actions'])} (replies to the store come with claims). "
            f"Stores: {', '.join(p['stores'])}. Skip: {', '.join(p['excluded_categories'])}. "
            f"Only act if net savings >= ${p['min_net_savings_usd']}. Ask first above "
            f"${p['ask_first_above_usd']}. Never more than ${p['max_rebuy_price_usd']} at once, "
            f"${p['monthly_cap_usd']} a month. Keep a {p['buffer_days']}-day buffer before windows close.")


def build_prompt(f: dict) -> str:
    item, listing, pc = f["item"], f["listing"], f["policy_check"]
    latest = f["history"][0] if f["history"] else None
    lines = [
        f"Today: {f['today']}",
        f"Opportunity kind: {f['kind']}",
        f"Item: {item['title']} ({item['variant']}), category {item['category']}, store {item['store']}",
        f"Paid ${float(item['price_paid']):.2f}, ordered {item['order_date']}, delivered {item['delivery_date']}",
    ]
    if listing:
        lines.append(f"Matched listing: {listing['title']} ({listing['variant']}) at {listing['store']}")
    if latest:
        lines.append(f"Latest price ${float(latest['price']):.2f} seen {latest['seen_at']}")
    lines.append("Price history (newest first): " + ", ".join(
        f"${float(h['price']):.2f}@{h['seen_at']}" for h in f["history"]))
    pol = f["policy"]
    lines += [
        f"Store policy row: claim_channel={pol['claim_channel']}, price_adjust_allowed="
        f"{bool(pol['price_adjust_allowed'])}, adjust {pol['adjust_window_days']} days from "
        f"{pol['adjust_window_starts']} (ends {f['adjust_window_end']}, open={f['adjust_window_open']}), "
        f"returns {pol['return_window_days']} days from {pol['return_window_starts']} (ends "
        f"{f['return_window_end']}, open={f['return_window_open']}), final sale: {pol['final_sale_categories']}",
        f"Policy answer: {pc['answer'] if pc else 'none'}",
        f"Policy quote: \"{pc['cited_text']}\" ({pc['source_url']})" if pc and pc["cited_text"]
        else "Policy quote: none (no source)",
        f"Your Pact: {pact_summary(f['pact'])}",
    ]
    return "\n".join(lines)


# ---------- proposal ----------

def _canonical(o) -> str:
    return json.dumps(o, sort_keys=True, separators=(",", ":"), default=str)


def assemble(choice: LLMChoice, f: dict, *, email_to: str | None = None,
             thread_id: str | None = None, trigger: str = "") -> dict:
    """Fill every fact from the DB. `trigger` makes follow-up ids unique per inbound reply;
    identical claims get identical ids, so a double click hashes the same."""
    opp, item = f["opp"], f["item"]
    a = choice.action_type
    p = {
        "opportunity_id": opp["id"], "line_item_id": opp["line_item_id"], "store": item["store"],
        "listing_id": opp["listing_id"], "action_type": a, "email": None, "rebuy": None,
        "return": None, "alert_at": choice.alert_at.isoformat() if choice.alert_at else None,
    }
    if a in TEMPLATE_FOR:
        if email_to is None:
            if a == "price_adjust_email":
                email_to = os.environ.get("DEMO_SUPPORT_INBOX", "")
            elif a == "claim_packet":
                email_to = f["user"]["email"] or ""
        p["email"] = {"to": email_to.strip().lower(), "template": TEMPLATE_FOR[a], "thread_id": thread_id}
    if a == "rebuy_return":
        p["rebuy"] = {"listing_id": opp["listing_id"]}
        p["return"] = {"order_no": item["order_no"]}
    p["proposal_id"] = "prop_" + hashlib.sha256((_canonical(p) + trigger).encode()).hexdigest()[:16]
    p["reasoning"] = choice.reasoning
    return Proposal.model_validate(p).model_dump(by_alias=True)


def save(p: dict) -> dict:
    h = pact.proposal_hash(p)
    if not db.query("SELECT id FROM proposals WHERE id = %s", (p["proposal_id"],)):
        db.execute(
            "INSERT INTO proposals (id, opportunity_id, action_type, payload_json, proposal_hash, "
            "reasoning, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (p["proposal_id"], p["opportunity_id"], p["action_type"], json.dumps(p), h,
             p["reasoning"], clock.ts()))
    return p


FALLBACK_NOTE = " (Chosen by Clawback's built-in rules because the AI didn't answer in time.)"


def ask_llm(system: str, user: str, tool: dict, facts: dict, model, fallback: dict):
    """Returns (choice, source). source is:
      "llm"            valid answer from the configured LLM
      "rules"          the LLM was unreachable/timed out -> offline rules decided (same guidance)
      "invalid"        the LLM answered badly twice -> the explicit 'do nothing' fallback
    Bad output never falls back to the rules: a model that says nonsense gets no second brain.
    """
    llm = get_llm()
    for _ in range(2):
        try:
            raw = llm.call_tool(system, user, tool, facts)
        except Exception as e:  # timeout, connection, 5xx, missing SDK: the provider is down
            track("llm_unavailable", {"tool": tool["name"], "error": repr(e)[:200]})
            if isinstance(llm, RuleBasedLLM):
                break
            try:
                choice = model.model_validate(RuleBasedLLM().call_tool(system, user, tool, facts))
                choice.reasoning = choice.reasoning[: 600 - len(FALLBACK_NOTE)] + FALLBACK_NOTE
                return choice, "rules"
            except (ValidationError, ValueError, TypeError):
                break
        if raw is None:
            continue
        try:
            return model.model_validate(raw), "llm"
        except (ValidationError, ValueError, TypeError):
            continue
    return model.model_validate(fallback), "invalid"


def propose(opportunity_id: int) -> dict:
    f = load_facts(opportunity_id)
    choice, _source = ask_llm(SYSTEM, build_prompt(f), TOOL, f, LLMChoice,
                              {"action_type": "none", "template": None, "alert_at": None,
                               "reasoning": "We couldn't decide safely. Nothing was done."})
    return save(assemble(choice, f))
