"""Role simulation is opt-in and isolated; shared reads do not grant actions."""
import base64
import uuid

from fastapi.testclient import TestClient
from test_teams import team
from lastmile.workspace import now_iso


def choose(team, organisation, role, previous="", incident_id=None):
    client = TestClient(team["app"])
    result = client.post("/api/teams/demo/select", json={"organisation": organisation, "role": role, "incident_id": incident_id}, headers={"x-demo-session": previous} if previous else {})
    assert result.status_code == 200, result.text
    client.headers["x-demo-session"] = result.json()["token"]
    return client


def act(client, base, action, data, expected=200):
    version = client.get(base).json()["version"]
    result = client.post(base + "/commands", json={"version": version, "request_id": str(uuid.uuid4()), "action": action, "data": data})
    assert result.status_code == expected, result.text
    return result.json()


def test_demo_disabled_by_default_and_no_forged_roles(team, monkeypatch):
    monkeypatch.delenv("LASTMILE_TEAM_DEMO", raising=False)
    client = TestClient(team["app"])
    assert client.get("/api/teams/setup").json()["demo"] is False
    assert client.post("/api/teams/demo/select", json={"organisation": "dmc", "role": "duty_officer"}).status_code == 403
    assert client.get("/api/teams/me", headers={"x-demo-session": "demo_dmc_administrator"}).status_code == 403


def test_demo_tabs_roles_history_and_real_accounts_isolation(team, monkeypatch):
    monkeypatch.setenv("LASTMILE_TEAM_DEMO", "true")
    admin = choose(team, "dmc", "duty_officer")
    assert admin.get(team["base"]).status_code == 403
    assert admin.get("/api/teams/members").status_code == 403
    assert admin.post("/api/teams/invitations", json={"organisation": "water", "roles": ["coordinator"], "districts": ["*"]}).status_code == 403
    doc = admin.post("/api/teams/incidents", json={"title": "Role selection exercise", "district": "Nuwara Eliya", "participants": ["dmc", "water"]}).json()
    base = "/api/teams/incidents/" + doc["id"]
    assert doc["coordination"]["demo"] is True
    # Only the bodies the creator invited take part; the creator's body leads.
    assert doc["coordination"]["participants"] == ["dmc", "water"] and doc["coordination"]["lead"] == "dmc"
    water = choose(team, "water", "field_engineer", admin.headers["x-demo-session"], doc["id"])
    assert admin.get("/api/teams/me").json()["user"]["organisation"] == "dmc"
    assert water.get("/api/teams/me").json()["user"]["organisation"] == "water"
    assert water.get(base).json()["coordination"]["role_history"][-1]["from"] == "dmc / EOC Duty Officer"
    # Any team can open an incident; an observer cannot.
    own = water.post("/api/teams/incidents", json={"title": "Water-led exercise", "district": "Nuwara Eliya", "participants": ["water"], "hazard": "drought"}).json()
    assert own["coordination"]["lead"] == "water" and own["coordination"]["participants"] == ["water"]
    assert choose(team, "water", "observer").post("/api/teams/incidents", json={"title": "Denied creation", "district": "Nuwara Eliya", "participants": ["water"]}).status_code == 403
    assert water.post("/api/teams/demo/select", json={"organisation": "water", "role": "coordinator"}).status_code == 422
    act(water, base, "phase", {"phase": "recovery", "reason": "Unauthorised central decision attempt"}, 403)
    monkeypatch.setenv("LASTMILE_TEAM_DEMO", "false")
    assert admin.get(base).status_code == 403


def test_shared_report_targeted_notifications_and_persona_history(team, monkeypatch):
    monkeypatch.setenv("LASTMILE_TEAM_DEMO", "1")
    admin, reporter, water, observer = [choose(team, o, r) for o, r in [("dmc", "duty_officer"), ("division", "relief_officer"), ("water", "service_supervisor"), ("rda", "observer")]]
    doc = admin.post("/api/teams/incidents", json={"title": "Shared water response exercise", "district": "Nuwara Eliya", "participants": ["dmc", "district", "division", "ndrsc", "water", "rda"]}).json()
    base = "/api/teams/incidents/" + doc["id"]
    raw = b"EXERCISE Kotmale water unavailable. One inspection requested."
    data = {"title": "Water report", "observed_at": now_iso(), "attachment": {"name": "water.txt", "base64": base64.b64encode(raw).decode()}, "recipients": ["water"]}
    act(observer, base, "contribute", data, 403)
    act(reporter, base, "contribute", {**data, "recipients": ["invented-body"]}, 422)
    assert not reporter.get(base).json()["sources"]
    doc = act(reporter, base, "contribute", data)
    source = doc["sources"][-1]
    assert source["status"] == "reported"
    assert observer.get(base).json()["sources"][-1]["text"] == raw.decode()
    assert observer.get(base + f"/sources/{source['id']}/attachment").content == raw
    assert reporter.get(base).json()["coordination"]["notifications"] == []
    note = water.get(base).json()["coordination"]["notifications"][-1]
    act(reporter, base, "read_notification", {"notification_id": note["id"]}, 403)
    version = water.get(base).json()["version"]
    read = act(water, base, "read_notification", {"notification_id": note["id"]})
    assert read["version"] == version
    assert water.get("/api/teams/me").json()["user"]["id"] in read["coordination"]["notifications"][-1]["read_by"]
    act(reporter, base, "review_source", {"source_id": source["id"], "decision": "accepted"}, 403)
    act(admin, base, "review_source", {"source_id": source["id"], "decision": "accepted"})
    act(water, base, "notify_report", {"source_id": source["id"], "recipients": ["division"], "note": "Inspection team will review this report"})
    assert reporter.get(base).json()["coordination"]["notifications"][-1]["from"] == "water"
    reply = reporter.post(base + "/chat", json={"text": "Search report " + source["id"], "request_id": str(uuid.uuid4())})
    assert reply.status_code == 200, reply.text
    assert reply.json()["coordination"]["chat"]["turns"][-1]["status"] == "completed"
    assert admin.get(base).json()["coordination"]["chat"]["messages"] == []
    restored = choose(team, "division", "relief_officer")
    assert restored.get(base).json()["coordination"]["chat"]["messages"]
