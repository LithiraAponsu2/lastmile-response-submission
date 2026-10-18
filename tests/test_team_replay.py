"""Historical business time never rewrites observation or audit/account time."""
from test_teams import team, get, command, source, REPORT


def test_replay_preserves_original_dates_and_expires_holds_on_advance(team):
    start = "2025-11-28T12:00:00+00:00"
    command(team, "admin", "replay_clock", {"as_of": start, "reason": "Labelled historical exercise with original report dates"})
    text = REPORT + "\nEXERCISE request: 20 people need accommodation."
    sid = source(team, text, observed_at=start)
    facts = {"capacity": (100, "Current capacity: 100 people"), "occupied": (40, "Occupied: 40 people"), "external_pending": (0, "External pending: 0 people"), "status": ("accepting", "Status: accepting"), "access": ("passable", "Access: passable"), "water": ("working", "Water: working"), "power": ("working", "Power: working")}
    d = command(team, "owner", "confirm_asset", {"name": "Historical fictional hall", "kind": "shelter", "source_id": sid, "facts": {k: {"value": v, "quote": q} for k, (v, q) in facts.items()}})
    a = d["coordination"]["assets"][-1]
    assert a["balance"]["available"] == 60
    assert d["sources"][0]["observed_at"] == start and d["sources"][0]["received_at"] != start
    d = command(team, "contributor", "request", {"title": "Historical exercise need", "kind": "shelter", "quantity": 20, "unit": "people", "source_id": sid})
    r = d["coordination"]["requests"][-1]
    d = command(team, "admin", "offer", {"request_id": r["id"], "asset_id": a["id"], "quantity": 20})
    o = d["coordination"]["offers"][-1]
    d = command(team, "owner", "respond", {"offer_id": o["id"], "decision": "accepted"})
    assert d["coordination"]["assets"][0]["balance"]["available"] == 40
    assert d["coordination"]["offers"][0]["responded_at"] == start
    d = command(team, "admin", "replay_clock", {"as_of": "2025-11-28T12:31:00+00:00", "reason": "Advance past accepted reservation expiry"})
    assert d["coordination"]["assets"][0]["balance"]["available"] == 60
    d = command(team, "admin", "replay_clock", {"as_of": "2025-11-28T13:01:00+00:00", "reason": "Advance beyond illustrative resource freshness"})
    assert d["coordination"]["assets"][0]["balance"]["available"] is None
    command(team, "admin", "replay_clock", {"as_of": start, "reason": "Backward clock must remain blocked"}, 409)
    command(team, "owner", "replay_clock", {"as_of": "2025-11-29T12:00:00+00:00", "reason": "Unprivileged advance must remain blocked"}, 403)


def test_replay_deadlines_and_escalation_are_explicit_unsent_records(team):
    start = "2025-11-28T12:00:00+00:00"
    command(team, "admin", "replay_clock", {"as_of": start, "reason": "Historical general assignment exercise"})
    sid = source(team, "EXERCISE Kotmale request 1 inspection of the road.", observed_at=start)
    d = command(team, "contributor", "request", {"title": "Inspect access", "kind": "road", "quantity": 1, "unit": "inspection", "source_id": sid})
    d = command(team, "admin", "offer", {"request_id": d["coordination"]["requests"][-1]["id"], "recipient": "rda", "quantity": 1})
    assignment = d["coordination"]["offers"][-1]
    command(team, "admin", "assignment_details", {"offer_id": assignment["id"], "due_at": "2025-11-28T12:10:00+00:00", "reason": "Exercise confirmation is needed within ten minutes"})
    d = command(team, "admin", "replay_clock", {"as_of": "2025-11-28T12:11:00+00:00", "reason": "Advance to demonstrate the overdue queue"})
    assert d["coordination"]["offers"][-1]["overdue"]
    d = command(team, "admin", "escalate", {"offer_id": assignment["id"], "reason": "Exercise deadline missed; liaison follow-up required"})
    assert d["coordination"]["escalations"][-1]["delivery"] == "not_sent_exercise_record"
