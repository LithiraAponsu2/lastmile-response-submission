"""Plans: several linked changes the AI prepares, checked as the approving person and applied all together or not at all."""
import json
import uuid

import pytest

from lastmile import team_plans as plans
from lastmile.team_agent import TeamTools
from lastmile.workspace import WorkspaceError
from test_teams import command, get, team  # noqa: F401  (fixture)

ROAD = "EXERCISE field note: the Kotmale road is blocked by a slip near the bridge."


def chat(team, text, who="admin"):
    r = team[who].post(team["base"] + "/chat", json={"text": text, "request_id": str(uuid.uuid4())})
    assert r.status_code == 200, r.text
    return r.json()


def propose(*steps):
    return "/propose " + " ;; ".join(f"{action} {json.dumps(data)}" for action, data in steps)


def report_then_request():
    return propose(("contribute", {"title": "Road report", "text": ROAD, "observed_at": "2026-10-01T00:00:00Z", "recipients": []}),
                   ("ask", {"to": "rda", "kind": "road", "title": "Clear the Kotmale road", "priority": "urgent", "source_id": "s1"}))


def approve(team, plan, who="admin", expected=200, request_id=None):
    r = team[who].post(team["base"] + f"/plans/{plan['id']}/approve", json={"hash": plan["hash"], "request_id": request_id or str(uuid.uuid4())})
    assert r.status_code == expected, r.text
    return r.json()


def tools_for(team, who="admin"):
    actor = team[who].get("/api/teams/me").json()["user"]
    return TeamTools(team["app"].state.team_workspace, actor, get(team)["id"], lambda **kwargs: None)


def test_one_turn_prepares_linked_steps_through_the_adk_loop(team):
    doc = chat(team, report_then_request())
    turn = doc["coordination"]["chat"]["turns"][-1]
    assert turn["status"] == "completed" and turn["runtime"] == "adk-offline"
    assert turn["models"] == [{"role": "planner", "model": "offline-exercise-tools"}] and turn["usage"]["cached_tokens"] == 0
    assert [t["tool"] for t in turn["tools"]] == ["prepare_action", "prepare_action"] and turn["tools"][-1]["step_count"] == 2  # one real call per model step
    plan = doc["coordination"]["plans"][-1]
    assert [s["action"] for s in plan["steps"]] == ["contribute", "ask"]
    assert plan["steps"][1]["data"]["source_id"] == {"$step": "s1", "record": "sources"}
    assert plan["steps"][1]["summary"] == "Request to Road Development Authority: Clear the Kotmale road (urgent priority)"
    assert plan["current"] and not plan["checks"] and "Prepared a plan with 2 steps" in doc["coordination"]["chat"]["messages"][-1]["text"]
    assert doc["version"] == get(team)["version"] and not doc["sources"]  # nothing applied yet


def test_approving_applies_every_step_and_links_them(team):
    plan = chat(team, report_then_request())["coordination"]["plans"][-1]
    request_id = str(uuid.uuid4())
    done = approve(team, plan, request_id=request_id)
    source = done["sources"][-1]
    ask = done["coordination"]["asks"][-1]
    assert source["text"] == ROAD and ask["source_id"] == source["id"] and ask["to"] == "rda"
    applied = done["coordination"]["plans"][-1]
    assert applied["status"] == "applied" and applied["created"]["s1"]["sources"] == [source["id"]]
    assert done["version"] == plan["version"] + 1  # one transaction, one new version
    notes = [e["detail"] for e in done["events"][-3:]]
    assert all("prepared with AI, approved by Coordinator" in n for n in notes[:2]) and "2 steps, prepared with AI" in notes[2]
    assert approve(team, plan, request_id=request_id)["version"] == done["version"]  # the same request is idempotent
    approve(team, plan, expected=409)  # a second application is refused


