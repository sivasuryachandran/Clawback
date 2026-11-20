"""Send a few clearly-labelled sample events to Amplitude so the three dashboard charts can be BUILT before the demo.

Amplitude only offers an event (and its properties) in the chart builder after it has been received once. Run this once,
build the charts from docs/RUNBOOK.md, then filter them with  seed != true  (or use a separate test project).

    python scripts/seed_amplitude_events.py            # dry run: prints what it WOULD send
    python scripts/seed_amplitude_events.py --send     # sends them (needs AMPLITUDE_API_KEY in .env)

Every event has user_id "u_seed" and the property seed=true, so it can never be mistaken for the real demo run.
Nothing is written to the local analytics log, so Under the hood's counts stay honest.
"""
import os
import sys
from pathlib import Path

os.environ["ANALYTICS_LOG"] = "off"          # sample events must not count as today's real events
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SEED = {"seed": True, "user_id": "u_seed"}


def build_events() -> list[tuple[str, dict]]:
    """The demo's story in the section 3.10 vocabulary: the stops, the money, the plumbing events."""
    ev: list[tuple[str, dict]] = []
    for decision, rule, action in [("ALLOW", None, "price_adjust_email"), ("ALLOW", None, "claim_followup"),
                                   ("ASK", "R12", "rebuy_return"), ("BLOCK", "R5", "price_adjust_email"),
                                   ("BLOCK", "R4", "price_adjust_email"), ("BLOCK", "R8", "claim_packet"),
                                   ("ALLOW", None, "deadline_alert")]:
        ev.append(("gate_decision", dict(SEED, decision=decision, failed_rule=rule or "none", action_type=action, pact_version=2)))
    ev.append(("money_recovered", dict(SEED, amount_usd=34.0, store="mockstore")))
    ev.append(("money_recovered", dict(SEED, amount_usd=40.0, store="mockstore")))
    for kind, drop in [("price_drop", 34.0), ("price_drop", 40.0), ("price_drop", 25.0), ("price_drop", 20.0)]:
        ev.append(("opportunity_found", dict(SEED, kind=kind, drop_usd=drop, store="mockstore")))
    ev.append(("receipt_parsed", dict(SEED, model="sample", tokens=800, latency_ms=600, confidence=0.97, cost_usd=0.0004)))
    ev.append(("action_executed", dict(SEED, action_type="price_adjust_email", store="mockstore", amount_usd=34.0)))
    ev.append(("claim_packet_ready", dict(SEED, store="bestbuy")))
    ev.append(("approval_resolved", dict(SEED, status="executed")))
    ev.append(("pact_signed", dict(SEED, pact_version=2)))
    ev.append(("pact_paused", dict(SEED, pact_version=2)))
    return ev


def main(argv: list[str]) -> int:
    send = "--send" in argv
    events = build_events()
    print(f"{len(events)} sample events ({'SENDING' if send else 'dry run, nothing sent'}):")
    for name, props in events:
        print(f"  {name:20} {{{', '.join(f'{k}={v}' for k, v in props.items() if k not in ('seed', 'user_id'))}}}")
    if not send:
        print("\nRe-run with --send to send them. Every event carries seed=true; filter the charts with  seed != true.")
        return 0
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    if not os.environ.get("AMPLITUDE_API_KEY", "").strip():
        print("\nAMPLITUDE_API_KEY is not set in .env, so nothing was sent.")
        return 1
    from backend.analytics import events as analytics
    for name, props in events:
        analytics.track(name, props)
    analytics._executor.shutdown(wait=True)             # let the background sender finish
    s = analytics.status()
    print(f"\nsent {s['sent']}, failed {s['failed']}" + (f" ({s['last_error']})" if s["last_error"] else ""))
    return 0 if s["failed"] == 0 and s["sent"] == len(events) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
