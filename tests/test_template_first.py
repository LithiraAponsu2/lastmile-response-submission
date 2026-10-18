"""Template-first mode (LASTMILE_DRAFTING=templates): the fallback plan if a language is weak in the live model.

AI still reads the bulletin (Intake, guarded) and checks the drafts (Verifier); the public text comes from the
pack's approved templates and is flagged so the reviewer knows no AI wrote it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from lastmile import publish
from lastmile.api import create_app
from lastmile.config import Settings
from lastmile.llm import FakeLLM, Fault
from lastmile.pipeline import Options
from lastmile.schemas import Derived, Facts
from lastmile.store import Store
from lastmile.templates import render_draft, template_draft


class Counting(FakeLLM):
    def __init__(self, faults=None):
        super().__init__(faults)
        self.localized = 0

    def localize(self, *a, **k):
        self.localized += 1
        return super().localize(*a, **k)


def test_no_localizer_call_and_every_language_flagged(make_pipe, corpus, lk):
    llm = Counting()
    run = make_pipe(llm=llm, drafting="templates").run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    assert run.status == "ready" and llm.localized == 0
    flagged = {i.lang for i in run.issues if i.code == "template_draft"}
    assert flagged == set(lk.langs) and all(i.severity == "warn" for i in run.issues if i.code == "template_draft")
    for lg in lk.langs:
        assert run.drafts[lg] == render_draft(run.facts, run.derived, lg, lk)
    assert any(t.step == "template_draft" and t.actor == "code" for t in run.trace)
    assert not any(t.step.startswith("localize") for t in run.trace)


def test_verifier_still_runs_and_cannot_force_a_futile_retry(make_pipe, corpus):
    llm = Counting([Fault("verify", "flag", "si", (0, 1, 2))])
    run = make_pipe(llm=llm, drafting="templates").run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    assert run.attempts == {"en": 1, "si": 1, "ta": 1}
    flag = next(i for i in run.issues if i.code == "mistranslation")
    assert flag.severity == "warn" and "reviewer must read" in flag.message
    assert run.status == "ready"


def test_intake_is_still_guarded(make_pipe, corpus):
    run = make_pipe(llm=FakeLLM([Fault("intake", "add_number")]), drafting="templates").run(
        corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    assert run.status == "blocked" and "intake_figure_not_in_source" in {i.code for i in run.issues}
    assert run.drafts == {}


def test_template_that_cannot_render_blocks(lk):
    draft, issue = template_draft(Facts(), Derived(), "en", lk)
    assert issue.code == "template_unavailable" and issue.severity == "block" and not draft.headline


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        Options(drafting="freestyle")


def test_settings_and_api_carry_the_mode(monkeypatch, corpus):
    monkeypatch.setenv("LASTMILE_DRAFTING", "Templates")
    assert Settings.from_env().drafting == "templates"
    c = TestClient(create_app(Settings(drafting="templates", rate_limit_per_min=100), store=Store()))
    assert c.get("/api/health").json()["drafting"] == "templates"
    run = c.post("/api/runs", json={"pack_id": "lk-landslide",
                                    "source_text": corpus["lk-clean-multilevel"]["text"]}).json()
    assert run["status"] == "ready" and "template_draft" in {i["code"] for i in run["issues"]}
    done = c.post(f"/api/runs/{run['id']}/review", json={"reviewer": "A. Perera", "decision": "approve"}).json()
    assert done["status"] == "published"


def test_bad_mode_fails_at_startup():
    with pytest.raises(ValueError):
        create_app(Settings(drafting="nope"), store=Store())


def test_template_draft_warning_survives_human_edit_recheck(make_pipe, corpus, registry, settings, store):
    run = make_pipe(drafting="templates").run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    d = run.drafts["en"]
    edited = publish.edit_draft(store, registry, run.id, "en", {"instruction": d.instruction + " Stay safe."}, "A. Perera")
    assert edited.status == "ready"
    # the human now owns the English text; the template flag for the other languages remains visible
    assert {i.lang for i in edited.issues if i.code == "template_draft"} >= {"si", "ta"}
