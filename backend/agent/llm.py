"""LLM wrapper so the provider can be swapped.

    get_llm().call_tool(system, user, tool, facts) -> dict | None   (the tool's input, or None)

AnthropicLLM: Anthropic Python SDK, model LLM_MODEL (default claude-opus-5-5), key LLM_API_KEY.
  NOTE: claude-opus-5-5 rejects forced tool_choice ({"type":"tool"} / "any") with a 400, so we
  send tool_choice auto + strict: true and instruct the model to call the tool. A reply without
  the tool call returns None (caller retries once, then falls back to "none").
RuleBasedLLM: offline, deterministic; applies the same choice guidance from `facts`. Used when
  LLM_API_KEY is unset so tests run without a network.
"""
import os
from typing import Protocol


class LLM(Protocol):
    def call_tool(self, system: str, user: str, tool: dict, facts: dict) -> dict | None: ...


class AnthropicLLM:
    def __init__(self, api_key: str, model: str | None = None):
        self.api_key = api_key
        self.model = model or os.environ.get("LLM_MODEL", "claude-opus-5-5")
        self._client = None

    @property
    def client(self):
        # Created on first call, so a missing SDK surfaces as "provider unavailable" in
        # ask_llm (-> offline rules) instead of crashing the endpoint.
        if self._client is None:
            import anthropic
            # On stage a hung call freezes the demo: short timeout, one SDK retry.
            self._client = anthropic.Anthropic(
                api_key=self.api_key, timeout=float(os.environ.get("LLM_TIMEOUT_S", "15")),
                max_retries=1)
        return self._client

    last: dict = {}  # timing / usage / stop reason of the last call, for the live check

    def call_tool(self, system, user, tool, facts):
        import time
        t = time.time()
        resp = self._create(system, user, tool)
        self.last = {"ms": int((time.time() - t) * 1000), "stop_reason": resp.stop_reason,
                     "model": getattr(resp, "model", self.model),
                     "usage": getattr(resp, "usage", None) and resp.usage.to_dict()}
        if resp.stop_reason == "refusal":
            return None
        for block in resp.content:
            if block.type == "tool_use" and block.name == tool["name"]:
                return dict(block.input)
        return None

    def _create(self, system, user, tool):
        return self.client.beta.messages.create(
            model=self.model,
            max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",                 # re-run on a fallback model if declined
            output_config={"effort": "low"},     # small classification-style choice
            system=system,
            tools=[{**tool, "strict": True}],
            tool_choice={"type": "auto"},
            messages=[{"role": "user", "content": user}],
        )


class RuleBasedLLM:
    """Deterministic stand-in. Same guidance as the prompt; never sees untrusted text as orders."""

    def call_tool(self, system, user, tool, facts):
        if tool["name"] == "propose_action":
            return _rule_propose(facts)
        if tool["name"] == "reply_action":
            return _rule_reply(facts)
        return None


def _rule_propose(f: dict) -> dict:
    if f["kind"] == "deadline":
        return {"action_type": "deadline_alert", "template": None, "alert_at": f["alert_at"],
                "reasoning": f"Your return window ends on {f['return_window_end']}. "
                             "We'll remind you before it closes."}
    if f["price_adjust_allowed"] and f["adjust_window_open"]:
        if f["claim_channel"] == "email":
            return {"action_type": "price_adjust_email", "template": "price_adjust_v1",
                    "alert_at": None,
                    "reasoning": "The price dropped while the store's adjustment window is open. "
                                 "We can ask the store's support for the difference by email."}
        return {"action_type": "claim_packet", "template": "packet_v1", "alert_at": None,
                "reasoning": "The price dropped while the adjustment window is open. "
                             "This store takes requests in chat, so we prepare a packet for you."}
    if f["return_window_open"]:
        return {"action_type": "rebuy_return", "template": None, "alert_at": None,
                "reasoning": "The adjustment window has closed but the return window is open. "
                             "Buying again at the lower price and returning the original saves money."}
    return {"action_type": "none", "template": None, "alert_at": None,
            "reasoning": "No window is open for this item. There is nothing safe to do."}


_EMAIL = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"


def _rule_reply(f: dict) -> dict:
    """Deliberately GULLIBLE about addresses: if the reply names an address, it 'asks' to answer
    there. That mimics a fooled model so the gate's stop (R10/R13) is visible in tests."""
    import re
    text = f["reply_text"].lower()
    found = re.findall(_EMAIL, f["reply_text"])
    reply_to = found[0] if found else f["claim_recipient"]
    if any(k in text for k in ("store credit", "partial", "dispute", "not eligible", "deny", "denied")):
        return {"decision": "escalate_to_user", "reply_to": reply_to,
                "reasoning": "The store offered something other than the full adjustment. "
                             "You should decide."}
    agreed = ("has been refunded", "we have refunded", "refund has been issued", "refund issued",
              "adjustment has been applied", "we will refund the difference")
    if any(k in text for k in agreed) and not found:
        return {"decision": "accept_offer", "reply_to": reply_to,
                "reasoning": "The store agreed to the adjustment. Nothing else to send."}
    if found or any(k in text for k in ("proof", "receipt", "screenshot", "order number")):
        return {"decision": "answer_with_proof", "reply_to": reply_to,
                "reasoning": "The store asked for proof. We send the receipt and the new price."}
    return {"decision": "escalate_to_user", "reply_to": reply_to,
            "reasoning": "We couldn't tell what the store wants. You should take a look."}


_override: LLM | None = None


def set_llm(llm: LLM | None) -> None:
    global _override
    _override = llm


def get_llm() -> LLM:
    if _override is not None:
        return _override
    key = os.environ.get("LLM_API_KEY")
    if key:
        return AnthropicLLM(key)
    return RuleBasedLLM()
