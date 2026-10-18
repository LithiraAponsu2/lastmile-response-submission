from __future__ import annotations

import ast
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lastmile import publish
from lastmile.api import create_app
from lastmile.config import Settings
from lastmile.llm import FakeLLM
from lastmile.pipeline import Pipeline
from lastmile.plugins import HttpPlugin, IssuerPlugin, PluginContext, from_spec, run_plugins
from lastmile.schemas import Issue
from lastmile.store import Store


class Server:
    def __init__(self, handler):
        self.requests = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                outer.requests.append(body)
                status, payload = handler(body)
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/inspect"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


@pytest.fixture
def server():
    made = []

    def make(handler):
        s = Server(handler)
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()


def pipe(registry, plugins, store=None):
    return Pipeline(FakeLLM(), registry, store or Store(), plugins=plugins)


def codes(run):
    return {i.code for i in run.issues}


def test_issuer_plugin(registry, corpus):
    ok = pipe(registry, [IssuerPlugin()]).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    assert "issuer_not_in_source" not in codes(ok)             # "NBRO" appears in the bulletin
    text = corpus["lk-clean-multilevel"]["text"].replace("NBRO", "SOMEONE")
    warn = pipe(registry, [IssuerPlugin()]).run(text, "lk-landslide")
    issue = next(i for i in warn.issues if i.code == "issuer_not_in_source")
    assert issue.source == "plugin" and issue.severity == "warn" and issue.stage == "post_facts"
    assert warn.status == "ready"                              # a warning, not a block
    assert any(t.actor == "plugin" for t in warn.trace)


def test_http_plugin_blocks_and_gets_full_context(registry, corpus, server):
    s = server(lambda body: (200, {"issues": [{"code": "rumour_overlap", "severity": "block",
                                               "message": "Matches a known false rumour", "lang": "si"}]}))
    run = pipe(registry, [HttpPlugin(s.url, name="rumours")]).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    assert run.status == "blocked"
    issue = next(i for i in run.issues if i.code == "rumour_overlap")
    assert issue.source == "plugin" and issue.lang == "si" and issue.message.startswith("[rumours]")
    req = s.requests[0]
    assert req["stage"] == "post_draft" and req["pack_id"] == "lk-landslide"
    assert set(req["drafts"]) == {"en", "si", "ta"} and req["derived"]["max_level"] == 3 and req["facts"]["places"]


def test_post_facts_plugin_runs_before_drafting_and_can_stop_it(registry, corpus, server):
    s = server(lambda body: (200, {"issues": [{"code": "region_rule", "severity": "block", "message": "no"}]}))
    llm = FakeLLM()
    run = Pipeline(llm, registry, Store(), plugins=[HttpPlugin(s.url, stages=("post_facts",))]).run(
        corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    assert run.status == "blocked" and run.drafts == {} and llm.calls[("localize", "en")] == 0


def test_plugin_down_is_a_warning_unless_critical(registry, corpus):
    dead = "http://127.0.0.1:9/inspect"                          # nothing listens on port 9
    soft = pipe(registry, [HttpPlugin(dead, timeout=0.5)]).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    assert soft.status == "ready"
    assert next(i for i in soft.issues if i.code == "plugin_unavailable").severity == "warn"
    hard = pipe(registry, [HttpPlugin(dead, timeout=0.5, critical=True)]).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    assert hard.status == "blocked"


def test_plugin_garbage_response_is_contained(registry, corpus, server):
    s = server(lambda body: (200, {"issues": "not a list"}))
    run = pipe(registry, [HttpPlugin(s.url)]).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    assert run.status == "ready" and "plugin_unavailable" in codes(run)


def test_plugin_cannot_inject_other_sources_or_unknown_severity(registry, corpus, server):
    s = server(lambda body: (200, {"issues": [{"code": "x" * 200, "severity": "catastrophic", "message": "m" * 900,
                                               "source": "code"}]}))
    run = pipe(registry, [HttpPlugin(s.url)]).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    i = next(i for i in run.issues if i.source == "plugin" and i.code.startswith("xxx"))
    assert len(i.code) == 60 and i.severity == "warn" and len(i.message) <= 540


def test_editing_clears_a_post_draft_plugin_block_once_fixed(registry, corpus, server):
    state = {"flag": True}
    s = server(lambda body: (200, {"issues": ([{"code": "banned_phrase", "severity": "block", "message": "x", "lang": "en"}]
                                              if "Stay alert." in body["drafts"]["en"]["instruction"] else [])}))
    plug = [HttpPlugin(s.url)]
    store = Store()
    run = pipe(registry, plug, store).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    assert run.status == "blocked"
    fixed = run.drafts["en"].instruction.replace("Stay alert.", "")
    publish.edit_draft(store, registry, run.id, "en", {"instruction": fixed}, "Alice P", plug)
    assert run.status == "ready" and "banned_phrase" not in codes(run)
    # approval re-runs the plugin too
    out = publish.review(store, registry, Settings(), run.id, "Bob S", "approve", plugins=plug)  # not the editor
    assert out.status == "published"


def test_from_spec_and_validation():
    ps = from_spec("issuer, http:https://a.example/x;post_facts+post_draft;critical ,http:http://b.example/y")
    assert [p.name for p in ps] == ["issuer", "http:a.example", "http:b.example"]
    assert ps[1].stages == ("post_facts", "post_draft") and ps[1].critical and ps[2].stages == ("post_draft",)
    assert from_spec("") == []
    for bad in ("nope", "http:ftp://x", "http:https://x;post_nothing"):
        with pytest.raises(ValueError):
            from_spec(bad)


def test_plugins_cannot_publish_structurally():
    src = (Path(__file__).resolve().parent.parent / "lastmile" / "plugins.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [getattr(node, "module", "") or ""] + [a.name for a in node.names]
            assert not any("publish" in n or "store" in n for n in names)


def test_api_exposes_plugins_and_applies_them(corpus):
    app = create_app(Settings(rate_limit_per_min=1000), store=Store(), plugins=[IssuerPlugin()])
    c = TestClient(app)
    assert c.get("/api/health").json()["plugins"] == ["issuer"]
    text = corpus["np-flood-clean"]["text"]
    run = c.post("/api/runs", json={"pack_id": "np-flood", "source_text": text}).json()
    assert "issuer_not_in_source" in {i["code"] for i in run["issues"]}


def test_run_plugins_ignores_other_stages(registry, corpus):
    run = pipe(registry, []).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    ctx = PluginContext("post_draft", registry.get("lk-landslide"), run.source_text, run.facts, run.derived, run.drafts)
    issues, notes = run_plugins([IssuerPlugin()], ctx)
    assert issues == [] and notes == []
