"""Cross-incident guards within the supported single active writer."""
from concurrent.futures import ThreadPoolExecutor
import uuid

from test_teams import team, get, command, source, need, offer, REPORT


def second_incident(team):
    d = team["admin"].post("/api/teams/incidents", json={"title": "Second linked exercise", "district": "Nuwara Eliya", "participants": ["division", "ndrsc"]}).json()
    return {**team, "base": "/api/teams/incidents/" + d["id"]}


def linked_asset(team):
    sid = source(team, REPORT)
    values = {"capacity": (100, "Current capacity: 100 people"), "occupied": (40, "Occupied: 40 people"), "external_pending": (0, "External pending: 0 people"), "status": ("accepting", "Status: accepting"), "access": ("passable", "Access: passable"), "water": ("working", "Water: working"), "power": ("working", "Power: working")}
    doc = command(team, "owner", "confirm_asset", {"name": "Shared exercise hall", "kind": "shelter", "resource_key": "fictional-hall-001", "source_id": sid, "facts": {k: {"value": v, "quote": q} for k, (v, q) in values.items()}})
    return doc["coordination"]["assets"][-1]


def test_competing_incidents_cannot_accept_the_same_shared_capacity(team):
    other = second_incident(team)
    a, b = linked_asset(team), linked_asset(other)
    one, two = offer(team, a, need(team, 40), 40), offer(other, b, need(other, 40), 40)
    first_version, second_version = get(team)["version"], get(other)["version"]
    def accept(args):
        case, assignment, version = args
        return case["owner"].post(case["base"] + "/commands", json={"version": version, "request_id": uuid.uuid4().hex, "action": "respond", "data": {"offer_id": assignment["id"], "decision": "accepted"}}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(accept, [(team, one, first_version), (other, two, second_version)]))
    assert sorted(statuses) == [200, 409]
    for case in (team, other):
        balance = get(case)["coordination"]["assets"][0]["balance"]
        assert balance["available"] == 20 and balance["held"] == 40 and balance["shared"]


def test_fictional_funding_pool_budget_is_shared_between_incidents(team):
    other = second_incident(team)
    records = []
    for case in (team, other):
        policy = source(case, "EXERCISE fictional pooled budget 10000.00 LKR and per-approval limit 8000.00 LKR.", "finance")
        command(case, "finance", "finance_scheme", {"id": "fictional-shared-relief", "name": "Fictional shared relief", "budget": "10000.00", "limit": "8000.00", "pool_key": "fictional-shared-pool-001", "head": "EXERCISE-POOL", "source_id": policy})
        command(case, "finance", "finance_scheme", {"id": "fictional-pool-alias", "name": "Fictional duplicate pool", "budget": "10000.00", "limit": "8000.00", "pool_key": "fictional-shared-pool-001", "head": "EXERCISE-POOL", "source_id": policy}, 409)
        sid = source(case, "EXERCISE request pooled support LKR 8000.00")
        d = command(case, "contributor", "finance_request", {"purpose": "Shared pool exercise request", "amount": "8000.00", "source_id": sid, "head": "EXERCISE-POOL", "scheme_id": "fictional-shared-relief"})
        record = d["coordination"]["finance"][-1]
        command(case, "admin", "finance_action", {"finance_id": record["id"], "state": "recommended"})
        records.append(record)
    command(team, "finance", "finance_action", {"finance_id": records[0]["id"], "state": "approved", "amount": "8000.00"})
    command(other, "finance", "finance_action", {"finance_id": records[1]["id"], "state": "approved", "amount": "8000.00"}, 409)
    command(team, "finance", "finance_decision", {"finance_id": records[0]["id"], "decision": "cancelled", "reason": "Unused exercise approval is cancelled before any movement"})
    d = command(other, "finance", "finance_action", {"finance_id": records[1]["id"], "state": "approved", "amount": "8000.00"})
    assert d["coordination"]["finance"][-1]["approved"] == "8000.00"
