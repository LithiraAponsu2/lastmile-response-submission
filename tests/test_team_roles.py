"""Named job roles per active organisation; inactive bodies stay visible but unselectable."""
from fastapi.testclient import TestClient
from test_teams import PASSWORD, team
from test_team_demo import act, choose
from lastmile.team_accounts import ACTIVE, ORGANISATIONS, job_role
from lastmile.workspace import now_iso


def test_catalogue_lists_active_jobs_and_greys_out_the_rest(team, monkeypatch):
    monkeypatch.setenv("LASTMILE_TEAM_DEMO", "true")
    orgs = TestClient(team["app"]).get("/api/teams/setup").json()["organisations"]
    assert [o["id"] for o in orgs if o["active"]] == ["dmc", "district", "division", "ndrsc", "water", "rda", "partner", "edl"] == ACTIVE
    for org in orgs:
        if not org["active"]:
            assert org["roles"] == []
            continue
        # Two jobs per body plus a read-only observer (DMC adds an account-only admin).
        jobs = [r for r in org["roles"] if not r.get("accounts_only")]
        assert len(jobs) == 3 and jobs[-1]["id"] == "observer", org["id"]
        assert all(r["title"] and r["summary"] and r["tasks"] and r["idle"] for r in jobs)


def test_demo_selection_validates_job_against_organisation(team, monkeypatch):
    monkeypatch.setenv("LASTMILE_TEAM_DEMO", "true")
    client = TestClient(team["app"])
    pick = lambda org, role: client.post("/api/teams/demo/select", json={"organisation": org, "role": role})
    assert pick("transport", "observer").status_code == 422
    assert "not active" in pick("health", "observer").json()["detail"]
    assert pick("water", "duty_officer").status_code == 422
    assert pick("dmc", "workspace_admin").status_code == 422
    assert pick("dmc", "coordinator").status_code == 422
    for org in ACTIVE:
        for job in [r for r in next(o for o in ORGANISATIONS if o["id"] == org)["roles"] if not r.get("accounts_only")]:
            user = pick(org, job["id"]).json()["user"]
            assert user["roles"] == sorted(job["roles"]) and user["job_title"] == job["title"]
    duty = choose(team, "dmc", "duty_officer")
    assert duty.post("/api/teams/incidents", json={"title": "Inactive body", "district": "Nuwara Eliya", "participants": ["dmc", "health"]}).status_code == 422
    doc = duty.post("/api/teams/incidents", json={"title": "Active bodies only", "district": "Nuwara Eliya", "participants": ["dmc"]}).json()
    assert doc["coordination"]["participants"] == ["dmc"]
    act(duty, "/api/teams/incidents/" + doc["id"], "participant", {"organisation": "health"}, 422)


def test_demo_funding_needs_requester_recommender_and_approver(team, monkeypatch):
    monkeypatch.setenv("LASTMILE_TEAM_DEMO", "true")
    officer, district, programme, finance = [choose(team, o, r) for o, r in [("division", "relief_officer"), ("district", "district_coordinator"), ("ndrsc", "programme_officer"), ("ndrsc", "finance_approver")]]
    doc = district.post("/api/teams/incidents", json={"title": "Funding roles exercise", "district": "Nuwara Eliya", "participants": ["dmc", "district", "division", "ndrsc", "water", "rda"]}).json()
    base = "/api/teams/incidents/" + doc["id"]
    source = act(officer, base, "contribute", {"title": "Relief estimate", "text": "EXERCISE relief supplies estimated LKR 20000.00", "observed_at": now_iso()})["sources"][-1]["id"]
    act(district, base, "review_source", {"source_id": source, "decision": "accepted"})
    request = {"purpose": "Exercise relief supplies", "amount": "20000.00", "source_id": source, "scheme_id": "fictional-relief-v1", "head": "EXERCISE-RELIEF"}
    f = act(officer, base, "finance_request", request)["coordination"]["finance"][-1]
    act(officer, base, "finance_action", {"finance_id": f["id"], "state": "recommended"}, 403)
    act(finance, base, "finance_action", {"finance_id": f["id"], "state": "approved", "amount": "20000.00"}, 403)
    act(district, base, "finance_action", {"finance_id": f["id"], "state": "recommended"})
    act(programme, base, "finance_action", {"finance_id": f["id"], "state": "approved", "amount": "20000.00"}, 403)
    approved = act(finance, base, "finance_action", {"finance_id": f["id"], "state": "approved", "amount": "20000.00"})
    assert approved["coordination"]["finance"][-1]["status"] == "approved"


def test_invitations_by_job_role_and_custom_sets(team):
    admin = team["admin"]
    assert admin.get("/api/teams/me").json()["user"]["job_title"] == "Workspace administrator"
    invite = admin.post("/api/teams/invitations", json={"organisation": "rda", "job_role": "road_engineer", "districts": ["Nuwara Eliya"]})
    assert invite.status_code == 200, invite.text
    client = TestClient(team["app"])
    assert client.post("/api/teams/join", json={"username": "engineer", "name": "Engineer", "password": PASSWORD, "invitation": invite.json()["token"]}).status_code == 200
    assert client.post("/api/teams/login", json={"username": "engineer", "password": PASSWORD}).status_code == 200
    me = client.get("/api/teams/me").json()["user"]
    assert me["roles"] == sorted(job_role("rda", "road_engineer")["roles"]) and me["job_title"] == "Executive Engineer (Roads)"
    assert admin.post("/api/teams/invitations", json={"organisation": "rda", "job_role": "duty_officer", "districts": ["*"]}).status_code == 422
    assert admin.post("/api/teams/invitations", json={"organisation": "rda", "districts": ["*"]}).status_code == 422
    members = {m["username"]: m for m in admin.get("/api/teams/members").json()}
    # Raw permission sets still resolve to the matching job, or a custom role.
    assert members["contributor"]["job_title"] == "Field Relief Officer"
    assert members["owner"]["job_title"] == "Divisional Secretary"
    assert members["finance"]["job_title"] == "Finance Approver"
