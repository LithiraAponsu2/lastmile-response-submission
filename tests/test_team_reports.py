"""Reports from any organisation, with acknowledgement requests the recipients must answer."""
from test_teams import team  # noqa: F401  (fixture)
from test_team_demo import act, choose
from lastmile.workspace import now_iso


def setup(team, monkeypatch):
    monkeypatch.setenv("LASTMILE_TEAM_DEMO", "true")
    people = {name: choose(team, org, job) for name, (org, job) in {
        "relief": ("division", "relief_officer"), "engineer": ("water", "field_engineer"), "supervisor": ("water", "service_supervisor"),
        "roads": ("rda", "technical_officer"), "rda_observer": ("rda", "observer"), "duty": ("dmc", "duty_officer"), "info": ("dmc", "information_officer"),
    }.items()}
    doc = people["duty"].post("/api/teams/incidents", json={"title": "Reports exercise", "district": "Nuwara Eliya", "participants": ["dmc", "district", "division", "ndrsc", "water", "rda"]}).json()
    return people, "/api/teams/incidents/" + doc["id"]


def report(p, base, who, title, recipients=None, message=None):
    data = {"title": title, "text": f"EXERCISE {title}: field observation for acknowledgement tests.", "observed_at": now_iso()}
    if recipients is not None:
        data["recipients"] = recipients
    if message:
        data["message"] = message
    return act(p[who], base, "contribute", data)["sources"][-1]


def test_every_acting_job_can_report_and_observers_cannot(team, monkeypatch):
    p, base = setup(team, monkeypatch)
    for who in ["relief", "engineer", "roads", "info", "duty"]:
        report(p, base, who, f"Report from {who}", [])
    act(p["rda_observer"], base, "contribute", {"title": "Observer", "text": "EXERCISE observers only read reports.", "observed_at": now_iso()}, 403)
    orgs = [s["organisation"] for s in p["duty"].get(base).json()["sources"]]
    assert orgs == ["division", "water", "rda", "dmc", "dmc"]


def test_recipients_must_acknowledge_and_everyone_sees_who_has(team, monkeypatch):
    p, base = setup(team, monkeypatch)
    source = report(p, base, "relief", "Hall water failure", ["water", "rda", "division"], "Please check the hall supply")
    # The sender's own body is never asked; each recipient is listed as waiting.
    acks = {a["organisation"]: a for a in next(s for s in p["duty"].get(base).json()["sources"] if s["id"] == source["id"])["acknowledgements"]}
    assert set(acks) == {"water", "rda"} and all(a["acknowledged_at"] is None and a["required"] for a in acks.values())
    water_view = p["engineer"].get(base).json()
    note = water_view["coordination"]["notifications"][-1]
    assert note["note"] == "Please check the hall supply" and note["ack_required"]
    items = lambda who: [i for i in p[who].get("/api/teams/dashboard").json()["attention"] if i["kind"] == "ack"]
    assert [i["record"] for i in items("supervisor")] == [source["id"]] and items("duty") == []
    act(p["duty"], base, "acknowledge", {"notification_id": note["id"]}, 403)  # not sent to DMC
    act(p["rda_observer"], base, "acknowledge", {"notification_id": note["id"]}, 403)  # observers only read
    act(p["engineer"], base, "acknowledge", {"notification_id": note["id"], "note": "Crew checking now"})
    act(p["supervisor"], base, "acknowledge", {"notification_id": note["id"]}, 409)  # one acknowledgement per organisation
    assert items("supervisor") == []
    acks = {a["organisation"]: a for a in next(s for s in p["relief"].get(base).json()["sources"] if s["id"] == source["id"])["acknowledgements"]}
    assert acks["water"]["acknowledged_at"] and acks["water"]["note"] == "Crew checking now" and acks["rda"]["acknowledged_at"] is None
    events = p["duty"].get(base).json()["events"]
    assert any(e["action"] == "acknowledge" and e["detail"] == "water acknowledged Hall water failure" for e in events)


def test_sharing_without_asking_and_asking_later(team, monkeypatch):
    p, base = setup(team, monkeypatch)
    source = report(p, base, "roads", "Landslide on access road", [])
    assert p["duty"].get(base).json()["coordination"]["notifications"] == []
    act(p["roads"], base, "contribute", {"title": "Bad", "text": "EXERCISE recipients must be a list.", "observed_at": now_iso(), "recipients": "dmc"}, 422)
    act(p["roads"], base, "notify_report", {"source_id": source["id"], "recipients": ["dmc"], "note": "Road closed until cleared"})
    duty = p["duty"].get(base).json()
    note = duty["coordination"]["notifications"][-1]
    assert note["ack_required"] and note["from"] == "rda"
    act(p["info"], base, "acknowledge", {"notification_id": note["id"]})
    assert next(s for s in p["roads"].get(base).json()["sources"] if s["id"] == source["id"])["acknowledgements"][0]["acknowledged_at"]


def test_acknowledging_from_an_older_tab_still_applies(team, monkeypatch):
    import uuid
    p, base = setup(team, monkeypatch)
    source = report(p, base, "relief", "Bridge damage near hall", ["water"])
    stale = p["engineer"].get(base).json()
    note = stale["coordination"]["notifications"][-1]
    report(p, base, "roads", "Unrelated road report", [])  # another body changes the incident meanwhile
    post = lambda action, data: p["engineer"].post(base + "/commands", json={"version": stale["version"], "request_id": str(uuid.uuid4()), "action": action, "data": data})
    assert post("ask", {"to": "division", "kind": "water", "title": "Stale ask", "quantity": 1, "unit": "bowser"}).status_code == 409  # real changes still need a fresh view
    result = post("acknowledge", {"notification_id": note["id"]})
    assert result.status_code == 200, result.text
    acks = {a["organisation"]: a for a in next(s for s in result.json()["sources"] if s["id"] == source["id"])["acknowledgements"]}
    assert acks["water"]["acknowledged_at"] and result.json()["version"] > stale["version"] + 1
