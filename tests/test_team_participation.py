"""Which organisations take part: any team opens an incident, invites bodies, others ask to join."""
from test_teams import team  # noqa: F401  (fixture)
from test_team_demo import act, choose


def people(team, monkeypatch):
    monkeypatch.setenv("LASTMILE_TEAM_DEMO", "true")
    return {name: choose(team, org, job) for name, (org, job) in {
        "engineer": ("water", "field_engineer"), "water_lead": ("water", "service_supervisor"), "duty": ("dmc", "duty_officer"),
        "roads": ("rda", "technical_officer"), "road_lead": ("rda", "road_engineer"), "relief": ("ndrsc", "programme_officer"),
        "observer": ("district", "observer"),
    }.items()}


def test_any_team_opens_an_incident_and_only_invited_bodies_enter(team, monkeypatch):
    p = people(team, monkeypatch)
    doc = p["engineer"].post("/api/teams/incidents", json={"title": "Pipe burst exercise", "district": "Nuwara Eliya", "participants": ["dmc"], "hazard": "flood"}).json()
    base = "/api/teams/incidents/" + doc["id"]
    c = doc["coordination"]
    assert c["lead"] == "water" and c["hazard"] == "flood" and c["participants"] == ["dmc", "water"]
    assert c["participation_view"]["organisations"][1] == {**c["participation_view"]["organisations"][1], "organisation": "water", "via": "created"}
    assert p["duty"].get(base).status_code == 200
    # RDA was not invited: it sees the card (title, bodies, lead) but not the records.
    assert p["roads"].get(base).status_code == 403
    card = next(x for x in p["roads"].get("/api/teams/incidents").json() if x["id"] == doc["id"])
    assert card["member"] is False and card["lead"] == "water" and card["active_participants"] == ["dmc", "water"] and "reports" not in card
    board = p["roads"].get("/api/teams/dashboard").json()
    assert [x["id"] for x in board["others"]] == [doc["id"]] and all(i["id"] != doc["id"] for i in board["incidents"])
    assert board["incident_types"]["suggested"]["landslide"][-1] == "rda"
    assert p["observer"].post("/api/teams/incidents", json={"title": "Observer cannot open", "district": "Nuwara Eliya", "participants": ["district"]}).status_code == 403


def test_ask_to_join_then_a_lead_answers(team, monkeypatch):
    p = people(team, monkeypatch)
    doc = p["duty"].post("/api/teams/incidents", json={"title": "Landslide exercise", "district": "Nuwara Eliya", "participants": ["dmc", "water"]}).json()
    base = "/api/teams/incidents/" + doc["id"]
    join = lambda who, note=None: p[who].post(base + "/join", json={"note": note})
    assert join("observer").status_code == 403
    card = join("roads", "Our crew is already clearing the access road").json()
    assert card["my_join_request"] == "pending" and card["member"] is False
    assert join("roads").status_code == 409
    items = [i for i in p["water_lead"].get("/api/teams/dashboard").json()["attention"] if i["kind"] == "join"]
    assert items[0]["title"] == "RDA asks to join" and items[0]["detail"] == "Our crew is already clearing the access road"
    assert [i for i in p["engineer"].get("/api/teams/dashboard").json()["attention"] if i["kind"] == "join"] == []  # not a lead
    request_id = p["duty"].get(base).json()["coordination"]["participation_view"]["join_requests"][0]["id"]
    act(p["engineer"], base, "join_decision", {"request_id": request_id, "decision": "approve"}, 403)
    act(p["water_lead"], base, "join_decision", {"request_id": request_id, "decision": "approve"})
    assert p["roads"].get(base).status_code == 200
    joined = {o["organisation"]: o for o in p["roads"].get(base).json()["coordination"]["participation_view"]["organisations"]}
    assert joined["rda"]["via"] == "joined" and joined["rda"]["by_org"] == "water"
    # A declined request needs a reason, and the body can ask again later.
    assert join("relief").status_code == 200
    second = [r for r in p["duty"].get(base).json()["coordination"]["participation_view"]["join_requests"] if r["status"] == "pending"][0]["id"]
    act(p["duty"], base, "join_decision", {"request_id": second, "decision": "decline"}, 422)
    act(p["duty"], base, "join_decision", {"request_id": second, "decision": "decline", "reason": "No relief task yet"})
    assert next(x for x in p["relief"].get("/api/teams/incidents").json() if x["id"] == doc["id"])["my_join_request"] == "declined"
    assert join("relief").status_code == 200


