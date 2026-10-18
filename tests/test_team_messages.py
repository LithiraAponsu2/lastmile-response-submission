"""Public messages: SMS, email and press releases from templates, approved, then sent (recorded, never transmitted)."""
import uuid

from test_teams import team  # noqa: F401  (fixture)
from test_team_demo import act, choose


def setup(team, monkeypatch):
    monkeypatch.setenv("LASTMILE_TEAM_DEMO", "true")
    people = {name: choose(team, org, job) for name, (org, job) in {
        "duty": ("dmc", "duty_officer"), "information": ("dmc", "information_officer"),
        "secretary": ("division", "divisional_secretary"), "observer": ("rda", "observer"),
    }.items()}
    doc = people["duty"].post("/api/teams/incidents", json={"title": "Kotmale flood practice", "district": "Nuwara Eliya", "participants": ["dmc", "division", "rda"]}).json()
    return people, "/api/teams/incidents/" + doc["id"]


def last_document(doc):
    return doc["coordination"]["documents"][-1]


def test_sms_from_template_is_approved_then_sent_to_areas(team, monkeypatch):
    p, base = setup(team, monkeypatch)
    doc = act(p["duty"], base, "document", {"kind": "sms", "template_id": "tpl-evacuate-now", "details": "Water is over the Kotmale road.", "areas": ["district-nuwara-eliya"]})
    sms = last_document(doc)
    assert sms["text"].startswith("EVACUATE NOW: Nuwara Eliya District. Water is over the Kotmale road.") and sms["text"].endswith("Help: 117")
    assert sms["status"] == "draft" and sms["template_name"] == "Evacuate now" and sms["planned"]["areas"] == ["district-nuwara-eliya"]
    # Sending needs approval first; only a communications reviewer approves.
    act(p["duty"], base, "send_document", {"document_id": sms["id"], "hash": sms["hash"], "areas": ["district-nuwara-eliya"]}, 409)
    act(p["duty"], base, "approve_document", {"document_id": sms["id"], "hash": sms["hash"]}, 403)
    # Another change in between does not make a message stale (it is its own text, not a snapshot).
    act(p["secretary"], base, "contribute", {"title": "Road report", "text": "EXERCISE road under water at Kotmale.", "observed_at": "2026-10-01T00:00:00Z"})
    act(p["information"], base, "approve_document", {"document_id": sms["id"], "hash": sms["hash"]})
    act(p["observer"], base, "send_document", {"document_id": sms["id"], "hash": sms["hash"], "areas": ["district-nuwara-eliya"]}, 403)
    act(p["duty"], base, "send_document", {"document_id": sms["id"], "hash": sms["hash"], "areas": []}, 422)
    act(p["duty"], base, "send_document", {"document_id": sms["id"], "hash": sms["hash"], "areas": ["district-atlantis"]}, 422)
    sent = last_document(act(p["duty"], base, "send_document", {"document_id": sms["id"], "hash": sms["hash"], "areas": ["district-nuwara-eliya", "district-kandy"]}))
    assert sent["status"] == "sent" and sent["sent_to"]["areas"] == ["district-kandy", "district-nuwara-eliya"]
    assert sent["delivery"] == "simulated_no_transmission" and sent["reach"] > 1000000  # district populations from the Population list
    act(p["duty"], base, "send_document", {"document_id": sms["id"], "hash": sms["hash"], "areas": ["district-kandy"]}, 409)  # already sent
    # Once sent, the SMS is on the public status page, with the areas it went to; no login needed.
    from fastapi.testclient import TestClient
    public = TestClient(team["app"]).get("/api/public/status").json()["messages"]
    assert [(m["kind"], m["areas"], m["issuer"]) for m in public] == [("sms", ["district-kandy", "district-nuwara-eliya"], "DMC / Emergency Operations Centre")]
    assert public[0]["text"] == sms["text"] and "hash" not in public[0] and "sent_by" not in public[0]


