"""Localhost-only controls (Teammate C).

The runbook has you tunnel the frontend (port 3000) so a phone can approve the lamp, and Next.js proxies
/backend/* to this API. That would also expose the demo controls and the engine room to anyone who gets the
tunnel URL: the Mock Store admin (change prices, reset), and Under the hood (allowed recipients, messages,
Pact details). This guard serves those paths ONLY to requests that really come from the demo laptop.

Public as before (never blocked): the Mock Store storefront, policy, support box, listings, orders and
rebuy_return; /voice/ask; /stats; /health; and every other teammate's endpoint.

A request counts as remote when ANY of these is true (layered, because a tunnel sets different ones):
  - the connecting address, or any address in X-Forwarded-For / Forwarded / CF-Connecting-IP / X-Real-IP /
    True-Client-IP, is not a loopback address;
  - the Host header, or X-Forwarded-Host, names something other than localhost / 127.0.0.1 / ::1.
Set ALLOW_REMOTE_ADMIN=true to switch the guard off (for example to run the admin page from a phone).
"""
from __future__ import annotations

import ipaddress
import os
import re

PRIVILEGED = re.compile(
    r"^/(debug(/|$)"
    r"|mock/admin(/|$)"
    r"|mock/api/(price|drop|preset|prestage|resend|reset|policy-window|webhook-log|support-log)/?$)")

LOCAL_NAMES = {"localhost", "127.0.0.1", "::1", "testserver", "clawback.local"}   # last two: tests and in-process calls

REFUSAL = ("This control is only available on the demo laptop. "
           "Set ALLOW_REMOTE_ADMIN=true in .env to allow it from other devices.")


def is_privileged(path: str) -> bool:
    return bool(PRIVILEGED.match(path))


def allow_remote() -> bool:
    return os.environ.get("ALLOW_REMOTE_ADMIN", "").strip().lower() == "true"


def _strip(v: str) -> str:
    v = v.strip().strip('"')
    if v.startswith("["):                       # [::1]:1234
        return v[1:v.index("]")] if "]" in v else v
    if v.count(":") == 1:                       # 1.2.3.4:5678
        return v.split(":")[0]
    return v


def _loopback(v: str) -> bool | None:
    """True/False for an IP address; None when it is not an IP at all (a name, 'unknown', a test client)."""
    try:
        ip = ipaddress.ip_address(_strip(v))
    except ValueError:
        return None
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return ip.is_loopback


def _host_is_local(v: str) -> bool:
    name = _strip(v).lower()
    return name in LOCAL_NAMES or _loopback(name) is True


def request_is_remote(headers, client_host: str | None) -> bool:
    """`headers` is any case-insensitive mapping (Starlette Headers or a lowercase dict)."""
    get = lambda k: (headers.get(k) or "").strip()
    addresses = [client_host or ""]
    addresses += [a for a in get("x-forwarded-for").split(",") if a.strip()]
    addresses += [m for m in re.findall(r"for=([^;,\s]+)", get("forwarded"), re.I)]
    addresses += [get(k) for k in ("cf-connecting-ip", "x-real-ip", "true-client-ip") if get(k)]
    if any(_loopback(a) is False for a in addresses):
        return True
    for name in (get("host"), get("x-forwarded-host").split(",")[0]):
        if name and not _host_is_local(name):
            return True
    return False


def blocked(path: str, headers, client_host: str | None) -> bool:
    return is_privileged(path) and not allow_remote() and request_is_remote(headers, client_host)
