"""Endpoints A owns (docs/C_CONTRACTS.md ROUTER_MODULES): POST /ingest/run, GET /orders."""

from __future__ import annotations

from fastapi import APIRouter

from backend.db import query
from backend.ingest.gmail_ingest import ingest_new_emails
from backend.ingest.parser import parse_unparsed_emails

router = APIRouter()


@router.post("/ingest/run")
def ingest_run():
    new_emails = ingest_new_emails()
    parsed = parse_unparsed_emails()
    return {"new_emails": new_emails, "parsed": parsed}


@router.get("/orders")
def list_orders():
    orders = query("SELECT * FROM orders ORDER BY id", ())
    items = query("SELECT * FROM line_items ORDER BY id", ())
    by_order: dict[int, list[dict]] = {}
    for it in items:
        by_order.setdefault(it["order_id"], []).append(it)
    return [{**o, "items": by_order.get(o["id"], [])} for o in orders]
