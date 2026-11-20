"""Amplitude analytics (Teammate C): `track(event, props)` never raises (section 3.4).

Events (section 4.4): receipt_parsed, opportunity_found, gate_decision (with failed_rule),
action_executed, claim_packet_ready, money_recovered, approval_resolved, pact_signed, pact_paused.

- A no-op toward Amplitude when AMPLITUDE_API_KEY is unset, so it can never break the demo.
- Sends from one background worker (fire and forget, ~3 s timeout).
- Keeps a small in-memory buffer + per-day counts, which Under the hood shows (and which carries
  receipt_parsed's model / tokens / latency, since the schema has no table for them). Every event is
  also appended to `.analytics_events.jsonl` (gitignored; ANALYTICS_LOG=off disables, or point it
  elsewhere), so a server restart or a `uvicorn --reload` mid-demo does not zero today's counts.
- Drops keys that could carry personal data before anything leaves the machine.

Smoke test the key at kickoff:   python -m backend.analytics.events
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import httpx

log = logging.getLogger("clawback.analytics")

KNOWN_EVENTS = (
    "receipt_parsed", "opportunity_found", "gate_decision", "action_executed", "claim_packet_ready",
    "money_recovered", "approval_resolved", "pact_signed", "pact_paused",
)
# Never forward these to a third party, whatever a caller put in props.
_DROP_KEYS = {"email", "to", "to_addr", "from_addr", "body", "pact_text", "text", "token", "api_key",
              "secret", "signature", "password", "reply_text", "subject"}

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="amplitude")
_lock = threading.Lock()
_buffer: deque = deque(maxlen=200)
_counts: dict[str, Counter] = {}
_stat = {"queued": 0, "sent": 0, "failed": 0, "last_error": None}
_make_client = lambda: httpx.Client(timeout=3.0)    # tests swap in a MockTransport client
_ROOT = Path(__file__).resolve().parents[2]
_loaded = False


def _log_path() -> Path | None:
    raw = os.environ.get("ANALYTICS_LOG", "").strip()
    if raw.lower() == "off":
        return None
    return Path(raw) if raw else _ROOT / ".analytics_events.jsonl"


def _load_once() -> None:
    """On first use after a (re)start, rebuild the buffer and today's counts from the log file."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    path = _log_path()
    if path is None or not path.exists():
        return
    try:
        today = _today()
        rows = []
        for line in path.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue                       # a torn or corrupt line is skipped, never fatal
        with _lock:
            for r in rows:
                if r.get("day") == today and r.get("event"):
                    _counts.setdefault(today, Counter())[r["event"]] += 1
            for r in rows[-200:]:
                if r.get("event"):
                    _buffer.appendleft({"event": r["event"], "at": r.get("at", ""), "props": r.get("props", {})})
            while len(_buffer) > 200:
                _buffer.pop()
    except Exception as exc:
        log.warning("could not read the analytics log: %s", exc)


def _append(event: str, at: str, day: str, props: dict) -> None:
    path = _log_path()
    if path is None:
        return
    try:
        with path.open("a") as f:
            f.write(json.dumps({"event": event, "at": at, "day": day, "props": props}, default=str) + "\n")
    except Exception as exc:
        log.warning("could not write the analytics log: %s", exc)


def _url() -> str:
    return ("https://api.eu.amplitude.com/2/httpapi" if os.environ.get("AMPLITUDE_REGION", "").lower() == "eu"
            else "https://api2.amplitude.com/2/httpapi")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _safe_props(props) -> dict:
    out = {}
    for k, v in (props or {}).items():
        if str(k).lower() in _DROP_KEYS:
            continue
        out[str(k)] = v
    return json.loads(json.dumps(out, default=str))    # JSON-safe copy


def _user_id(props) -> str:
    uid = str((props or {}).get("user_id") or "u_demo")
    return uid if len(uid) >= 5 else uid.ljust(5, "_")