def test_leads_add_bodies_directly_or_by_sending_a_request(team, monkeypatch):
    p = people(team, monkeypatch)
    doc = p["duty"].post("/api/teams/incidents", json={"title": "Access exercise", "district": "Nuwara Eliya", "participants": ["dmc", "water"]}).json()
    base = "/api/teams/incidents/" + doc["id"]
    act(p["engineer"], base, "participant", {"organisation": "ndrsc"}, 403)  # members ask a lead
    act(p["water_lead"], base, "participant", {"organisation": "ndrsc", "reason": "Relief stock needed"})
    act(p["water_lead"], base, "participant", {"organisation": "ndrsc"}, 409)
    # Sending a request to a body outside the incident adds it, if the sender is a lead.
    act(p["engineer"], base, "ask", {"to": "rda", "kind": "road", "title": "Clear the access road"}, 422)
    act(p["duty"], base, "ask", {"to": "rda", "kind": "road", "title": "Clear the access road"})
    record = {o["organisation"]: o for o in p["roads"].get(base).json()["coordination"]["participation_view"]["organisations"]}
    assert record["rda"]["via"] == "request" and record["ndrsc"]["reason"] == "Relief stock needed"


def test_stand_down_keeps_history_and_reading_but_stops_actions(team, monkeypatch):
    p = people(team, monkeypatch)
    doc = p["duty"].post("/api/teams/incidents", json={"title": "Stand down exercise", "district": "Nuwara Eliya", "participants": ["dmc", "water", "rda"]}).json()
    base = "/api/teams/incidents/" + doc["id"]
    act(p["duty"], base, "stand_down", {"organisation": "dmc", "reason": "Lead cannot leave"}, 422)
    act(p["roads"], base, "stand_down", {"organisation": "rda", "reason": "Not a lead"}, 403)
    act(p["water_lead"], base, "stand_down", {"organisation": "rda", "reason": "Water is not RDA's lead"}, 403)
    act(p["road_lead"], base, "stand_down", {"organisation": "rda", "reason": "Road cleared; nothing left for RDA"})
    view = p["roads"].get(base).json()["coordination"]["participation_view"]
    assert "rda" not in view["active"] and next(o for o in view["organisations"] if o["organisation"] == "rda")["status"] == "stood_down"
    act(p["roads"], base, "contribute", {"title": "Late note", "text": "EXERCISE note after standing down.", "observed_at": doc["created_at"]}, 403)
    act(p["duty"], base, "ask", {"to": "rda", "kind": "road", "title": "Reopen the road works"})  # a lead's request brings RDA back
    assert "rda" in p["roads"].get(base).json()["coordination"]["participation_view"]["active"]
    events = [e["action"] for e in p["duty"].get(base).json()["events"]]
    assert "stand_down" in events and "ask" in events


def test_partners_have_limited_access(team, monkeypatch):
    p = people(team, monkeypatch)
    lead, volunteer = choose(team, "partner", "partner_coordinator"), choose(team, "partner", "partner_volunteer")
    assert lead.post("/api/teams/incidents", json={"title": "Partner cannot open", "district": "Nuwara Eliya", "participants": ["partner"]}).status_code == 403
    doc = p["duty"].post("/api/teams/incidents", json={"title": "Partner exercise", "district": "Nuwara Eliya", "participants": ["dmc", "ndrsc", "partner"]}).json()
    base = "/api/teams/incidents/" + doc["id"]
    act(p["duty"], base, "ask", {"kind": "funding", "title": "Relief cash", "amount": "1000"})
    ask = act(p["duty"], base, "ask", {"to": "partner", "kind": "supplies", "title": "Hygiene kits for the hall", "quantity": 100, "unit": "kits"})["coordination"]["asks"][-1]
    seen = lead.get(base).json()["coordination"]
    # The shared picture, but never funding.
    assert [a["kind"] for a in seen["asks"]] == ["supplies"] and seen["finance"] == [] and seen["schemes"] == []
    assert "accept" in next(a for a in seen["asks"] if a["id"] == ask["id"])["can"]
    act(lead, base, "ask_update", {"ask_id": ask["id"], "step": "accept"})
    act(volunteer, base, "ask_update", {"ask_id": ask["id"], "step": "start"})
    act(lead, base, "ask", {"to": "ndrsc", "kind": "funding", "title": "Cash please", "amount": "500"}, 403)
    act(lead, base, "participant", {"organisation": "rda", "reason": "Road access"}, 403)  # partners do not manage who takes part
    act(lead, base, "area_status", {"area": "ds-nuwara-eliya-kothmale", "sector": "food", "level": "restoring", "note": "Kits arriving", "public": True}, 403)
    act(lead, base, "area_status", {"area": "ds-nuwara-eliya-kothmale", "sector": "food", "level": "restoring", "note": "Kits arriving"})
