"""Rehearsal app (stand-in for C's backend/main.py).

    uvicorn dev.app:app --port 8000
"""
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI  # noqa: E402

from backend.agent.api import router as agent_router  # noqa: E402
from dev.dev_routes import router as dev_router  # noqa: E402
from dev.stubs.mockstore import router as mock_router  # noqa: E402

app = FastAPI(title="Clawback rehearsal (Teammate B)")
app.include_router(agent_router)   # the one line B adds to backend/main.py tomorrow
app.include_router(mock_router)    # C's
app.include_router(dev_router)     # rehearsal only


@app.get("/health")
def health():
    return {"ok": True}