def test_press_release_goes_to_listed_media_and_typed_text_wins(team, monkeypatch):
    p, base = setup(team, monkeypatch)
    act(p["duty"], base, "document", {"kind": "press_release", "template_id": "tpl-evacuate-now"}, 422)  # an SMS template
    act(p["duty"], base, "document", {"kind": "email", "template_id": "tpl-no-such-template"}, 422)
    act(p["duty"], base, "document", {"kind": "situation", "template_id": "tpl-evacuate-now"}, 422)  # reports are generated, not templated
    act(p["secretary"], base, "document", {"kind": "sms", "text": "Hello"}, 403)
    act(p["duty"], base, "document", {"kind": "sms", "text": "x" * 481}, 422)
    doc = act(p["duty"], base, "document", {"kind": "press_release", "template_id": "tpl-media-release-situation", "details": "Two safety centres are open in Kothmale."})
    release = last_document(doc)
    assert release["subject"] == "Media release: Kotmale flood practice" and "Two safety centres are open in Kothmale." in release["text"]
    assert release["body"].startswith("Subject: Media release: Kotmale flood practice\n\nMEDIA RELEASE")
    act(p["information"], base, "approve_document", {"document_id": release["id"], "hash": release["hash"]})
    act(p["duty"], base, "send_document", {"document_id": release["id"], "hash": release["hash"], "outlets": ["media-not-listed"]}, 422)
    sent = last_document(act(p["duty"], base, "send_document", {"document_id": release["id"], "hash": release["hash"], "outlets": ["media-national-radio-news", "media-tamil-daily-newspaper"]}))
    assert sent["status"] == "sent" and sent["sent_to"]["outlets"] == ["media-national-radio-news", "media-tamil-daily-newspaper"] and sent["reach"] is None
    # Emails to newsrooms stay internal; press releases and SMS appear publicly, newest first; drafts never do.
    email = last_document(act(p["duty"], base, "document", {"kind": "email", "template_id": "tpl-press-briefing-notice", "details": "Briefing at 15:00."}))
    act(p["information"], base, "approve_document", {"document_id": email["id"], "hash": email["hash"]})
    act(p["duty"], base, "send_document", {"document_id": email["id"], "hash": email["hash"], "outlets": ["media-wire-news-agency"]})
    from fastapi.testclient import TestClient
    public = TestClient(team["app"]).get("/api/public/status").json()["messages"]
    assert [m["kind"] for m in public] == ["press_release"] and public[0]["subject"] == "Media release: Kotmale flood practice"
    typed = last_document(act(p["duty"], base, "document", {"kind": "sms", "template_id": "tpl-flood-warning", "text": "Exactly my words.", "language": "si"}))
    assert typed["text"] == "Exactly my words." and typed["language"] == "si"


def test_chat_drafts_an_sms_from_a_template_for_review(team, monkeypatch):
    p, base = setup(team, monkeypatch)
    reply = p["duty"].post(base + "/chat", json={"text": "Create an SMS for Kothmale saying go to the temple hall now, then send it", "request_id": str(uuid.uuid4())})
    assert reply.status_code == 200, reply.text
    chat = reply.json()["coordination"]["chat"]
    plan = reply.json()["coordination"]["plans"][-1]
    step = plan["steps"][0]
    assert step["action"] == "document" and step["data"]["kind"] == "sms" and step["data"]["template_id"] == "tpl-evacuate-now"
    assert step["data"]["areas"] == ["ds-nuwara-eliya-kothmale"]  # a DS division named in the request; otherwise the whole district
    assert step["data"]["text"].startswith("EVACUATE NOW: Kothmale DS.")
    assert "Go to the temple hall now." in step["data"]["text"]
    assert "SMS draft for Kothmale DS" in chat["messages"][-1]["text"] and not plan["checks"]
    # Applying the plan creates the draft; nothing is sent.
    applied = p["duty"].post(base + f"/plans/{plan['id']}/approve", json={"hash": plan["hash"], "request_id": str(uuid.uuid4())})
    assert applied.status_code == 200, applied.text
    draft = last_document(applied.json())
    assert draft["status"] == "draft" and draft["text"] == step["data"]["text"]
    # The tools list templates and media desks for a real model.
    templates = team["app"].state.reference.dataset("templates")["records"]
    assert {t["channel"] for t in templates} == {"sms", "email", "press_release", "letter"}
