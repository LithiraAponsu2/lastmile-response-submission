from __future__ import annotations

import ast
from pathlib import Path

import pytest
from lxml import etree

from lastmile import cap, eval as lm_eval, publish
from lastmile.config import Settings
from lastmile.llm import FakeLLM, Fault, LLMError
from lastmile.pipeline import Options, Pipeline
from lastmile.publish import PublishError
from lastmile.store import Store

from clock import live_text

ROOT = Path(__file__).resolve().parent.parent / "lastmile"


# ------------------------------------------------- the whole corpus, one test each
@pytest.mark.parametrize("case_id", [c["id"] for c in lm_eval.load_corpus()])
def test_corpus_case(case_id, registry, corpus):
    case = corpus[case_id]
    run, _ = lm_eval.run_case(case, corpus, registry, guards=True, verify=True)
    exp = case["expect"]
    assert run.status == exp["status"], [(i.code, i.message) for i in run.blocking()]
    if "msg_type" in exp:
        assert run.msg_type == exp["msg_type"]
    codes = {i.code for i in run.issues}
    for c in exp.get("codes", []):
        assert c in codes, codes
    for lang, n in exp.get("attempts", {}).items():
        assert run.attempts[lang] == n
    if run.status == "blocked" and case["defect"] in ("source", "intake"):
        # the model is never asked to write anything when the facts are untrustworthy,
        # unless the problem only shows up in drafts
        assert run.status != "ready"


def test_safety_net_beats_baseline(registry):
    rep = lm_eval.evaluate("fake", registry=registry)
    assert rep["pipeline_matches_expectations"] == rep["cases"]
    assert rep["pipeline_unsafe_in_queue"] == 0
    assert rep["baseline_unsafe_in_queue"] >= rep["risky_cases"] * 0.6
    assert rep["pipeline_false_blocks"] == 0
    assert "f-intake-swapped-levels" in rep["residual_cases"]       # honest about what code cannot see


# ------------------------------------------------------------ pipeline details
def test_architecture_agents_cannot_publish():
    """No model-facing module may import the publish module or hold approval powers."""
    for name in ("pipeline.py", "llm.py", "prompts.py", "templates.py", "checks.py", "rules.py", "mock_gemini.py"):
        tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.ImportFrom):
                mods = [node.module or ""] + [a.name for a in node.names]
            elif isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            assert not any("publish" in m for m in mods), f"{name} imports {mods}"
    for cls in (FakeLLM, Pipeline):
        for attr in dir(cls):
            assert not any(w in attr.lower() for w in ("publish", "send", "approve", "broadcast")), attr


def test_pipeline_never_returns_published(make_pipe, corpus):
    run = make_pipe().run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    assert run.status == "ready" and run.cap_xml is None and not run.approvals


def test_blocked_run_gets_no_drafts_and_no_ai_writing(registry, corpus):
    llm = FakeLLM()
    run = Pipeline(llm, registry, Store()).run(corpus["lk-unknown-place"]["text"], "lk-landslide")
    assert run.status == "blocked" and run.drafts == {}
    assert llm.calls[("localize", "en")] == 0


def test_repair_loop_gives_feedback_and_counts_attempts(make_pipe, corpus):
    seen = []

    class Spy(FakeLLM):
        def localize(self, facts, derived, lang, pack, feedback=None):
            seen.append((lang, feedback))
            return super().localize(facts, derived, lang, pack, feedback)

    spy = Spy([Fault("localize", "drop_number", "si", (0,))])
    run = make_pipe(llm=spy).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    assert run.status == "ready" and run.attempts == {"en": 1, "si": 2, "ta": 1}
    si = [fb for lg, fb in seen if lg == "si"]
    assert si[0] is None and si[1] and si[1][0].code == "missing_number"


def test_model_down_falls_back_to_templates_with_warning(make_pipe, corpus):
    class Down(FakeLLM):
        def localize(self, *a, **k):
            raise LLMError("503 unavailable")

    run = make_pipe(llm=Down()).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    assert run.status == "ready"
    assert {i.code for i in run.issues} >= {"llm_fallback_template"}
    assert all(run.drafts[lg].headline for lg in ("en", "si", "ta"))


def test_model_down_without_fallback_blocks(make_pipe, corpus):
    class Down(FakeLLM):
        def localize(self, *a, **k):
            raise LLMError("503")

    run = make_pipe(llm=Down(), fallback_templates=False).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    assert run.status == "blocked" and "llm_error" in {i.code for i in run.issues}


def test_intake_failure_blocks(make_pipe, corpus):
    class Down(FakeLLM):
        def intake(self, *a, **k):
            raise LLMError("timeout")

    run = make_pipe(llm=Down()).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    assert run.status == "blocked" and run.issues[-1].code == "llm_error"


def test_unexpected_exception_in_model_layer_does_not_crash(make_pipe, corpus):
    class Weird(FakeLLM):
        def intake(self, *a, **k):
            raise KeyError("boom")

    run = make_pipe(llm=Weird()).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    assert run.status == "blocked"


