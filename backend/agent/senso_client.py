"""Policy source behind one interface:

    PolicySource.ask(store, question) -> {answer, cited_text, source_url, source_id, revision}

No citation found -> cited_text/source_url/source_id are None (the gate then stops on R6).
Nothing here ever writes policy text; it only quotes documents in seed/policies/.
"""
import hashlib
import os
import re
from pathlib import Path
from typing import Protocol

STORE_NAMES = {"mockstore": "Mock Store", "target": "Target", "bestbuy": "Best Buy"}
PLACEHOLDER = "PASTE OFFICIAL"
_STOP = {"does", "do", "is", "the", "a", "an", "and", "or", "for", "how", "what", "when",
         "it", "its", "at", "to", "of", "in", "on", "after", "this", "that", "with", "be",
         "are", "can", "i", "my", "item", "items", "store", "s"}


class PolicySource(Protocol):
    def ask(self, store: str, question: str) -> dict: ...


def _no_source(answer: str) -> dict:
    return {"answer": answer, "cited_text": None, "source_url": None, "source_id": None,
            "revision": None}


# A few synonyms so policy wording ("the time period begins the day you receive") matches
# our questions ("how long is the return window, when does it start"). Stand-in only:
# real Senso does semantic search.
_SYN = {"begin": "start", "begins": "start", "period": "window", "periods": "window",
        "receive": "delivery", "delivered": "delivery", "days": "long", "day": "long"}


def _stems(text: str, drop: set[str]) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if w in _STOP or w in drop:
            continue
        w = _SYN.get(w, w)
        out.add(w[:5] if len(w) >= 5 else w)
    return out


class LocalPolicySource:
    """Best-matching sentence from seed/policies/<store>.md. Quotes text that exists, or nothing."""

    def __init__(self, folder: str | None = None):
        self.folder = Path(folder or os.environ.get("POLICY_DIR", "seed/policies"))

    def _load(self, store: str):
        """-> (revision, [(sentence, source_url)]). A `Source: <url>` line applies to the
        sentences after it, so one file can quote several official pages. Lines starting
        with `>` are our notes, never quoted."""
        path = self.folder / f"{store}.md"
        if not path.exists():
            return None, []
        raw = path.read_text(encoding="utf-8")
        body = re.sub(r"<!--.*?-->", "", raw, flags=re.S)
        url, out = None, []
        for ln in body.splitlines():
            ln = ln.strip()
            if ln.lower().startswith("source:"):
                url = ln.split(":", 1)[1].strip()
                url = None if PLACEHOLDER in url.upper() else url
                continue
            ln = ln.lstrip("-*").strip()
            if not ln or ln.startswith(("#", ">")) or PLACEHOLDER in ln.upper():
                continue
            if ln.lower().startswith("text exactly as"):
                continue  # our own provenance note, not policy text
            if url is None:
                continue  # never quote text that has no source
            out += [(s.strip(), url) for s in re.split(r"(?<=[.!?])\s+", ln) if s.strip()]
        revision = "sha256:" + hashlib.sha256(raw.encode()).hexdigest()[:12]
        return revision, out

    def ask(self, store: str, question: str) -> dict:
        revision, sentences = self._load(store)
        if not sentences:
            return _no_source(f"No policy text loaded for {store}.")
        drop = {w.lower() for w in STORE_NAMES.get(store, store).split()}
        q = _stems(question, drop)
        scored = [(len(q & _stems(s, drop)), -i, s, u) for i, (s, u) in enumerate(sentences)]
        best = max(scored)
        if best[0] == 0:
            return _no_source("No matching sentence in the policy.")
        return {"answer": best[2], "cited_text": best[2], "source_url": best[3],
                "source_id": f"local:{store}", "revision": revision}


