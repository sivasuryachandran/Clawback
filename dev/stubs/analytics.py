# STUB for rehearsal: replaced by Teammate C's real module (backend/analytics/events.py) tomorrow
import json

EVENTS: list[tuple[str, dict]] = []  # kept in memory so tests can look


def track(event: str, props: dict) -> None:
    """Never raises."""
    try:
        EVENTS.append((event, dict(props)))
        print(f"[track] {event} {json.dumps(props, default=str)}")
    except Exception:
        pass
