"""Public requests: anyone asks for help with words, photos and a place; code and the AI file, route, close or hold them."""
import base64
import uuid

import pytest
from fastapi.testclient import TestClient

from lastmile import public_requests as public
from lastmile.team_areas import DIVISIONS
from test_teams import command, get, team  # noqa: F401  (fixture)

KOTHMALE = DIVISIONS["ds-nuwara-eliya-kothmale"]["centre"]
GALLE = DIVISIONS["ds-galle-galle-four-gravets"]["centre"] if "ds-galle-galle-four-gravets" in DIVISIONS else next(v["centre"] for k, v in DIVISIONS.items() if k.startswith("ds-galle-"))
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64).decode()


@pytest.fixture
def people(team):
    team["app"].state.public_requests.per_hour = 100
    edl, _ = team["join"]("edl", "edl", ["agency_supervisor", "contributor"])
    team["edl"] = edl
    team["public"] = TestClient(team["app"])
    yield team
    edl.close()
    team["public"].close()


def ask_for_help(team, text, where=KOTHMALE, **extra):
    r = team["public"].post("/api/public/requests", json={"text": text, "latitude": where[0], "longitude": where[1], **extra})
    assert r.status_code == 201, r.text
    return r.json()


def inbox(team, code):
    return public.PublicRequests.find(team["app"].state.public_requests, code=code)


def suggestion(team, who="admin"):
    plans = [p for p in get(team, who)["coordination"]["plans"] if p["origin"] == "event" and p["trigger"]["kind"] == "public_request" and p["status"] == "pending"]
    return plans[-1] if plans else None


def test_a_power_complaint_is_filed_routed_to_ceb_and_tracked_to_done(people):
    team = people
    tracked = ask_for_help(team, "Our electricity is broken since the storm last night, 12 people in our lane have no power. Please fix it.", photos=[{"base64": PNG}], phone="0771234567", consent=True)
    code = tracked["code"]
    assert tracked["stage"] == "Being reviewed" and tracked["area"] == "Kothmale DS, Nuwara Eliya" and "text" not in tracked and "phone" not in tracked
    record = inbox(team, code)
    assert record["status"] == "filed" and record["reading"]["need"] == "power" and record["reading"]["people"] == 12 and record["model"]["model"] == "offline-rules"
    doc = get(team)
    [filed] = doc["coordination"]["public_requests"]
    assert filed["code"] == code and filed["phone"] == "0771234567" and filed["photos"][0]["mime"] == "image/png"
    plan = suggestion(team)
    [step] = plan["steps"]
    assert step["action"] == "ask" and step["data"]["to"] == "edl" and step["data"]["kind"] == "power" and step["data"]["quantity"] == 12
    assert step["data"]["public_request_id"] == filed["id"] and step["data"]["delivery_area"] == "ds-nuwara-eliya-kothmale"
    r = team["admin"].post(team["base"] + f"/plans/{plan['id']}/approve", json={"hash": plan["hash"], "request_id": str(uuid.uuid4())})
    assert r.status_code == 200, r.text
    assert team["public"].get(f"/api/public/requests/{code}").json()["stage"] == "Being handled by Electricity Distribution Lanka (CEB)"
    ask = r.json()["coordination"]["asks"][-1]
    for step_name, stage in (("accept", "Help on the way"), ("done", "Done")):
        command(team, "edl", "ask_update", {"ask_id": ask["id"], "step": step_name, "note": "Line repaired at the transformer." if step_name == "done" else "Crew on the way."})
        assert team["public"].get(f"/api/public/requests/{code}").json()["stage"] == stage
    # The phone number is for the lead body and the body helping, nobody else; the photo is for staff only.
    assert get(team, "edl")["coordination"]["public_requests"][0]["phone"] == "0771234567"
    assert get(team, "contributor")["coordination"]["public_requests"][0]["phone"] is None
    assert team["admin"].get(f"/api/teams/public-requests/{filed['id']}/photos/0").content.startswith(b"\x89PNG")
    assert team["public"].get(f"/api/teams/public-requests/{filed['id']}/photos/0").status_code == 401


