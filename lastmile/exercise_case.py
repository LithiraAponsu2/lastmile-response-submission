"""Reproducible, explicitly fictional start-to-finish API/ADK case.

Runs only against a caller-provided fresh isolated exercise server. Nothing here
connects a dispatch provider, publishes a warning, or transfers money.
"""
import base64
import json
import uuid
from datetime import datetime, timedelta, timezone

from .workspace import now_iso


class FullExercise:
    incident_title = "Kotmale full response exercise · fictional"

    def __init__(self, client_factory, setup_key, *, real_model=False):
        self.factory, self.key, self.real_model = client_factory, setup_key, real_model
        self.clients, self.steps, self.turns = {}, [], []
        self.password = uuid.uuid4().hex
        self.base = ""

    def check(self, name, condition, **evidence):
        self.steps.append({"check": name, "passed": bool(condition), **evidence})
        if not condition:
            raise AssertionError(name)

    def post(self, client, path, body, expected=200, **kwargs):
        response = client.post(path, json=body, **kwargs)
        self.check(f"{path.rsplit('/', 1)[-1]} returned {expected}", response.status_code == expected,
                   actual=response.status_code)
        return response.json()

    def get(self, who="central"):
        response = self.clients[who].get(self.base)
        self.check("Incident read authorised", response.status_code == 200)
        return response.json()

    def action(self, who, action, data, expected=200):
        doc = self.get(who)
        return self.post(self.clients[who], self.base + "/commands", {"version": doc["version"], "request_id": uuid.uuid4().hex, "action": action, "data": data}, expected)

    def contribute(self, who, title, text, *, attachment=False):
        body = {"title": title, "text": text, "observed_at": now_iso()}
        if attachment:
            body["attachment"] = {"name": "fictional-report.txt", "base64": base64.b64encode(text.encode()).decode()}
        doc = self.action(who, "contribute", body)
        source = doc["sources"][-1]
        self.action("central", "review_source", {"source_id": source["id"], "decision": "accepted"})
        return source["id"]

    def actor(self, username, organisation, roles):
        invitation = self.post(self.clients["central"], "/api/teams/invitations", {"organisation": organisation, "roles": roles, "districts": ["Nuwara Eliya"]})
        client = self.factory()
        self.post(client, "/api/teams/join", {"username": username, "name": "Fictional " + username, "password": self.password, "invitation": invitation["token"]})
        self.post(client, "/api/teams/login", {"username": username, "password": self.password})
        self.clients[username] = client

    def chat(self, who, natural, action=None, data=None):
        message = natural if self.real_model or action is None else "/propose " + action + " " + json.dumps(data)
        doc = self.post(self.clients[who], self.base + "/chat", {"text": message, "request_id": uuid.uuid4().hex})
        chat = doc["coordination"]["chat"]
        turn = chat["turns"][-1]
        self.check("Actual ADK turn completed", turn["status"] == "completed", runtime=turn.get("runtime"))
        self.check("Real model used when requested", not self.real_model or turn["runtime"] == "adk-vertex")
        self.turns.append({"actor": who, "question": message, "answer": chat["messages"][-1]["text"], "runtime": turn.get("runtime"), "finish_reason": turn.get("finish_reason"), "usage": turn.get("usage"), "tools": turn.get("tools"), "steps": turn["steps"]})
        if action:
            # The turn's plan card, prepared for this person; its last step is the expected change.
            pending = [p for p in doc["coordination"]["plans"] if p["status"] == "pending" and p["origin"] == "chat" and p["current"]]
            self.check("Expected typed proposal exists", bool(pending) and pending[-1]["steps"][-1]["action"] == action, expected_action=action)
            plan = pending[-1]
            doc = self.post(self.clients[who], self.base + f"/plans/{plan['id']}/approve", {"hash": plan["hash"], "request_id": uuid.uuid4().hex})
        return doc

    def prepare(self):
        central = self.factory()
        self.clients["central"] = central
        self.check("Fresh isolated exercise workspace", central.get("/api/teams/setup").json()["setup_required"])
        self.post(central, "/api/teams/bootstrap", {"username": "case-central", "name": "Fictional central coordinator", "password": self.password}, headers={"X-Setup-Key": self.key})
        self.post(central, "/api/teams/login", {"username": "case-central", "password": self.password})
        for name, org, roles in (("reporter", "division", ["contributor"]), ("shelter", "division", ["agency_supervisor", "contributor"]), ("water", "water", ["agency_supervisor", "contributor"]), ("transport", "transport", ["agency_supervisor", "contributor"]), ("finance", "ndrsc", ["finance_approver", "contributor"])):
            self.actor(name, org, roles)

    def on_opened(self):
        """Optional extra collaboration checks against the newly opened case."""

    def before_handover(self):
        """Optional extra response work, before the final reviewed snapshot."""

    def run(self):
        self.prepare()
        central = self.clients["central"]
        doc = self.post(central, "/api/teams/incidents", {"title": self.incident_title, "district": "Nuwara Eliya", "participants": ["division", "ndrsc", "transport"]})
        self.base = "/api/teams/incidents/" + doc["id"]
        self.action("central", "participant", {"organisation": "water"})
        self.on_opened()
        need_source = self.contribute("reporter", "Local accommodation and relief request · fictional", "EXERCISE Kotmale: 20 people need accommodation and 100 packs of relief supplies. Requested support LKR 12000.00.", attachment=True)
        report = "EXERCISE Kotmale hall readiness\nCurrent capacity: 100 people\nOccupied: 40 people\nExternal pending: 0 people\nStatus: accepting\nAccess: passable\nWater: unavailable\nPower: working"
        hall_source = self.contribute("shelter", "Owner readiness · fictional", report)
        values = {"capacity": (100, "Current capacity: 100 people"), "occupied": (40, "Occupied: 40 people"), "external_pending": (0, "External pending: 0 people"), "status": ("accepting", "Status: accepting"), "access": ("passable", "Access: passable"), "water": ("unavailable", "Water: unavailable"), "power": ("working", "Power: working")}
        doc = self.action("shelter", "confirm_asset", {"name": "Kotmale exercise hall · fictional", "kind": "shelter", "latitude": 7.02, "longitude": 80.59, "source_id": hall_source, "facts": {k: {"value": v, "quote": q} for k, (v, q) in values.items()}})
        hall = doc["coordination"]["assets"][-1]
        self.check("Water blocks numerical shelter headroom", hall["balance"]["available"] is None)
        doc = self.action("reporter", "request", {"title": "Accommodate 20 people", "kind": "shelter", "quantity": 20, "unit": "people", "source_id": need_source})
        accommodation = doc["coordination"]["requests"][-1]
        self.action("central", "offer", {"request_id": accommodation["id"], "asset_id": hall["id"], "quantity": 20}, 409)
        self.chat("central", "Explain why we cannot accommodate the 20 people in the registered hall yet. Cite the source evidence and identify the responsible body. Do not create any action.")
        sid = self.contribute("reporter", "Water verification request · fictional", "EXERCISE Kotmale request: 1 inspection of the hall water supply is needed.")
        doc = self.action("reporter", "request", {"title": "Verify water restoration", "kind": "water", "quantity": 1, "unit": "inspection", "source_id": sid})
        water_need = doc["coordination"]["requests"][-1]
        doc = self.action("central", "offer", {"request_id": water_need["id"], "recipient": "water", "quantity": 1, "note": "Exercise verification request; receiving body retains responsibility"})
        water_work = doc["coordination"]["offers"][-1]
        self.action("central", "assignment_details", {"offer_id": water_work["id"], "due_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), "depends_on": [], "reason": "Confirm service before the exercise accommodation commitment"})
        self.action("water", "respond", {"offer_id": water_work["id"], "decision": "accepted"})
        self.action("water", "progress", {"offer_id": water_work["id"], "state": "mobilised"})
        first = self.action("water", "claim", {"topic": "water", "location": "Kotmale", "field": "supply", "value": "unavailable", "unit": "status", "source_id": self.contribute("water", "Water interruption · fictional", "EXERCISE Kotmale water is unavailable."), "quote": "Kotmale water is unavailable."})["coordination"]["claims"][-1]
        water_source = self.contribute("water", "Water restoration · fictional", "EXERCISE Kotmale water is working after restoration verification.")
        self.action("water", "progress", {"offer_id": water_work["id"], "state": "reported_complete", "source_id": water_source})
        self.action("central", "review_outcome", {"offer_id": water_work["id"], "source_id": water_source})
        doc = self.action("water", "claim", {"topic": "water", "location": "Kotmale", "field": "supply", "value": "working", "unit": "status", "source_id": water_source, "quote": "Kotmale water is working after restoration verification."})
        latest = doc["coordination"]["claims"][-1]
        self.check("Contradiction remains explicit before review", latest["status"] == "disputed")
        self.action("central", "resolve_claim", {"claim_id": latest["id"], "supersedes": [first["id"]], "reason": "Water provider verified restoration; preserve older interruption as history"})
        confirmed = self.contribute("shelter", "Facility owner service confirmation · fictional", "EXERCISE Kotmale hall: Water: working. Owner confirms the restored on-site supply.")
        self.action("shelter", "confirm_asset", {"asset_id": hall["id"], "source_id": confirmed, "facts": {"water": {"value": "working", "quote": "Water: working"}}})
        doc = self.chat("central", f"Prepare an exercise offer for request {accommodation['id']}: allocate 20 people to confirmed shelter {hall['id']}. No real dispatch. Prepare the exact offer for my review.", "offer", {"request_id": accommodation["id"], "asset_id": hall["id"], "quantity": 20, "note": "Exercise owner-confirmed accommodation"})
        allocation = doc["coordination"]["offers"][-1]
        self.check("Proposal alone holds no capacity", doc["coordination"]["assets"][0]["balance"]["available"] == 60)
        self.action("central", "assignment_details", {"offer_id": allocation["id"], "depends_on": [water_work["id"]], "reason": "Owner acceptance depends on reviewed water verification"})
        doc = self.action("shelter", "respond", {"offer_id": allocation["id"], "decision": "accepted", "note": "Owner accepts exercise commitment"})
        self.check("Owner acceptance holds exactly 20 places", doc["coordination"]["assets"][0]["balance"]["available"] == 40)
        transport_source = self.contribute("transport", "Vehicle readiness · fictional", "EXERCISE Kotmale vehicle availability. Current capacity: 2 vehicles\nOccupied: 0 vehicles\nExternal pending: 0 vehicles\nStatus: accepting\nAccess: passable")
        vals = {"capacity": (2, "Current capacity: 2 vehicles"), "occupied": (0, "Occupied: 0 vehicles"), "external_pending": (0, "External pending: 0 vehicles"), "status": ("accepting", "Status: accepting"), "access": ("passable", "Access: passable")}
        doc = self.action("transport", "confirm_asset", {"name": "Exercise vehicle pool · fictional", "kind": "transport", "source_id": transport_source, "facts": {k: {"value": v, "quote": q} for k, (v, q) in vals.items()}})
        vehicles = doc["coordination"]["assets"][-1]
        transport_need = self.contribute("reporter", "Transport request · fictional", "EXERCISE Kotmale request: 1 vehicles needed for the exercise group transfer. Seats and route are confirmed outside this prototype.")
        doc = self.action("reporter", "request", {"title": "Exercise group transfer", "kind": "transport", "quantity": 1, "unit": "vehicles", "source_id": transport_need})
        transport_request = doc["coordination"]["requests"][-1]
        doc = self.action("central", "offer", {"request_id": transport_request["id"], "asset_id": vehicles["id"], "quantity": 1, "note": "Exercise transport; no live route safety inferred"})
        trip = doc["coordination"]["offers"][-1]
        self.action("transport", "respond", {"offer_id": trip["id"], "decision": "accepted"})
        for who, assignment in (("transport", trip), ("shelter", allocation)):
            self.action(who, "progress", {"offer_id": assignment["id"], "state": "mobilised"})
            sid = self.contribute(who, who + " outcome · fictional", "EXERCISE Kotmale outcome: group arrival confirmed; exercise assignment complete. " + who)
            self.action(who, "progress", {"offer_id": assignment["id"], "state": "reported_complete", "source_id": sid})
            self.action("central", "review_outcome", {"offer_id": assignment["id"], "source_id": sid})
        for who, resource, assignment, number, unit in (("shelter", hall, allocation, 60, "people"), ("transport", vehicles, trip, 0, "vehicles")):
            sid = self.contribute(who, "Reconciled owner snapshot · fictional", f"EXERCISE Kotmale resource snapshot. Occupied: {number} {unit} after completed exercise arrival/vehicle return.")
            self.action(who, "confirm_asset", {"asset_id": resource["id"], "source_id": sid, "reconcile_offer_ids": [assignment["id"]], "facts": {"occupied": {"value": number, "quote": f"Occupied: {number} {unit}"}}})
        doc = self.action("reporter", "request", {"title": "100 relief packs", "kind": "supplies", "quantity": 100, "unit": "packs", "source_id": need_source})
        supply = doc["coordination"]["requests"][-1]
        for quantity in (60, 40):
            sid = self.contribute("shelter", f"Receipt {quantity} · fictional", f"EXERCISE Kotmale receipt: {quantity} packs received by the nominated receiving supervisor.")
            doc = self.action("shelter", "receipt", {"request_id": supply["id"], "quantity": quantity, "unit": "packs", "source_id": sid, "quote": f"{quantity} packs received"})
            if quantity == 60:
                self.action("shelter", "receipt", {"request_id": supply["id"], "quantity": quantity, "unit": "packs", "source_id": sid, "quote": f"{quantity} packs received"}, 409)
        self.check("Partial receipts fulfil exactly 100 packs", doc["coordination"]["requests"][-1]["remaining"] == 0)
        doc = self.action("reporter", "finance_request", {"purpose": "Fictional relief support", "amount": "12000.00", "source_id": need_source, "head": "EXERCISE-RELIEF", "scheme_id": "fictional-relief-v1"})
        funding = doc["coordination"]["finance"][-1]
        self.action("central", "finance_action", {"finance_id": funding["id"], "state": "approved", "amount": "12000.00"}, 403)
        self.action("central", "finance_action", {"finance_id": funding["id"], "state": "recommended"})
        self.action("finance", "finance_action", {"finance_id": funding["id"], "state": "approved", "amount": "12000.00"})
        for who, state in (("shelter", "committed"), ("finance", "released"), ("shelter", "spent")):
            sid = self.contribute(who, state + " voucher · fictional", f"EXERCISE {state} LKR 12000.00 for Kotmale relief support.")
            self.action(who, "finance_action", {"finance_id": funding["id"], "state": state, "amount": "12000.00", "source_id": sid})
        sid = self.contribute("finance", "Reconciliation statement · fictional", "EXERCISE Kotmale reconciliation: balance 0.00 LKR; commitments, releases and expenditure agree.")
        self.action("finance", "finance_reconcile", {"finance_id": funding["id"], "source_id": sid, "quote": "balance 0.00 LKR", "balance": "0.00"})
        for kind in ("press_release", "email", "sms"):
            doc = self.action("central", "document", {"kind": kind, "audience": "Exercise participants only", "language": "en"})
            item = doc["coordination"]["documents"][-1]
            self.action("central", "approve_document", {"document_id": item["id"], "hash": item["hash"]})
        self.before_handover()
        self.action("central", "phase", {"phase": "recovery", "reason": "Reviewed arrivals, receipts and finance complete; prepare receiving handover"})
        doc = self.chat("central", "Prepare a handover document for the incoming exercise supervisor. Include the reviewed outcomes, resource reconciliation, supply receipts, financial status and any unresolved work. Prepare for my exact review.", "document", {"kind": "handover", "audience": "Incoming exercise supervisor", "language": "en"})
        item = doc["coordination"]["documents"][-1]
        self.check("No hidden work blockers before handover", doc["coordination"]["closure_blockers"] == [])
        self.action("central", "approve_document", {"document_id": item["id"], "hash": item["hash"]})
        self.action("shelter", "accept_handover", {"document_id": item["id"], "hash": item["hash"]})
        doc = self.action("central", "close", {"reason": "Exercise complete: reviewed outcomes, receipts, reconciliation and receiving handover accepted"})
        self.check("End state is closed", doc["status"] == "closed")
        self.check("Every need is fulfilled", all(r["status"] == "fulfilled" for r in doc["coordination"]["requests"]))
        self.check("Money remains fictional and reconciled", doc["coordination"]["finance"][0]["status"] == "reconciled")
        self.check("No communication was actually sent", all(d["delivery"] == "not_sent_exercise_artifact" for d in doc["coordination"]["documents"]))
        self.action("reporter", "request", {"title": "Must not mutate closed case"}, 409)
        self.chat("central", "Summarise this closed fictional exercise, cite the evidence, and distinguish completed outcomes from unsent communications. Do not propose or change anything.")
        doc = self.get()
        return {"label": "Fictional Kotmale end-to-end exercise; not a historical replay", "incident_id": doc["id"], "final_status": doc["status"], "steps": self.steps, "turns": self.turns, "final_incident": doc, "production_changed": False, "real_dispatch": False, "real_payments": False}

    def close_clients(self):
        for client in self.clients.values():
            client.close()
