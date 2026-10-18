"""One shared stock record: two incidents can never take the same packs; a stale suggestion is rebuilt from what is left."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from lastmile import stock_ledger
from lastmile.team_areas import DIVISIONS
from test_teams import command, get, team  # noqa: F401  (fixture)

KOTHMALE = DIVISIONS["ds-nuwara-eliya-kothmale"]["centre"]
HANGURANKETA = DIVISIONS["ds-nuwara-eliya-hanguranketa"]["centre"]  # also nearest to the Kandy store


@pytest.fixture
def two(team):
    """The fixture's Nuwara Eliya incident, plus a second one in the same district."""
    team["app"].state.public_requests.per_hour = 100
    second = team["admin"].post("/api/teams/incidents", json={"title": "Walapane landslide exercise", "district": "Nuwara Eliya", "participants": ["division", "ndrsc", "water"]}).json()
    team["base2"] = "/api/teams/incidents/" + second["id"]
    team["public"] = TestClient(team["app"])
    yield team
    team["public"].close()


def act(team, base, action, data, expected=200, who="admin"):
    doc = team[who].get(base).json()
    r = team[who].post(base + "/commands", json={"version": doc["version"], "request_id": str(uuid.uuid4()), "action": action, "data": data})
    assert r.status_code == expected, r.text
    return r.json()


def free(team, stock_id):
    return team["app"].state.team_workspace.ledger.available(stock_id)


def test_two_incidents_cannot_take_the_same_bowsers(two):
    team = two
    assert free(team, "stock-001") == 6
    first = act(team, team["base"], "reserve_stock", {"stock_id": "stock-001", "quantity": 4})
    assert first["coordination"]["reservations"][-1]["state"] == "held" and free(team, "stock-001") == 2
    refused = act(team, team["base2"], "reserve_stock", {"stock_id": "stock-001", "quantity": 4}, 409)
    assert "Only 2 water bowser available" in refused["detail"]
    assert not team["admin"].get(team["base2"]).json()["coordination"].get("reservations")  # nothing half-saved
    act(team, team["base2"], "reserve_stock", {"stock_id": "stock-001", "quantity": 2})
    assert free(team, "stock-001") == 0


def test_two_approvals_at_the_same_moment_never_over_commit(two):
    team = two
    def reserve(base):
        doc = team["admin"].get(base).json()
        return team["admin"].post(base + "/commands", json={"version": doc["version"], "request_id": str(uuid.uuid4()), "action": "reserve_stock",
                                                             "data": {"stock_id": "stock-001", "quantity": 4}}).status_code
    with ThreadPoolExecutor(2) as pool:
        codes = sorted(pool.map(reserve, [team["base"], team["base2"]]))
    assert codes == [200, 409] and free(team, "stock-001") == 2


def test_holds_follow_the_request_and_expire(two):
    team = two
    ask = act(team, team["base2"], "ask", {"to": "water", "kind": "water", "title": "Bowsers for Kothmale school"}, who="contributor")["coordination"]["asks"][-1]
    act(team, team["base2"], "reserve_stock", {"stock_id": "stock-001", "quantity": 3, "ask_id": ask["id"]})
    water, _ = team["join"]("water", "water", ["agency_supervisor", "contributor"])
    team["water"] = water
    act(team, team["base2"], "ask_update", {"ask_id": ask["id"], "step": "decline", "note": "No driver until tomorrow."}, who="water")
    doc = team["admin"].get(team["base2"]).json()
    assert doc["coordination"]["reservations"][-1]["state"] == "released" and free(team, "stock-001") == 6  # declined: given back
    held = act(team, team["base"], "reserve_stock", {"stock_id": "stock-001", "quantity": 5})["coordination"]["reservations"][-1]
    assert free(team, "stock-001") == 1
    ledger = team["app"].state.team_workspace.ledger
    later = datetime.now(timezone.utc) + timedelta(hours=stock_ledger.HOLD_HOURS, minutes=1)
    assert ledger.available("stock-001", now=later) == 6  # an unaccepted hold expires
    assert held["hold_id"] and held["expires_at"]


def ask_for_food(team, people, where=KOTHMALE):
    r = team["public"].post("/api/public/requests", json={"text": f"We need food, {people} people at the temple have nothing to eat after the floods.", "latitude": where[0], "longitude": where[1]})
    assert r.status_code == 201, r.text
    return r.json()


