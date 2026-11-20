"""Offline tests for backend/ingest/airbyte_core.py (Teammate B): fake Airbyte Core destinations."""
import base64
import json
import sqlite3

import pytest

from backend import db


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def gmail_record(mid: str, subject: str, body: str) -> dict:
    """Shape of a Gmail API message as the Airbyte Gmail source emits it."""
    return {"id": mid, "threadId": f"t-{mid}", "internalDate": "1763600000000", "snippet": body[:40],
            "payload": {"mimeType": "multipart/alternative",
                        "headers": [{"name": "From", "value": "orders@mockstore.test"},
                                    {"name": "Subject", "value": subject},
                                    {"name": "Date", "value": "Tue, 11 Nov 2026 10:00:00 -0800"}],
                        "parts": [{"mimeType": "text/plain", "body": {"data": _b64(body)}},
                                  {"mimeType": "text/html", "body": {"data": _b64(f'<p>{body}</p>')}}]}}


def wrapped(rec: dict) -> dict:
    return {"_airbyte_ab_id": "ab-" + rec["id"], "_airbyte_emitted_at": 1763600000000, "_airbyte_data": rec}


@pytest.fixture
def core(tmp_path, monkeypatch):
    monkeypatch.delenv("SINGLESTORE_URL", raising=False)
    monkeypatch.setenv("CLAWBACK_DB", str(tmp_path / "t.db"))
    for k in ("AIRBYTE_CORE_DEST", "AIRBYTE_CORE_STREAM", "AIRBYTE_CORE_CLIENT_ID", "AIRBYTE_CORE_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AIRBYTE_CORE_URL", "http://127.0.0.1:9")  # nothing listens: fast "unreachable"
    db.init_schema()
    import backend.ingest.parser as parser
    calls = []
    monkeypatch.setattr(parser, "parse_unparsed_emails", lambda: calls.append(1) or 0)
    from backend.ingest import airbyte_core
    return airbyte_core, calls, tmp_path


def raw():
    return db.query("SELECT gmail_msg_id, from_addr, subject, received_at, body, parsed FROM raw_emails ORDER BY id")


def test_jsonl_destination_with_airbyte_wrapper(core, monkeypatch):
    ac, calls, tmp = core
    d = tmp / "dest" / "gmail"
    d.mkdir(parents=True)
    recs = [gmail_record("m1", "Your Mock Store order MS-55021", "Order MS-55021 Aurora Wireless Earbuds $129.99"),
            gmail_record("m2", "Your Mock Store order MS-55022", "Order MS-55022 Nimbus Smart Lamp $239.00")]
    (d / "_airbyte_raw_messages.jsonl").write_text("\n".join(json.dumps(wrapped(r)) for r in recs), encoding="utf-8")
    (d / "_airbyte_raw_labels.jsonl").write_text(json.dumps(wrapped({"id": "L1", "name": "INBOX"})), encoding="utf-8")
    monkeypatch.setenv("AIRBYTE_CORE_DEST", f"jsonl:{tmp / 'dest'}")
    out = ac.ingest()
    assert (out["source"], out["read"], out["inserted"]) == ("airbyte_core", 2, 2)
    assert out["airbyte_reachable"] is False and calls == [1]
    rows = raw()
    assert [r["gmail_msg_id"] for r in rows] == ["m1", "m2"]
    assert rows[0]["from_addr"] == "orders@mockstore.test" and rows[0]["subject"].endswith("MS-55021")
    assert rows[0]["body"] == "Order MS-55021 Aurora Wireless Earbuds $129.99"  # text/plain part, decoded
    assert rows[0]["received_at"].startswith("Tue, 11 Nov 2026") and not rows[0]["parsed"]


def test_ids_already_present_are_skipped(core, monkeypatch):
    ac, _, tmp = core
    f = tmp / "messages.jsonl"
    f.write_text(json.dumps(gmail_record("m1", "Order", "body")), encoding="utf-8")
    monkeypatch.setenv("AIRBYTE_CORE_DEST", f"jsonl:{f}")
    assert ac.ingest()["inserted"] == 1
    second = ac.ingest()
    assert (second["inserted"], second["skipped_existing"]) == (0, 1)
    assert len(raw()) == 1


