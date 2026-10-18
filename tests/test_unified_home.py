"""One home document and bounded pre-role demo discovery."""
from fastapi.testclient import TestClient
from test_teams import team
from test_team_demo import choose, act
from lastmile.workspace import now_iso


def test_canonical_home_redirect_and_demo_catalogue_scope(team, monkeypatch):
    monkeypatch.setenv("LASTMILE_TEAM_DEMO", "true")
    public = TestClient(team["app"])
    root = public.get("/")
    assert root.status_code == 200 and "teams.js" in root.text and "lastmile-ui.js" in root.text
    bundle = public.get("/static/vendor/components/lastmile-ui.js", headers={"accept-encoding": "gzip"})
    assert bundle.status_code == 200 and bundle.headers["content-encoding"] == "gzip"
    assert "accept-encoding" in bundle.headers["vary"].lower()
    redirect = public.get("/teams", follow_redirects=False)
    assert redirect.status_code == 307 and redirect.headers["location"] == "/"
    legacy = team["store"].create("Earlier private incident must survive")
    assert public.get("/api/teams/demo/catalogue").json() == []
    central = choose(team, "dmc", "duty_officer")
    doc = central.post("/api/teams/practice", json={}).json()
    base = "/api/teams/incidents/" + doc["id"]
    act(central, base, "contribute", {"title": "Private original body", "text": "EXERCISE source text excluded from the public metadata catalogue", "observed_at": now_iso()})
    catalogue = public.get("/api/teams/demo/catalogue").json()
    assert len(catalogue) == 1
    assert catalogue[0]["pending_reports"] == 1 and catalogue[0]["open_requests"] == 1 and catalogue[0]["reports"] == 4
    assert "sources" not in catalogue[0] and "coordination" not in catalogue[0]
    assert "Private original body" not in str(catalogue)
    assert team["store"].get(legacy["id"])["title"] == "Earlier private incident must survive"
    assert public.get(base).status_code == 401
    assert public.get(team["base"]).status_code == 401
    monkeypatch.setenv("LASTMILE_TEAM_DEMO", "false")
    assert public.get("/api/teams/demo/catalogue").status_code == 403