def test_a_step_that_fails_at_approval_rolls_the_whole_plan_back(team, monkeypatch):
    plan = chat(team, report_then_request())["coordination"]["plans"][-1]
    service = team["app"].state.team_workspace
    original = service.apply
    def refuse_requests(doc, actor, action, data):
        if action == "ask":
            raise WorkspaceError("The receiving body left the incident.", 409)
        return original(doc, actor, action, data)
    monkeypatch.setattr(service, "apply", refuse_requests)
    r = team["admin"].post(team["base"] + f"/plans/{plan['id']}/approve", json={"hash": plan["hash"], "request_id": str(uuid.uuid4())})
    assert r.status_code == 409 and r.json()["detail"].startswith("Step 2 (Request to Road Development Authority")
    after = get(team)
    assert not after["sources"] and after["version"] == plan["version"] and after["coordination"]["plans"][-1]["status"] == "pending"


def test_a_failing_step_names_itself_while_preparing(team):
    doc = chat(team, propose(("contribute", {"title": "Road report", "text": ROAD, "observed_at": "2026-10-01T00:00:00Z", "recipients": []}),
                             ("ask", {"to": "rda", "kind": "teleport", "title": "Something", "source_id": "s1"})))
    assert "Step 2 (" in doc["coordination"]["chat"]["messages"][-1]["text"] and "kind of help" in doc["coordination"]["chat"]["messages"][-1]["text"]
    assert [s["action"] for s in doc["coordination"]["plans"][-1]["steps"]] == ["contribute"]  # only the checked step is on the card
    with pytest.raises(WorkspaceError, match="no step s4"):
        tools_for(team).create_request(to="rda", kind="road", title="Clear it", source_id="s4")


def test_editing_re_checks_and_re_signs_the_plan(team):
    plan = chat(team, propose(("ask", {"to": "rda", "kind": "road", "title": "Clear the Kotmale road", "priority": "normal"})))["coordination"]["plans"][-1]
    edit = lambda h, changes, expected=200: team["admin"].post(team["base"] + f"/plans/{plan['id']}/edit", json={"hash": h, "changes": changes})
    assert edit("0" * 64, [{"id": "s1", "data": {"priority": "urgent"}}]).status_code == 409  # edits apply to the plan as seen
    assert edit(plan["hash"], [{"id": "s1", "data": {"kind": "teleport"}}]).status_code == 422  # checked again
    assert edit(plan["hash"], [{"id": "s1", "data": {"colour": "red"}}]).status_code == 422  # not a field of this step
    r = edit(plan["hash"], [{"id": "s1", "data": {"priority": "urgent", "title": "Clear the Kotmale road for 3 lorries"}}])
    assert r.status_code == 200, r.text
    edited = r.json()["coordination"]["plans"][-1]
    assert edited["hash"] != plan["hash"] and edited["steps"][0]["data"]["priority"] == "urgent"
    assert edited["steps"][0]["summary"].endswith("(urgent priority)")  # the step's line follows the edit
    assert edited["steps"][0]["human_fields"] == ["priority", "title"] and not edited["checks"]  # a number the person typed is theirs
    approve(team, plan, expected=409)  # the old version of the card no longer applies
    done = approve(team, edited)
    assert done["coordination"]["asks"][-1]["priority"] == "urgent"


def test_removing_a_step_another_step_needs_is_refused(team):
    plan = chat(team, report_then_request())["coordination"]["plans"][-1]
    r = team["admin"].post(team["base"] + f"/plans/{plan['id']}/edit", json={"hash": plan["hash"], "changes": [{"id": "s1", "remove": True}]})
    assert r.status_code == 409 and "used by s2" in r.json()["detail"]
    r = team["admin"].post(team["base"] + f"/plans/{plan['id']}/edit", json={"hash": plan["hash"], "changes": [{"id": "s2", "remove": True}]})
    assert r.status_code == 200 and [s["id"] for s in r.json()["coordination"]["plans"][-1]["steps"]] == ["s1"]


