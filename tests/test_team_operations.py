"""Lifecycle, cross-sector evidence and complete exercise transitions."""
import uuid
from datetime import datetime, timedelta, timezone

from test_teams import team, get, command, source, asset, need, offer
from lastmile.workspace import now_iso


def test_participant_claim_conflict_and_resolution(team):
    outsider, _ = team["join"]("waterofficer", "water", ["contributor", "agency_supervisor"])
    assert outsider.get(team["base"]).status_code == 403
    command(team, "contributor", "participant", {"organisation": "water"}, 403)
    command(team, "admin", "participant", {"organisation": "water"})
    assert outsider.get(team["base"]).status_code == 200
    first = source(team, "EXERCISE Kotmale: water status unavailable.")
    second = source(team, "EXERCISE Kotmale: water status working.", "owner")
    for sid, value in ((first, "unavailable"), (second, "working")):
        d = command(team, "admin", "claim", {"topic": "water", "location": "Kotmale", "field": "supply", "value": value, "unit": "status", "source_id": sid, "quote": f"water status {value}."})
    claims = d["coordination"]["claims"]
    assert all(c["status"] == "disputed" for c in claims)
    assert any(b["kind"] == "claim" for b in d["coordination"]["closure_blockers"])
    command(team, "admin", "resolve_claim", {"claim_id": claims[1]["id"], "supersedes": [], "reason": "Latest owner evidence verified"}, 409)
    d = command(team, "admin", "resolve_claim", {"claim_id": claims[1]["id"], "supersedes": [claims[0]["id"]], "reason": "Latest owner evidence verified"})
    assert [c["status"] for c in d["coordination"]["claims"]] == ["superseded", "current"]
    assert all(c["quote"] for c in d["coordination"]["claims"])


def test_partial_supply_receipt_cannot_duplicate_or_overfulfil(team):
    s = source(team, "EXERCISE request 100 packs of relief supplies at Kotmale.")
    d = command(team, "contributor", "request", {"title": "Food packs", "kind": "supplies", "quantity": 100, "unit": "packs", "source_id": s})
    r = d["coordination"]["requests"][-1]
    first = source(team, "EXERCISE receipt: 60 packs received at Kotmale.", "owner")
    data = {"request_id": r["id"], "quantity": 60, "unit": "packs", "source_id": first, "quote": "60 packs received"}
    d = command(team, "owner", "receipt", data)
    assert d["coordination"]["requests"][-1]["remaining"] == 40
    assignment = {"request_id": r["id"], "quantity": 100, "recipient": "division"}
    command(team, "admin", "offer", assignment, 409)
    pending = command(team, "admin", "offer", {**assignment, "quantity": 40})["coordination"]["offers"][-1]
    command(team, "owner", "receipt", data, 409)
    second = source(team, "EXERCISE receipt: 40 packs received at Kotmale.", "owner")
    command(team, "owner", "receipt", {**data, "quantity": 50, "source_id": second, "quote": "40 packs received"}, 422)
    d = command(team, "owner", "receipt", {**data, "quantity": 40, "source_id": second, "quote": "40 packs received"})
    assert d["coordination"]["requests"][-1]["status"] == "fulfilled"
    assert d["coordination"]["requests"][-1]["received"] == 100
    command(team, "owner", "respond", {"offer_id": pending["id"], "decision": "accepted"}, 409)
    command(team, "admin", "offer", {**assignment, "quantity": 1}, 409)


def test_dependency_graph_blocks_acceptance_and_rejects_cycles(team):
    a = asset(team)
    o1 = offer(team, a, need(team))
    o2 = offer(team, a, need(team))
    due = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    command(team, "admin", "assignment_details", {"offer_id": o2["id"], "depends_on": [o1["id"]], "due_at": due, "reason": "First group must finish before second"})
    command(team, "admin", "assignment_details", {"offer_id": o1["id"], "depends_on": [o2["id"]], "reason": "Invalid reverse dependency"}, 409)
    command(team, "owner", "respond", {"offer_id": o2["id"], "decision": "accepted"}, 409)
    command(team, "owner", "respond", {"offer_id": o1["id"], "decision": "accepted"})
    evidence = source(team, "EXERCISE 30 people arrived; first group outcome.", "owner")
    command(team, "owner", "progress", {"offer_id": o1["id"], "state": "reported_complete", "source_id": evidence})
    command(team, "admin", "review_outcome", {"offer_id": o1["id"], "source_id": evidence})
    d = command(team, "owner", "respond", {"offer_id": o2["id"], "decision": "accepted"})
    assert d["coordination"]["offers"][-1]["blocked_by"] == []


