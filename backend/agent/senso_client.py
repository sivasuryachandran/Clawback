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


def _stems(text: str, drop: set[str]) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if w in _STOP or w in drop:
            continue
        out.add(w[:5] if len(w) >= 5 else w)
    return out


class LocalPolicySource:
    """Best-matching sentence from seed/policies/<store>.md. Quotes text that exists, or nothing."""

    def __init__(self, folder: str | None = None):
        self.folder = Path(folder or os.environ.get("POLICY_DIR", "seed/policies"))

    def _load(self, store: str):
        path = self.folder / f"{store}.md"
        if not path.exists():
            return None, None, []
        raw = path.read_text(encoding="utf-8")
        lines = raw.splitlines()
        url = None
        if lines and lines[0].lower().startswith("source:"):
            url = lines[0].split(":", 1)[1].strip()
            if PLACEHOLDER in url.upper():
                url = None
        body = re.sub(r"<!--.*?-->", "", "\n".join(lines[1:]), flags=re.S)
        sentences = []
        for ln in body.splitlines():
            ln = ln.strip().lstrip("-*").strip()
            if not ln or ln.startswith("#") or PLACEHOLDER in ln.upper():
                continue
            if ln.lower().startswith("text exactly as"):
                continue  # our own provenance note, not policy text
            sentences += [s.strip() for s in re.split(r"(?<=[.!?])\s+", ln) if s.strip()]
        revision = "sha256:" + hashlib.sha256(raw.encode()).hexdigest()[:12]
        return url, revision, sentences

    def ask(self, store: str, question: str) -> dict:
        url, revision, sentences = self._load(store)
        if not sentences or not url:
            return _no_source(f"No policy text loaded for {store}.")
        drop = {w.lower() for w in STORE_NAMES.get(store, store).split()}
        q = _stems(question, drop)
        scored = [(len(q & _stems(s, drop)), -i, s) for i, s in enumerate(sentences)]
        best = max(scored)
        if best[0] == 0:
            return _no_source("No matching sentence in the policy.")
        return {"answer": best[2], "cited_text": best[2], "source_url": url,
                "source_id": f"local:{store}", "revision": revision}


class SensoPolicySource:
    """Real Senso. NOT WIRED TONIGHT (rehearsal ran fully local, no Senso docs fetched).

    To finish at the event, check Senso's current docs for:
      1. how to ingest a markdown/text document (CLI `@senso-ai/cli` command or HTTP endpoint),
         and whether it returns a document id we can store as store_policies.senso_doc_id;
      2. the search/answer call: request shape, and whether the response includes the
         source chunk text, source URL/title, a source/content id and a version or revision;
      3. auth header name for SENSO_API_KEY, base URL, rate limits, indexing delay.
    Map the response to {answer, cited_text, source_url, source_id, revision}. If the response
    has no source chunk, return _no_source(...) so the gate stops on R6. Never synthesize a quote.
    """

    def __init__(self, api_key: str, base_url: str | None = None):
        self.api_key = api_key
        self.base_url = base_url or os.environ.get("SENSO_BASE_URL")

    def ingest(self, store: str, path: str) -> str:
        raise NotImplementedError("Senso ingest not wired: check Senso docs (see class docstring)")

    def ask(self, store: str, question: str) -> dict:
        raise NotImplementedError("Senso search not wired: check Senso docs (see class docstring)")


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
