"""Lifecycle defects found by the simulated-user research (docs/SAFETY.md).

Each test reproduces a path where system state, not the model, could put a wrong alert on the air.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from lastmile import publish
from lastmile.config import Settings
from lastmile.llm import FakeLLM
from lastmile.pipeline import Options, Pipeline
from lastmile.publish import PublishError
from lastmile.store import Store

from clock import live_text

LIVE = Settings(allow_actual=True)
REPLAY = Settings()


def pipe(registry, store, allow_actual=False):
    return Pipeline(FakeLLM(), registry, store, Options(allow_actual=allow_actual))


def publish_live(registry, store, text):
    run = pipe(registry, store, allow_actual=True).run(live_text(text), "lk-landslide", mode="live")
    publish.review(store, registry, LIVE, run.id, "Alice Perera", "approve")
    return publish.review(store, registry, LIVE, run.id, "Bob Silva", "approve")


# ------------------------------------------------------------------ a1: Exercise never touches Actual
def test_exercise_cancel_cannot_cancel_a_live_alert(registry, store, corpus):
    live = publish_live(registry, store, corpus["lk-clean-multilevel"]["text"])
    assert live.status == "published" and live.cap_status == "Actual"
    drill = pipe(registry, store).run(live_text(corpus["lk-cancel"]["text"].replace(
        "Issued: 2025-11-28 09:00", "Issued: 2025-11-28 09:00\nValid until: 2025-11-28 09:00")), "lk-landslide")
    # an Exercise cancel sees no Exercise alert to cancel, so it is blocked, and the live alert stays in force
    assert drill.status == "blocked" and "cancel_without_target" in {i.code for i in drill.issues}
    assert [a.identifier for a in store.active_alerts()] == [live.cap_identifier]


def test_live_update_does_not_reference_an_exercise_alert(registry, store, corpus):
    drill = pipe(registry, store).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    publish.review(store, registry, REPLAY, drill.id, "Trainee", "approve")
    live = pipe(registry, store, allow_actual=True).run(
        live_text(corpus["lk-update-escalation"]["text"]), "lk-landslide", mode="live")
    assert live.msg_type == "Alert" and live.references == []


def test_store_refuses_to_close_an_alert_of_the_other_status(registry, corpus):
    from lastmile.schemas import PublishedAlert
    st = Store()
    actual = PublishedAlert(identifier="A1", run_id="r1", pack_id="lk-landslide", sender="s", sent="2025-11-27T18:00:00+05:30",
                            msg_type="Alert", status="Actual", place_levels={"LK-BD-PAS": 3})
    st.add_published(actual, [])
    st.add_published(PublishedAlert(identifier="E1", run_id="r2", pack_id="lk-landslide", sender="s",
                                    sent="2025-11-27T19:00:00+05:30", msg_type="Cancel", status="Exercise",
                                    cancelled=True), supersedes=["A1"])
    assert [a.identifier for a in st.active_alerts()] == ["A1"]


# ------------------------------------------------------- a2: decisions are re-checked at approval
def test_same_bulletin_drafted_twice_cannot_both_publish(registry, store, corpus):
    text = corpus["lk-clean-multilevel"]["text"]
    first, second = (pipe(registry, store).run(text, "lk-landslide") for _ in range(2))
    assert first.msg_type == second.msg_type == "Alert"
    publish.review(store, registry, REPLAY, first.id, "A. Perera", "approve")
    with pytest.raises(PublishError) as e:
        publish.review(store, registry, REPLAY, second.id, "A. Perera", "approve")
    assert e.value.code == "active_alerts_changed"
    assert second.status == "blocked" and "active_alerts_changed" in {i.code for i in second.issues}
    assert len(store.active_alerts()) == 1
    # the finding stays: a reviewer edit cannot clear it, only a fresh draft
    publish.edit_draft(store, registry, second.id, "en", {"instruction": second.drafts["en"].instruction + " "}, "B. Silva")
    assert second.status == "blocked"
    again = pipe(registry, store).run(text, "lk-landslide")
    assert again.msg_type == "Update" and "duplicate_of_active" in {i.code for i in again.issues}


def test_cancel_whose_target_disappeared_is_refused(registry, store, corpus):
    first = pipe(registry, store).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    publish.review(store, registry, REPLAY, first.id, "A. Perera", "approve")
    c1 = pipe(registry, store).run(corpus["lk-cancel"]["text"], "lk-landslide")
    c2 = pipe(registry, store).run(corpus["lk-cancel"]["text"], "lk-landslide")
    publish.review(store, registry, REPLAY, c1.id, "A. Perera", "approve")
    with pytest.raises(PublishError) as e:
        publish.review(store, registry, REPLAY, c2.id, "A. Perera", "approve")
    assert e.value.code == "active_alerts_changed"


# --------------------------------------------------------------------- a3: dropped areas are visible
def test_update_that_drops_an_area_says_so(registry, store, corpus):
    first = pipe(registry, store).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    publish.review(store, registry, REPLAY, first.id, "A. Perera", "approve")
    upd = pipe(registry, store).run(corpus["lk-update-escalation"]["text"], "lk-landslide")
    dropped = next(i for i in upd.issues if i.code == "areas_dropped_from_active")
    assert "Hanguranketha (L1)" in dropped.message and dropped.severity == "warn"


# ------------------------------------------------------------------------- a4: the clock matters
def test_live_alert_already_expired_is_refused(registry, store, corpus):
    run = pipe(registry, store, allow_actual=True).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide", mode="live")
    assert run.status == "ready"                                         # drafted from a 2025 bulletin…
    with pytest.raises(PublishError) as e:
        publish.review(store, registry, LIVE, run.id, "Alice Perera", "approve")
    assert e.value.code == "expired_before_send"                         # …which is long over by now
    assert run.status == "blocked" and not store.active_alerts()


def test_active_endpoint_drops_expired_alerts(registry, corpus):
    from fastapi.testclient import TestClient
    from lastmile.api import create_app
    store = Store()
    c = TestClient(create_app(Settings(rate_limit_per_min=100), store=store, registry=registry))
    rid = c.post("/api/runs", json={"pack_id": "lk-landslide", "source_text": corpus["lk-clean-multilevel"]["text"]}).json()["id"]
    c.post(f"/api/runs/{rid}/review", json={"reviewer": "A. Perera", "decision": "approve"})
    assert len(c.get("/api/active", params={"at": "2025-11-27T20:00:00+05:30"}).json()) == 1
    assert c.get("/api/active", params={"at": "2025-11-29T20:00:00+05:30"}).json() == []     # after expiry
    assert c.get("/api/active", params={"at": "2025-11-27T17:00:00+05:30"}).json() == []     # before it was sent
    assert c.get("/api/active", params={"at": "2025-11-27T20:00:00"}).status_code == 422     # no offset


def test_live_alert_inside_its_window_publishes(registry, store, corpus):
    done = publish_live(registry, store, corpus["lk-clean-multilevel"]["text"])
    assert done.status == "published" and done.cap_status == "Actual"
    later = datetime.now(timezone.utc) + timedelta(days=3)
    run = pipe(registry, store, allow_actual=True).run(live_text(corpus["lk-clean-multilevel"]["text"], now=later),
                                                       "lk-landslide", mode="live")
    assert run.msg_type == "Alert"            # the first one is not in force at the new bulletin's issue time


# ------------------------------------------------------ a5: human edits cannot quietly weaken advice
def test_weakened_evacuate_edit_is_blocked_in_every_language(registry, store, corpus, lk):
    run = pipe(registry, store).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    strong, weak = lk.hazard.actions["evacuate"]["ta"], lk.hazard.actions["monitor"]["ta"]
    d = run.drafts["ta"]
    publish.edit_draft(store, registry, run.id, "ta", {"instruction": d.instruction.replace(strong, weak),
                                                       "sms": d.sms.replace(strong, weak)}, "Kumar R")
    assert run.status == "blocked"
    assert {i.code for i in run.issues if i.lang == "ta"} >= {"instruction_intent_missing"}
    with pytest.raises(PublishError):
        publish.review(store, registry, REPLAY, run.id, "Someone Else", "approve")


def test_editor_cannot_be_the_only_approver(registry, store, corpus):
    run = pipe(registry, store).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    publish.edit_draft(store, registry, run.id, "en",
                       {"instruction": run.drafts["en"].instruction + " Help neighbours who need it."}, "Kumar Rajan")
    assert run.status == "ready" and run.editors == ["Kumar Rajan"]
    for alias in ("Kumar Rajan", "kumar", "K. Rajan"):
        with pytest.raises(PublishError) as e:
            publish.review(store, registry, REPLAY, run.id, alias, "approve")
        assert e.value.code == "editor_cannot_self_approve"
    assert publish.review(store, registry, REPLAY, run.id, "Nimali Silva", "approve").status == "published"


def test_live_alert_editor_may_be_one_of_two_approvers(registry, store, corpus):
    run = pipe(registry, store, allow_actual=True).run(live_text(corpus["lk-clean-multilevel"]["text"]),
                                                       "lk-landslide", mode="live")
    publish.edit_draft(store, registry, run.id, "en", {"instruction": run.drafts["en"].instruction + " "}, "Alice Perera")
    publish.review(store, registry, LIVE, run.id, "Alice Perera", "approve")
    assert publish.review(store, registry, LIVE, run.id, "Bob Silva", "approve").status == "published"


@pytest.mark.parametrize("a,b,same", [
    ("Alice Perera", "alice  perera", True), ("Alice", "Alice Perera", True), ("A. Perera", "Alice Perera", True),
    ("A Perera", "Anura Perera", True), ("Alice Perera", "Bob Silva", False), ("Alice Perera", "Alice Silva", False),
    ("Ravi", "Ravindra", False),
])
def test_reviewer_name_matching_errs_towards_same_person(a, b, same):
    assert publish.same_reviewer(a, b) is same


def test_alice_and_alice_perera_do_not_make_two_approvers(registry, store, corpus):
    run = pipe(registry, store, allow_actual=True).run(live_text(corpus["lk-clean-multilevel"]["text"]),
                                                       "lk-landslide", mode="live")
    publish.review(store, registry, LIVE, run.id, "Alice", "approve")
    with pytest.raises(PublishError) as e:
        publish.review(store, registry, LIVE, run.id, "Alice Perera", "approve")
    assert e.value.code == "duplicate_approver" and run.status == "ready"


def test_edited_language_is_reverified_as_advice(registry, store, corpus):
    from lastmile.llm import Fault
    from lastmile.pipeline import reverify_edit
    run = pipe(registry, store).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    publish.edit_draft(store, registry, run.id, "si", {"instruction": run.drafts["si"].instruction + " "}, "Kumar R")
    reverify_edit(run, registry.get("lk-landslide"), FakeLLM([Fault("verify", "flag", "si", (0,))]), "si")
    flag = next(i for i in run.issues if i.code == "mistranslation")
    assert flag.lang == "si" and flag.severity == "warn" and "After a human edit" in flag.message
    assert run.status == "ready"                                          # advice to the next reviewer, not a gate

    class Down(FakeLLM):
        def verify(self, *a, **k):
            raise RuntimeError("503")
    reverify_edit(run, registry.get("lk-landslide"), Down(), "si")
    assert any(i.code == "verifier_unavailable" and i.lang == "si" for i in run.issues)


# ------------------------------------------------------ a6: the audit shows exactly what changed
def test_audit_keeps_before_after_and_the_original_ai_text(registry, store, corpus):
    run = pipe(registry, store).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    original = run.drafts["en"].instruction
    publish.edit_draft(store, registry, run.id, "en", {"instruction": original + " Check on neighbours."}, "Kumar R")
    publish.edit_draft(store, registry, run.id, "en", {"instruction": original}, "Nimali S")
    edits = [a for a in run.audit if a["action"] == "edit_draft"]
    assert edits[0]["changes"] == [{"lang": "en", "field": "instruction", "before": original,
                                    "after": original + " Check on neighbours."}]
    assert edits[1]["changes"][0]["after"] == original
    assert run.original_drafts["en"].instruction == original
    assert run.editors == ["Kumar R", "Nimali S"]


def test_runs_are_stamped_with_what_produced_them(registry, store, corpus, lk):
    from lastmile import prompts
    run = pipe(registry, store).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    assert run.provenance == {"llm": "fake", "models": {}, "prompts": prompts.PROMPTS_VERSION,
                              "pack": lk.fingerprint, "drafting": "model"}
    from lastmile.llm import GeminiLLM
    g = Pipeline(GeminiLLM(model_fast="f-1", model_verifier="v-2"), registry, store).run("short text x", "lk-landslide")
    assert g.provenance["models"] == {"fast": "f-1", "verifier": "v-2"}


# ------------------------------------------------------ a7: the hazard check can fire again
def test_intake_prompt_no_longer_dictates_the_hazard(lk):
    from lastmile import prompts
    system, _ = prompts.intake_prompt("x" * 20, lk)
    assert "always" not in system.split("- hazard:")[1].split("\n")[0]


@pytest.mark.parametrize("observed,ok", [("landslide", True), ("Landslide", True), ("landslide-nbro", True),
                                         ("mudslide", True), ("", True), ("cyclone", False), ("flood", False),
                                         ("tsunami", False)])
def test_hazard_matching_uses_pack_keywords(lk, observed, ok):
    assert lk.hazard.matches(observed) is ok


def test_flood_pack_accepts_flood(registry):
    assert registry.get("np-flood").hazard.matches("flash flood")


# ------------------------------------------------------ a8: drills look like drills on the phone
def test_exercise_payloads_say_exercise_and_actual_ones_do_not(registry, store, corpus, lk):
    run = pipe(registry, store).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    done = publish.review(store, registry, REPLAY, run.id, "A. Perera", "approve")
    assert done.outbox["status"] == "Exercise"
    for lg in lk.langs:
        prefix = lk.region.languages[lg].exercise_prefix
        assert done.outbox["sms"][lg]["text"].startswith(prefix)
        assert done.outbox["voice"][lg]["script"].startswith(prefix)
    live = publish_live(registry, Store(), corpus["lk-clean-multilevel"]["text"])
    assert live.outbox["status"] == "Actual"
    assert not any(live.outbox["sms"][lg]["text"].startswith(lk.region.languages[lg].exercise_prefix) for lg in lk.langs)


# ------------------------------------------------------ b6: retract a false alarm in seconds
def test_retraction_cancels_exactly_that_alert_with_error_wording(registry, corpus, lk):
    import time as _t
    from fastapi.testclient import TestClient
    from lastmile.api import create_app
    c = TestClient(create_app(Settings(rate_limit_per_min=100), store=Store(), registry=registry))
    rid = c.post("/api/runs", json={"pack_id": "lk-landslide", "source_text": corpus["lk-clean-multilevel"]["text"]}).json()["id"]
    first = c.post(f"/api/runs/{rid}/review", json={"reviewer": "A. Perera", "decision": "approve"}).json()
    t0 = _t.perf_counter()
    r = c.post(f"/api/runs/{rid}/retract")
    assert r.status_code == 200, r.text
    canc = r.json()
    assert canc["status"] == "ready" and canc["msg_type"] == "Cancel" and canc["cancel_target"] == first["cap_identifier"]
    assert "sent in error" in canc["drafts"]["en"]["sms"] and "Passara" in canc["drafts"]["en"]["sms"]
    assert "தவறாக" in canc["drafts"]["ta"]["sms"]
    done = c.post(f"/api/runs/{canc['id']}/review", json={"reviewer": "B. Silva", "decision": "approve"}).json()
    assert done["status"] == "published" and _t.perf_counter() - t0 < 120          # Hawaii 2018: 38 minutes
    assert c.get("/api/active", params={"at": "2025-11-27T20:00:00+05:30"}).json() == []
    assert c.post(f"/api/runs/{canc['id']}/retract").status_code == 409             # a cancel is not retractable


def test_retraction_targets_one_alert_only(registry, store, corpus):
    first = pipe(registry, store).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    publish.review(store, registry, REPLAY, first.id, "A. Perera", "approve")
    nepal = Pipeline(FakeLLM(), registry, store).run(corpus["np-flood-clean"]["text"], "np-flood")
    publish.review(store, registry, REPLAY, nepal.id, "A. Perera", "approve")
    text = ("CORRECTION - ALERT SENT IN ERROR\nIssued: 2025-11-27 18:01\nThe alert was sent in error.")
    run = pipe(registry, store).run(text, "lk-landslide", cancel_target=first.cap_identifier)
    assert run.msg_type == "Cancel" and len(run.references) == 1 and first.cap_identifier in run.references[0]
    publish.review(store, registry, REPLAY, run.id, "B. Silva", "approve")
    assert [a.identifier for a in store.active_alerts()] == [nepal.cap_identifier]


# ------------------------------------------------------ b7: wording and area sanity
def test_exercise_saying_not_a_drill_is_refused(registry, store, corpus):
    run = pipe(registry, store).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    publish.edit_draft(store, registry, run.id, "en",
                       {"headline": "THIS IS NOT A DRILL. " + run.drafts["en"].headline}, "Kumar R")
    with pytest.raises(PublishError) as e:
        publish.review(store, registry, REPLAY, run.id, "Nimali S", "approve")
    assert e.value.code == "real_wording_in_exercise"


def test_actual_alert_saying_this_is_a_test_is_refused(registry, store, corpus):
    run = pipe(registry, store, allow_actual=True).run(live_text(corpus["lk-clean-multilevel"]["text"]),
                                                       "lk-landslide", mode="live")
    publish.edit_draft(store, registry, run.id, "en",
                       {"instruction": run.drafts["en"].instruction + " This is a test."}, "Kumar R")
    with pytest.raises(PublishError) as e:
        publish.review(store, registry, LIVE, run.id, "Nimali S", "approve")
    assert e.value.code == "drill_wording_in_actual"


def test_large_target_area_is_flagged(registry, store):
    text = """NBRO LANDSLIDE EARLY WARNING (SYNTHETIC EXAMPLE)
Issued: 2025-11-27 18:00
Valid until: 2025-11-28 18:00
Level 1 (YELLOW) - Be aware: Badulla, Nuwara Eliya, Kandy, Kegalle, Ratnapura, Matale, Monaragala, Kurunegala, Hambantota
Advice: Stay alert and follow official announcements."""
    run = pipe(registry, store).run(text, "lk-landslide")
    big = next(i for i in run.issues if i.code == "large_target_area")
    assert big.severity == "warn" and "km²" in big.message


def test_cap_refuses_an_alert_without_any_area(registry, corpus):
    from lastmile import cap
    from lastmile.schemas import Derived
    run = pipe(registry, Store()).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    with pytest.raises(cap.CapError):
        cap.build_cap(registry.get("lk-landslide"), identifier="X", sent=datetime.now(timezone.utc), status="Exercise",
                      msg_type="Alert", facts=run.facts, derived=Derived(), drafts=run.drafts)
