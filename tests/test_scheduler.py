import sys
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.pact import api as pact_api, devkit, scheduler as sch
from backend.pact.schedules_api import router as sched_router


@pytest.fixture
def env(monkeypatch):
    import backend
    db = devkit.FakeDB()
    monkeypatch.setitem(sys.modules, "backend.db", db); monkeypatch.setattr(backend, "db", db, raising=False)
    monkeypatch.setitem(sys.modules, "backend.watch", type(sys)("w")); sys.modules["backend.watch"].tick = lambda: []
    devkit.seed(db)
    app = FastAPI(); app.include_router(pact_api.router); app.include_router(sched_router)
    return db, TestClient(app)


def test_validate_and_next_after():
    assert sch.validate({"kind": "price_watch", "schedule_type": "interval", "interval_min": 15})["interval_min"] == 15
    for bad in ({"kind": "send_email"}, {"kind": "price_watch", "interval_min": 0}, {"kind": "daily_summary", "schedule_type": "daily", "at_time": "25:00"}):
        with pytest.raises(Exception):
            sch.validate(bad)
    t = {"schedule_type": "daily", "at_time": "08:00", "interval_min": None}
    n = sch.next_after(t, datetime(2026, 11, 20, 9, 0))
    assert n == datetime(2026, 11, 21, 8, 0)
    assert sch.next_after({"schedule_type": "interval", "interval_min": 5}, datetime(2026, 1, 1, 0, 0)) == datetime(2026, 1, 1, 0, 5)
    assert sch.describe({"schedule_type": "daily", "at_time": "08:00", "interval_min": None}) == "Daily at 08:00"


def test_only_known_kinds_can_be_created(env):
    _, c = env
    assert c.post("/schedules", json={"kind": "rebuy_return", "interval_min": 5}).status_code == 422
    r = c.post("/schedules", json={"kind": "daily_summary", "schedule_type": "daily", "at_time": "07:30"})
    assert r.status_code == 200 and r.json()["when"] == "Daily at 07:30"


def test_seeded_schedules_run_and_record(env):
    _, c = env
    tasks = c.get("/schedules").json()["tasks"]
    assert [t["kind"] for t in tasks] == ["price_watch", "daily_summary", "deadline_check"]
    r = c.post(f"/schedules/{tasks[1]['id']}/run").json()
    assert r["status"] == "ok" and "stopped by your rules" in r["result"]
    assert r["task"]["last_status"] == "ok"


def test_price_watch_skips_when_pact_paused(env):
    db, c = env
    pw = c.get("/schedules").json()["tasks"][0]
    assert c.post(f"/schedules/{pw['id']}/run").json()["status"] == "ok"
    c.post("/pact/pause")
    r = c.post(f"/schedules/{pw['id']}/run").json()
    assert r["status"] == "skipped" and "paused" in r["result"]
    ds = c.get("/schedules").json()["tasks"][1]
    assert c.post(f"/schedules/{ds['id']}/run").json()["status"] == "ok"      # read-only still runs


def test_toggle_delete_and_run_due(env):
    db, c = env
    t = c.get("/schedules").json()["tasks"][1]
    assert c.post(f"/schedules/{t['id']}/toggle", json={"enabled": False}).json()["enabled"] is False
    db.execute("UPDATE scheduled_tasks SET next_run=%s", ((datetime.now() - timedelta(minutes=1)).strftime(sch.FMT),))
    assert sch.run_due() == 2                      # the disabled one is not run
    assert c.delete(f"/schedules/{t['id']}").json()["ok"] is True
    assert len(c.get("/schedules").json()["tasks"]) == 2


def test_calendar_has_deadlines_activity_and_schedules(env):
    from datetime import date, timedelta
    _, c = env
    t = date.today()
    months = {t.strftime("%Y-%m"), (t + timedelta(days=40)).strftime("%Y-%m")}
    events = [e for m in months for e in c.get(f"/calendar?month={m}").json()["events"]]
    kinds = {e["kind"] for e in events}
    assert {"schedule", "deadline", "activity"} <= kinds
    assert any("return" in e["title"].lower() and "Cotton Throw Blanket" in e["title"] for e in events)
    assert c.get("/calendar?month=nope").status_code == 422
