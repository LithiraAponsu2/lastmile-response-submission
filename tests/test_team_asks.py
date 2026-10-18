"""Organisation-to-organisation requests: send, accept/decline, deliver, confirm; funding adds recommend/approve."""
from test_teams import team  # noqa: F401  (fixture)
from test_team_demo import act, choose


def setup(team, monkeypatch):
    monkeypatch.setenv("LASTMILE_TEAM_DEMO", "true")
    people = {name: choose(team, org, job) for name, (org, job) in {
        "secretary": ("division", "divisional_secretary"), "relief": ("division", "relief_officer"),
        "supervisor": ("water", "service_supervisor"), "engineer": ("water", "field_engineer"),
        "duty": ("dmc", "duty_officer"), "observer": ("rda", "observer"),
        "coordinator": ("district", "district_coordinator"), "finance": ("ndrsc", "finance_approver"), "programme": ("ndrsc", "programme_officer"),
    }.items()}
    doc = people["duty"].post("/api/teams/incidents", json={"title": "Requests exercise", "district": "Nuwara Eliya", "participants": ["dmc", "district", "division", "ndrsc", "water", "rda"]}).json()
    return people, "/api/teams/incidents/" + doc["id"]


def ask_of(doc, ask_id):
    return next(a for a in doc["coordination"]["asks"] if a["id"] == ask_id)


def test_request_lifecycle_between_two_bodies(team, monkeypatch):
    p, base = setup(team, monkeypatch)
    doc = act(p["relief"], base, "ask", {"to": "water", "kind": "water", "title": "Two water bowsers for the hall", "quantity": 2, "unit": "bowsers", "note": "40 people, no water"})
    ask = doc["coordination"]["asks"][-1]
    assert (ask["from"], ask["to"], ask["status"], ask["quantity"]) == ("division", "water", "asked", 2)
    # Each viewer is told exactly which steps they can take.
    assert act(p["supervisor"], base, "ask_update", {"ask_id": ask["id"], "step": "comment", "note": "Checking bowsers"})
    seen = lambda who: ask_of(p[who].get(base).json(), ask["id"])["can"]
    assert {"accept", "decline"} <= set(seen("supervisor"))
    assert "accept" not in seen("engineer") and seen("observer") == []
    act(p["engineer"], base, "ask_update", {"ask_id": ask["id"], "step": "accept"}, 403)
    act(p["observer"], base, "ask_update", {"ask_id": ask["id"], "step": "comment", "note": "Observing only"}, 403)
    act(p["supervisor"], base, "ask_update", {"ask_id": ask["id"], "step": "accept"})
    # Anyone in the receiving body delivers; the asking side confirms.
    act(p["engineer"], base, "ask_update", {"ask_id": ask["id"], "step": "start"})
    act(p["engineer"], base, "ask_update", {"ask_id": ask["id"], "step": "done", "note": "Bowsers parked at the hall"})
    act(p["engineer"], base, "ask_update", {"ask_id": ask["id"], "step": "confirm"}, 403)
    act(p["relief"], base, "ask_update", {"ask_id": ask["id"], "step": "reopen"}, 422)  # a reason is required
    act(p["relief"], base, "ask_update", {"ask_id": ask["id"], "step": "reopen", "note": "Only one bowser arrived"})
    act(p["engineer"], base, "ask_update", {"ask_id": ask["id"], "step": "done", "note": "Second bowser arrived"})
    final = ask_of(act(p["secretary"], base, "ask_update", {"ask_id": ask["id"], "step": "confirm"}), ask["id"])
    assert final["status"] == "confirmed" and final["can"] == []
    assert [h["step"] for h in final["history"]] == ["asked", "comment", "accept", "start", "done", "reopen", "done", "confirm"]
    events = p["duty"].get(base).json()["events"]
    assert any(e["action"] == "ask" and "division → water" in e["detail"] for e in events)


