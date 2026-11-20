"""Inbound path for REAL email replies (the /mock/support box is the other one).

    poll_replies(reader=None) -> list of results, one per new reply in one of OUR claim threads

Readers behind one interface:
  FileInboxReader (default tonight): every inbox/*.eml is one reply. The thread is taken from
    the X-Clawback-Thread header, or from In-Reply-To matched against the Message-IDs in
    outbox/*.eml. Handled files move to inbox/processed/ (or inbox/ignored/, inbox/failed/).
  GmailInboxReader: NOT WIRED. At the event, read new messages in our claim threads with the
    Airbyte Gmail connector (A's read side) or the Gmail API, dedupe on the Gmail message id,
    and return the Gmail threadId (it must equal what send_email stored in messages.thread_id).

Only replies in threads WE started are handled; everything else is ignored. Messages from
our own sender address are ignored (no loops).
"""
import os
from email import policy as email_policy
from email.parser import BytesParser
from pathlib import Path
from typing import Protocol

from backend.agent.deps import db, track


class InboxReader(Protocol):
    def fetch_new(self) -> list[dict]:
        """[{ref, thread_id or None, sender, body}]"""

    def done(self, ref, outcome: str) -> None:
        """Mark one message handled: outcome is processed | ignored | failed."""


def _text(msg) -> str:
    part = msg.get_body(preferencelist=("plain",)) if msg.is_multipart() else msg
    return (part.get_content() if part is not None else "").strip()


class FileInboxReader:
    def __init__(self, folder: str | None = None, outbox: str | None = None):
        self.folder = Path(folder or os.environ.get("INBOX_DIR", "inbox"))
        self.outbox = Path(outbox or os.environ.get("OUTBOX_DIR", "outbox"))

    def _sent_ids(self) -> dict[str, str]:
        out = {}
        for p in self.outbox.glob("*.eml") if self.outbox.exists() else []:
            m = BytesParser(policy=email_policy.default).parsebytes(p.read_bytes(), headersonly=True)
            if m["Message-ID"] and m["X-Clawback-Thread"]:
                out[m["Message-ID"].strip()] = m["X-Clawback-Thread"].strip()
        return out

    def fetch_new(self) -> list[dict]:
        if not self.folder.exists():
            return []
        sent = None
        items = []
        for p in sorted(self.folder.glob("*.eml")):
            m = BytesParser(policy=email_policy.default).parsebytes(p.read_bytes())
            thread = (m["X-Clawback-Thread"] or "").strip() or None
            if not thread and m["In-Reply-To"]:
                sent = sent if sent is not None else self._sent_ids()
                thread = sent.get(m["In-Reply-To"].strip())
            items.append({"ref": p, "thread_id": thread,
                          "sender": (m["From"] or "").strip(), "body": _text(m)})
        return items

    def done(self, ref, outcome: str) -> None:
        dest = self.folder / outcome
        dest.mkdir(parents=True, exist_ok=True)
        Path(ref).replace(dest / Path(ref).name)


class GmailInboxReader:
    def fetch_new(self) -> list[dict]:
        raise NotImplementedError("Gmail inbound not wired: see module docstring")

    def done(self, ref, outcome: str) -> None:
        raise NotImplementedError


def get_reader() -> InboxReader:
    return GmailInboxReader() if os.environ.get("INBOX_READER") == "gmail" else FileInboxReader()


def poll_replies(reader: InboxReader | None = None) -> list[dict]:
    from backend.actor.email import SENDER
    from backend.agent.replies import handle_reply
    reader = reader or get_reader()
    results = []
    for m in reader.fetch_new():
        base = {"ref": str(m["ref"]), "thread_id": m["thread_id"], "sender": m["sender"]}
        ours = db.query("SELECT opportunity_id FROM messages WHERE thread_id = %s AND "
                        "direction = 'out' ORDER BY id LIMIT 1", (m["thread_id"],)) if m["thread_id"] else []
        if not ours or SENDER in m["sender"].lower() or not m["body"]:
            reader.done(m["ref"], "ignored")
            results.append(base | {"outcome": "ignored", "why": "not one of our claim threads"
                                   if not ours else "own message or empty"})
            continue
        try:
            r = handle_reply(ours[0]["opportunity_id"], m["body"], m["thread_id"], sender=m["sender"])
            reader.done(m["ref"], "processed")
            results.append(base | {"outcome": "processed"} | r)
        except Exception as e:  # one bad message must not block the rest
            reader.done(m["ref"], "failed")
            track("reply_poll_failed", {"error": repr(e)[:200]})
            results.append(base | {"outcome": "failed", "why": repr(e)[:200]})
    return results