def _post(event: str, props: dict, user_id: str, when_ms: int, insert_id: str | None = None) -> None:
    key = os.environ.get("AMPLITUDE_API_KEY", "").strip()
    body = {"api_key": key, "events": [{
        "user_id": user_id, "event_type": event, "event_properties": props, "time": when_ms,
        "insert_id": insert_id or str(uuid.uuid4()), "platform": "clawback-backend"}]}
    try:
        with _make_client() as c:
            r = c.post(_url(), json=body)
        if r.status_code >= 300:
            raise RuntimeError(f"Amplitude HTTP {r.status_code}: {r.text[:120]}")
        with _lock:
            _stat["sent"] += 1
    except Exception as exc:
        with _lock:
            _stat["failed"] += 1
            _stat["last_error"] = f"{type(exc).__name__}: {exc}"[:200]
        log.warning("amplitude send failed: %s", exc)


def track(event: str, props: dict | None = None, *, insert_id: str | None = None) -> None:
    """Record an event. Never raises, never blocks the caller. `insert_id` makes Amplitude drop a repeat of the same
    event (used for events derived from database rows, so a restart can never double-count)."""
    try:
        _load_once()
        safe = _safe_props(props)
        now = datetime.now(timezone.utc)
        with _lock:
            _buffer.appendleft({"event": event, "at": now.strftime("%H:%M:%S"), "props": safe})
            _counts.setdefault(_today(), Counter())[event] += 1
        _append(event, now.strftime("%H:%M:%S"), _today(), safe)
        if os.environ.get("AMPLITUDE_API_KEY", "").strip():
            with _lock:
                _stat["queued"] += 1
            tag = os.environ.get("ANALYTICS_RUN_TAG", "").strip()      # lets a chart show one run only (rehearsals stay out)
            sent = dict(safe, run_tag=tag[:40]) if tag else safe
            _executor.submit(_post, event, sent, _user_id(props), int(now.timestamp() * 1000), insert_id)
    except Exception as exc:        # tracking must never take the app down
        log.warning("track(%s) failed: %s", event, exc)


# ---- read side (Under the hood, voice) ----------------------------------------------------------------

def logged_keys() -> set[str]:
    """`dedupe_key`s of events already in the log file (derived events carry one), across all days."""
    path = _log_path()
    keys: set[str] = set()
    if path is None or not path.exists():
        return keys
    try:
        for line in path.read_text().splitlines():
            try:
                k = (json.loads(line).get("props") or {}).get("dedupe_key")
            except ValueError:
                continue
            if k:
                keys.add(str(k))
    except Exception as exc:
        log.warning("could not read the analytics log: %s", exc)
    return keys


def today_counts() -> dict[str, int]:
    _load_once()
    with _lock:
        return dict(_counts.get(_today(), Counter()))


def recent(event: str | None = None, limit: int = 20) -> list[dict]:
    _load_once()
    with _lock:
        rows = [dict(r) for r in _buffer if event is None or r["event"] == event]
    return rows[:limit]


def status() -> dict:
    with _lock:
        s = dict(_stat)
    s["enabled"] = bool(os.environ.get("AMPLITUDE_API_KEY", "").strip())
    s["region"] = "eu" if os.environ.get("AMPLITUDE_REGION", "").lower() == "eu" else "us"
    return s


def reset_for_tests(reload_from_disk: bool = False) -> None:
    """Clear memory. By default also mark the log as loaded, so a test starts empty."""
    global _loaded
    _loaded = not reload_from_disk
    with _lock:
        _buffer.clear()
        _counts.clear()
        _stat.update(queued=0, sent=0, failed=0, last_error=None)


if __name__ == "__main__":   # python -m backend.analytics.events
    from dotenv import load_dotenv
    load_dotenv()
    key = os.environ.get("AMPLITUDE_API_KEY", "").strip()
    if not key:
        raise SystemExit("AMPLITUDE_API_KEY is not set in .env, so track() is a no-op. Set it and run again.")
    body = {"api_key": key, "events": [{"user_id": "u_smoke", "event_type": "clawback_smoke_test",
                                        "time": int(time.time() * 1000), "insert_id": str(uuid.uuid4())}]}
    r = httpx.post(_url(), json=body, timeout=10)
    print(r.status_code, r.text)
    raise SystemExit(0 if r.status_code < 300 else 1)