def test_verifier_outage_is_a_warning_not_a_silent_pass(make_pipe, corpus):
    class NoVerify(FakeLLM):
        def verify(self, *a, **k):
            raise LLMError("verifier 429")

    run = make_pipe(llm=NoVerify()).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    assert run.status == "ready" and "verifier_unavailable" in {i.code for i in run.issues}


def test_model_supplied_place_ids_are_ignored(make_pipe, corpus):
    class Liar(FakeLLM):
        def intake(self, source_text, pack):
            f = super().intake(source_text, pack)
            for p in f.places:
                p.canonical = "LK-GL"       # try to smuggle Galle in
            return f

    run = make_pipe(llm=Liar()).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    assert "LK-GL" not in {p.canonical for p in run.facts.places}


def test_source_length_limit(make_pipe):
    with pytest.raises(ValueError):
        make_pipe(max_source_chars=50).run("x" * 51, "lk-landslide")


def test_baseline_mode_has_no_guards(make_pipe, corpus):
    run = make_pipe(guards=False, verify=False).run(corpus["lk-mislabeled-level"]["text"], "lk-landslide")
    assert run.status == "ready"                      # the naive pipeline lets a mislabelled alert through


def test_trace_marks_actor_for_every_step(clean_run):
    actors = {t.step: t.actor for t in clean_run.trace}
    assert actors["intake"] == "ai" and actors["localize"] == "ai"
    assert actors["rules"] == "code" and actors["verify"] == "code"


# --------------------------------------------------------------- publish gate
def approve(store, registry, settings, run, who="Alice Perera", decision="approve", note=""):
    return publish.review(store, registry, settings, run.id, who, decision, note)


def test_approve_publishes_valid_cap(registry, store, settings, make_pipe, corpus):
    run = make_pipe().run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    out = approve(store, registry, settings, run)
    assert out.status == "published" and out.cap_status == "Exercise"
    assert cap.validate(out.cap_xml) == []
    assert out.outbox["sms"]["si"]["segments"] >= 1 and out.outbox["voice"]["ta"]["tts_lang"] == "ta-IN"
    assert [a["action"] for a in out.audit] == ["created", "approve", "published"]
    assert store.active_alerts()[0].identifier == out.cap_identifier
    with pytest.raises(PublishError) as e:
        approve(store, registry, settings, run, who="Bob Silva")
    assert e.value.code == "already_published"


def test_blocked_run_cannot_be_approved(registry, store, settings, make_pipe, corpus):
    run = make_pipe().run(corpus["lk-mislabeled-level"]["text"], "lk-landslide")
    with pytest.raises(PublishError) as e:
        approve(store, registry, settings, run)
    assert e.value.code == "blocked" and run.status == "blocked" and run.cap_xml is None


def test_reviewer_name_required_and_sanitised(registry, store, settings, make_pipe, corpus):
    run = make_pipe().run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    for bad in ("", " ", "x", "y" * 81):
        with pytest.raises(PublishError):
            approve(store, registry, settings, run, who=bad)
    out = approve(store, registry, settings, run, who="Ann\x00e Perera")
    assert out.approvals[0].reviewer == "Anne Perera"


def test_unknown_decision_and_unknown_run(registry, store, settings, make_pipe, corpus):
    run = make_pipe().run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    with pytest.raises(PublishError):
        publish.review(store, registry, settings, run.id, "Alice P", "yolo")
    with pytest.raises(PublishError) as e:
        publish.review(store, registry, settings, "run_nope", "Alice P", "approve")
    assert e.value.status == 404


def test_reject_is_terminal(registry, store, settings, make_pipe, corpus):
    run = make_pipe().run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    out = approve(store, registry, settings, run, decision="reject", note="not today")
    assert out.status == "rejected" and out.cap_xml is None
    with pytest.raises(PublishError):
        approve(store, registry, settings, run, who="Bob Silva")
    with pytest.raises(PublishError):
        publish.edit_draft(store, registry, run.id, "en", {"headline": "x"}, "Alice P")


def test_live_mode_is_exercise_unless_deployment_allows_actual(registry, store, make_pipe, corpus):
    run = make_pipe().run(live_text(corpus["lk-clean-multilevel"]["text"]), "lk-landslide", mode="live")
    out = approve(store, registry, Settings(allow_actual=False), run)
    assert out.cap_status == "Exercise" and out.status == "published"


def test_actual_needs_two_distinct_reviewers(registry, store, make_pipe, corpus):
    s = Settings(allow_actual=True)
    run = make_pipe().run(live_text(corpus["lk-clean-multilevel"]["text"]), "lk-landslide", mode="live")
    first = approve(store, registry, s, run, who="Alice Perera")
    assert first.status == "ready" and first.cap_xml is None            # 1/2: nothing leaves
    with pytest.raises(PublishError) as e:
        approve(store, registry, s, run, who="alice perera")           # same person, different case
    assert e.value.code == "duplicate_approver"
    done = approve(store, registry, s, run, who="Bob Silva")
    assert done.status == "published" and done.cap_status == "Actual"
    assert cap.validate(done.cap_xml) == []
    assert b"<note>EXERCISE" not in done.cap_xml.encode()


