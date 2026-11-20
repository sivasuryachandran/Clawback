"""POST /voice/ask (Teammate C; section 3.7)."""
import logging

import httpx
from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from backend.debug import dbaccess, sample

from . import intents

log = logging.getLogger("clawback.voice")
router = APIRouter(prefix="/voice", tags=["voice"])


class AskIn(BaseModel):
    text: str = Field("", max_length=600)
    session_id: str = Field("default", max_length=64)


async def _pause(request: Request) -> bool:
    """Call Siva's POST /pact/pause inside this app, forwarding the caller's own login
    (cookie / Authorization), so voice can only do what that signed-in person could do."""
    headers = {k: v for k, v in request.headers.items() if k.lower() in ("cookie", "authorization", "x-user-id")}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=request.app),
                                     base_url="http://clawback.local", timeout=15.0) as c:
            r = await c.post("/pact/pause", json={"reason": "paused by voice"}, headers=headers)
        if r.status_code >= 300:
            log.warning("voice pause failed: HTTP %s %s", r.status_code, r.text[:120])
        return r.status_code < 300
    except Exception as exc:
        log.warning("voice pause failed: %s", exc)
        return False


@router.post("/ask")
async def voice_ask(body: AskIn, request: Request, mock: int = 0):
    """question text -> {intent, answer, open_card_id}. Answers come only from data the app already
    has. Voice never approves, buys, sends, edits limits or un-pauses. ?mock=1 uses sample data."""
    def run():
        if mock:
            with dbaccess.use_override(sample.build().query):
                return intents.ask(body.text, body.session_id)
        return intents.ask(body.text, body.session_id)
    try:
        result = await run_in_threadpool(run)
    except Exception as exc:       # database down etc.: say so plainly, never a stack trace
        log.warning("voice ask failed: %s", exc)
        return {"intent": "error", "answer": "Something went wrong on our side. Nothing was sent or bought.",
                "open_card_id": None}
    if result.pop("_do_pause", False):
        result = intents.pause_result(await _pause(request))
    return result