def test_obvious_junk_is_closed_but_danger_never_is(people):
    team = people
    junk = ask_for_help(team, "Buy cheap phones today at www.example-shop.com discount!!")
    assert junk["stage"] == "Closed" and "advertisement" in junk["closed_reason"]
    risky = ask_for_help(team, "lol test, my grandmother is trapped under the wall, www.help.com")
    assert inbox(team, risky["code"])["status"] != "closed"  # a danger word is never closed automatically
    queue = team["admin"].get("/api/teams/public-requests").json()
    assert [r["code"] for r in queue["closed"]] == [junk["code"]]
    reopened = team["admin"].post(f"/api/teams/public-requests/{queue['closed'][0]['id']}/status", json={"status": "hold", "reason": "Checked: a real number"})
    assert reopened.status_code == 200 and reopened.json()["status"] == "hold"


def test_doubtful_requests_ask_the_person_one_question(people):
    team = people
    tracked = ask_for_help(team, "Please send something to us here quickly")
    record = inbox(team, tracked["code"])
    assert record["status"] == "verify" and tracked["question"] == "What is happening, and what help do you need?"
    answered = team["public"].post(f"/api/public/requests/{tracked['code']}/answer", json={"text": "Twenty people at the temple, we need food."})
    assert answered.status_code == 200 and answered.json()["question"] is None
    assert inbox(team, tracked["code"])["answer"]["text"].startswith("Twenty people")


def test_a_request_with_no_incident_waits_for_a_person_to_file_it_or_open_one(people):
    team = people
    tracked = ask_for_help(team, "Flood water entered 30 houses, 40 people need dry food and drinking water.", where=GALLE)
    record = inbox(team, tracked["code"])
    assert record["status"] == "hold" and "No open incident covers" in record["decision"]["reason"]
    queue = team["admin"].get("/api/teams/public-requests").json()
    assert [r["code"] for r in queue["hold"]] == [tracked["code"]]
    assert team["contributor"].get("/api/teams/public-requests").json()["hold"] == []  # coordinators handle the queue
    opened = team["admin"].post(f"/api/teams/public-requests/{record['id']}/open", json={"title": "Floods · Galle", "hazard": "flood", "participants": ["division", "ndrsc"]})
    assert opened.status_code == 200, opened.text
    doc = opened.json()
    assert doc["coordination"]["district"] == "Galle" and doc["coordination"]["public_requests"][0]["code"] == tracked["code"]
    assert inbox(team, tracked["code"])["status"] == "filed"
    plans = [p for p in doc["coordination"]["plans"] if p["origin"] == "event" and p["trigger"]["kind"] == "public_request"]
    assert plans and plans[-1]["steps"][0]["data"]["to"] == "ndrsc" and plans[-1]["steps"][0]["data"]["quantity"] == 40


def test_limits_photos_and_location(people):
    team = people
    bad = team["public"].post("/api/public/requests", json={"text": "Water is rising near the school", "latitude": KOTHMALE[0], "longitude": KOTHMALE[1], "photos": [{"base64": base64.b64encode(b"GIF89a....").decode()}]})
    assert bad.status_code == 422 and "JPEG, PNG or WebP" in bad.json()["detail"]
    assert team["public"].post("/api/public/requests", json={"text": "Water is rising", "latitude": 1.0, "longitude": 1.0}).status_code == 422
    assert team["public"].post("/api/public/requests", json={"text": "Water is rising", "area": "ds-nuwara-eliya-kothmale", "phone": "0771234567"}).status_code == 422  # no consent
    assert team["public"].post("/api/public/requests", json={"text": "Water is rising", "area": "ds-nuwara-eliya-kothmale"}, headers={"Origin": "https://elsewhere.example"}).status_code == 403
    team["app"].state.public_requests.per_hour = 2
    team["app"].state.public_requests.recent.clear()
    for _ in range(2):
        ask_for_help(team, "Water is rising near the school")
    r = team["public"].post("/api/public/requests", json={"text": "Water is rising near the school", "area": "ds-nuwara-eliya-kothmale"})
    assert r.status_code == 429 and "117" in r.json()["detail"]


