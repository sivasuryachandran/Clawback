# Receipts for the Airbyte demo sync (dummy data)

`receipts.jsonl` is the six seed receipts (`seed/receipts/*.eml`, all labelled dummy purchases) flattened to one JSON object per line:
`gmail_msg_id, from, subject, received_at, body`.

Airbyte's **File** source reads it from its raw GitHub URL (the repo is public; the data is made up):
`https://raw.githubusercontent.com/sivasuryachandran/Clawback/main/seed/airbyte_demo/receipts.jsonl`
(format JSONL, dataset/stream name `messages`) and a **Local JSON** destination writes it under the host folder mounted at `/local`
(`~/airbyte-local/...`). Then `AIRBYTE_CORE_DEST=jsonl:~/airbyte-local/<folder>` and `python -m backend.ingest.airbyte_core` read it into `raw_emails`.
This is a real Airbyte sync with a file source; the Gmail link is simulated.
