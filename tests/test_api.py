from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from lxml import etree

from lastmile import cap
from lastmile.api import create_app
from lastmile.config import Settings
from lastmile.store import Store


def client(**kw) -> TestClient:
    return TestClient(create_app(Settings(**kw), store=Store()))


@pytest.fixture
def c() -> TestClient:
    return client(rate_limit_per_min=1000)


@pytest.fixture
def clean_text(corpus):
    return corpus["lk-clean-multilevel"]["text"]


def post_run(c, text, **kw):
    return c.post("/api/runs", json={"pack_id": "lk-landslide", "source_text": text, **kw})


def test_health_packs_samples(c):
    assert c.get("/api/health").json()["ok"]
    packs = c.get("/api/packs").json()
    assert {p["id"] for p in packs} == {"lk-landslide", "np-flood"}
    samples = c.get("/api/samples").json()
    assert len(samples) >= 8 and all(s["text"] for s in samples)
    assert c.get("/api/inject-options").json()["enabled"] is True


def test_full_flow_draft_edit_approve_download(c, clean_text):
    r = post_run(c, clean_text)
    assert r.status_code == 200
    run = r.json()
    rid = run["id"]
    assert run["status"] == "ready" and run["required_approvals"] == 1 and run["effective_cap_status"] == "Exercise"
    assert run["sms_info"]["si"]["encoding"] == "UCS-2" and run["places"][0]["canonical"] == "LK-BD-PAS"

    assert c.get(f"/api/runs/{rid}/cap.xml").status_code == 409            # no final CAP before approval
    assert c.get(f"/api/runs/{rid}/outbox").status_code == 409
    prev = c.get(f"/api/runs/{rid}/cap-preview.xml")
    assert prev.status_code == 200 and b"<status>Draft</status>" in prev.content

    svg = c.get(f"/api/runs/{rid}/map.svg")
    assert svg.headers["content-type"].startswith("image/svg+xml")
    assert "default-src 'none'" in svg.headers["content-security-policy"]
    etree.fromstring(svg.content)
    gj = c.get(f"/api/runs/{rid}/geojson").json()
    assert len(gj["features"]) == 4

    # break a draft through the API, see it blocked, fix it
    en = run["drafts"]["en"]
    bad = c.put(f"/api/runs/{rid}/drafts/en", json={"reviewer": "A. Perera", "fields": {"description": en["description"].replace("150", "")}})
    assert bad.json()["status"] == "blocked"
    assert c.post(f"/api/runs/{rid}/review", json={"reviewer": "A. Perera", "decision": "approve"}).status_code == 409
    fixed = c.put(f"/api/runs/{rid}/drafts/en", json={"reviewer": "A. Perera", "fields": {"description": en["description"]}})
    assert fixed.json()["status"] == "ready"

    own = c.post(f"/api/runs/{rid}/review", json={"reviewer": "Anura Perera", "decision": "approve"})
    assert own.status_code == 409 and own.json()["error"] == "editor_cannot_self_approve"   # "A. Perera" edited
    done = c.post(f"/api/runs/{rid}/review", json={"reviewer": "B. Silva", "decision": "approve", "note": "ok"})
    assert done.status_code == 200 and done.json()["status"] == "published"
    final = c.get(f"/api/runs/{rid}/cap.xml")
    assert final.status_code == 200 and cap.validate(final.content) == []
    assert c.get(f"/api/runs/{rid}/outbox").json()["sms"]["en"]["segments"] == 1
    assert len(c.get("/api/active", params={"at": "2025-11-27T20:00:00+05:30"}).json()) == 1
    assert c.get("/api/active").json() == []                                  # a 2025 replay has expired by now
    assert c.get("/api/active", params={"at": "yesterday"}).status_code == 422
    assert c.get("/api/runs").json()[0]["id"] == rid


def test_validation_and_errors(c, clean_text):
    assert c.post("/api/runs", json={"pack_id": "lk-landslide", "source_text": "short"}).status_code == 422
    assert c.post("/api/runs", json={"pack_id": "../x", "source_text": clean_text}).status_code == 422
    assert post_run(c, clean_text, mode="yolo").status_code == 422
    r = c.post("/api/runs", json={"pack_id": "nope", "source_text": clean_text})
    assert r.status_code == 404
    assert c.get("/api/runs/run_missing").status_code == 404
    big = client(max_source_chars=100)
    assert big.post("/api/runs", json={"pack_id": "lk-landslide", "source_text": "x" * 101}).status_code == 413
    r = c.post("/api/runs/run_missing/review", json={"reviewer": "A. Perera", "decision": "approve"})
    assert r.status_code == 404
    run = post_run(c, clean_text).json()
    assert c.post(f"/api/runs/{run['id']}/review", json={"reviewer": "A", "decision": "approve"}).status_code == 422
    assert c.put(f"/api/runs/{run['id']}/drafts/en", json={"reviewer": "A. Perera", "fields": {"nope": "x"}}).status_code == 422


