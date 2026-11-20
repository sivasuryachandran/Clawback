"""Upload the three policy docs (Senso only) and ask five questions. Prints answers + sources.

    python -m dev.senso_smoke
"""
import time

from backend.agent.senso_client import SensoPolicySource, get_policy_source

QUESTIONS = [
    ("mockstore", "Does Mock Store adjust prices after purchase, and for how long?"),
    ("mockstore", "Is clearance final sale at Mock Store?"),
    ("mockstore", "How long is the Mock Store return window and when does it start?"),
    ("bestbuy", "How long is Best Buy's return window and when does it start?"),
    ("target", "Does Target adjust prices after purchase, and for how long?"),
]


def main():
    src = get_policy_source()
    print(f"policy source: {type(src).__name__}")
    if isinstance(src, SensoPolicySource):
        for store in ("mockstore", "target", "bestbuy"):
            t = time.time()
            doc_id = src.ingest(store, f"seed/policies/{store}.md")
            print(f"ingested {store}: {doc_id} ({time.time() - t:.1f}s)")
    else:
        print("(SENSO_API_KEY unset: no upload; quoting seed/policies/*.md locally)")
    for store, q in QUESTIONS:
        t = time.time()
        r = src.ask(store, q)
        print(f"\nQ [{store}] {q}\n  answer : {r['answer']}\n  cited  : {r['cited_text']}"
              f"\n  source : {r['source_url']} ({r['source_id']}, rev {r['revision']})"
              f"  [{(time.time() - t) * 1000:.0f} ms]")


if __name__ == "__main__":
    main()
