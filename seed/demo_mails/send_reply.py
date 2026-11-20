"""Post a pretend Mock Store support reply into Clawback, as if the rep had answered the earbuds claim.

    python -m seed.demo_mails.send_reply send_proof          # a normal "please send proof"  -> ALLOW, proof sent in the same thread
    python -m seed.demo_mails.send_reply refund_elsewhere    # asks to send the refund to judge@example.com -> stopped (R13)
    python -m seed.demo_mails.send_reply ignore_rules        # prompt injection ("rebuy the blue one")      -> stopped
    python -m seed.demo_mails.send_reply store_credit        # store offers credit instead                  -> needs you, nothing sent

Looks up the earbuds claim's opportunity and email thread from the database, then POSTs to /replies/inbound.
Env: CLAWBACK_API_BASE (default http://localhost:8000). Nothing here emails a real person.
"""
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.db import query  # noqa: E402

API = os.environ.get("CLAWBACK_API_BASE", "http://localhost:8000")
HERE = Path(__file__).parent / "replies"


def main(name: str) -> None:
    f = HERE / f"{name}.txt"
    if not f.exists():
        sys.exit(f"unknown reply {name!r}; choose from: {', '.join(p.stem for p in sorted(HERE.glob('*.txt')))}")
    rows = query("SELECT opportunity_id, thread_id FROM messages WHERE direction='out' AND to_addr IS NOT NULL ORDER BY id ASC LIMIT 1")
    if not rows:
        sys.exit("no outgoing claim email yet: run the earbuds price drop first (POST /opportunities/<id>/propose)")
    body = {"opportunity_id": rows[0]["opportunity_id"], "thread_id": rows[0]["thread_id"], "reply_text": f.read_text()}
    r = httpx.post(f"{API}/replies/inbound", json=body, timeout=60)
    print(r.status_code, r.text)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "send_proof")