def test_inject_demo_repairs_and_is_fake_only(c, clean_text):
    run = post_run(c, clean_text, inject="drop_number").json()
    assert run["status"] == "ready" and run["attempts"]["si"] == 2
    assert post_run(c, clean_text, inject="nonsense").status_code == 422
    real = client(llm="gemini")
    assert real.get("/api/inject-options").json()["enabled"] is False
    assert real.post("/api/runs", json={"pack_id": "lk-landslide", "source_text": clean_text, "inject": "drop_number"}).status_code == 422


def test_api_key_protects_everything_but_health(clean_text):
    c = client(api_key="s3cret", rate_limit_per_min=1000)
    assert c.get("/api/health").status_code == 200
    assert post_run(c, clean_text).status_code == 401
    assert c.get("/api/runs").status_code == 401
    assert c.get("/api/active").status_code == 401
    assert post_run(c, clean_text, ).status_code == 401
    ok = c.post("/api/runs", headers={"X-API-Key": "s3cret"}, json={"pack_id": "lk-landslide", "source_text": clean_text})
    assert ok.status_code == 200
    assert c.get(f"/api/runs/{ok.json()['id']}", headers={"X-API-Key": "wrong"}).status_code == 401


def test_rate_limit(clean_text):
    c = client(rate_limit_per_min=3)
    codes = [post_run(c, clean_text).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]


def test_security_headers_and_static(c):
    r = c.get("/")
    assert r.status_code == 200 and "LastMile" in r.text
    assert "teams.js" in r.text
    assert "lastmile-ui.js" in r.text
    assert c.get("/teams", follow_redirects=False).headers["location"] == "/"
    assert c.get("/console", follow_redirects=False).headers["location"] == "/"
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert c.get("/static/teams.js").status_code == 200
    for retired in ("app.js", "index.html", "workspace.html", "workspace.js", "conversation.js"):
        assert c.get("/static/" + retired).status_code == 404
    assert c.get("/api/health").headers["cache-control"] == "no-store"
    assert c.get("/static/../pyproject.toml").status_code in (404, 400)


def test_pages_have_no_inline_script_or_innerhtml_of_data():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "web"
    for page in sorted(root.glob("*.html")):
        assert "<script>" not in page.read_text(encoding="utf-8"), page.name
    for script in sorted(root.glob("*.js")):
        js = script.read_text(encoding="utf-8")
        assert "innerHTML" not in js and "outerHTML" not in js and "document.write" not in js, script.name
        assert "eval(" not in js, script.name


def test_xss_payload_in_source_is_inert(c, corpus):
    text = corpus["lk-clean-multilevel"]["text"].replace("Be aware", "<img src=x onerror=alert(1)>")
    run = post_run(c, text).json()
    assert run["status"] == "ready"
    # nothing from the source ends up in generated CAP unescaped
    xml = c.get(f"/api/runs/{run['id']}/cap-preview.xml").content
    assert b"onerror" not in xml
    assert cap.validate(xml) == []


def test_scorecard_reports_the_safety_net(c):
    s = c.get("/api/scorecard").json()
    m, t = s["drafting_model"], s["drafting_templates"]
    assert m["pipeline_unsafe_in_queue"] == 0 and m["baseline_unsafe_in_queue"] > 0 and m["pipeline_false_blocks"] == 0
    assert m["pipeline_matches_expectations"] == m["cases"] and t["pipeline_unsafe_in_queue"] == 0
    assert "not language quality" in s["model"]
    assert c.get("/api/scorecard").json() == s                                     # computed once


def test_pages_have_no_inline_event_handlers():
    """The CSP (script-src 'self') blocks inline handlers such as onsubmit="…": they would silently not run.
    Handlers belong in the page scripts."""
    import re
    from pathlib import Path

    web = Path(__file__).resolve().parent.parent / "web"
    for page in sorted(web.glob("*.html")):
        found = re.findall(r"<[^>]*\son[a-z]+\s*=", page.read_text(encoding="utf-8"), flags=re.I)
        assert not found, f"{page.name}: inline event handler(s) {found}"