def test_requests_validate_recipient_decline_reason_and_cancel(team, monkeypatch):
    p, base = setup(team, monkeypatch)
    act(p["relief"], base, "ask", {"to": "division", "kind": "water", "title": "Ask ourselves"}, 422)
    act(p["relief"], base, "ask", {"to": "health", "kind": "water", "title": "Inactive body"}, 422)
    act(p["observer"], base, "ask", {"to": "water", "kind": "water", "title": "Observer cannot send"}, 403)
    act(p["relief"], base, "ask", {"to": "water", "kind": "water", "title": "Bad quantity", "quantity": 0}, 422)
    ask = act(p["secretary"], base, "ask", {"to": "rda", "kind": "road", "title": "Clear the access road"})["coordination"]["asks"][-1]
    rda = choose(team, "rda", "road_engineer")
    act(rda, base, "ask_update", {"ask_id": ask["id"], "step": "decline"}, 422)
    declined = ask_of(act(rda, base, "ask_update", {"ask_id": ask["id"], "step": "decline", "note": "Crew committed elsewhere until 18:00"}), ask["id"])
    assert declined["status"] == "declined" and declined["can"] == []
    other = act(p["secretary"], base, "ask", {"to": "water", "kind": "supplies", "title": "Jerry cans"})["coordination"]["asks"][-1]
    act(p["supervisor"], base, "ask_update", {"ask_id": other["id"], "step": "cancel", "note": "Not ours"}, 403)
    assert ask_of(act(p["duty"], base, "ask_update", {"ask_id": other["id"], "step": "cancel", "note": "Duplicate request"}), other["id"])["status"] == "cancelled"


def test_funding_request_needs_recommendation_then_separate_approval(team, monkeypatch):
    p, base = setup(team, monkeypatch)
    act(p["relief"], base, "ask", {"kind": "funding", "title": "Relief packs", "amount": "250000.00"}, 422)  # over the per-request limit
    ask = act(p["relief"], base, "ask", {"kind": "funding", "title": "Relief packs for 40 people", "amount": "20000.00"})["coordination"]["asks"][-1]
    assert ask["to"] == "ndrsc" and ask["amount"] == "20000.00"
    act(p["finance"], base, "ask_update", {"ask_id": ask["id"], "step": "accept"}, 403)  # not recommended yet
    act(p["coordinator"], base, "ask_update", {"ask_id": ask["id"], "step": "recommend"})
    act(p["programme"], base, "ask_update", {"ask_id": ask["id"], "step": "accept"}, 403)
    act(p["finance"], base, "ask_update", {"ask_id": ask["id"], "step": "accept"})
    act(p["finance"], base, "ask_update", {"ask_id": ask["id"], "step": "done", "note": "Released to the division"})
    act(p["relief"], base, "ask_update", {"ask_id": ask["id"], "step": "confirm"})
    dash = p["duty"].get("/api/teams/dashboard").json()
    assert dash["finance"]["requested"] == "20000.00" and dash["finance"]["released"] == "20000.00"


def test_dashboard_points_each_role_at_its_request_step(team, monkeypatch):
    p, base = setup(team, monkeypatch)
    ask = act(p["relief"], base, "ask", {"to": "water", "kind": "water", "title": "Bowsers"})["coordination"]["asks"][-1]
    titles = lambda who: [i["title"] for i in p[who].get("/api/teams/dashboard").json()["attention"] if i.get("record") == ask["id"]]
    assert titles("supervisor") == ["New request for your organisation"] and titles("engineer") == [] and titles("relief") == []
    act(p["supervisor"], base, "ask_update", {"ask_id": ask["id"], "step": "accept"})
    assert titles("engineer") == ["Deliver a request your organisation accepted"]
    act(p["engineer"], base, "ask_update", {"ask_id": ask["id"], "step": "done"})
    assert titles("relief") == ["Confirm a delivered request"] and titles("secretary") == ["Confirm a delivered request"]
    card = p["duty"].get("/api/teams/dashboard").json()["incidents"][0]
    assert card["open_requests"] == 1 and card["requests"] == 1


def test_sample_case_tells_the_request_story(team, monkeypatch):
    p, _ = setup(team, monkeypatch)
    response = p["duty"].post("/api/teams/practice", json={})
    assert response.status_code == 200, response.text
    doc = response.json()
    ask = doc["coordination"]["asks"][0]
    assert (ask["from"], ask["to"], ask["status"]) == ("division", "water", "asked")
    supervisor = p["supervisor"].get("/api/teams/incidents/" + doc["id"]).json()
    assert {"accept", "decline"} <= set(ask_of(supervisor, ask["id"])["can"])


