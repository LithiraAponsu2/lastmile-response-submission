"""AI suggestions: a recorded warning, a new report, an overdue request or a disagreement prepares a plan for the right role."""
import uuid
from datetime import timedelta

from lastmile import team_suggestions
from lastmile.team_messages import HOTLINE
from lastmile.workspace import now_iso
from test_teams import command, get, team  # noqa: F401  (fixture)

NBRO = "EXERCISE. NBRO Level 3 (Red) landslide early warning for Kothmale and Walapane DS divisions. Level 2 (Amber): Hanguranketha."
SITREP = """Disaster Management Center - Situation Report as at 1000hrs on 09th October 2026 - exercise
20 Nuwara Eliya 2100 6900 1800 6100 2 1 5 41 4 120 400 3 90 300 10 40 100 340
"""


def events(doc, kind=None):
    return [p for p in doc["coordination"]["plans"] if p["origin"] == "event" and (kind is None or p["trigger"]["kind"] == kind)]


def approve(team, plan, who="admin", expected=200):
    r = team[who].post(team["base"] + f"/plans/{plan['id']}/approve", json={"hash": plan["hash"], "request_id": str(uuid.uuid4())})
    assert r.status_code == expected, r.text
    return r.json()


def report(team, text, who="contributor"):
    return command(team, who, "contribute", {"title": "Field report", "text": text, "observed_at": now_iso()})


def test_a_recorded_warning_prepares_messages_in_three_languages(team):
    sid = report(team, "EXERCISE note from Kothmale: slopes cracking near the school after heavy rain all night.")["sources"][-1]["id"]
    doc = command(team, "admin", "warning", {"hazard": "landslide", "level": "red", "areas": ["ds-nuwara-eliya-kothmale"], "issuer": "nbro",
                                             "issued_at": now_iso(), "advice": "Leave slopes now.", "source_id": sid})
    plan = events(doc, "warning_messages")[-1]
    assert plan["prepared_for"] == {"organisation": "dmc", "roles": ["communications_reviewer", "coordinator"]} and plan["current"]
    english, sinhala, tamil, release = (s["data"] for s in plan["steps"])
    assert english["language"] == "en" and english["text"].startswith("EVACUATE NOW: Kothmale DS. NBRO Red landslide warning.") and english["areas"] == ["ds-nuwara-eliya-kothmale"]
    assert sinhala["language"] == "si" and "රතු" in sinhala["text"] and "Kothmale DS" in sinhala["text"] and HOTLINE in sinhala["text"]
    assert tamil["language"] == "ta" and "சிவப்பு" in tamil["text"] and "Kothmale DS" in tamil["text"] and HOTLINE in tamil["text"]
    assert release["kind"] == "press_release" and "Advice: Leave slopes now." in release["text"] and release["outlets"]
    assert not plan["checks"] and not plan["prepared_by_ai"] and plan["models"] == [{"role": "suggestion", "model": "offline-rules"}]
    assert events(get(team, "owner")) == []  # for the DMC's communications reviewers and coordinators only
    done = approve(team, plan)
    drafts = done["coordination"]["documents"][-4:]
    assert [(d["kind"], d["language"], d["status"]) for d in drafts] == [("sms", "en", "draft"), ("sms", "si", "draft"), ("sms", "ta", "draft"), ("press_release", "en", "draft")]
    assert "prepared automatically, approved by Coordinator" in done["events"][-2]["detail"]
    assert len(events(get(team), "warning_messages")) == 1  # one suggestion per warning, however often it is scanned


def test_a_translation_that_loses_a_fact_is_blocked_even_after_an_edit(team):
    sid = report(team, "EXERCISE note from Kothmale: slopes cracking near the school after heavy rain all night.")["sources"][-1]["id"]
    doc = command(team, "admin", "warning", {"hazard": "flood", "level": "amber", "areas": ["ds-nuwara-eliya-kothmale"], "issuer": "irrigation",
                                             "issued_at": now_iso(), "advice": "Move valuables up.", "source_id": sid})
    plan = events(doc, "warning_messages")[-1]
    tamil = plan["steps"][2]
    changed = tamil["data"]["text"].replace(HOTLINE, "119").replace("செம்மஞ்சள்", "சிவப்பு")
    r = team["admin"].post(team["base"] + f"/plans/{plan['id']}/edit", json={"hash": plan["hash"], "changes": [{"id": tamil["id"], "data": {"text": changed}}]})
    assert r.status_code == 200, r.text
    edited = events(r.json(), "warning_messages")[-1]
    [check] = edited["checks"]
    assert check["step"] == "s3" and check["level"] == "block" and "drops 117" in check["message"] and "adds 119" in check["message"] and "செம்மஞ்சள்" in check["message"]
    approve(team, edited, expected=409)