def test_sqlite_destination_raw_table(core, monkeypatch):
    ac, _, tmp = core
    path = tmp / "dest.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE _airbyte_raw_messages (_airbyte_ab_id TEXT, _airbyte_emitted_at INT, _airbyte_data TEXT)")
    con.execute("INSERT INTO _airbyte_raw_messages VALUES (?,?,?)",
                ("a1", 1, json.dumps(gmail_record("s1", "Best Buy order", "Order BBY01 Wireless Mouse $49.99"))))
    con.commit()
    con.close()
    monkeypatch.setenv("AIRBYTE_CORE_DEST", f"sqlite:{path}")
    out = ac.ingest()
    assert (out["source"], out["inserted"]) == ("airbyte_core", 1)
    assert raw()[0]["body"] == "Order BBY01 Wireless Mouse $49.99"


def test_sqlite_destination_flat_columns_and_internal_date(core, monkeypatch):
    ac, _, tmp = core
    path = tmp / "flat.sqlite"
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE messages (id TEXT, "from" TEXT, subject TEXT, body TEXT, internalDate TEXT)')
    con.execute("INSERT INTO messages VALUES ('f1','shop@target.test','Target order','Order TGT1 Throw $29.99','1763600000000')")
    con.commit()
    con.close()
    monkeypatch.setenv("AIRBYTE_CORE_DEST", f"sqlite:{path}")
    ac.ingest()
    r = raw()[0]
    assert (r["from_addr"], r["subject"], r["body"]) == ("shop@target.test", "Target order", "Order TGT1 Throw $29.99")
    assert r["received_at"].startswith("2025-11-20")  # from internalDate (ms since epoch, UTC)


def test_no_destination_falls_back_loudly(core, capsys):
    ac, calls, _ = core
    out = ac.ingest()
    assert out["source"] == "seed_fallback" and "AIRBYTE_CORE_DEST is not set" in out["reason"]
    assert "AIRBYTE CORE NOT USED" in capsys.readouterr().out
    assert out["inserted"] == len(raw()) > 0 and calls == [1]  # seeded receipts, then parsed


def test_unreadable_destination_falls_back_with_reason(core, monkeypatch):
    ac, _, tmp = core
    monkeypatch.setenv("AIRBYTE_CORE_DEST", f"sqlite:{tmp / 'missing.sqlite'}")
    out = ac.ingest()
    assert out["source"] == "seed_fallback" and "missing.sqlite" in out["reason"]
    monkeypatch.setenv("AIRBYTE_CORE_DEST", "ftp://somewhere")
    assert "unknown AIRBYTE_CORE_DEST" in ac.ingest()["reason"]


def test_credentials_never_printed(core, monkeypatch, capsys):
    ac, _, _ = core
    monkeypatch.setenv("AIRBYTE_CORE_DEST", "postgresql://user:s3cret@db.local/airbyte")
    out = ac.ingest()  # psycopg missing or host unreachable -> fallback
    assert out["source"] == "seed_fallback"
    assert "s3cret" not in json.dumps(out) and "s3cret" not in capsys.readouterr().out


def test_siva_demo_file_through_a_local_json_destination(core, monkeypatch):
    """seed/airbyte_demo/receipts.jsonl (File source) as Airbyte's Local JSON destination writes it,
    under a ~ path exactly as seed/airbyte_demo/README.md documents."""
    from pathlib import Path
    ac, calls, tmp = core
    home = tmp / "home"
    dest = home / "airbyte-local" / "clawback"
    dest.mkdir(parents=True)
    src = [json.loads(l) for l in Path("seed/airbyte_demo/receipts.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    (dest / "_airbyte_raw_messages.jsonl").write_text(
        "\n".join(json.dumps({"_airbyte_ab_id": f"ab{i}", "_airbyte_emitted_at": 1, "_airbyte_data": r}) for i, r in enumerate(src)),
        encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))   # Windows expanduser
    monkeypatch.setenv("AIRBYTE_CORE_DEST", "jsonl:~/airbyte-local/clawback")
    out = ac.ingest()
    assert (out["source"], out["read"], out["inserted"]) == ("airbyte_core", len(src), len(src)) and len(src) == 6
    rows = raw()
    assert rows[0]["gmail_msg_id"] == src[0]["gmail_msg_id"] and rows[0]["from_addr"] == src[0]["from"]
    assert rows[0]["subject"] == src[0]["subject"] and rows[0]["body"] == src[0]["body"]
    assert rows[0]["received_at"] == src[0]["received_at"] and calls == [1]
