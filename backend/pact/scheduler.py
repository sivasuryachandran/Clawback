"""Scheduled tasks: things Clawback does on a timer. Safe by construction:
- a schedule can only run a FIXED kind (price_watch, daily_summary, deadline_check);
- money-moving work is never done here: price_watch hands new drops to B's propose endpoint, so the Pact gate still decides;
- price_watch skips while the Pact is paused or expired. Read-only kinds still run.
Handlers are pluggable: scheduler.register("price_watch", fn) replaces the default.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta

KINDS = {
    "price_watch": "Check prices",
    "daily_summary": "Daily summary",
    "deadline_check": "Return deadline check",
}
READ_ONLY = {"daily_summary", "deadline_check"}
FMT = "%Y-%m-%d %H:%M:%S"
HANDLERS: dict = {}


def _db():
    from backend import db
    return db


def _now() -> datetime:                       # local time: "daily at 8:00" means the user's 8:00
    return datetime.now().replace(microsecond=0)


def register(kind: str, fn) -> None:
    HANDLERS[kind] = fn


# ------------------------------------------------------------------ timing
def describe(t: dict) -> str:
    if t["schedule_type"] == "daily":
        return f"Daily at {t['at_time']}"
    n = int(t["interval_min"] or 0)
    return "Every minute" if n == 1 else f"Every {n} minutes" if n < 60 else "Every hour" if n == 60 else f"Every {n // 60} hours" if n % 60 == 0 else f"Every {n} minutes"


def next_after(t: dict, after: datetime) -> datetime:
    if t["schedule_type"] == "daily":
        h, m = (int(x) for x in str(t["at_time"]).split(":")[:2])
        cand = after.replace(hour=h, minute=m, second=0)
        return cand if cand > after else cand + timedelta(days=1)
    return after + timedelta(minutes=max(1, int(t["interval_min"] or 15)))


def validate(body: dict) -> dict:
    kind = body.get("kind")
    if kind not in KINDS:
        raise ValueError("Pick one of the available tasks.")
    st = body.get("schedule_type", "interval")
    if st not in ("interval", "daily"):
        raise ValueError("Pick how often.")
    out = {"kind": kind, "schedule_type": st, "interval_min": None, "at_time": None, "name": (body.get("name") or KINDS[kind])[:80]}
    if st == "interval":
        n = int(body.get("interval_min") or 0)
        if not 1 <= n <= 7 * 24 * 60:
            raise ValueError("Pick between 1 minute and 7 days.")
        out["interval_min"] = n
    else:
        h, m = (int(x) for x in str(body.get("at_time", "")).split(":")[:2])
        if not (0 <= h < 24 and 0 <= m < 60):
            raise ValueError("Pick a valid time.")
        out["at_time"] = f"{h:02d}:{m:02d}"
    return out


# ---------------------------------------------------------------- handlers
def _pact_ok(user_id: str) -> tuple[bool, str]:
    from .context import active_pact
    pact, meta = active_pact(user_id)
    if not pact:
        return False, "No signed Pact yet."
    if meta["revoked"]:
        return False, "Your Pact is paused."
    return True, ""


def _h_price_watch(task: dict) -> str:
    ok, why = _pact_ok(task["user_id"])
    if not ok:
        raise Skip(why + " Not checking prices.")
    try:
        from backend.watch import tick
    except ImportError:
        raise Skip("Price watching isn't connected yet.")
    opp_ids = tick() or []
    base = os.environ.get("CLAWBACK_API_BASE", "http://localhost:8001")
    sent = 0
    for oid in opp_ids:                         # B's endpoint runs propose -> gate -> act; the gate still decides
        try:
            import httpx
            httpx.post(f"{base}/opportunities/{oid}/propose", timeout=30)
            sent += 1
        except Exception:
            pass
    return "No new price drops." if not opp_ids else f"Found {len(opp_ids)} price change{'s' if len(opp_ids) > 1 else ''}; sent {sent} to your rules."


def _h_daily_summary(task: dict) -> str:
    db = _db()
    rows = db.query("SELECT decision, COUNT(*) AS n FROM audit_log GROUP BY decision")
    c = {r["decision"]: r["n"] for r in rows}
    return f"Done {c.get('ALLOW', 0)}, waiting for you {c.get('ASK', 0)}, stopped by your rules {c.get('BLOCK', 0)}."


def _h_deadline_check(task: dict) -> str:
    from .gate import window_end
    from .context import _json
    db = _db()
    items = db.query("SELECT li.title, li.store, o.order_date, o.delivery_date FROM line_items li JOIN orders o ON o.id=li.order_id")
    soon = []
    for it in items:
        pol = db.query("SELECT * FROM store_policies WHERE store=%s", (it["store"],))
        if not pol:
            continue
        end = window_end("deadline_alert", it, pol[0])
        if end and 0 <= (end - datetime.now().date()).days <= 7:
            soon.append(f"{it['title']} ({end.isoformat()})")
    return "Return closes soon: " + ", ".join(soon) if soon else "No return deadlines in the next 7 days."


class Skip(Exception):
    pass


HANDLERS.update(price_watch=_h_price_watch, daily_summary=_h_daily_summary, deadline_check=_h_deadline_check)


# ------------------------------------------------------------------ running
def run_task(task: dict) -> tuple[str, str]:
    """Run one task now. Returns (status, text). Never raises."""
    try:
        text = HANDLERS[task["kind"]](task)
        status = "ok"
    except Skip as s:
        status, text = "skipped", str(s)
    except Exception as e:
        status, text = "failed", f"Something went wrong on our side. Nothing was sent or bought. ({type(e).__name__})"
    now = _now()
    db = _db()
    db.execute("UPDATE scheduled_tasks SET last_run=%s, last_status=%s, last_result=%s, next_run=%s WHERE id=%s",
               (now.strftime(FMT), status, text[:500], next_after(task, now).strftime(FMT), task["id"]))
    db.execute("INSERT INTO scheduled_runs (task_id, ran_at, status, result) VALUES (%s,%s,%s,%s)", (task["id"], now.strftime(FMT), status, text[:500]))
    return status, text


def run_due() -> int:
    now = _now().strftime(FMT)
    due = _db().query("SELECT * FROM scheduled_tasks WHERE enabled=1 AND next_run<=%s", (now,))
    for t in due:
        run_task(t)
    return len(due)


async def loop(every_s: int = 15):
    while True:
        try:
            await asyncio.get_running_loop().run_in_executor(None, run_due)
        except Exception:
            pass
        await asyncio.sleep(every_s)


def attach(app) -> None:
    """One line for C's main.py:  scheduler.attach(app)"""
    @app.on_event("startup")
    async def _start():
        asyncio.create_task(loop())