def food_plans(team):
    """Public requests go to the newest open incident covering the area: here the second one."""
    return [p for p in team["admin"].get(team["base2"]).json()["coordination"]["plans"] if p["origin"] == "event" and p["trigger"]["kind"] == "public_request"]


def test_a_food_request_reserves_the_nearest_rations_with_the_rule_shown(two):
    team = two
    ask_for_food(team, 100)
    [plan] = [p for p in food_plans(team) if p["status"] == "pending"]
    ask, reserve = plan["steps"]
    assert ask["data"]["to"] == "ndrsc" and ask["data"]["kind"] == "supplies"
    assert reserve["action"] == "reserve_stock" and reserve["data"]["stock_id"] == "stock-005" and reserve["data"]["quantity"] == 100
    assert reserve["data"]["ask_id"] == {"$step": "s1", "record": "asks"} and "one dry ration pack per person" in reserve["data"]["note"]
    assert "Reserve 100 dry rations from NDRSC Kandy district store" in reserve["summary"] and "(500 available now)" in reserve["summary"]
    done = team["admin"].post(team["base2"] + f"/plans/{plan['id']}/approve", json={"hash": plan["hash"], "request_id": str(uuid.uuid4())})
    assert done.status_code == 200, done.text
    reservation = done.json()["coordination"]["reservations"][-1]
    assert reservation["state"] == "held" and reservation["ask_id"] == done.json()["coordination"]["asks"][-1]["id"] and free(team, "stock-005") == 400


def test_a_suggestion_built_on_old_stock_is_rebuilt_not_applied(two):
    """The user's case: both requests were planned when 500 were free; the first takes 300; the second is re-planned."""
    team = two
    ask_for_food(team, 300)
    ask_for_food(team, 300, where=HANGURANKETA)  # another place: not the same problem, so not grouped
    first, second = [p for p in food_plans(team) if p["status"] == "pending"]
    assert [s["data"]["quantity"] for s in first["steps"][1:]] == [300] and [s["data"]["quantity"] for s in second["steps"][1:]] == [300]  # both saw 500
    r = team["admin"].post(team["base2"] + f"/plans/{first['id']}/approve", json={"hash": first["hash"], "request_id": str(uuid.uuid4())})
    assert r.status_code == 200, r.text
    plans = food_plans(team)
    assert next(p for p in plans if p["id"] == second["id"])["status"] == "replaced"  # the old picture is retired at once
    [fresh] = [p for p in plans if p["status"] == "pending"]
    reserves = [(s["data"]["stock_id"], s["data"]["quantity"]) for s in fresh["steps"][1:]]
    assert reserves[0] == ("stock-005", 200) and sum(q for _, q in reserves) == 300 and len(reserves) == 2  # 200 left in Kandy, the rest from the next store
    stale = team["admin"].post(team["base2"] + f"/plans/{second['id']}/approve", json={"hash": second["hash"], "request_id": str(uuid.uuid4())})
    assert stale.status_code == 409
    r = team["admin"].post(team["base2"] + f"/plans/{fresh['id']}/approve", json={"hash": fresh["hash"], "request_id": str(uuid.uuid4())})
    assert r.status_code == 200, r.text
    assert free(team, "stock-005") == 0


def test_an_approval_that_finds_the_stock_gone_says_so_and_re_plans(two, monkeypatch):
    team = two
    ask_for_food(team, 100)
    [plan] = [p for p in food_plans(team) if p["status"] == "pending"]
    # As if the immediate rebuild had not run yet: the stock goes between building the card and pressing Apply.
    monkeypatch.setattr(team["app"].state.team_workspace, "on_stock_taken", lambda *a, **k: None)
    act(team, team["base"], "reserve_stock", {"stock_id": "stock-005", "quantity": 450})  # another incident takes most of it meanwhile
    r = team["admin"].post(team["base2"] + f"/plans/{plan['id']}/approve", json={"hash": plan["hash"], "request_id": str(uuid.uuid4())})
    assert r.status_code == 409 and "Only 50 dry rations" in r.json()["detail"] and "new suggestion" in r.json()["detail"]
    assert free(team, "stock-005") == 50 and not team["admin"].get(team["base2"]).json()["coordination"].get("reservations")
    [fresh] = [p for p in food_plans(team) if p["status"] == "pending"]
    assert [(s["data"]["stock_id"], s["data"]["quantity"]) for s in fresh["steps"][1:]][0] == ("stock-005", 50)
