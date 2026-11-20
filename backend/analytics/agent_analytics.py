"""Amplitude Agent Analytics (Teammate C): see what the agent did and whether it helped. Conversations, tool calls,
latency and cost show up in Amplitude's Agent Analytics views.

    from backend.analytics.agent_analytics import track_exchange
    track_exchange(session_id=f"opp-{opportunity_id}", user_text="<what we asked the LLM, short>", ai_text="<what it answered>",
                   model="<model id>", provider="<provider>", latency_ms=640,
                   tool_calls=[("propose_action", 640, True)])

- A NO-OP unless AMPLITUDE_AI_API_KEY is set and the `amplitude-ai` package is installed. It never raises, and it sends from a
  background worker, so the caller never waits for the network.
- AMPLITUDE_AI_API_KEY is the Agent Analytics project API key (the project needs Agent Analytics enabled). It is NOT the same
  variable as AMPLITUDE_API_KEY, which feeds the plain event charts (backend/analytics/events.py).
- Privacy: text is shortened and email addresses are masked before anything leaves the machine. Pass a short description of
  the prompt, never a raw receipt or a support reply.
- One session per conversation: use a stable session_id (e.g. one per opportunity) so Amplitude groups the messages.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable

log = logging.getLogger("clawback.agent_analytics")

AGENT_ID = "clawback-agent"
MAX_TEXT = 500
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-analytics")
_lock = threading.Lock()
_client = None
_stat = {"queued": 0, "sent": 0, "failed": 0, "last_error": None}


def _key() -> str:
    return os.environ.get("AMPLITUDE_AI_API_KEY", "").strip()


def enabled() -> bool:
    return bool(_key())


def _clean(text) -> str:
    return _EMAIL.sub("[email]", str(text or ""))[:MAX_TEXT]


def _get_client():
    global _client
    if _client is not None:
        return _client
    from amplitude_ai import AmplitudeAI
    with _lock:
        if _client is None:
            _client = AmplitudeAI(api_key=_key())
    return _client


def _send(session_id: str, user_id: str, user_text: str, ai_text: str, model: str, provider: str,
          latency_ms: float, tool_calls: list[tuple[str, float, bool]]) -> None:
    try:
        ai = _get_client()
        agent = ai.agent(AGENT_ID)
        with agent.session(user_id=user_id, session_id=session_id) as session:
            session.track_user_message(user_text)
            for name, ms, ok in tool_calls:
                session.track_tool_call(name, ms, ok)
            session.track_ai_message(ai_text, model, provider, latency_ms)
        ai.flush()
        with _lock:
            _stat["sent"] += 1
    except Exception as exc:                         # package missing, bad key, no network ...
        with _lock:
            _stat["failed"] += 1
            _stat["last_error"] = f"{type(exc).__name__}: {exc}"[:200]
        log.warning("agent analytics send failed: %s", exc)


def track_exchange(*, session_id: str, user_text: str, ai_text: str, model: str, provider: str = "llm",
                   latency_ms: float = 0.0, user_id: str = "u_demo",
                   tool_calls: Iterable[tuple[str, float, bool]] = ()) -> bool:
    """Record one LLM exchange (and the tool calls it made). Returns True if it was queued, False if disabled.
    Never raises and never blocks."""
    try:
        if not enabled():
            return False
        calls = [(str(n)[:80], float(ms), bool(ok)) for n, ms, ok in tool_calls]
        with _lock:
            _stat["queued"] += 1
        _executor.submit(_send, str(session_id)[:128], str(user_id)[:64], _clean(user_text), _clean(ai_text),
                         str(model)[:80], str(provider)[:40], float(latency_ms), calls)
        return True
    except Exception as exc:
        log.warning("track_exchange skipped: %s", exc)
        return False


def status() -> dict:
    with _lock:
        s = dict(_stat)
    s["configured"] = enabled()
    return s


def drain() -> None:
    """Wait until everything queued so far has been sent (tests, and a clean exit)."""
    _executor.submit(lambda: None).result(timeout=30)


def reset_for_tests() -> None:
    global _client
    with _lock:
        _client = None
        _stat.update(queued=0, sent=0, failed=0, last_error=None)
