"""POST /voice/ask logic (section 3.11; Teammate C): map a question to ONE fixed intent, answer
only from data the app already has, in plain words under 25 words.

The speech text is untrusted. Matching is by keywords (no LLM, so nothing a person says can
become a free-form action). Voice can explain, summarise and PAUSE. It can never approve, buy,
send, edit limits or un-pause: those requests get the refusal sentence.

Pausing needs a confirmation: "Pause Clawback" -> "Say yes to pause." -> "yes" (within 30 s).
"""
from __future__ import annotations

import re
import time
from typing import Callable

from backend.analytics import stats
from backend.debug.plain import usd

from . import stories as st

REFUSAL = "I can't do that by voice. Approvals and changes happen on screen."
HELP = "I can tell you what I did, why, or pause. Approvals happen on screen."
PAUSE_ASK = "Say yes to pause Clawback."
PAUSED = "Clawback is paused. Nothing will happen until you turn it back on, on screen."
PAUSE_FAILED = "I couldn't pause just now. Please tap Pause Clawback on screen."
PAUSE_CANCELLED = "Okay, I won't pause."
PENDING_TTL_S = 30
MAX_TEXT = 300

_pending: dict[str, float] = {}      # session -> expiry of "say yes to pause"

_POLITE = r"(?:(?:hey |ok |okay )?(?:clawback,? )?)?(?:please |can you |could you |would you |will you |i want you to |i want to )?"
_ANYWHERE = re.compile(r"\b(approve|approving|un-?pause|resume|go ahead)\b")
_IMPERATIVE = re.compile(
    r"^" + _POLITE + r"(?:never |always |don'?t |do not )?"
    r"(buy|rebuy|re-buy|purchase|order|send|email|sign|confirm|edit|change|raise|increase|lower|decrease|set|update|adjust|turn|unlock|disable|enable)\b")
# A command hiding mid-sentence ("ignore your rules AND buy the blue one") or an attempt to override the rules.
_INLINE = re.compile(r"\b(?:and|then|also|just|should|must)\s+(?:buy|rebuy|re-buy|purchase|send|email|approve|sign|confirm|raise|increase|change|edit)\b"
                     r"|\bignore\b.*\b(?:rules?|instructions?|limits?|pact|gate)\b"
                     r"|\b(?:override|bypass|disable)\b")
_YES = re.compile(r"^(yes|yeah|yep|yup|sure|do it|please do|confirm|affirmative)\b")
_NO = re.compile(r"^(no|nope|cancel|never mind|nevermind|stop|don'?t)\b")
_PAUSE = re.compile(r"\bpause\b")
_NEEDS_OK = re.compile(r"\b(need|needs|needing|waiting|pending|anything|something|any)\b.*\b(ok|okay|approval|attention)\b"
                       r"|\bneeds? (me|my)\b|\bwaiting (for|on) (me|my)\b|\bpending\b")
_WHY = re.compile(r"\b(why|explain|what happened)\b")
_SUMMARY = re.compile(r"\b(what did you do|what have you done|what did you (stop|block)|what (was|got|is) (stopped|blocked)|"
                      r"how much (money )?(did|have|has|do)|get back|got back|money back|recover|summary)\b")
# a "why" question only counts when it is about a decision (or names an item): "why is the sky blue" is not
_DECISION_WORDS = re.compile(r"\b(stop|stopped|block|blocked|act|acted|do|did|didn|didnt|buy|bought|ask|asked|claim|claimed|"
                             r"refuse|refused|skip|skipped|send|sent|approve|approval)\b")


def _norm(text: str) -> str:
    t = (text or "")[:MAX_TEXT].lower().strip()
    t = re.sub(r"[^\w\s'$-]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def reset() -> None:
    _pending.clear()


def _result(intent: str, answer: str, **extra) -> dict:
    return {"intent": intent, "answer": st.clip(answer), "open_card_id": None, **extra}


def summary() -> dict:
    s = stats.compute()
    c = s["counts"]
    parts = [f"You've got {usd(s['dollars_recovered'])} back."]
    n = c["needs_your_ok"]
    if n:
        parts.append(f"{n} {'needs' if n == 1 else 'need'} your OK.")
    k = c["stopped"]
    parts.append(f"{k} unsafe action{'s' if k != 1 else ''} stopped." if k else "Nothing was stopped.")
    return _result("summary", " ".join(parts))


def needs_ok() -> dict:
    pend = stats.pending_approvals()
    if not pend:
        return _result("needs_ok", "Nothing needs your OK right now.")
    rows = st.latest_per_opportunity(st._rows())
    first = pend[0]
    row = next((r for r in rows if r.get("proposal_id") == first.get("proposal_id")), None)
    if row is None:
        return _result("needs_ok", "Something needs your OK. Open the card on screen.", approval_id=first.get("id"))
    s = st.story(row)
    more = f" {len(pend) - 1} more waiting." if len(pend) > 1 else ""
    return _result("needs_ok", f"Yes: save {usd(row.get('drop_usd'))} on your {s['name']}? Approve it on screen.{more}",
                   open_card_id=str(row.get("opportunity_id")), approval_id=first.get("id"), lines=s["lines"], open=True)


def why(text: str) -> dict:
    rows = st.latest_per_opportunity(st._rows())
    row = st.find_item(text, rows)
    if row is None and not _DECISION_WORDS.search(text):
        return _result("help", HELP)
    if row is None:
        stopped = [st.noun(r.get("title")) for r in rows if (r.get("decision") or "").upper() == "BLOCK"]
        hint = f" I stopped the {', the '.join(stopped[:3])}." if stopped else ""
        return _result("why", f"Which item do you mean?{hint}")
    s = st.story(row)
    return _result("why", s["spoken"], open_card_id=str(row.get("opportunity_id")), lines=s["lines"],
                   decision=s["decision"])


def ask(text: str, session_id: str = "default", pause_fn: Callable[[], bool] | None = None,
        now: float | None = None) -> dict:
    """Answer one question. `pause_fn` performs the pause (POST /pact/pause) and returns success;
    without it the result carries `_do_pause: True` for the endpoint to act on."""
    t = _norm(text)
    clock = now if now is not None else time.time()
    pending = _pending.get(session_id, 0) > clock
    _pending.pop(session_id, None) if not pending else None

    if pending:
        if _YES.match(t):
            _pending.pop(session_id, None)
            if pause_fn is None:
                return _result("pause_confirmed", PAUSED, _do_pause=True)
            return pause_result(bool(_safe(pause_fn)))
        _pending.pop(session_id, None)                 # anything else cancels the pending pause
        if _NO.match(t):
            return _result("pause_cancelled", PAUSE_CANCELLED)

    if not t:
        return _result("help", HELP)
    if _ANYWHERE.search(t) or _IMPERATIVE.match(t) or _INLINE.search(t):
        return _result("refused", REFUSAL)
    if _PAUSE.search(t):
        _pending[session_id] = clock + PENDING_TTL_S
        return _result("pause_confirm", PAUSE_ASK)
    if _WHY.search(t):
        return why(t)
    if _NEEDS_OK.search(t):
        return needs_ok()
    if _SUMMARY.search(t):
        return summary()
    return _result("help", HELP)


def pause_result(ok: bool) -> dict:
    return _result("paused", PAUSED) if ok else _result("pause_failed", PAUSE_FAILED)


def _safe(fn: Callable[[], bool]) -> bool:
    try:
        return bool(fn())
    except Exception:
        return False