def test_a_new_bulletin_report_becomes_records_to_check(team):
    doc = report(team, NBRO)
    plan = events(get(team), "report_reading")[-1]  # for the lead's coordinators, not the person who reported
    assert plan["prepared_for"] == {"organisation": "dmc", "roles": ["coordinator"]}
    assert [(s["action"], s["data"]["level"], s["data"]["issuer"], s["data"]["areas"]) for s in plan["steps"]] == [
        ("warning", "red", "nbro", ["ds-nuwara-eliya-kothmale", "ds-nuwara-eliya-walapane"]), ("warning", "amber", "nbro", ["ds-nuwara-eliya-hanguranketa"])]
    assert all(s["data"]["source_id"] == doc["sources"][-1]["id"] and s["data"]["advice"] in NBRO for s in plan["steps"])
    done = approve(team, plan)
    assert [(w["level"], w["issuer"]) for w in done["coordination"]["warnings"][-2:]] == [("red", "nbro"), ("amber", "nbro")]
    # The recorded warnings now prepare their public messages in turn.
    assert len(events(done, "warning_messages")) == 2


def test_a_situation_report_becomes_district_figures_and_unclear_reports_say_why(team):
    report(team, SITREP)
    plan = events(get(team), "report_reading")[-1]
    [step] = plan["steps"]
    assert step["action"] == "area_figures" and step["data"]["area"] == "district-nuwara-eliya" and step["data"]["people_affected"] == 6100  # current, not peak
    doc = report(team, "EXERCISE. Red for Kothmale and Walapane DS divisions according to a message we received.")
    keys = {p["trigger"]["key"] for p in events(get(team), "report_reading")}
    assert "report:" + doc["sources"][-1]["id"] not in keys  # no issuer or hazard named: nothing to record, so no card


def test_an_overdue_request_prepares_an_escalation_note_for_the_asker(team, monkeypatch):
    doc = command(team, "contributor", "ask", {"to": "rda", "kind": "road", "title": "Clear the Kothmale road", "priority": "urgent"})
    ask = doc["coordination"]["asks"][-1]
    assert events(doc, "escalation_note") == []
    later = lambda c: team_suggestions.timestamp(ask["created_at"]) + timedelta(hours=2)
    monkeypatch.setattr(team_suggestions, "business_now", later)
    team["app"].state.suggestions.scan(get(team)["id"])
    plan = events(get(team, "owner"), "escalation_note")[-1]
    assert plan["prepared_for"] == {"organisation": "division", "roles": ["agency_supervisor", "coordinator"]}
    [step] = plan["steps"]
    assert step["data"]["step"] == "escalate" and "Clear the Kothmale road" in step["data"]["note"] and "urgent priority" in step["data"]["note"]
    done = approve(team, plan, who="owner")
    assert done["coordination"]["asks"][-1]["escalations"][-1]["note"] == step["data"]["note"]
    team["app"].state.suggestions.scan(get(team)["id"])
    assert len(events(get(team, "owner"), "escalation_note")) == 1  # escalated by hand at this level: nothing new to suggest


def test_two_bodies_disagreeing_on_a_figure_asks_the_right_body_to_settle_it(team):
    command(team, "admin", "area_figures", {"area": "ds-nuwara-eliya-kothmale", "as_of": now_iso(), "people_affected": 450})
    command(team, "contributor", "area_figures", {"area": "ds-nuwara-eliya-kothmale", "as_of": now_iso(), "people_affected": 520})
    plan = events(get(team), "disagreement")[-1]
    assert plan["prepared_for"] == {"organisation": "dmc", "roles": ["coordinator"]}
    [step] = plan["steps"]
    assert step["data"]["to"] == "division" and step["data"]["title"] == "Settle people affected in Kothmale DS: 450 vs 520"
    assert "DMC / Emergency Operations Centre: 450" in plan["trigger"]["why"] and "Divisional Secretariat: 520" in plan["trigger"]["why"]
    done = approve(team, plan)
    assert done["coordination"]["asks"][-1]["title"] == step["data"]["title"] and done["coordination"]["asks"][-1]["to"] == "division"


def test_dismissed_suggestions_stay_dismissed_and_can_be_switched_off(team, monkeypatch):
    report(team, NBRO)
    plan = events(get(team), "report_reading")[-1]
    r = team["admin"].post(team["base"] + f"/plans/{plan['id']}/dismiss", json={"reason": "Recorded by hand"})
    assert r.status_code == 200
    team["app"].state.suggestions.scan(get(team)["id"])
    assert [p["status"] for p in events(get(team), "report_reading")] == ["dismissed"]
    team["app"].state.suggestions.enabled = False
    before = events(get(team), "report_reading")
    report(team, NBRO.replace("EXERCISE.", "EXERCISE update."))
    assert events(get(team), "report_reading") == before
