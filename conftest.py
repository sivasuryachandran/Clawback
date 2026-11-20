"""Repo-root pytest hook (Teammate C).

After every run it writes `.pytest_last.json`. The Under the hood page reads that file for
its "Tests" panel (section 3.10): "Last `pytest -q` result (pass count, time) written to a
file by the test run".
"""
import json
import os
import time

import pytest

# backend/main.py calls load_dotenv(). A developer's real .env (blank allow-list, a fixed CLAWBACK_TODAY ...) must never change what the
# tests see, in this process or in the app subprocesses the end-to-end tests start (they inherit this variable).
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

_started = {"t": None}


def pytest_sessionstart(session):
    _started["t"] = time.time()


def pytest_sessionfinish(session, exitstatus):
    try:
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        stats = reporter.stats if reporter else {}
        passed = len(stats.get("passed", []))
        failed = len(stats.get("failed", [])) + len(stats.get("error", []))
        skipped = len(stats.get("skipped", []))
        payload = {
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "duration_s": round(time.time() - (_started["t"] or time.time()), 2),
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "exit_status": int(exitstatus),
        }
        path = os.path.join(str(session.config.rootpath), ".pytest_last.json")
        with open(path, "w") as f:
            json.dump(payload, f)
    except Exception:  # a reporting hook must never break the test run
        pass


@pytest.fixture(autouse=True)
def _isolate_analytics(tmp_path, monkeypatch):
    """No test, ours or a teammate's, may write to the repo's real analytics log or start with another test's events."""
    monkeypatch.setenv("ANALYTICS_LOG", str(tmp_path / "analytics_events.jsonl"))
    try:
        from backend.analytics import events, recovery
        events.reset_for_tests()
        recovery.reset_for_tests()
        yield
        events.reset_for_tests()
        recovery.reset_for_tests()
    except ImportError:          # backend not importable in this run: nothing to isolate
        yield
