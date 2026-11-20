"""Statsig feature gates (Teammate C): put one change (a prompt, a model, a tool) behind a gate, ship it to teammates
first, watch what happened, keep it or switch it off.

    from backend.analytics.gates import gate_enabled
    if gate_enabled("proposal_prompt_v2", user_id="u_demo"):      # False until a rule is added in the Statsig console
        ...new behaviour...

- A NO-OP unless STATSIG_SERVER_SECRET is set and `statsig-python-core` is installed: it returns `default` and the app behaves
  exactly as before. Nothing here can break a demo: it never raises and never waits long (see INIT_TIMEOUT_S).
- The secret is the Server Secret Key from the Statsig console's API keys page. Keep it in .env, never commit it.
- A new gate returns False until a rule is added in the console (Feature Management > Feature Gates > Add New Rule), and rules
  are not auto-saved: press Save.
"""
from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

log = logging.getLogger("clawback.gates")

INIT_TIMEOUT_S = 5.0          # first use only: if Statsig does not answer in time, use the defaults for this process
_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="statsig-init")
_client = None
_failed: str | None = None


def _secret() -> str:
    return os.environ.get("STATSIG_SERVER_SECRET", "").strip()


def enabled() -> bool:
    return bool(_secret())


def _init(secret: str):
    from statsig_python_core import Statsig
    client = Statsig(secret)
    if hasattr(client, "initialize_with_details"):
        details = client.initialize_with_details().result()
        if not getattr(details, "is_config_spec_ready", True):      # a wrong secret "initializes" but has no gates
            raise RuntimeError("Statsig returned no gate config: check STATSIG_SERVER_SECRET and the network")
    else:
        client.initialize().wait()
    return client


def _get_client():
    global _client, _failed
    if _client is not None:
        return _client
    if _failed or not _secret():
        return None
    with _lock:
        if _client is not None or _failed:
            return _client
        try:
            _client = _executor.submit(_init, _secret()).result(timeout=INIT_TIMEOUT_S)
        except FutureTimeout:
            _failed = f"Statsig did not answer within {INIT_TIMEOUT_S:g} s; gate defaults are used"
            log.warning(_failed)
        except Exception as exc:                               # package missing, bad key, no network ...
            _failed = f"{type(exc).__name__}: {exc}"[:200]
            log.warning("Statsig unavailable, gate defaults are used: %s", _failed)
    return _client


def gate_enabled(name: str, user_id: str = "u_demo", default: bool = False) -> bool:
    """True if the gate passes for this user. Returns `default` when Statsig is not configured or not reachable."""
    try:
        client = _get_client()
        if client is None:
            return default
        from statsig_python_core import StatsigUser
        return bool(client.check_gate(StatsigUser(user_id), name))
    except Exception as exc:
        log.warning("gate %s check failed, using the default: %s", name, exc)
        return default


def shutdown() -> None:
    """Flush Statsig's exposure log (call once when the app stops). Never raises."""
    try:
        if _client is not None:
            _client.shutdown().wait()
    except Exception as exc:
        log.warning("Statsig shutdown failed: %s", exc)


def status() -> dict:
    """For Under the hood: configured? connected? why not?"""
    return {"configured": enabled(), "connected": _client is not None, "problem": _failed}


def reset_for_tests() -> None:
    global _client, _failed
    _client, _failed = None, None
