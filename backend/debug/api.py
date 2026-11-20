"""Read-only debug endpoints for the Under the hood page (Teammate C). GET only: nothing here writes."""
from fastapi import APIRouter, HTTPException, Request

from . import dbaccess, panels, queries, sample

router = APIRouter(prefix="/debug", tags=["debug"])


@router.get("/overview")
def get_overview(request: Request, line_item_id: int | None = None, mock: int = 0):
    """Everything Under the hood shows, in one JSON. ?mock=1 = labelled sample data."""
    return panels.build(line_item_id=line_item_id, use_sample=bool(mock),
                          routers=getattr(request.app.state, "mounted", {}))


@router.get("/matching")
def get_matching(line_item_id: int | None = None, mock: int = 0):
    """Matcher candidates for ONE purchase, on demand (never part of the polled overview: each call
    is a SIE embed + rerank request). Cached 30 s."""
    def go():
        return panels._panel(lambda: panels.matching_candidates(line_item_id, bool(mock)))
    if mock:
        with dbaccess.use_override(sample.build().query):
            return go()
    return go()


@router.get("/query/{key}")
def run_saved_query(key: str, item: int | None = None, mock: int = 0):
    """Run one saved, read-only SQL query live (a fixed whitelist: the page never sends SQL)."""
    if key not in queries.LIVE_QUERIES:
        raise HTTPException(404, f"Unknown saved query '{key}'")
    try:
        if mock:
            with dbaccess.use_override(sample.build().query):
                return dict(queries.run(key, item), sample=True)
        return queries.run(key, item)
    except Exception as exc:
        return {"key": key, "label": queries.LIVE_QUERIES[key][0], "sql": queries.LIVE_QUERIES[key][1],
                "params": [], "rows": [], "error": f"{type(exc).__name__}: {exc}"[:300]}
