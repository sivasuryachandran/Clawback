"""Check the Amplitude Agent Analytics key and the Statsig secret from your own .env, before the demo.

    python scripts/sponsor_smoke.py                   # reports what is configured, sends nothing
    python scripts/sponsor_smoke.py --send            # sends one tagged test conversation, and checks one gate
    python scripts/sponsor_smoke.py --gate my_gate    # which gate to check (default: proposal_prompt_v2)

The test conversation uses user_id "u_smoke" and the text "smoke test", so it is easy to filter out in Amplitude.
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from backend.analytics import agent_analytics, gates  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--gate", default="proposal_prompt_v2")
    args = ap.parse_args()
    ok = True

    print("Agent Analytics:", "key set" if agent_analytics.enabled() else "AMPLITUDE_AI_API_KEY is empty (off)")
    print("Statsig:        ", "secret set" if gates.enabled() else "STATSIG_SERVER_SECRET is empty (gates use their defaults)")
    if not args.send:
        print("\nNothing sent. Add --send to try both.")
        return 0

    if agent_analytics.enabled():
        agent_analytics.track_exchange(session_id="smoke-test", user_id="u_smoke", user_text="smoke test", ai_text="smoke test reply",
                                       model="smoke", provider="smoke", latency_ms=1, tool_calls=[("smoke_tool", 1, True)])
        agent_analytics.drain()
        s = agent_analytics.status()
        good = s["sent"] == 1 and s["failed"] == 0
        ok &= good
        print("Agent Analytics send:", "sent with no local error. Amplitude does not report a wrong key back, so look for session smoke-test in Agent Analytics" if good else f"FAILED {s['last_error']}")
    if gates.enabled():
        value = gates.gate_enabled(args.gate, user_id="u_smoke")
        st = gates.status()
        good = st["connected"]
        ok &= good
        print(f"Statsig gate {args.gate!r} for u_smoke:", value if good else f"FAILED {st['problem']}")
        if good and not value:
            print("  (False is normal until you add a rule to the gate in the Statsig console and press Save.)")
        gates.shutdown()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
