"""Home dashboard: actor-scoped aggregates, shelter capacity and attention routing."""
from test_teams import PASSWORD, asset, command, get, need, offer, source, team  # noqa: F401  (fixture)


def dashboard(team, who="admin"):
    r = team[who].get("/api/teams/dashboard")
    assert r.status_code == 200, r.text
    return r.json()


def kinds(data):
    return {item["kind"] for item in data["attention"]}


def test_dashboard_scopes_incidents_and_routes_reports_to_reviewers(team):
    command(team, "contributor", "contribute", {"title": "Road blocked near Kotmale", "text": "EXERCISE road report: landslide blocks the access road.", "observed_at": get(team)["updated_at"]})
    coordinator = dashboard(team)
    assert coordinator["totals"]["active_incidents"] == 1 and coordinator["totals"]["pending_reports"] == 1
    report = next(item for item in coordinator["attention"] if item["kind"] == "report")
    assert report["tab"] == "sources" and report["incident_title"] == "Kotmale flood exercise" and report["record"]
    # The author cannot review their own report, so it is not their task.
    assert "report" not in kinds(dashboard(team, "contributor"))
    outsider, _ = team["join"]("outsider", "water", ["coordinator"])
    empty = outsider.get("/api/teams/dashboard").json()
    assert empty["incidents"] == [] and empty["attention"] == [] and empty["shelters"]["count"] == 0


def test_dashboard_shelter_capacity_counts_known_places_and_unknowns(team):
    shelter = asset(team)
    data = dashboard(team)
    assert data["shelters"]["count"] == 1 and data["shelters"]["known"] == 1
    row = data["shelters"]["items"][0]
    assert (row["capacity"], row["occupied"], row["available"]) == (100, 40, 60)
    assert data["shelters"]["available"] == 60 and data["incidents"][0]["places_available"] == 60
    # A reservation is held against the planning balance.
    request = need(team, 30)
    held = offer(team, shelter, request, 30)
    assert "assignment" in kinds(dashboard(team, "owner"))
    command(team, "owner", "respond", {"offer_id": held["id"], "decision": "accepted"})
    data = dashboard(team)
    assert data["shelters"]["available"] == 30 and data["totals"]["assignments_in_progress"] == 1
    # Unknown readiness is never converted into places.
    command(team, "owner", "confirm_asset", {"name": "Unconfirmed school", "kind": "shelter", "source_id": source(team, "EXERCISE school: capacity 50 people"), "facts": {"capacity": {"value": 50, "quote": "capacity 50 people"}}})
    data = dashboard(team)
    assert data["shelters"]["count"] == 2 and data["shelters"]["unknown"] == 1 and data["shelters"]["available"] == 30
    assert data["shelters"]["items"][0]["available"] is None
    assert any(item["kind"] == "resource" and item["title"] == "Update Unconfirmed school" for item in dashboard(team, "owner")["attention"])


def test_dashboard_activity_is_readable_and_lists_active_districts(team):
    source(team, "EXERCISE water update: supply restored at the hall.")
    data = dashboard(team)
    labels = [event["label"] for event in data["activity"]]
    assert "Report contributed" in labels and "Report reviewed" in labels
    assert data["districts"] == [{"name": "Nuwara Eliya", "active_incidents": 1}]


def test_dashboard_incident_cards_carry_next_step_bodies_and_latest_activity(team):
    command(team, "contributor", "contribute", {"title": "Road blocked near Kotmale", "text": "EXERCISE road report: landslide blocks the access road.", "observed_at": get(team)["updated_at"]})
    board = dashboard(team)
    card = board["incidents"][0]
    assert card["created_at"] and "dmc" in card["participants"] and card["resources"] == 0
    assert card["finance"] == {"requested": "0", "released": "0"}
    # The card names the most urgent item for this role, the same one the attention list puts first.
    # Here DMC is asked to acknowledge the report (priority 1) before reviewing it.
    assert card["next"] == next(i for i in board["attention"] if i["incident_id"] == card["id"])
    assert card["next"]["kind"] == "ack" and {"ack", "report"} <= {i["kind"] for i in board["attention"]} and card["attention"] >= 2
    assert card["latest"]["label"] == "Report contributed"
    # The author cannot review their own report, so it is never their next step.
    assert (dashboard(team, "contributor")["incidents"][0]["next"] or {}).get("kind") != "report"