def test_a_reader_count_without_words_in_the_message_is_removed():
    reading = public.PublicReading(relevant=True, need="food", people=500, people_quote="many people", urgency="high", summary="Food needed.", language="en")
    checked, notes = public.checked(reading, "Many people at the temple need food")
    assert checked.people is None and "removed" in notes[0]
    kept, _ = public.checked(public.PublicReading(relevant=True, need="food", people=80, people_quote="80 people", urgency="high", summary="x", language="en"), "We are 80 people here")
    assert kept.people == 80


def test_the_ai_reader_failing_falls_back_to_rules_and_says_so(people):
    team = people
    service = team["app"].state.public_requests
    def broken():
        def read(text, photos):
            raise RuntimeError("model unavailable")
        return read
    service.reader = broken
    tracked = ask_for_help(team, "No drinking water for 3 days, 25 people at the community hall.")
    record = inbox(team, tracked["code"])
    assert record["status"] == "filed" and record["reading"]["need"] == "water" and "rules reader was used" in record["reading_notes"][0]


def cards(team):
    return [p for p in get(team)["coordination"]["plans"] if p["origin"] == "event" and p["trigger"]["kind"] == "public_request" and p["status"] == "pending"]


def approve(team, plan):
    r = team["admin"].post(team["base"] + f"/plans/{plan['id']}/approve", json={"hash": plan["hash"], "request_id": str(uuid.uuid4())})
    assert r.status_code == 200, r.text
    return r.json()["coordination"]["asks"][-1]


def test_a_similar_report_waits_for_a_person_and_joins_only_when_they_say_it_is_the_same(people):
    team = people
    first = ask_for_help(team, "No electricity on our street since the storm, the line is down.")
    second = ask_for_help(team, "Power is cut in our village after the wind, please fix the line.")
    other = ask_for_help(team, "No drinking water at the community hall since morning.")
    items = {x["code"]: x for x in get(team)["coordination"]["public_requests"]}
    assert items[second["code"]]["similar_to"] == items[first["code"]]["id"] and not items[second["code"]].get("group_of") and not items[other["code"]].get("similar_to")
    assert sorted(p["steps"][0]["data"]["kind"] for p in cards(team)) == ["power", "water"]  # the possible repeat has no card of its own yet
    r = team["admin"].post(f"/api/teams/public-requests/{items[second['code']]['id']}/similar", json={"same": True})
    assert r.status_code == 200, r.text
    items = {x["code"]: x for x in r.json()["coordination"]["public_requests"]}
    assert items[first["code"]]["similar"] == 1 and items[second["code"]]["group_of"] == items[first["code"]]["id"] and "similar_to" not in items[second["code"]]
    approve(team, next(p for p in cards(team) if p["steps"][0]["data"]["kind"] == "power"))
    for code in (first["code"], second["code"]):  # a person joined them, so both see who is handling it
        assert team["public"].get(f"/api/public/requests/{code}").json()["stage"] == "Being handled by Electricity Distribution Lanka (CEB)"
    again = team["admin"].post(f"/api/teams/public-requests/{items[second['code']]['id']}/similar", json={"same": False})
    assert again.status_code == 409


