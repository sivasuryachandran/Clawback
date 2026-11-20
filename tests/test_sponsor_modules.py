"""Statsig gates and Amplitude Agent Analytics: no-ops without keys, never raise, and call the SDKs the way they document.
The SDKs are replaced by fakes, so nothing here touches the network."""
import sys
import time
import types
from concurrent.futures import Future

import pytest

from backend.analytics import agent_analytics, gates


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("STATSIG_SERVER_SECRET", "AMPLITUDE_AI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    gates.reset_for_tests()
    agent_analytics.reset_for_tests()
    yield
    gates.reset_for_tests()
    agent_analytics.reset_for_tests()


# ---- fakes ----------------------------------------------------------------------------------------

class _Wait:
    def wait(self):
        return None


def fake_statsig(monkeypatch, *, passes=("on_gate",), init_error=None, init_delay=0.0, ready=True):
    calls = {"init": 0, "checks": [], "shutdown": 0, "secret": None}

    class StatsigUser:
        def __init__(self, user_id):
            self.user_id = user_id

    class Statsig:
        def __init__(self, secret):
            calls["secret"] = secret

        def initialize(self):
            calls["init"] += 1
            time.sleep(init_delay)
            if init_error:
                raise init_error
            return _Wait()

        def initialize_with_details(self):
            calls["init"] += 1
            time.sleep(init_delay)
            if init_error:
                raise init_error
            fut = Future()
            fut.set_result(types.SimpleNamespace(is_config_spec_ready=ready))
            return fut

        def check_gate(self, user, name, options=None):
            calls["checks"].append((user.user_id, name))
            return name in passes

        def shutdown(self):
            calls["shutdown"] += 1
            return _Wait()

    mod = types.ModuleType("statsig_python_core")
    mod.Statsig, mod.StatsigUser = Statsig, StatsigUser
    monkeypatch.setitem(sys.modules, "statsig_python_core", mod)
    return calls


def fake_amplitude(monkeypatch, *, fail=False):
    log = {"messages": [], "tools": [], "ai": [], "flushes": 0, "key": None, "session": None}

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def track_user_message(self, content, **kw):
            log["messages"].append(content)

        def track_tool_call(self, tool_name, latency_ms, success, **kw):
            log["tools"].append((tool_name, latency_ms, success))

        def track_ai_message(self, content, model, provider, latency_ms, **kw):
            if fail:
                raise RuntimeError("boom")
            log["ai"].append((content, model, provider, latency_ms))

    class Agent:
        def session(self, session_id=None, user_id=None, **kw):
            log["session"] = (session_id, user_id)
            return Session()

    class AmplitudeAI:
        def __init__(self, amplitude=None, api_key=None, config=None):
            log["key"] = api_key

        def agent(self, agent_id, **kw):
            log["agent"] = agent_id
            return Agent()

        def flush(self):
            log["flushes"] += 1

    mod = types.ModuleType("amplitude_ai")
    mod.AmplitudeAI = AmplitudeAI
    monkeypatch.setitem(sys.modules, "amplitude_ai", mod)
    return log


# ---- gates ----------------------------------------------------------------------------------------

def test_gate_without_secret_returns_default_and_never_imports_sdk(monkeypatch):
    monkeypatch.setitem(sys.modules, "statsig_python_core", None)     # an import would raise
    assert gates.gate_enabled("on_gate") is False
    assert gates.gate_enabled("on_gate", default=True) is True
    assert gates.status() == {"configured": False, "connected": False, "problem": None}


def test_gate_with_secret_asks_statsig(monkeypatch):
    calls = fake_statsig(monkeypatch)
    monkeypatch.setenv("STATSIG_SERVER_SECRET", "secret-test-123")
    assert gates.gate_enabled("on_gate", user_id="u_demo") is True
    assert gates.gate_enabled("off_gate", user_id="u_demo") is False
    assert calls["secret"] == "secret-test-123"
    assert calls["init"] == 1                                         # connected once, reused
    assert calls["checks"] == [("u_demo", "on_gate"), ("u_demo", "off_gate")]
    assert gates.status()["connected"] is True
    gates.shutdown()
    assert calls["shutdown"] == 1


def test_gate_falls_back_when_sdk_missing(monkeypatch):
    monkeypatch.setenv("STATSIG_SERVER_SECRET", "secret-test-123")
    monkeypatch.setitem(sys.modules, "statsig_python_core", None)
    assert gates.gate_enabled("on_gate", default=True) is True
    st = gates.status()
    assert st["configured"] and not st["connected"] and st["problem"]


def test_gate_falls_back_when_init_fails_and_does_not_retry(monkeypatch):
    calls = fake_statsig(monkeypatch, init_error=RuntimeError("Invalid API Key"))
    monkeypatch.setenv("STATSIG_SERVER_SECRET", "bad")
    assert gates.gate_enabled("on_gate") is False
    assert gates.gate_enabled("on_gate") is False
    assert calls["init"] == 1                                         # one slow failure, not one per request
    assert "Invalid API Key" in gates.status()["problem"]


def test_gate_with_wrong_secret_has_no_config_so_uses_default(monkeypatch):
    fake_statsig(monkeypatch, ready=False)
    monkeypatch.setenv("STATSIG_SERVER_SECRET", "wrong")
    assert gates.gate_enabled("on_gate", default=True) is True      # not False-by-accident: the default decides
    st = gates.status()
    assert not st["connected"] and "STATSIG_SERVER_SECRET" in st["problem"]


def test_gate_init_timeout_uses_default(monkeypatch):
    fake_statsig(monkeypatch, init_delay=0.5)
    monkeypatch.setattr(gates, "INIT_TIMEOUT_S", 0.05)
    monkeypatch.setenv("STATSIG_SERVER_SECRET", "secret-test-123")
    assert gates.gate_enabled("on_gate", default=False) is False
    assert "did not answer" in gates.status()["problem"]


def test_gate_check_error_uses_default(monkeypatch):
    calls = fake_statsig(monkeypatch)
    monkeypatch.setenv("STATSIG_SERVER_SECRET", "secret-test-123")
    assert gates.gate_enabled("on_gate") is True

    def boom(*a, **k):
        raise RuntimeError("network down")
    sys.modules["statsig_python_core"].Statsig.check_gate = boom
    assert gates.gate_enabled("on_gate", default=True) is True
    assert calls["init"] == 1


# ---- agent analytics ------------------------------------------------------------------------------

def test_agent_analytics_off_without_key(monkeypatch):
    monkeypatch.setitem(sys.modules, "amplitude_ai", None)
    assert agent_analytics.track_exchange(session_id="s", user_text="a", ai_text="b", model="m") is False
    assert agent_analytics.status()["queued"] == 0


def test_agent_analytics_sends_one_session(monkeypatch):
    log = fake_amplitude(monkeypatch)
    monkeypatch.setenv("AMPLITUDE_AI_API_KEY", "key-test-123")
    assert agent_analytics.track_exchange(session_id="opp-7", user_text="Propose an action", ai_text="price_adjust_email",
                                          model="m1", provider="p1", latency_ms=640, user_id="u_demo",
                                          tool_calls=[("propose_action", 640, True)]) is True
    agent_analytics.drain()
    assert log["key"] == "key-test-123"
    assert log["agent"] == agent_analytics.AGENT_ID
    assert log["session"] == ("opp-7", "u_demo")
    assert log["messages"] == ["Propose an action"]
    assert log["tools"] == [("propose_action", 640.0, True)]
    assert log["ai"] == [("price_adjust_email", "m1", "p1", 640.0)]
    assert log["flushes"] == 1
    s = agent_analytics.status()
    assert (s["queued"], s["sent"], s["failed"], s["configured"]) == (1, 1, 0, True)


def test_agent_analytics_masks_emails_and_shortens(monkeypatch):
    log = fake_amplitude(monkeypatch)
    monkeypatch.setenv("AMPLITUDE_AI_API_KEY", "key-test-123")
    agent_analytics.track_exchange(session_id="s", user_text="Write to jane.doe@example.com about it " + "x" * 2000,
                                   ai_text="cc a+b@store.co.uk", model="m")
    agent_analytics.drain()
    assert "jane.doe@example.com" not in log["messages"][0]
    assert "[email]" in log["messages"][0]
    assert len(log["messages"][0]) <= agent_analytics.MAX_TEXT
    assert log["ai"][0][0] == "cc [email]"


def test_agent_analytics_failure_is_counted_not_raised(monkeypatch):
    fake_amplitude(monkeypatch, fail=True)
    monkeypatch.setenv("AMPLITUDE_AI_API_KEY", "key-test-123")
    assert agent_analytics.track_exchange(session_id="s", user_text="a", ai_text="b", model="m") is True
    agent_analytics.drain()
    s = agent_analytics.status()
    assert s["failed"] == 1 and s["sent"] == 0 and "boom" in s["last_error"]


def test_agent_analytics_sdk_missing_is_counted_not_raised(monkeypatch):
    monkeypatch.setenv("AMPLITUDE_AI_API_KEY", "key-test-123")
    monkeypatch.setitem(sys.modules, "amplitude_ai", None)
    agent_analytics.track_exchange(session_id="s", user_text="a", ai_text="b", model="m")
    agent_analytics.drain()
    assert agent_analytics.status()["failed"] == 1


def test_agent_analytics_bad_arguments_never_raise(monkeypatch):
    fake_amplitude(monkeypatch)
    monkeypatch.setenv("AMPLITUDE_AI_API_KEY", "key-test-123")
    assert agent_analytics.track_exchange(session_id="s", user_text=None, ai_text=None, model="m",
                                          latency_ms="not a number") is False


# ---- Under the hood -------------------------------------------------------------------------------

def test_under_the_hood_analytics_panel_reports_both():
    from backend.debug import panels
    p = panels.analytics_panel(use_sample=True)
    assert p["agent_analytics"]["configured"] is False
    assert p["gates"]["configured"] is False