def test_edit_clears_approvals_and_rechecks(registry, store, make_pipe, corpus):
    s = Settings(allow_actual=True)
    run = make_pipe().run(live_text(corpus["lk-clean-multilevel"]["text"]), "lk-landslide", mode="live")
    approve(store, registry, s, run, who="Alice Perera")
    assert run.approvals
    # a sloppy edit that drops the figure -> blocked, approvals gone
    desc = run.drafts["en"].description.replace("150", "")
    publish.edit_draft(store, registry, run.id, "en", {"description": desc}, "Alice Perera")
    assert run.status == "blocked" and not run.approvals
    with pytest.raises(PublishError):
        approve(store, registry, s, run, who="Bob Silva")
    # fix it properly -> ready again
    publish.edit_draft(store, registry, run.id, "en", {"description": desc.replace("hours: ", "hours: 150")}, "Alice Perera")
    assert run.status == "ready"
    assert "edit_draft" in [a["action"] for a in run.audit]


def test_edit_validations(registry, store, make_pipe, corpus):
    run = make_pipe().run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    with pytest.raises(PublishError):
        publish.edit_draft(store, registry, run.id, "fr", {"headline": "x"}, "Alice P")
    with pytest.raises(PublishError):
        publish.edit_draft(store, registry, run.id, "en", {"facts": "x"}, "Alice P")
    with pytest.raises(PublishError):
        publish.edit_draft(store, registry, run.id, "en", {"headline": "x"}, "")


def test_update_supersedes_and_cancel_closes(registry, store, settings, make_pipe, corpus):
    first = make_pipe().run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    approve(store, registry, settings, first)
    upd = make_pipe().run(corpus["lk-update-escalation"]["text"], "lk-landslide")
    assert upd.msg_type == "Update" and len(upd.references) == 1
    out = approve(store, registry, settings, upd)
    root = etree.fromstring(out.cap_xml.encode())
    refs = root.findtext(f"{{{cap.CAP_NS}}}references")
    assert first.cap_identifier in refs
    assert [a.identifier for a in store.active_alerts()] == [upd.cap_identifier]       # first one is superseded
    canc = make_pipe().run(corpus["lk-cancel"]["text"], "lk-landslide")
    assert canc.msg_type == "Cancel" and canc.status == "ready"
    done = approve(store, registry, settings, canc)
    assert cap.validate(done.cap_xml) == [] and not store.active_alerts()


def test_preview_cap_is_draft_status(registry, store, settings, make_pipe, corpus):
    run = make_pipe().run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    xml = publish.preview_cap(run, registry, settings)
    assert cap.validate(xml) == [] and b"<status>Draft</status>" in xml and b"DRAFT" in xml
    blocked = make_pipe().run(corpus["lk-unknown-place"]["text"], "lk-landslide")
    with pytest.raises(PublishError):
        publish.preview_cap(blocked, registry, settings)


def test_persistence_roundtrip(tmp_path, registry, settings, corpus):
    st = Store(tmp_path)
    run = Pipeline(FakeLLM(), registry, st).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    done = approve(st, registry, settings, run)
    again = Store(tmp_path)
    assert again.get(run.id).status == "published"
    assert again.active_alerts()[0].identifier == done.cap_identifier
    assert (tmp_path / "outbox" / run.id / f"{done.cap_identifier}.xml").is_file()


# ------------------------------------- warnings must survive approval and edits of OTHER languages
def test_reviewer_must_read_warning_survives_the_first_live_approval(registry, store, corpus):
    s = Settings(allow_actual=True)
    llm = FakeLLM([Fault("verify", "flag", "ta", (0, 1, 2))])
    run = Pipeline(llm, registry, store).run(live_text(corpus["lk-clean-multilevel"]["text"]), "lk-landslide", mode="live")
    assert run.status == "ready"
    publish.review(store, registry, s, run.id, "Alice Perera", "approve")
    assert run.status == "ready" and len(run.approvals) == 1               # live: a second reviewer is needed
    flag = [i for i in run.issues if i.code == "mistranslation" and i.lang == "ta"]
    assert flag and flag[0].severity == "warn"                              # ...and still sees the warning
    done = publish.review(store, registry, s, run.id, "Bob Silva", "approve")
    assert done.status == "published" and any(i.code == "mistranslation" for i in done.issues)


def test_fallback_flag_is_retired_only_for_the_language_a_human_edited(registry, store, corpus):
    class Down(FakeLLM):
        def localize(self, *a, **k):
            raise LLMError("503")

    run = Pipeline(Down(), registry, store).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    flagged = lambda: {i.lang for i in run.issues if i.code == "llm_fallback_template"}  # noqa: E731
    assert flagged() == {"en", "si", "ta"}
    publish.edit_draft(store, registry, run.id, "en", {"instruction": run.drafts["en"].instruction + " Stay safe."}, "A. Perera")
    assert flagged() == {"si", "ta"} and run.status == "ready"
