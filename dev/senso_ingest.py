"""Load seed/policies/*.md into Senso and time the indexing. Needs SENSO_API_KEY.

    python -m dev.senso_ingest

One Senso raw-text document per `Source:` section (so every Senso content_id maps to exactly
one official page URL). Writes seed/policies/senso_ids.json:
    {content_id: {store, source_url, kb_node_id, title, indexed_s}}
Markdown FILE upload is refused by Senso (.md rejected), hence POST /org/kb/raw with text.

Live edit (stretch): the docs give a CLI command for a new version of a raw document:
    senso kb update-raw <id> --data '{"title": "...", "text": "..."}' --output json
(no HTTP path for it was found in the docs). Then re-run search; the result's version_id changes.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv


def sections(path: Path) -> list[tuple[str, str]]:
    """[(source_url, text)] for one policy file; notes ('>' lines, comments, headings) dropped."""
    raw = re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8"), flags=re.S)
    out, url, buf = [], None, []
    for ln in raw.splitlines():
        s = ln.strip()
        if s.lower().startswith("source:"):
            if url and buf:
                out.append((url, "\n".join(buf)))
            url, buf = s.split(":", 1)[1].strip(), []
            continue
        if not s or s.startswith(("#", ">")) or s.lower().startswith("text exactly as"):
            continue
        buf.append(s)
    if url and buf:
        out.append((url, "\n".join(buf)))
    return [(u, t) for u, t in out if "PASTE OFFICIAL" not in u.upper()]


def main() -> int:
    load_dotenv()
    key = os.environ.get("SENSO_API_KEY")
    if not key:
        print("SENSO_API_KEY is not set (put it in .env). Nothing uploaded.")
        return 2
    from backend.agent.senso_client import SensoPolicySource
    src = SensoPolicySource(key)
    ids_path = src.ids_path
    ids = json.loads(ids_path.read_text(encoding="utf-8")) if ids_path.exists() else {}
    pending = []
    for store in ("mockstore", "target", "bestbuy"):
        for url, text in sections(Path("seed/policies") / f"{store}.md"):
            title = f"{store} policy | {url}"
            if any(v["title"] == title for v in ids.values()):
                print(f"skip   {title} (already in {ids_path})")
                continue
            t0 = time.time()
            try:
                r = src.create_raw(title, text)
            except FileExistsError as e:
                print(f"409    {title}: {e}. Delete it in Senso or edit the text, then re-run.")
                continue
            print(f"upload {title} -> content {r.get('id')} node {r.get('kb_node_id')} "
                  f"({r.get('processing_status')}, {time.time() - t0:.1f}s)")
            ids[r["id"]] = {"store": store, "source_url": url, "kb_node_id": r["kb_node_id"], "title": title}
            pending.append((r["id"], r["kb_node_id"], time.time()))
    # Docs: poll every 5 s until content.processing_status is complete.
    deadline = time.time() + 600
    while pending and time.time() < deadline:
        for item in list(pending):
            cid, node, t0 = item
            st = src.status(node)
            if st in ("complete", "failed"):
                ids[cid]["indexed_s"] = round(time.time() - t0, 1)
                ids[cid]["status"] = st
                print(f"{st:<8} {ids[cid]['title']} after {ids[cid]['indexed_s']} s")
                pending.remove(item)
        if pending:
            time.sleep(5)
    for cid, node, _ in pending:
        print(f"STILL PROCESSING after 10 min: {ids[cid]['title']}")
    ids_path.write_text(json.dumps(ids, indent=2), encoding="utf-8")
    print(f"\nwrote {ids_path} ({len(ids)} documents). Next: python -m dev.senso_smoke")
    return 0 if not pending else 1


if __name__ == "__main__":
    sys.exit(main())
