"""The only three email templates. Every value is filled from the database."""
from backend.agent.deps import db
from backend.agent.senso_client import STORE_NAMES

FOOTER = "\n\n--\nSent by Clawback on behalf of {user_name}"


def facts(opportunity_id: int) -> dict:
    opp = db.query("SELECT * FROM opportunities WHERE id = %s", (opportunity_id,))[0]
    item = db.query(
        "SELECT li.*, o.order_no, o.order_date, o.delivery_date FROM line_items li "
        "JOIN orders o ON o.id = li.order_id WHERE li.id = %s", (opp["line_item_id"],))[0]
    listing = (db.query("SELECT * FROM listings WHERE id = %s", (opp["listing_id"],)) or [None])[0]
    latest = (db.query("SELECT price, seen_at FROM price_history WHERE listing_id = %s ORDER BY "
                       "seen_at DESC LIMIT 1", (opp["listing_id"],)) or [None])[0]
    pc = (db.query("SELECT * FROM policy_checks WHERE opportunity_id = %s AND senso_source_id "
                   "IS NOT NULL ORDER BY id DESC LIMIT 1", (opportunity_id,)) or [None])[0]
    from backend.agent.propose import USER_ID
    user = db.query("SELECT * FROM users WHERE id = %s", (USER_ID,))[0]
    paid = float(item["price_paid"])
    price = float(latest["price"]) if latest else None
    return {
        "user_name": user["name"], "user_email": user["email"],
        "store": item["store"], "store_name": STORE_NAMES.get(item["store"], item["store"]),
        "order_no": item["order_no"], "order_date": item["order_date"],
        "title": item["title"], "variant": item["variant"], "paid": paid,
        "price": price, "seen_at": latest and latest["seen_at"],
        "url": listing and listing["url"], "diff": round(paid - price, 2) if price is not None else None,
        "quote": pc and pc["cited_text"], "source_url": pc and pc["source_url"],
    }


def render(template: str, f: dict) -> tuple[str, str]:
    quote = (f'Your policy says: "{f["quote"]}" ({f["source_url"]})' if f["quote"] else "")
    if template == "price_adjust_v1":
        subject = f"Price adjustment request, order {f['order_no']}"
        body = (f"Hello {f['store_name']} support,\n\n"
                f"On {f['order_date']} I bought {f['title']} ({f['variant']}) for ${f['paid']:.2f}, "
                f"order {f['order_no']}. It is now ${f['price']:.2f} on your site ({f['url']}, "
                f"seen {f['seen_at']}).\n{quote}\n\n"
                f"Please refund the difference of ${f['diff']:.2f} to the original payment method.\n\n"
                f"Thank you,\n{f['user_name']}")
    elif template == "proof_v1":
        subject = f"Re: Price adjustment request, order {f['order_no']}"
        body = (f"Hello,\n\nHere is the proof you asked for:\n"
                f"- Order {f['order_no']}, placed {f['order_date']}: {f['title']} ({f['variant']}), "
                f"paid ${f['paid']:.2f}\n"
                f"- Current price: ${f['price']:.2f} at {f['url']} (seen {f['seen_at']})\n"
                f"{quote}\n\nThank you,\n{f['user_name']}")
    elif template == "packet_v1":
        subject = f"Ready to send in {f['store_name']} chat: {f['title']}"
        body = (f"Hi {f['user_name']},\n\n{f['store_name']} doesn't take requests by email. "
                f"Everything is below, ready to paste into {f['store_name']}'s chat.\n\n"
                "{packet}")
    else:
        raise ValueError(f"unknown template {template!r}")
    return subject, body + FOOTER.format(user_name=f["user_name"])
