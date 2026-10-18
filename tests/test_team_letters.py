"""Official letters: issued from a template, shared for acknowledgement, never evidence; and the flood request end to end."""
import uuid

from lastmile import team_drafting
from lastmile.team_agent import TeamTools
from test_teams import command, get, source, team  # noqa: F401  (fixture)

FLOOD = ("Kothmale and Walapane at Red, Hanguranketa Amber, river at major flood. "
         "Send a report to NDRSC and the Divisional Secretariat asking dry rations for 1,200 families, and notify them.")


def chat(team, text, who="admin"):
    r = team[who].post(team["base"] + "/chat", json={"text": text, "request_id": str(uuid.uuid4())})
    assert r.status_code == 200, r.text
    return r.json()


def letter(team, who="admin", to=("ndrsc",), text="The Kotmale road is closed. Please plan deliveries through Ramboda."):
    doc = command(team, who, "document", {"kind": "letter", "to": list(to), "subject": "Road closure at Kotmale", "text": text})
    return doc["coordination"]["documents"][-1]


def test_a_letter_is_issued_from_its_template_with_reference_and_signature(team):
    first = letter(team)
    assert first["status"] == "issued" and first["kind"] == "letter" and first["to"] == ["ndrsc"]
    assert first["reference"].startswith("LM/DMC/") and first["reference"].endswith("-01")
    body = first["body"]
    assert body.startswith("DMC / Emergency Operations Centre\nRef: " + first["reference"])
    assert "To: NDRSC relief coordination" in body and "Subject: Road closure at Kotmale" in body and "Please plan deliveries through Ramboda." in body
    assert "\nCoordinator\nWorkspace administrator, DMC / Emergency Operations Centre" in body  # the signer's name and job title
    assert letter(team)["reference"].endswith("-02")
    command(team, "admin", "document", {"kind": "letter", "to": ["dmc"], "subject": "To ourselves", "text": "A letter to our own organisation is refused."}, 422)
    command(team, "admin", "document", {"kind": "letter", "to": ["water"], "subject": "Not here", "text": "The water board is not in this incident yet."}, 422)
    command(team, "admin", "document", {"kind": "situation", "to": ["ndrsc"], "audience": "Partners"}, 422)  # only letters are addressed
    observer, _ = team["join"]("observer", "rda", ["auditor"])
    team["observer"] = observer
    command(team, "observer", "document", {"kind": "letter", "to": ["ndrsc"], "subject": "Observers", "text": "Observers do not issue letters for their body."}, 403)


def test_sharing_puts_the_letter_in_reports_for_acknowledgement_but_never_as_evidence(team):
    issued = letter(team, text="We need 40 tents at Kotmale school for 120 people tonight.")
    doc = command(team, "admin", "share_document", {"document_id": issued["id"], "recipients": ["ndrsc", "division"]})
    shared = doc["sources"][-1]
    assert shared["status"] == "issued" and shared["document_id"] == issued["id"] and shared["text"] == issued["body"]
    assert [a["organisation"] for a in shared["acknowledgements"]] == ["division", "ndrsc"]
    assert doc["coordination"]["documents"][-1]["shared"][0]["recipients"] == ["division", "ndrsc"]
    note = next(n for n in get(team, "finance")["coordination"]["notifications"] if n["source_id"] == shared["id"])
    command(team, "finance", "acknowledge", {"notification_id": note["id"]})
    # A request may point at the letter, but the letter cannot stand as evidence for anything.
    command(team, "contributor", "ask", {"to": "ndrsc", "kind": "shelter", "title": "Tents for Kotmale school", "source_id": shared["id"]})
    command(team, "contributor", "request", {"title": "Tents", "kind": "shelter", "quantity": 120, "unit": "people", "source_id": shared["id"]}, 409)
    team["coordinator"], _ = team["join"]("second", "dmc", ["coordinator"])
    command(team, "coordinator", "review_source", {"source_id": shared["id"], "decision": "accepted"}, 409)  # not a report awaiting review
    command(team, "contributor", "area_status", {"area": "ds-nuwara-eliya-kothmale", "sector": "shelter", "level": "disrupted", "note": "Tents needed", "source_id": shared["id"]}, 422)
    command(team, "contributor", "share_document", {"document_id": issued["id"], "recipients": ["rda"]}, 403)  # only the issuing body shares it
    report = source(team, "EXERCISE road report from Kotmale: closed.")
    command(team, "admin", "share_document", {"document_id": "document_missing", "recipients": ["ndrsc"]}, 404)
    assert report


