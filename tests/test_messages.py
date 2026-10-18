"""What recipients actually read (gaps found by the simulated-recipient research)."""

from __future__ import annotations

import pytest

from lastmile import checks, publish
from lastmile.config import Settings
from lastmile.llm import FakeLLM
from lastmile.pipeline import Pipeline
from lastmile.store import Store

FIVE_RED = """NBRO LANDSLIDE EARLY WARNING (SYNTHETIC EXAMPLE)
Issued: 2025-11-27 18:00
Valid until: 2025-11-28 18:00
Level 3 (RED) - Evacuate: Passara, Haldummulla, Bandarawela (Badulla), Hanguranketha, Walapane (Nuwara Eliya)
Rainfall: 150 mm in the past 24 hours.
Advice: Residents in Level 3 areas should evacuate to safe locations immediately."""


def run_of(registry, text, store=None):
    return Pipeline(FakeLLM(), registry, store or Store()).run(text, "lk-landslide")


# a9 -------------------------------------------------------------------------------------------
def test_advice_is_given_per_level_with_its_areas(registry, corpus, lk):
    run = run_of(registry, corpus["lk-clean-multilevel"]["text"])
    for lg in lk.langs:
        instr = run.drafts[lg].instruction
        for lv, pids in run.derived.places_by_level.items():
            intent = lk.level_def(lv).intent
            names = ", ".join(lk.place_name(p, lg) for p in pids)
            assert f"{lk.label(lv, lg)} ({names}): {lk.hazard.actions[intent][lg]}" in instr
        # the voice script carries the same per-level advice, once
        assert run.drafts[lg].spoken.count(lk.hazard.actions["evacuate"][lg]) == 1


# a10 ------------------------------------------------------------------------------------------
def test_published_sms_has_issuer_hotline_and_a_checkable_reference(registry, corpus, lk):
    store = Store()
    run = run_of(registry, corpus["lk-clean-multilevel"]["text"], store)
    done = publish.review(store, registry, Settings(), run.id, "A. Perera", "approve")
    ref = done.outbox["reference"]
    assert ref == done.cap_identifier.rsplit("-", 1)[-1].upper()
    for lg in lk.langs:
        meta = lk.region.languages[lg]
        sms, voice = done.outbox["sms"][lg]["text"], done.outbox["voice"][lg]["script"]
        assert sms.startswith(meta.exercise_prefix) and "LastMile:" in sms
        assert meta.help_sms.format(hotline="117") in sms and sms.endswith(f"{meta.ref_label} {ref}")
        assert voice.endswith(meta.help_voice.format(hotline="117"))
        assert done.outbox["sms"][lg]["segments"] <= lk.channels.sms_max_segments


def test_model_may_not_invent_a_hotline_but_the_packs_one_is_fine(registry, corpus, lk):
    run = run_of(registry, corpus["lk-clean-multilevel"]["text"])
    d = run.drafts["en"].model_copy()
    ok = d.model_copy(update={"instruction": d.instruction + " Call 117."})
    bad = d.model_copy(update={"instruction": d.instruction + " Call 119."})
    codes = lambda x: {i.code for i in checks.check_draft(run.facts, run.derived, x, "en", lk)}  # noqa: E731
    assert "unsupported_number" not in codes(ok) and "unsupported_number" in codes(bad)


# a11 ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("lang,long_form,sms_form", [
    ("en", "Friday 28 November 2025, 18:00", "28/11 18:00"),
    ("si", "2025 නොවැම්බර් 28 සිකුරාදා, 18:00", "28/11 18:00"),
    ("ta", "28 நவம்பர் 2025 வெள்ளி, 18:00", "28/11 18:00"),
])
def test_times_are_written_for_people(registry, corpus, lk, lang, long_form, sms_form):
    run = run_of(registry, corpus["lk-clean-multilevel"]["text"])
    d = run.drafts[lang]
    assert long_form in d.description and long_form in d.spoken and sms_form in d.sms
    assert "2025-11-28" not in d.all_text()


