"""Senso live-edit stunt: Mock Store price adjustments 14 -> 7 days, as a NEW VERSION in Senso.

    python -m dev.senso_live_edit edit     # 14 -> 7 in Senso, time until search returns it
    python -m dev.senso_live_edit revert   # back to the text in seed/policies/mockstore.md

Needs SENSO_API_KEY in .env and the Senso CLI (npm install -g @senso-ai/cli; Node >= 22.12):
the docs give no HTTP call for updating, only `senso kb update-raw <kb_node_id>`.
The store_policies row must change too (adjust_window_days 14 -> 7) for the gate's R7; that row
lives in A's database, so on stage change it there (or see test_live_edit_14_to_7 for the flow).
Then re-gate the STORED earbuds claim: it stops on R7, quoting the new sentence and revision.
Always run `revert` afterwards, or every later earbuds claim stops on R7.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

OLD, NEW = "14 days from purchase", "7 days from purchase"


def _mockstore_doc() -> tuple[str, str, str]:
    from dev.senso_ingest import sections
    ids = json.loads(Path(os.environ.get("SENSO_IDS", "seed/policies/senso_ids.json")).read_text(encoding="utf-8"))
    cid, meta = next((c, m) for c, m in ids.items() if m["store"] == "mockstore")
    [(_, text)] = sections(Path("seed/policies/mockstore.md"))
    return cid, meta, text


def _senso_cmd() -> list[str]:
    """`node <cli.js>` rather than the senso.cmd wrapper: on Windows cmd.exe mangles the JSON
    argument ("The filename, directory name, or volume label syntax is incorrect")."""
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if npm:
        root = subprocess.run([npm, "root", "-g"], capture_output=True, text=True, shell=False).stdout.strip()
        js = Path(root) / "@senso-ai" / "cli" / "dist" / "cli.js"
        if js.exists():
            return [shutil.which("node") or "node", str(js)]
    exe = shutil.which("senso")
    if not exe:
        raise SystemExit("Senso CLI not found: npm install -g @senso-ai/cli")
    return [exe]


def _cli(*args: str) -> dict:
    r = subprocess.run([*_senso_cmd(), *args, "--output", "json"], capture_output=True, text=True,
                       env={**os.environ, "SENSO_NO_UPDATE_CHECK": "1"}, timeout=120)
    if r.returncode != 0:
        raise SystemExit(f"senso {args[0]} {args[1]} failed: {(r.stderr or r.stdout)[:400]}")
    try:
        return json.loads(r.stdout) if r.stdout.strip() else {}
    except json.JSONDecodeError:
        return {"raw": r.stdout[:400]}


def main(mode: str) -> int:
    load_dotenv()
    if not os.environ.get("SENSO_API_KEY"):
        print("SENSO_API_KEY is not set.")
        return 2
    from backend.agent.senso_client import SensoPolicySource
    cid, meta, text = _mockstore_doc()
    if OLD not in text:
        raise SystemExit(f"seed/policies/mockstore.md no longer contains {OLD!r}")
    want = NEW if mode == "edit" else OLD
    body = text.replace(OLD, NEW) if mode == "edit" else text
    t0 = time.time()
    out = _cli("kb", "update-raw", meta["kb_node_id"], "--data", json.dumps({"title": meta["title"], "text": body}))
    print(f"update-raw {meta['kb_node_id']} ({mode}) in {time.time() - t0:.1f}s: "
          f"{json.dumps(out)[:200]}")
    src = SensoPolicySource(os.environ["SENSO_API_KEY"])
    q = "Does Mock Store adjust or match a lower price after purchase for electronics items, and for how long?"
    t1, last = time.time(), None
    while time.time() - t1 < 300:
        r = src.ask("mockstore", q)
        last = r
        if r["cited_text"] and want in r["cited_text"]:
            print(f"search returns the {mode}ed sentence after {time.time() - t0:.1f}s total: "
                  f"\"{r['cited_text']}\" (rev {r['revision']})")
            return 0
        time.sleep(3)
    print(f"NOT searchable after 5 min; last quote: {last and last['cited_text']!r} (rev {last and last['revision']})")
    return 1


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("edit", "revert"):
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
