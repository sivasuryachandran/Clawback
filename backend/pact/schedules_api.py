"""/schedules endpoints (Siva). Mount with include_router; start the timer with scheduler.attach(app)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import scheduler as sch

router = APIRouter()


def _uid(request: Request) -> str:
    return request.headers.get("x-user-id") or "u_demo"


def _view(t: dict) -> dict:
    return {**t, "when": sch.describe(t), "label": sch.KINDS.get(t["kind"], t["kind"]), "enabled": bool(t["enabled"])}


def _own(task_id: int, uid: str) -> dict:
    rows = sch._db().query("SELECT * FROM scheduled_tasks WHERE id=%s AND user_id=%s", (task_id, uid))
    if not rows:
        raise HTTPException(404, "Not found.")
    return rows[0]


@router.get("/schedules")
def list_schedules(request: Request):
    rows = sch._db().query("SELECT * FROM scheduled_tasks WHERE user_id=%s ORDER BY id", (_uid(request),))
    return {"kinds": sch.KINDS, "tasks": [_view(r) for r in rows]}


class NewTask(BaseModel):
    kind: str
    name: str | None = None
    schedule_type: str = "interval"
    interval_min: int | None = None
    at_time: str | None = None


@router.post("/schedules")
def create(body: NewTask, request: Request):
    try:
        v = sch.validate(body.model_dump())
    except Exception as e:
        raise HTTPException(422, str(e))
    now = sch._now()
    row = {**v, "user_id": _uid(request), "enabled": 1}
    nxt = sch.next_after(row, now).strftime(sch.FMT)
    tid = sch._db().execute(
        "INSERT INTO scheduled_tasks (user_id, name, kind, schedule_type, interval_min, at_time, enabled, next_run, created_at) VALUES (%s,%s,%s,%s,%s,%s,1,%s,%s)",
        (row["user_id"], v["name"], v["kind"], v["schedule_type"], v["interval_min"], v["at_time"], nxt, now.strftime(sch.FMT)))
    return _view(_own(tid, row["user_id"]))


class Toggle(BaseModel):
    enabled: bool


@router.post("/schedules/{task_id}/toggle")
def toggle(task_id: int, body: Toggle, request: Request):
    t = _own(task_id, _uid(request))
    nxt = sch.next_after(t, sch._now()).strftime(sch.FMT)
    sch._db().execute("UPDATE scheduled_tasks SET enabled=%s, next_run=%s WHERE id=%s", (1 if body.enabled else 0, nxt, task_id))
    return _view(_own(task_id, _uid(request)))


@router.post("/schedules/{task_id}/run")
def run_now(task_id: int, request: Request):
    t = _own(task_id, _uid(request))
    status, text = sch.run_task(t)
    return {"status": status, "result": text, "task": _view(_own(task_id, _uid(request)))}


@router.delete("/schedules/{task_id}")
def delete(task_id: int, request: Request):
    _own(task_id, _uid(request))
    sch._db().execute("DELETE FROM scheduled_tasks WHERE id=%s", (task_id,))
    return {"ok": True}


@router.get("/calendar")
def calendar(month: str, request: Request):
    """Everything dated in one month: schedule runs, store windows closing, and what Clawback did."""
    from calendar import monthrange
    from datetime import date, timedelta
    from .gate import window_end
    uid = _uid(request)
    try:
        y, m = (int(x) for x in month.split("-"))
        first, last = date(y, m, 1), date(y, m, monthrange(y, m)[1])
    except Exception:
        raise HTTPException(422, "month must look like 2026-11")
    db, ev, today = sch._db(), [], date.today()

    def add(d, kind, title):
        if first <= d <= last:
            ev.append({"date": d.isoformat(), "kind": kind, "title": title})

    for t in db.query("SELECT * FROM scheduled_tasks WHERE user_id=%s AND enabled=1", (uid,)):
        d = max(first, today)
        while d <= last:
            add(d, "schedule", f"{t['name']} · {sch.describe(t).lower()}")
            d += timedelta(days=1)
    for it in db.query("SELECT li.title, li.store, o.order_date, o.delivery_date FROM line_items li JOIN orders o ON o.id=li.order_id"):
        pol = db.query("SELECT * FROM store_policies WHERE store=%s", (it["store"],))
        if not pol:
            continue
        for action, label in (("deadline_alert", "Last day to return"), ("price_adjust_email", "Last day to ask for a price adjustment on")):
            end = window_end(action, it, pol[0])
            if end:
                add(end, "deadline", f"{label} {it['title']}")
    per_day: dict = {}
    for a in db.query("SELECT ts, decision FROM audit_log"):
        d = str(a["ts"])[:10]
        per_day.setdefault(d, {}).setdefault(a["decision"], 0)
        per_day[d][a["decision"]] += 1
    names = {"ALLOW": "done", "ASK": "needs your OK", "BLOCK": "stopped by your rules"}
    for d, counts in per_day.items():
        add(date.fromisoformat(d), "activity", ", ".join(f"{n} {names.get(k, k)}" for k, n in counts.items()))
    return {"month": month, "today": today.isoformat(), "events": sorted(ev, key=lambda e: (e["date"], e["kind"]))}