def test_plans_belong_to_the_person_and_go_stale(team):
    plan = chat(team, report_then_request())["coordination"]["plans"][-1]
    assert get(team, "owner")["coordination"]["plans"] == []
    approve(team, plan, who="owner", expected=403)
    command(team, "contributor", "contribute", {"title": "Another report", "text": "EXERCISE another field note from Kotmale.", "observed_at": "2026-10-01T00:00:00Z"})
    assert not get(team)["coordination"]["plans"][-1]["current"]
    approve(team, plan, expected=409)
    r = team["admin"].post(team["base"] + f"/plans/{plan['id']}/refresh", json={})
    assert r.status_code == 200 and r.json()["coordination"]["plans"][-1]["current"]
    fresh = r.json()["coordination"]["plans"][-1]
    r = team["admin"].post(team["base"] + f"/plans/{plan['id']}/dismiss", json={"reason": "Handled by phone"})
    assert r.status_code == 200 and r.json()["coordination"]["plans"][-1]["status"] == "dismissed"
    approve(team, fresh, expected=409)


def test_invented_numbers_are_blocked_until_a_person_writes_them():
    steps = [{"id": "s1", "action": "ask", "data": {"title": "Send 1,500 hygiene kits", "note": "40 people at the hall", "to": "ndrsc"}, "summary": "Request"}]
    doc = {"coordination": {"assets": [{"id": "asset_1", "facts": {"occupied": {"value": 40}}}]}, "events": [{"at": "2026-10-18T09:15:00Z"}]}
    checks = plans.number_checks(steps, "please ask NDRSC for hygiene kits", doc)
    assert [(c["field"], c["level"]) for c in checks] == [("title", "block")] and "1500" in checks[0]["message"]  # 40 is in the records
    assert plans.number_checks(steps, "ask NDRSC for 1,500 hygiene kits", doc) == []  # the person said it
    assert plans.number_checks([{**steps[0], "human_fields": ["title"]}], "", doc) == []  # the person typed it
    assert plans.number_checks([{**steps[0], "code_fields": ["title"]}], "", doc) == []  # code filled it from a template
    assert plans.number_checks(steps, "", {"coordination": {}, "events": [{"at": "1500"}]})  # timestamps never ground a number


def test_a_blocked_plan_cannot_be_applied(team):
    plan = chat(team, propose(("ask", {"to": "rda", "kind": "road", "title": "Clear the Kotmale road"})))["coordination"]["plans"][-1]
    store = team["store"]
    doc = store.get(get(team)["id"])
    store.mutate(doc["id"], doc["version"], "test", lambda d: d["coordination"]["plans"][-1]["checks"].append({"step": "s1", "field": "title", "level": "block", "message": "Step 1: 900 is not in your message."}), metadata=True)
    r = team["admin"].post(team["base"] + f"/plans/{plan['id']}/approve", json={"hash": plan["hash"], "request_id": str(uuid.uuid4())})
    assert r.status_code == 409 and "flagged numbers" in r.json()["detail"]


def test_typed_tools_find_places_and_add_checked_steps(team):
    tools = tools_for(team)
    found = tools.find_places("Kothmale and Walapane are cut off; tell the RDA and the water board")
    assert {d["id"] for d in found["divisions"]} == {"ds-nuwara-eliya-kothmale", "ds-nuwara-eliya-walapane"}
    assert {o["id"] for o in found["organisations"]} >= {"rda", "water"}
    assert tools.record_area_status(area="ds-nuwara-eliya-kothmale", sector="roads", level="disrupted", note="Slip near the bridge")["step"] == "s1"
    assert tools.create_request(to="rda", kind="road", title="Clear the Kothmale road", priority="urgent", delivery_area="ds-nuwara-eliya-kothmale")["step"] == "s2"
    with pytest.raises(WorkspaceError, match="Step 3"):
        tools.record_area_status(area="ds-atlantis", sector="roads", level="disrupted")
    assert [s["summary"] for s in tools.steps] == ["Roads & access in Kothmale DS: disrupted", "Request to Road Development Authority: Clear the Kothmale road (urgent priority)"]
    plan = tools.make_plan(tools._doc())
    assert plan["prepared_for"] == {"person": tools.actor["id"]} and not plan["checks"]