def test_two_places_with_the_same_need_keep_their_own_quantity_and_completion(people):
    """Regression: two food requests at different places in one division shared one quantity, and finishing one finished both."""
    team = people
    temple = ask_for_help(team, "20 people have no electricity at Temple A after the storm.")
    school = ask_for_help(team, "60 people have no electricity at School B after the storm.")
    items = {x["code"]: x for x in get(team)["coordination"]["public_requests"]}
    assert items[school["code"]]["similar_to"] == items[temple["code"]]["id"]
    [card] = cards(team)
    assert card["steps"][0]["data"]["quantity"] == 20
    assert team["admin"].post(f"/api/teams/public-requests/{items[school['code']]['id']}/similar", json={"same": False}).status_code == 200
    assert sorted(p["steps"][0]["data"]["quantity"] for p in cards(team)) == [20, 60]  # each place gets its own request
    ask = approve(team, next(p for p in cards(team) if p["steps"][0]["data"]["quantity"] == 20))
    for step in ("accept", "done"):
        command(team, "edl", "ask_update", {"ask_id": ask["id"], "step": step, "note": "Restored at Temple A only." if step == "done" else "Crew on the way."})
    assert team["public"].get(f"/api/public/requests/{temple['code']}").json()["stage"] == "Done"
    assert team["public"].get(f"/api/public/requests/{school['code']}").json()["stage"] == "Being reviewed"
    # A report after the first was done is never joined to it.
    late = ask_for_help(team, "No electricity near Temple A again, the line is down.")
    late_item = next(x for x in get(team)["coordination"]["public_requests"] if x["code"] == late["code"])
    assert late_item.get("similar_to") != items[temple["code"]]["id"]


def test_a_request_with_a_link_that_asks_for_help_is_checked_not_closed(people):
    """Regression: a link made the rules close a real request, on the path used when the AI is unavailable."""
    team = people
    text = "We need food for 12 people at the school. Details: https://example.org/request"
    tracked = ask_for_help(team, text)
    record = inbox(team, tracked["code"])
    assert record["status"] == "verify" and record["model"]["model"] == "offline-rules" and "link" in record["decision"]["reason"]
    # The AI calling it junk is not enough either: the plea and the stated count send it to a person.
    junk_reading = public.PublicReading(relevant=False, need="other", urgency="low", summary="An advert.", language="en", junk_reason="Looks like an advertisement.")
    assert public.decide(junk_reading, text, "ds-nuwara-eliya-kothmale", {"id": "x"})[0] == "verify"
    assert public.decide(junk_reading, "Buy cheap phones today at www.example-shop.com discount!!", "ds-nuwara-eliya-kothmale", {"id": "x"})[0] == "closed"


def test_a_need_with_a_photo_link_and_no_plea_is_checked_not_closed(people):
    """Regression (recheck of 4a0553d): residents describe a problem without "help" or a number and share a link to a photo."""
    team = people
    tracked = ask_for_help(team, "No drinking water at the school. Photo: https://example.org/water")
    record = inbox(team, tracked["code"])
    assert record["status"] == "verify" and record["model"]["model"] == "offline-rules" and record["reading"]["need"] == "water"
    junk_reading = public.PublicReading(relevant=False, need="other", urgency="low", summary="An advert.", language="en", junk_reason="Looks like an advertisement.")
    for text in ["No electricity at Temple A after the storm. Details: https://example.org/outage", "Flood water entered our houses. Photo: https://example.org/flood"]:
        assert public.decide(public.rules_read(text), text, "ds-nuwara-eliya-kothmale", {"id": "x"})[0] == "verify"
        assert public.decide(junk_reading, text, "ds-nuwara-eliya-kothmale", {"id": "x"})[0] == "verify"
    # Advertising words still close a message that asks for nothing, even when it names a hazard; so does a bare link.
    for text in ["Buy cheap phones today at www.example-shop.com, discount for flood season!!", "Visit www.example.com today"]:
        assert public.decide(public.rules_read(text), text, "ds-nuwara-eliya-kothmale", {"id": "x"})[0] == "closed"


def test_the_persons_answer_reaches_the_incident_and_the_dashboard(people):
    """Regression: the answer was saved centrally but the incident's copy, which operators see, still had none."""
    team = people
    tracked = ask_for_help(team, "Please send something to us here quickly")
    assert inbox(team, tracked["code"])["incident_id"]
    assert team["public"].post(f"/api/public/requests/{tracked['code']}/answer", json={"text": "12 people need drinking water at the school."}).status_code == 200
    item = next(x for x in get(team)["coordination"]["public_requests"] if x["code"] == tracked["code"])
    assert item["answer"]["text"] == "12 people need drinking water at the school." and item["status"] == "verify" and item["history"][-1]["stage"] == "answered"
    attention = team["admin"].get("/api/teams/dashboard").json()["attention"]
    assert any(a["kind"] == "public" and "answered: 12 people need drinking water" in a["detail"] for a in attention)