def test_transfer_is_acceptance_not_completion_and_close_requires_fresh_handover(team):
    r = need(team)
    command(team, "admin", "close", {"reason": "Should not close unresolved work"}, 409)
    command(team, "admin", "request_update", {"request_id": r["id"], "state": "transfer_pending", "recipient": "rda", "reference": "RECOVERY-EXERCISE-001", "reason": "Transfer to receiving recovery team"})
    command(team, "owner", "accept_transfer", {"request_id": r["id"], "reason": "Wrong organisation cannot accept"}, 403)
    road, _ = team["join"]("recoveryowner", "rda", ["agency_supervisor"])
    d = get(team)
    response = road.post(team["base"] + "/commands", json={"version": d["version"], "request_id": uuid.uuid4().hex, "action": "accept_transfer", "data": {"request_id": r["id"], "reason": "Ownership accepted under recovery reference"}})
    assert response.status_code == 200, response.text
    assert response.json()["coordination"]["requests"][-1]["status"] == "transferred_unresolved"
    command(team, "admin", "phase", {"phase": "recovery", "reason": "Response work handed to recovery"})
    command(team, "admin", "close", {"reason": "Still needs accepted handover"}, 409)
    d = command(team, "admin", "document", {"kind": "handover", "audience": "Receiving recovery supervisor"})
    item = d["coordination"]["documents"][-1]
    assert "transferred_unresolved" in item["body"] and "outcome remains unresolved" in item["body"]
    command(team, "admin", "approve_document", {"document_id": item["id"], "hash": item["hash"]})
    command(team, "owner", "accept_handover", {"document_id": item["id"], "hash": item["hash"]})
    d = command(team, "admin", "close", {"reason": "Recovery ownership accepted with unresolved item retained"})
    assert d["status"] == "closed"
    command(team, "contributor", "contribute", {"title": "Late report", "text": "EXERCISE late evidence", "observed_at": now_iso()}, 409)
    d = command(team, "admin", "reopen", {"reason": "New evidence requires reopening and review"})
    assert d["status"] == "active" and d["coordination"]["phase"] == "response"


def test_finance_return_reject_cancel_and_reconciliation(team):
    evidence = source(team, "EXERCISE relief request LKR 12000.00")
    def request():
        d = command(team, "contributor", "finance_request", {"purpose": "Exercise supplies", "amount": "12000.00", "source_id": evidence, "head": "EXERCISE-RELIEF", "scheme_id": "fictional-relief-v1"})
        return d["coordination"]["finance"][-1]
    f = request()
    command(team, "admin", "finance_decision", {"finance_id": f["id"], "decision": "returned", "reason": "Please obtain a revised scope"})
    command(team, "finance", "finance_decision", {"finance_id": f["id"], "decision": "rejected", "reason": "Replaced by a corrected request"})
    f = request()
    command(team, "admin", "finance_action", {"finance_id": f["id"], "state": "recommended"})
    command(team, "finance", "finance_action", {"finance_id": f["id"], "state": "approved", "amount": "12000.00"})
    for who, state in (("owner", "committed"), ("finance", "released"), ("owner", "spent")):
        sid = source(team, f"EXERCISE {state} voucher LKR 12000.00", who)
        command(team, who, "finance_action", {"finance_id": f["id"], "state": state, "amount": "12000.00", "source_id": sid})
    command(team, "finance", "finance_decision", {"finance_id": f["id"], "decision": "cancelled", "reason": "Cannot cancel money already evidenced"}, 409)
    sid = source(team, "EXERCISE reconciliation statement: balance 0.00 LKR; commitment, release and expenditure checked.", "finance")
    d = command(team, "finance", "finance_reconcile", {"finance_id": f["id"], "source_id": sid, "quote": "balance 0.00 LKR", "balance": "0.00"})
    assert d["coordination"]["finance"][-1]["status"] == "reconciled"
    command(team, "finance", "finance_action", {"finance_id": f["id"], "state": "released", "amount": "1.00", "source_id": sid}, 409)


def test_revised_documents_preserve_approved_originals_and_need_new_review(team):
    sid = source(team, "EXERCISE Kotmale water remains unavailable.")
    d = command(team, "admin", "document", {"kind": "press_release", "audience": "Exercise public audience"})
    item = d["coordination"]["documents"][-1]
    command(team, "admin", "approve_document", {"document_id": item["id"], "hash": item["hash"]})
    text = f"EXERCISE PUBLIC DRAFT: Kotmale water is reported unavailable. Confirmation is pending. Evidence [{sid}]. No public protective instruction is issued."
    d = command(team, "admin", "revise_document", {"document_id": item["id"], "body": text, "source_ids": [sid], "reason": "Concise evidence-linked exercise wording"})
    old, revised = d["coordination"]["documents"][-2:]
    assert old["body"] == item["body"] and old["status"] == "approved_exercise" and revised["status"] == "draft"
    command(team, "admin", "approve_document", {"document_id": revised["id"], "hash": revised["hash"]})
    d = command(team, "admin", "retract_document", {"document_id": old["id"], "reason": "Superseded by the reviewed revision"})
    assert d["coordination"]["documents"][-2]["status"] == "retracted_exercise"