# a12 ------------------------------------------------------------------------------------------
def test_update_says_update_and_what_changed(registry, corpus, lk):
    store = Store()
    first = run_of(registry, corpus["lk-clean-multilevel"]["text"], store)
    publish.review(store, registry, Settings(), first.id, "A. Perera", "approve")
    upd = run_of(registry, corpus["lk-update-escalation"]["text"], store)
    assert upd.derived.level_changes == {"LK-BD-HAL": [2, 3]}
    en = upd.drafts["en"]
    assert en.headline.startswith("UPDATE – ") and en.sms.startswith("UPDATE: ")
    assert en.description.startswith("UPDATE: Haldummulla is now at Level 3 – Red.")
    assert upd.drafts["ta"].description.startswith("புதுப்பிப்பு: ஹல்துமுல்ல இப்போது நிலை 3 – சிவப்பு.")


def test_update_without_marker_is_blocked(registry, corpus, lk):
    store = Store()
    first = run_of(registry, corpus["lk-clean-multilevel"]["text"], store)
    publish.review(store, registry, Settings(), first.id, "A. Perera", "approve")
    upd = run_of(registry, corpus["lk-update-escalation"]["text"], store)
    d = upd.drafts["si"].model_copy()
    word = lk.region.languages["si"].update_word
    d.sms = d.sms.replace(f"{word}: ", "")
    assert "update_marker_missing" in {i.code for i in checks.check_draft(upd.facts, upd.derived, d, "si", lk)}


def test_cancel_names_the_areas_and_when_to_return(registry, corpus, lk):
    store = Store()
    first = run_of(registry, corpus["lk-clean-multilevel"]["text"], store)
    publish.review(store, registry, Settings(), first.id, "A. Perera", "approve")
    canc = run_of(registry, corpus["lk-cancel"]["text"], store)
    assert canc.msg_type == "Cancel" and canc.status == "ready"
    en = canc.drafts["en"]
    assert "Passara" in en.sms and "Hanguranketha" in en.spoken and "local officials say it is safe" in en.sms
    assert "பசறை" in canc.drafts["ta"].headline


# a13 ------------------------------------------------------------------------------------------
def test_long_area_lists_never_leave_dots_and_always_fit(registry, lk):
    run = run_of(registry, FIVE_RED)
    assert run.status == "ready"
    for lg in lk.langs:
        d = run.drafts[lg]
        assert "..." not in d.all_text() and "…" not in d.all_text()
        assert lk.region.languages[lg].more_areas in d.sms
        env = checks.sms_envelope(lk, lg, d.sms, exercise=True, ref=checks.REF_PLACEHOLDER)
        assert checks.sms_segments(env)[2] <= lk.channels.sms_max_segments
    assert {i.lang for i in run.issues if i.code == "sms_omits_areas"} == set(lk.langs)


# a14 ------------------------------------------------------------------------------------------
def test_cap_says_truthfully_who_drafted_the_text(registry, corpus):
    from lastmile.pipeline import Options
    for drafting, expect in (("model", "AI-assisted; verified"), ("templates", "Approved pack templates")):
        store = Store()
        run = Pipeline(FakeLLM(), registry, store, Options(drafting=drafting)).run(
            corpus["lk-clean-multilevel"]["text"], "lk-landslide")
        assert publish.drafted_by(run).startswith(expect)
        done = publish.review(store, registry, Settings(), run.id, "A. Perera", "approve")
        assert expect in done.cap_xml


# b3 -------------------------------------------------------------------------------------------
def test_back_translations_are_kept_on_the_run(registry, corpus, lk):
    run = run_of(registry, corpus["lk-clean-multilevel"]["text"])
    assert set(run.back_translations) == set(lk.langs)
    assert all(bt.startswith("[fake model, not a real translation]") for bt in run.back_translations.values())


def test_back_translations_through_the_real_sdk(registry, corpus, monkeypatch):
    pytest.importorskip("google.genai")
    from lastmile.llm import GeminiLLM
    from lastmile.mock_gemini import MockGemini
    with MockGemini(registry) as mock:
        monkeypatch.setenv("GOOGLE_API_KEY", "offline-mock")
        monkeypatch.setenv("GOOGLE_GEMINI_BASE_URL", mock.url)
        monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
        run = Pipeline(GeminiLLM(retries=0), registry, Store()).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    assert run.status == "ready" and set(run.back_translations) == {"en", "si", "ta"}
