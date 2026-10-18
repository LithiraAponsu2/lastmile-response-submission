"""One full case across original evidence, ADK, owners, finance and closure."""
from fastapi.testclient import TestClient

from lastmile.api import create_app
from lastmile.config import Settings
from lastmile.exercise_case import FullExercise
from lastmile.store import Store
from lastmile.workspace import WorkspaceStore


def test_full_fictional_case_and_restart_persistence(tmp_path):
    db = tmp_path / "case.sqlite3"
    hub = WorkspaceStore(db)
    app = create_app(Settings(mail="off", api_key="case-setup-key", rate_limit_per_min=1000), store=Store(), workspace_store=hub)
    runner = FullExercise(lambda: TestClient(app), "case-setup-key")
    try:
        result = runner.run()
        assert all(s["passed"] for s in result["steps"])
        assert result["final_status"] == "closed" and len(result["turns"]) == 4
        assert len(result["final_incident"]["coordination"]["requests"]) == 4
        actor = runner.clients["central"].get("/api/teams/me").json()["user"]
        snapshot = app.state.team_workspace.view(hub.get(result["incident_id"]), actor)
        assert snapshot["coordination"]["closure_blockers"] == []
        runner.close_clients(); hub.close()
        restored = WorkspaceStore(db)
        assert restored.get(result["incident_id"])["status"] == "closed"
        assert restored.get(result["incident_id"])["coordination"]["finance"][0]["status"] == "reconciled"
        restored.close()
    finally:
        runner.close_clients()
