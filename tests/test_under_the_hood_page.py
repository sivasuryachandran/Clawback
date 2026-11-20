"""The judges' page: plain static files served by the API itself at /under-the-hood/ (Siva's UI is static, so no Next.js)."""
import re
from pathlib import Path

import pytest

from tests.c_fixtures import client  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
UTH = ROOT / "frontend" / "app" / "under-the-hood"
JS = (UTH / "under-the-hood.js").read_text()


def _code(src: str) -> str:
    """The script without its comments, so a comment saying "never innerHTML" cannot trip a check for innerHTML."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(^|\s)//[^\n]*", "", src)


CODE = _code(JS)


def test_the_page_is_served_by_the_api(client):
    r = client.get("/under-the-hood/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "<title>Under the hood" in r.text and 'src="under-the-hood.js"' in r.text and 'href="under-the-hood.css"' in r.text
    assert client.get("/under-the-hood/under-the-hood.js").status_code == 200
    assert client.get("/under-the-hood/under-the-hood.css").status_code == 200
    assert client.get("/under-the-hood", follow_redirects=False).status_code in (301, 302, 307, 308)   # no trailing slash still works


def test_the_page_cannot_be_used_to_read_other_files(client):
    for p in ["/under-the-hood/../main.py", "/under-the-hood/%2e%2e/main.py", "/under-the-hood/..%2fmain.py", "/under-the-hood/../../.env"]:
        assert client.get(p).status_code == 404, p


def test_the_page_is_reachable_through_a_tunnel_but_its_data_is_not(client):
    tunnel = {"x-forwarded-for": "203.0.113.9", "host": "abc.trycloudflare.com"}
    assert client.get("/under-the-hood/", headers=tunnel).status_code == 200            # just static files
    r = client.get("/debug/overview", headers=tunnel)
    assert r.status_code == 403 and "demo laptop" in r.json()["detail"]                  # the data stays laptop-only


def test_nothing_from_the_database_or_an_email_is_ever_written_as_html():
    """Subjects, thread ids, support messages and policy text are untrusted: only text nodes."""
    for sink in ["innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "setAttribute(\"on"]:
        assert sink not in CODE, sink
    assert "createTextNode" in CODE


def test_the_page_only_ever_reads():
    assert not re.search(r"\bmethod\s*:", CODE), "a fetch with a method means the page writes"
    assert not re.search(r"\b(POST|PUT|PATCH|DELETE)\b", CODE)
    calls = re.findall(r"fetch\(([^,]+)", CODE)
    assert calls and all("/debug/" in c for c in calls), calls                           # it only talks to the read-only debug endpoints


def test_a_refusal_is_explained_and_the_api_is_not_hardcoded():
    assert "r.status === 403" in JS and "demo laptop" in JS
    assert "location.origin" in JS and "localhost:8001" not in JS and "localhost:8000" not in JS   # same origin, any port


def test_polled_refreshes_respect_a_focused_field_and_the_matcher_is_not_polled():
    assert "render(!!force)" in JS                                                        # polled refresh = not forced
    assert re.search(r"INPUT\|SELECT", JS) and "contains(a)" in JS
    poll = JS[JS.index("setInterval"):]
    assert "load(false)" in poll and "loadMatch" not in poll                              # the matcher runs on demand only


def test_it_shows_every_panel_of_the_table():
    for title in ["Live gate trace", "Your Pact", "Approvals and run-once", "Policy", "Matching", "Live queries", "Ingest", "Safety", "Analytics", "Tests"]:
        assert f'panel("{title}"' in JS, title
    assert "SAMPLE DATA" in JS or "sample_note" in JS                                     # sample mode is labelled


def test_there_is_no_second_orb_or_next_js_shell_to_confuse_the_team():
    """Siva's static frontend already has the sphere and voice (frontend/orb.js, voice.js); a competing orb would only drift."""
    assert not (ROOT / "frontend" / "components" / "orb").exists()
    for stray in ["package.json", "next.config.mjs", "tsconfig.json"]:
        assert not (ROOT / "frontend" / stray).exists(), stray
    assert not list(UTH.glob("*.tsx")) and not list(UTH.glob("*.ts"))


def test_the_page_sets_no_cookies_and_stores_nothing():
    for api in ["localStorage", "sessionStorage", "document.cookie", "indexedDB"]:
        assert api not in CODE, api
