"""The bell: each body is told what other bodies did that concerns it, and what is waiting, with times."""
from test_teams import team  # noqa: F401  (fixture)
from test_team_demo import act, choose


def bell(client):
    r = client.get("/api/teams/notifications")
    assert r.status_code == 200, r.text
    return r.json()


def titles(client):
    return [i["title"] for i in bell(client)["items"]]


def test_opening_an_incident_and_asking_a_body_notifies_that_body_with_times(team, monkeypatch):
    monkeypatch.setenv("LASTMILE_TEAM_DEMO", "true")
    secretary, relief = choose(team, "division", "divisional_secretary"), choose(team, "division", "relief_officer")
    supervisor, roads = choose(team, "water", "service_supervisor"), choose(team, "rda", "observer")
    doc = secretary.post("/api/teams/incidents", json={"title": "Heavy rainfall, central hills", "district": "Nuwara Eliya", "participants": ["water", "dmc"], "hazard": "flood"}).json()
    base = "/api/teams/incidents/" + doc["id"]

    # The invited body hears about the new incident; the body that opened it does not hear about itself.
    opened = bell(supervisor)["items"][0]
    assert opened["title"] == "Divisional Secretariat opened an incident and added your organisation"
    assert opened["incident_id"] == doc["id"] and opened["at"] and opened["kind"] == "incident"
    assert not any("opened an incident" in t for t in titles(secretary))
    # A body in scope but not taking part is told it exists, and that it can ask to join.
    outside = bell(roads)["items"][0]
    assert outside["title"] == "New incident in Nuwara Eliya, led by Divisional Secretariat" and "Ask to join" in outside["detail"]

    ask = act(relief, base, "ask", {"to": "water", "kind": "water", "title": "Two water bowsers for the hall", "quantity": 2, "unit": "bowsers", "note": "40 people, no water"})["coordination"]["asks"][-1]
    news = bell(supervisor)
    first = news["items"][0]
    assert first["title"] == "Divisional Secretariat asked your organisation for help" and first["record"] == ask["id"] and first["tab"] == "requests"
    assert "2 bowsers" in first["detail"]
    waiting = next(w for w in news["waiting"] if w["kind"] == "ask")
    assert waiting["title"] == "New request for your organisation" and waiting["at"] == ask["created_at"]

    act(supervisor, base, "ask_update", {"ask_id": ask["id"], "step": "accept", "note": "Two bowsers leave at 14:00"})
    back = bell(relief)["items"][0]
    assert back["title"] == "NWSDB accepted your request" and "Two bowsers leave at 14:00" in back["detail"]
    act(supervisor, base, "ask_update", {"ask_id": ask["id"], "step": "start"})
    act(supervisor, base, "ask_update", {"ask_id": ask["id"], "step": "done", "note": "Bowsers at the hall"})
    assert titles(relief)[:2] == ["NWSDB marked your request as delivered", "NWSDB started work on your request"]
    # Newest first; a body's own steps are not news to it.
    items = bell(supervisor)["items"]
    assert [i["at"] for i in items] == sorted((i["at"] for i in items), reverse=True)
    assert not any(t.startswith("NWSDB") for t in titles(supervisor))
