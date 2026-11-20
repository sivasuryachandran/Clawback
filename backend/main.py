"""Clawback API shell (Teammate C owns this file).

Run:   uvicorn backend.main:app --reload --port 8000
Serves the API, the Mock Store under /mock, and /health.

HOW TEAMMATES PLUG IN (section 1: "everyone may add one include_router line"):
add ONE line to ROUTER_MODULES below naming the module that exposes `router` (an APIRouter).
A module that doesn't exist yet is skipped (the app still starts), and /health tells you what
is mounted. A module that exists but fails to import is logged loudly and reported in /health;
set STRICT_ROUTERS=true to make that crash the app instead.
"""
from __future__ import annotations

import importlib
import logging
import os

from dotenv import load_dotenv

load_dotenv()  # before anything reads os.environ (PACT_SIGNING_KEY etc. are read at call time)

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from backend.debug import guard  # noqa: E402

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("clawback")

app = FastAPI(title="Clawback", version="0.1.0",
              description="A personal agent that gets your money back, only inside a Pact you signed.")

_origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
_origins += [o.strip() for o in os.environ.get("CORS_EXTRA_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

@app.middleware("http")
async def localhost_only_controls(request, call_next):
    """The admin and Under the hood endpoints answer only to the demo laptop, even when the frontend is tunnelled."""
    if guard.blocked(request.url.path, request.headers, request.client.host if request.client else None):
        return JSONResponse({"detail": guard.REFUSAL}, status_code=403)
    return await call_next(request)


# (module path, owner). One line each; the module must expose `router`.
ROUTER_MODULES = [
    ("mockstore.routes", "C"),           # /mock/*
    ("backend.analytics.api", "C"),      # /stats
    ("backend.debug.api", "C"),          # /debug/overview, /debug/query/{key}, /debug/matching
    ("backend.voice.api", "C"),          # /voice/ask
    ("backend.pact.api", "Siva"),        # /pact, /pact/pause, /gate/check, /approvals
    ("backend.pact.schedules_api", "Siva"),  # /schedules, /calendar
    ("backend.persona.api", "Siva"),     # /persona/inquiry, /persona/webhook, /persona/recheck
    ("backend.ingest.api", "A"),         # /ingest/run, /orders
    ("backend.watch.api", "A"),          # /watch/tick, /watch/price-changed, /activity
    ("backend.agent.api", "B"),          # /opportunities/*, /replies/inbound, /claim-packets/*
]

MOUNTED: dict[str, str] = {}
ROUTERS: dict[str, object] = {}     # module name -> its APIRouter, for duplicate-route detection
app.state.mounted = MOUNTED   # read by /debug/overview


def mount(module_path: str, owner: str = "?") -> None:
    try:
        mod = importlib.import_module(module_path)
        router = getattr(mod, "router")
        app.include_router(router)
        ROUTERS[module_path] = router
        MOUNTED[module_path] = "mounted"
    except ModuleNotFoundError as exc:
        # only "this module isn't there yet" is quiet; a missing dependency inside it is an error
        if exc.name and (module_path == exc.name or module_path.startswith(exc.name + ".")):
            MOUNTED[module_path] = f"not present yet ({owner})"
            log.info("router %s not present yet (%s)", module_path, owner)
        else:
            _failed(module_path, exc)
    except Exception as exc:
        _failed(module_path, exc)


def _failed(module_path: str, exc: Exception) -> None:
    MOUNTED[module_path] = f"ERROR: {exc.__class__.__name__}: {exc}"
    log.exception("router %s failed to import", module_path)
    if os.environ.get("STRICT_ROUTERS", "false").strip().lower() == "true":
        raise exc


for _m, _o in ROUTER_MODULES:
    mount(_m, _o)


def _iter_routes(router, prefix: str = ""):
    """(full path, methods) for every route on a router, through its public `.routes`. Newer FastAPI wraps
    nested routers in lazy objects, so those are unwrapped best-effort."""
    for r in getattr(router, "routes", []):
        path = getattr(r, "path", None)
        if path is not None:
            yield prefix + path, getattr(r, "methods", None) or set()
            continue
        inner = getattr(r, "original_router", None)
        if inner is not None:
            yield from _iter_routes(inner, prefix + (getattr(getattr(r, "include_context", None), "prefix", "") or ""))


def attach_scheduler() -> None:
    """Siva's scheduler runs from a startup hook: backend.pact.scheduler.attach(app). Skipped if it isn't there yet."""
    try:
        from backend.pact import scheduler
        scheduler.attach(app)
        MOUNTED["backend.pact.scheduler"] = "attached"
    except ModuleNotFoundError as exc:
        if not (exc.name or "").startswith("backend"):
            _failed("backend.pact.scheduler", exc)
    except Exception as exc:
        _failed("backend.pact.scheduler", exc)


attach_scheduler()


def find_route_conflicts(routers: dict) -> list[str]:
    """Two mounted routers answering the same METHOD + path: FastAPI silently serves the first and hides the
    second, which looks like "my endpoint ignores my code". `routers` is {module name: APIRouter}; each
    conflict names both owners so the fix is obvious."""
    owner: dict[tuple[str, str], str] = {}
    out: list[str] = []
    for name, router in routers.items():
        for path, methods in _iter_routes(router):
            for method in sorted(methods):
                if method in ("HEAD", "OPTIONS"):
                    continue
                key = (method, path)
                if key in owner:
                    out.append(f"{method} {path}: {owner[key]} and {name}")
                else:
                    owner[key] = name
    return out


def demo_mode_on() -> bool:
    """Section 3.2: DEMO_MODE is on unless it is explicitly "false"."""
    return os.environ.get("DEMO_MODE", "true").strip().lower() != "false"


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok", "demo_mode": demo_mode_on(), "routers": MOUNTED, "route_conflicts": find_route_conflicts(ROUTERS)}