def test_offline_drafting_keeps_the_points_and_labels_itself():
    subject, text, model = team_drafting.offline("Flood in Kalutara", ["Ingiriya is at Amber", "We need 200 mats."], ["NDRSC"], {})
    assert subject == "Flood in Kalutara" and text == "Ingiriya is at Amber.\n\nWe need 200 mats." and model["model"] == "offline-template"


def test_the_flood_sentence_asks_once_then_prepares_letter_share_and_request(team):
    doc = chat(team, FLOOD)
    reply = doc["coordination"]["chat"]["messages"][-1]["text"]
    assert "official bulletin" in reply and "own assessment" in reply and "Kothmale Red" in reply
    assert doc["coordination"]["plans"] == []  # nothing prepared before the question is answered
    doc = chat(team, "It is our own assessment.")
    plan = doc["coordination"]["plans"][-1]
    assert [s["action"] for s in plan["steps"]] == ["document", "share_document", "ask"]
    letter_step, share_step, ask_step = plan["steps"]
    assert letter_step["data"]["to"] == ["ndrsc", "division"] and letter_step["data"]["subject"] == "Flood situation in Nuwara Eliya District: request for dry rations"
    text = letter_step["data"]["text"]
    assert "Status by area (our own assessment, not an official warning): Kothmale DS: Red; Walapane DS: Red; Hanguranketa DS: Amber." in text
    assert "river at major flood" in text and "Request: we request dry rations for 1,200 families." in text
    assert share_step["data"]["document_id"] == {"$step": "s1", "record": "documents"}
    assert ask_step["data"] | {} == {"to": "ndrsc", "kind": "supplies", "title": "Dry rations for 1,200 families", "quantity": 1200, "unit": "families",
                                     "priority": "high", "note": "Details are in the shared letter.", "source_id": {"$step": "s2", "record": "sources"}}
    assert not plan["checks"]  # every number is the person's own, from the earlier message
    assert plan["steps"][1]["summary"] == "Share the letter from step 1 with NDRSC relief coordination, Divisional Secretariat and ask them to acknowledge"
    done = team["admin"].post(team["base"] + f"/plans/{plan['id']}/approve", json={"hash": plan["hash"], "request_id": str(uuid.uuid4())})
    assert done.status_code == 200, done.text
    after = done.json()
    issued, shared, ask = after["coordination"]["documents"][-1], after["sources"][-1], after["coordination"]["asks"][-1]
    assert issued["status"] == "issued" and shared["document_id"] == issued["id"] and shared["status"] == "issued"
    assert ask["source_id"] == shared["id"] and ask["to"] == "ndrsc" and ask["quantity"] == 1200 and ask["priority"] == "high"
    assert {a["organisation"] for a in shared["acknowledgements"]} == {"ndrsc", "division"}
    assert sum("prepared with AI, approved by Coordinator" in e["detail"] for e in after["events"]) == 3


def test_an_official_level_is_named_as_such_in_the_letter(team):
    plan = chat(team, "As per the NBRO bulletin, Kothmale is at Red. Send a letter to the Divisional Secretariat and notify them.")["coordination"]["plans"][-1]
    assert [s["action"] for s in plan["steps"]] == ["document", "share_document"]
    assert "Status by area (as stated in the official bulletin): Kothmale DS: Red." in plan["steps"][0]["data"]["text"]


def test_the_gemini_drafter_uses_the_stronger_model_and_cannot_add_numbers(team):
    class Writer:
        model_verifier = "drafting-model-test"
        def write_structured(self, system, user, schema):
            assert "Do not add any fact" in system and schema is team_drafting.LetterText
            return schema(subject="Flood in Nuwara Eliya", paragraphs=["Kothmale is at Red.", "We request 5,000 dry ration packs."])
    actor = team["admin"].get("/api/teams/me").json()["user"]
    tools = TeamTools(team["app"].state.team_workspace, actor, get(team)["id"], lambda **kwargs: None)
    tools.drafter = team_drafting.gemini(Writer())
    tools.submitted_text = "Write to NDRSC: Kothmale is at Red (our own assessment), we need dry rations."
    tools.draft_letter(to=["ndrsc"], subject="Flood", points=["Kothmale is at Red", "we need dry rations"])
    assert tools.models[-1]["model"] == "drafting-model-test" and tools.models[-1]["role"] == "drafter"
    plan = tools.make_plan(tools._doc())
    assert [c["level"] for c in plan["checks"]] == ["block"] and "5000" in plan["checks"][0]["message"]  # the drafter invented a number
