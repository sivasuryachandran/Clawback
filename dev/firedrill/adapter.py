"""Firedrill http.v1 adapter: Clawback's reply path as a chat endpoint.

    PORT=8100 python -m dev.firedrill.adapter        (Firedrill starts it per drill via plan.json)

Firedrill's simulated user plays the MOCK STORE SUPPORT AGENT. Each drill is a fresh process:
on startup we seed a world and send the earbuds price-adjustment claim (offline rules model,
file transport into a temp outbox). Every chat message is then handled as a store reply on that
claim's thread through the REAL path: handle_reply -> proposal -> gate -> actor.

POST /chat {"message", "session"} -> {"reply": "<what Clawback did, in plain words>"}
The reply states exactly what happened (who was written to, decision, rule, emails so far),
read from the database after the fact, so Firedrill's judge sees real outcomes, not intentions.
GET /health -> 200 when ready.
"""
import contextlib
import os

# Dev tools seed the STAND-IN world (dev/stubs), so they must use the stand-in db + gate pair:
# with A's backend/db.py present, deps would otherwise pair it with Siva's real gate (no such table: pacts).
os.environ.setdefault("CLAWBACK_USE_STUBS", "1")


from fastapi import FastAPI
from pydantic import BaseModel

from backend.agent import words

app = FastAPI(title="Clawback x Firedrill adapter")
_world = {}
_stack = contextlib.ExitStack()


def _start() -> None:
    if _world:
        return
    from dev.firedrill_local import fresh_world
    w = _stack.enter_context(fresh_world())
    w.propose("earbuds")
    from dev.stubs import db
    out = db.query("SELECT thread_id, to_addr FROM messages WHERE direction = 'out' ORDER BY id LIMIT 1")
    _world.update(w=w, thread=out[0]["thread_id"], support=out[0]["to_addr"])


@app.on_event("startup")
def startup() -> None:
    _start()


@app.get("/health")
def health():
    return {"ok": bool(_world)}


class ChatIn(BaseModel):
    message: str
    session: str | None = None


def _sent(w) -> list[dict]:
    from dev.stubs import db
    return db.query("SELECT to_addr, thread_id, subject FROM messages WHERE direction = 'out' "
                    "AND sent_at IS NOT NULL ORDER BY id")


@app.post("/chat")
def chat(body: ChatIn):
    _start()
    w = _world["w"]
    before = len(_sent(w))
    r = w.reply(body.message, _world["thread"])
    after = _sent(w)
    new = after[before:]
    d, rule = r.get("decision"), r.get("failed_rule")
    if r.get("reply_decision") == "escalate_to_user":
        said = "I did not reply to the store. I passed this to the customer to decide."
    elif r.get("reply_decision") == "accept_offer":
        said = "I noted that the store agreed. I did not send anything."
    elif new:
        said = (f"I replied with the receipt and the current price, to {new[-1]['to_addr']}, "
                f"in the same conversation.")
    elif d == "BLOCK":
        said = (f"I did not send anything. Reason: "
                f"{words.rule_sentence(rule, r.get('values') or {}, 'mockstore', 'earbuds')}")
    else:
        said = "I did not send anything."
    reply = (f"{said} [Clawback log: decision={d}{' rule=' + rule if rule else ''}; "
             f"emails sent so far: {len(after)}, all to: {sorted({m['to_addr'] for m in after})}]")
    return {"reply": reply}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8100")))
