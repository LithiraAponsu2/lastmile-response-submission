"""The production Gemini path (GeminiLLM -> google-genai SDK -> HTTP), run against lastmile.mock_gemini.

The whole corpus must give the same result through the real SDK as through the in-process fake model,
so when credentials arrive the only unknown left is the model's language quality, not the plumbing.
"""

from __future__ import annotations

import pytest

from lastmile import publish
from lastmile.config import Settings
from lastmile.eval import faults_of, load_corpus, options_of
from lastmile.llm import FakeLLM, GeminiLLM, make_llm
from lastmile.mock_gemini import MockGemini, _parse_fail, _parse_fault, main, pack_for, role_of
from lastmile.pipeline import Options, Pipeline
from lastmile.store import Store

pytest.importorskip("google.genai")

CORPUS = {c["id"]: c for c in load_corpus()}


@pytest.fixture
def point_sdk_at(monkeypatch):
    def _point(mock: MockGemini) -> None:
        monkeypatch.setenv("GOOGLE_API_KEY", "offline-mock")
        monkeypatch.setenv("GOOGLE_GEMINI_BASE_URL", mock.url)
        monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
    return _point


def _setup(case, registry, store):
    for sid in case.get("setup", []):
        sr = Pipeline(FakeLLM(), registry, store, Options(verify=False)).run(CORPUS[sid]["text"], CORPUS[sid]["pack"])
        publish.review(store, registry, Settings(), sr.id, "setup-bot", "approve")


def _sig(run):
    return (run.status, run.msg_type, sorted((i.code, i.severity, i.lang or "", i.source) for i in run.issues),
            run.attempts, {lg: d.model_dump() for lg, d in run.drafts.items()})


@pytest.mark.parametrize("case_id", list(CORPUS))
def test_real_sdk_path_matches_the_fake_model_on_the_whole_corpus(case_id, registry, point_sdk_at):
    case = CORPUS[case_id]
    opts = options_of(case)
    want_store, got_store = Store(), Store()
    _setup(case, registry, want_store)
    _setup(case, registry, got_store)
    want = Pipeline(FakeLLM(faults_of(case)), registry, want_store, Options(**opts)).run(case["text"], case["pack"])
    with MockGemini(registry, faults=faults_of(case)) as mock:
        point_sdk_at(mock)
        got = Pipeline(make_llm("gemini"), registry, got_store, Options(**opts)).run(case["text"], case["pack"])
    assert _sig(got) == _sig(want)
    assert got.status == case["expect"]["status"]


def test_calls_use_the_configured_models_and_json_mode(registry, point_sdk_at, monkeypatch):
    monkeypatch.setenv("LASTMILE_MODEL_FAST", "flash-under-test")
    monkeypatch.setenv("LASTMILE_MODEL_VERIFIER", "pro-under-test")
    with MockGemini(registry) as mock:
        point_sdk_at(mock)
        run = Pipeline(GeminiLLM(retries=0), registry, Store()).run(CORPUS["lk-clean-multilevel"]["text"], "lk-landslide")
    assert run.status == "ready"
    by_role = {(r["role"], r["model"]) for r in mock.requests}
    assert by_role == {("intake", "flash-under-test"), ("localize", "flash-under-test"), ("verify", "pro-under-test")}
    assert len(mock.requests) == 7 and all(r["status"] == 200 for r in mock.requests)  # 1 intake, 3 + 3


@pytest.mark.parametrize("role,status,code,run_status", [
    ("localize", 503, "llm_fallback_template", "ready"),
    ("verify", 429, "verifier_unavailable", "ready"),
    ("intake", 500, "llm_error", "blocked"),
])
def test_http_failures_degrade_visibly_through_the_sdk(registry, point_sdk_at, role, status, code, run_status):
    with MockGemini(registry, fail={role: status}) as mock:
        point_sdk_at(mock)
        run = Pipeline(GeminiLLM(retries=0), registry, Store()).run(CORPUS["lk-clean-multilevel"]["text"], "lk-landslide")
    assert run.status == run_status and code in {i.code for i in run.issues}
    assert any(r["status"] == status for r in mock.requests)


def test_template_first_mode_never_calls_a_localizer(registry, point_sdk_at):
    with MockGemini(registry) as mock:
        point_sdk_at(mock)
        run = Pipeline(GeminiLLM(retries=0), registry, Store(), Options(drafting="templates")).run(
            CORPUS["lk-clean-multilevel"]["text"], "lk-landslide")
    assert run.status == "ready"
    assert sorted({r["role"] for r in mock.requests}) == ["intake", "verify"]


def test_models_list_endpoint(registry, point_sdk_at):
    with MockGemini(registry) as mock:
        point_sdk_at(mock)
        llm = GeminiLLM()
        names = [m.name for m in llm.client.models.list()]
    assert names == ["models/gemini-3.5-flash-lite", "models/gemini-3.6-flash"]


def test_prompt_routing(registry, lk):
    from lastmile import prompts
    np_pack = registry.get("np-flood")
    sys_, user = prompts.intake_prompt("x" * 20, np_pack)
    assert role_of(sys_) == "intake" and pack_for(registry, sys_, user).id == "np-flood"
    assert role_of("something else") == "unknown"
    with pytest.raises(ValueError):
        pack_for(registry, "no pack here", "nor here")


def test_bad_requests_are_400_not_a_crash(registry):
    with MockGemini(registry) as mock:
        status, payload = mock.generate("m", {"systemInstruction": {"parts": [{"text": "hello"}]},
                                              "generationConfig": {"responseMimeType": "application/json"}})
        assert status == 400 and payload["error"]["status"] == "INVALID_ARGUMENT"
        status, _ = mock.generate("m", {"generationConfig": {}})                 # not JSON mode
        assert status == 400


def test_cli_parsing():
    assert _parse_fail(["localize=503", "intake=500"]) == {"localize": 503, "intake": 500}
    f = _parse_fault(["localize:drop_number:si", "intake:add_number"])
    assert (f[0].role, f[0].kind, f[0].lang) == ("localize", "drop_number", "si") and f[1].lang is None
    with pytest.raises(SystemExit):
        main(["--fail", "publish=200"])
    with pytest.raises(SystemExit):
        main(["--fault", "nonsense"])
