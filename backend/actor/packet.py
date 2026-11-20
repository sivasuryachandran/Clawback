"""build_claim_packet(decision, opportunity_id) -> claim_packets.id

For chat stores (Target, Best Buy): receipt details, new price + URL + seen time, the quoted
policy sentence and a short chat script. Saved, then emailed to the USER (packet_v1).
"""
import json

from backend.actor.email import EmailRefused, get_transport, send_email
from backend.actor.templates import facts, render
from backend.agent import clock
from backend.agent.deps import db


def chat_script(f: dict) -> str:
    lines = [
        f"Hi, I bought {f['title']} ({f['variant']}) on {f['order_date']}, order {f['order_no']}, "
        f"for ${f['paid']:.2f}.",
        f"It's now ${f['price']:.2f} on {f['store_name']}'s site: {f['url']} (seen {f['seen_at']}).",
    ]
    if f["quote"]:
        lines.append(f"Your policy says: \"{f['quote']}\"")
    lines.append(f"Could you please adjust my order by the ${f['diff']:.2f} difference? Thank you!")
    return "\n".join(lines)


def build_claim_packet(decision, opportunity_id: int, transport=None) -> int:
    if getattr(decision, "decision", decision) != "ALLOW":
        raise EmailRefused("claim packet needs an ALLOW decision")
    f = facts(opportunity_id)
    script = chat_script(f)
    packet = {k: f[k] for k in ("store", "order_no", "order_date", "title", "variant", "paid",
                                "price", "url", "seen_at", "diff", "quote", "source_url")}
    # A retry (after a failed send) reuses the packet saved for this proposal: one packet each.
    existing = db.query("SELECT id, chat_script FROM claim_packets WHERE proposal_hash = %s "
                        "ORDER BY id LIMIT 1", (decision.proposal_hash,))
    if existing:
        pid, script = existing[0]["id"], existing[0]["chat_script"]
    else:
        pid = db.execute(
            "INSERT INTO claim_packets (opportunity_id, proposal_hash, store, packet_json, "
            "chat_script, created_at) VALUES (%s,%s,%s,%s,%s,%s)",
            (opportunity_id, decision.proposal_hash, f["store"], json.dumps(packet, default=str),
             script, clock.ts()))
    subject, body = render("packet_v1", f)
    body = body.replace("{packet}", script)
    send_email(decision, f["user_email"], subject, body, transport or get_transport())
    return pid