def test_requests_page_lists_requests_by_incident_for_participants_only(team, monkeypatch):
    p, base = setup(team, monkeypatch)
    act(p["relief"], base, "ask", {"to": "water", "kind": "water", "title": "Bowsers for the hall", "quantity": 2, "unit": "bowsers"})
    groups = p["supervisor"].get("/api/teams/requests").json()
    group = next(g for g in groups if g["title"] == "Requests exercise")
    ask = group["asks"][-1]
    assert ask["from"] == "division" and ask["to"] == "water" and "accept" in ask["can"]  # NWSDB supplies it and can answer
    # A body outside the incident sees nothing from it.
    outsider = choose(team, "rda", "road_engineer")
    lonely = p["relief"].post("/api/teams/incidents", json={"title": "Division only", "district": "Nuwara Eliya", "participants": ["division"]}).json()
    assert all(g["incident_id"] != lonely["id"] for g in outsider.get("/api/teams/requests").json())


def test_priority_delivery_and_escalation_up_the_ladder(team, monkeypatch):
    p, base = setup(team, monkeypatch)
    bad = {"to": "water", "kind": "water", "title": "Water for the hall"}
    act(p["relief"], base, "ask", {**bad, "priority": "whenever"}, 422)
    act(p["relief"], base, "ask", {**bad, "delivery_area": "ds-nowhere"}, 422)
    doc = act(p["relief"], base, "ask", {**bad, "priority": "urgent", "delivery_area": "ds-nuwara-eliya-kothmale", "delivery_place": "Kotmale practice hall",
                                         "place_key": "centre-kotmale-practice-hall", "due_at": "2026-01-01T00:00:00+00:00"})
    ask = doc["coordination"]["asks"][-1]
    assert (ask["priority"], ask["delivery_area"], ask["place_key"]) == ("urgent", "ds-nuwara-eliya-kothmale", "centre-kotmale-practice-hall")
    # Past its needed-by time: it moves one level up from the Divisional Secretariat, to the District.
    assert ask["escalation"]["to"] == "district" and "needed-by" in ask["escalation"]["reason"]
    assert "redirect" in ask_of(p["coordinator"].get(base).json(), ask["id"])["can"]
    assert "redirect" not in ask_of(p["engineer"].get(base).json(), ask["id"])["can"]
    attention = [i for i in p["coordinator"].get("/api/teams/dashboard").json()["attention"] if i["title"].startswith("Escalated")]
    assert attention and "Water for the hall" in attention[0]["detail"]
    # The asker escalates by hand: the second level is the DMC.
    act(p["relief"], base, "ask_update", {"ask_id": ask["id"], "step": "escalate"}, 422)  # a reason is required
    act(p["relief"], base, "ask_update", {"ask_id": ask["id"], "step": "escalate", "note": "Still no water, 40 people waiting"})
    act(p["relief"], base, "ask_update", {"ask_id": ask["id"], "step": "escalate", "note": "Children at the hall"})
    assert ask_of(p["duty"].get(base).json(), ask["id"])["escalation"]["to"] == "dmc"
    # The body it reached sends it to someone who can help; the clock starts again.
    act(p["coordinator"], base, "ask_update", {"ask_id": ask["id"], "step": "redirect", "to": "ndrsc", "note": "NDRSC has bottled water"}, 403)  # no longer theirs
    act(p["duty"], base, "ask_update", {"ask_id": ask["id"], "step": "redirect", "to": "division", "note": "x"}, 422)  # not back to the asker
    after = act(p["duty"], base, "ask_update", {"ask_id": ask["id"], "step": "redirect", "to": "ndrsc", "note": "NDRSC has bottled water"})
    moved = ask_of(after, ask["id"])
    assert moved["to"] == "ndrsc" and moved["escalations"] == [] and moved["history"][-1]["step"] == "redirect"
    assert "accept" in ask_of(p["programme"].get(base).json(), ask["id"])["can"]