class SensoPolicySource:
    """Real Senso, built from docs.senso.ai (read 2026-11-20; see REHEARSAL_NOTES.md).
    NOT RUN against the live API yet (no key on rehearsal night).

    Documented calls used:
      POST {base}/org/kb/raw            {title, text} -> 202 {id, kb_node_id, processing_status}
                                         409 if the same text already exists
      GET  {base}/org/kb/nodes/{kb_node_id} -> content.processing_status: pending|processing|complete|failed
      POST {base}/org/search/context    {query} -> results[]: content_id, version_id, chunk_text, score, ...
    Auth header X-API-Key; base https://apiv2.senso.ai/api/v1 (env SENSO_BASE_URL overrides).

    Search is org-wide: the HTTP docs show no way to scope it to one document (the CLI has
    --content-ids + --require-scoped-ids). So we keep our own map content_id -> (store, source
    URL) in seed/policies/senso_ids.json (written by dev/senso_ingest.py) and ONLY accept a
    result whose content_id belongs to the asked store. Anything else -> no citation -> R6.
    The quote is the best sentence INSIDE Senso's chunk_text (exact text, never synthesized);
    revision = Senso's version_id.
    """

    DEFAULT_BASE = "https://apiv2.senso.ai/api/v1"

    def __init__(self, api_key: str, base_url: str | None = None, http=None,
                 ids_path: str | None = None):
        import httpx
        self.base_url = (base_url or os.environ.get("SENSO_BASE_URL") or self.DEFAULT_BASE).rstrip("/")
        self.http = http or httpx.Client(timeout=float(os.environ.get("POLICY_TIMEOUT_S", "10")))
        self.headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        self.ids_path = Path(ids_path or os.environ.get("SENSO_IDS", "seed/policies/senso_ids.json"))

    # --- ingest side (dev/senso_ingest.py) ---
    def create_raw(self, title: str, text: str) -> dict:
        r = self.http.post(f"{self.base_url}/org/kb/raw", headers=self.headers,
                           json={"title": title, "text": text})
        if r.status_code == 409:
            raise FileExistsError("Senso 409: this exact text is already in the knowledge base")
        r.raise_for_status()
        return r.json()

    def status(self, kb_node_id: str) -> str:
        r = self.http.get(f"{self.base_url}/org/kb/nodes/{kb_node_id}", headers=self.headers)
        r.raise_for_status()
        return ((r.json().get("content") or {}).get("processing_status")) or "unknown"

    # --- query side ---
    def _ids(self) -> dict:
        import json
        return json.loads(self.ids_path.read_text(encoding="utf-8")) if self.ids_path.exists() else {}

    def ask(self, store: str, question: str) -> dict:
        ids = {cid: v for cid, v in self._ids().items() if v.get("store") == store}
        if not ids:
            return _no_source(f"No Senso document registered for {store} (run dev/senso_ingest.py).")
        r = self.http.post(f"{self.base_url}/org/search/context", headers=self.headers,
                           json={"query": question})
        r.raise_for_status()
        results = [x for x in (r.json().get("results") or [])
                   if x.get("content_id") in ids and (x.get("chunk_text") or "").strip()]
        if not results:
            return _no_source(f"Senso returned no passage from {store}'s policy.")
        best = max(results, key=lambda x: x.get("score") or 0)
        drop = {w.lower() for w in STORE_NAMES.get(store, store).split()}
        q = _stems(question, drop)
        sentences = [s.strip(" -*") for ln in best["chunk_text"].splitlines()
                     for s in re.split(r"(?<=[.!?])\s+", ln) if s.strip(" -*")]
        quote = max(sentences, key=lambda s: len(q & _stems(s, drop))) if sentences else best["chunk_text"]
        meta = ids[best["content_id"]]
        return {"answer": quote, "cited_text": quote, "source_url": meta.get("source_url"),
                "source_id": f"senso:{best['content_id']}",
                "revision": best.get("version_id")}


_override: PolicySource | None = None


def set_policy_source(src: PolicySource | None) -> None:
    """Tests use this to inject a fake source."""
    global _override
    _override = src


def get_policy_source() -> PolicySource:
    if _override is not None:
        return _override
    key = os.environ.get("SENSO_API_KEY")
    if key:
        return SensoPolicySource(key)
    return LocalPolicySource()