def test_a_request_being_checked_always_asks_the_person_something(people):
    """Live finding: the AI reader can send a request for a check with no question, leaving the person nothing to answer."""
    team = people
    service = team["app"].state.public_requests
    service.reader = lambda: lambda text, photos: (public.PublicReading(relevant=True, need="other", urgency="normal", summary="Vague.", language="en", doubt="Too vague to act on."), {"role": "public reader", "model": "test-model"})
    tracked = ask_for_help(team, "please send something to us here quickly")
    assert inbox(team, tracked["code"])["status"] == "verify" and tracked["question"] == "What is happening, and what help do you need?"


def test_filing_needs_an_acting_role_and_a_request_is_in_one_incident(people):
    """Regression: an auditor could refile a request into a second incident."""
    team = people
    auditor, _ = team["join"]("auditor", "division", ["auditor"])
    tracked = ask_for_help(team, "Our electricity is broken since the storm, please fix the line.")
    record = inbox(team, tracked["code"])
    second = team["admin"].post("/api/teams/incidents", json={"title": "Second exercise", "district": "Nuwara Eliya", "participants": ["division"]}).json()
    r = auditor.post(f"/api/teams/public-requests/{record['id']}/file", json={"incident_id": second["id"]})
    assert r.status_code == 403, r.text
    r = team["admin"].post(f"/api/teams/public-requests/{record['id']}/file", json={"incident_id": second["id"]})
    assert r.status_code == 409 and "another incident" in r.json()["detail"]
    assert inbox(team, tracked["code"])["incident_id"] == record["incident_id"]
    assert auditor.post(f"/api/teams/public-requests/{record['id']}/status", json={"status": "closed", "reason": "no"}).status_code == 403
    auditor.close()


def test_a_district_stays_a_district(people):
    """Regression: choosing a district recorded one of its divisions; the district itself is now kept."""
    team = people
    r = team["public"].post("/api/public/requests", json={"text": "Flood water entered houses, 40 people need dry food.", "area": "district-mannar"})
    assert r.status_code == 201 and r.json()["area"] == "Mannar District"
    assert inbox(team, r.json()["code"])["area"] == "district-mannar"


def test_the_tracker_never_follows_a_request_that_is_not_linked_to_it(people):
    """Regression: a request with no group matched unrelated requests that have no public link."""
    team = people
    command(team, "contributor", "ask", {"to": "rda", "kind": "road", "title": "An unrelated request with no public link"})
    tracked = ask_for_help(team, "Our electricity is broken since the storm, please fix the line.")
    assert team["public"].get(f"/api/public/requests/{tracked['code']}").json()["stage"] == "Being reviewed"


def test_the_public_requests_page_lists_every_state_with_its_incident_and_stage(people):
    """The sidebar page: requests in incidents carry the incident, their request and what the person sees; the phone stays with the handlers."""
    team = people
    tracked = ask_for_help(team, "Our electricity is broken since the storm last night, 12 people in our lane have no power.", phone="0771234567", consent=True)
    junk = ask_for_help(team, "Buy cheap phones today at www.example-shop.com discount!!")
    approve(team, suggestion(team))
    queue = team["admin"].get("/api/teams/public-requests").json()
    assert set(queue) == {"hold", "verify", "filed", "closed"}
    [filed] = queue["filed"]
    assert filed["code"] == tracked["code"] and filed["incident_title"] == "Kotmale flood exercise" and filed["incident_id"]
    assert filed["ask"]["to"] == "edl" and filed["stage"] == "Being handled by Electricity Distribution Lanka (CEB)" and filed["phone"] == "0771234567"
    assert [r["code"] for r in queue["closed"]] == [junk["code"]]
    # A participant who is not handling it sees the request in its incident, but not the phone number or the closed queue.
    mine = team["contributor"].get("/api/teams/public-requests").json()
    assert [r["code"] for r in mine["filed"]] == [tracked["code"]] and mine["filed"][0]["phone"] is None and mine["closed"] == []
