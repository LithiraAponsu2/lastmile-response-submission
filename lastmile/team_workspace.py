"""Code-owned multi-agency exercise transitions, evidence and commitments.

All commands execute against one incident version in one store transaction.
The model can propose these commands but cannot approve them or select its actor.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from .team_accounts import ACTIVE, DISTRICTS, ORGANISATIONS, require
from .checks import injection_scan
from .workspace import WorkspaceError, digest, now_iso, record_event, timestamp, uid

COMMAND_FIELDS = {
    "contribute": {"title", "text", "observed_at", "published_at", "attachment", "recipients", "message"},
    "notify_report": {"source_id", "recipients", "note"},
    "read_notification": {"notification_id"},
    "acknowledge": {"notification_id", "note"},
    "review_source": {"source_id", "decision"},
    "confirm_asset": {"asset_id", "name", "kind", "source_id", "latitude", "longitude", "facts", "reconcile_offer_ids", "resource_key"},
    "request": {"title", "kind", "quantity", "unit", "source_id", "recipient"},
    "offer": {"request_id", "asset_id", "quantity", "recipient", "note"},
    "respond": {"offer_id", "decision", "note"},
    "progress": {"offer_id", "state", "source_id", "note"},
    "review_outcome": {"offer_id", "source_id"},
    "finance_request": {"purpose", "amount", "source_id", "head", "scheme_id"},
    "finance_action": {"finance_id", "state", "amount", "source_id"},
    "document": {"kind", "audience", "language"},
    "approve_document": {"document_id", "hash"},
    "accept_handover": {"document_id", "hash"},
}
KINDS = {"shelter", "transport", "team", "road", "power", "water", "supplies", "other"}
from .team_operations import EXTRA_FIELDS, apply_extra, extend_view, pending_dependencies, document_sections
from .team_shared import resource_snapshot, pool_approved
from .team_clock import business_now
COMMAND_FIELDS.update(EXTRA_FIELDS)
from .team_asks import ASK_FIELDS, apply_ask, ask_view
COMMAND_FIELDS.update(ASK_FIELDS)
from .team_areas import AREA_FIELDS, apply_area, picture, vocabulary
COMMAND_FIELDS.update(AREA_FIELDS)
from . import team_participation as participation
COMMAND_FIELDS.update(participation.PARTICIPATION_FIELDS)
from . import team_messages as messages
COMMAND_FIELDS.update(messages.MESSAGE_FIELDS)
COMMAND_FIELDS["document"] = COMMAND_FIELDS["document"] | messages.DOCUMENT_EXTRA | {"to"}
from . import team_letters as letters
COMMAND_FIELDS.update(letters.LETTER_FIELDS)
from . import stock_ledger
COMMAND_FIELDS.update(stock_ledger.FIELDS)


def clean(value, label, maximum=2000, minimum=1):
    require(isinstance(value, str) and minimum <= len(value.strip()) and len(value) <= maximum, f"{label}: enter {minimum}–{maximum} characters.", 422)
    return value.strip()


def count(value, *, optional=False):
    if optional and value is None:
        return None
    require(type(value) is int and 0 <= value <= 1000000, "Quantity must be a whole number from 0 to 1,000,000; unknown is null.", 422)
    return value


def money(value):
    try:
        require(isinstance(value, str) and bool(re.fullmatch(r"\d{1,10}(?:\.\d{1,2})?", value)), "Amount must be a positive LKR decimal string, with at most two decimal places.", 422)
        amount = Decimal(value)
        require(amount > 0, "Amount must be greater than zero.", 422)
        return amount.quantize(Decimal("0.01"))
    except InvalidOperation:
        raise WorkspaceError("Enter a valid LKR amount.", 422) from None


def exact_amount(text, amount):
    pattern = str(int(amount)) + r"(?:\.00)?" if amount == amount.to_integral_value() else re.escape(str(amount))
    return bool(re.search(r"(?<![\d.])" + pattern + r"(?![\d.])", text.replace(",", "")))


def find(records, record_id):
    record = next((r for r in records if r["id"] == record_id), None)
    require(record is not None, "Record not found in this incident.", 404)
    return record


def has(actor, *roles):
    return bool(set(actor["roles"]) & set(roles))


def command_hash(action, data):
    return digest(json.dumps({"action": action, "data": data}, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


class TeamWorkspace:
    def __init__(self, store):
        self.store = store

    def in_scope(self, doc, actor):
        """Whether this person may know the incident exists: same demo/real world and district scope."""
        c = doc.get("coordination")
        if not c or doc.get("internal"):
            return False
        if actor.get("demo"):
            return c.get("demo") is True
        return "*" in actor["districts"] or c["district"] in actor["districts"]

    def access(self, doc, actor):
        c = doc.get("coordination")
        require(c and not doc.get("internal"), "Incident not found.", 404)
        if actor.get("demo"):
            require(c.get("demo") is True, "Role simulation can only access demo incidents.", 403)
        require(self.in_scope(doc, actor), "This incident is outside your district scope.", 403)
        # Only invited (or approved) organisations enter. Others can ask to join.
        require(actor["organisation"] in c["participants"], "Your organisation is not part of this incident. Ask to join from its card.", 403)
        return c

    def request_join(self, incident_id, actor, note):
        doc = self.store.get(incident_id)
        require(self.in_scope(doc, actor), "Incident not found.", 404)
        def apply(d):
            request = participation.request_join(d, actor, note)
            record_event(d, actor["id"], "join_request", f"{actor['organisation']} asked to join" + (f": {request['note']}" if request["note"] else ""))
        return self.store.mutate(incident_id, doc["version"], actor["id"], apply)

    def get(self, incident_id, actor):
        doc = self.store.get(incident_id)
        self.access(doc, actor)
        return doc

    def create(self, actor, title, district, participants, hazard=None):
        # Any team can open an incident; its organisation leads and invites the bodies it needs.
        require(has(actor, *participation.ACTING), "Observers can follow incidents but not open them.", 403)
        require(not has(actor, "partner"), "Partners take part in incidents they are invited to; they do not open them.", 403)
        require(hazard is None or hazard in participation.HAZARDS, "Choose a known incident type.", 422)
        require(district in DISTRICTS and ("*" in actor["districts"] or district in actor["districts"]), "Select a district within your membership.", 403)
        require(set(participants) <= {o["id"] for o in ORGANISATIONS}, "Unknown participant organisation.", 422)
        doc = self.store.new_document(clean(title, "Incident title", 120, 3))
        if actor.get("demo"):
            require(set(participants) <= set(ACTIVE), "Choose organisations active in this demo.", 422)
        doc["coordination"] = {"incident_id": doc["id"], "demo": bool(actor.get("demo")), "district": district, "participants": sorted(set(participants + [actor["organisation"]])),
            "assets": [], "requests": [], "offers": [], "asks": [], "area_status": [], "area_figures": [], "warnings": [], "finance": [], "documents": [], "replays": {}, "chats": {},
            "schemes": [{"id": "fictional-relief-v1", "name": "Relief fund", "currency": "LKR", "budget": "500000.00", "limit": "100000.00", "owner": "ndrsc", "policy": "exercise_only", "head": "EXERCISE-RELIEF"}]}
        participation.start(doc["coordination"], actor, doc["coordination"]["participants"], hazard)
        record_event(doc, actor["id"], "Coordination exercise opened", f"{district}; no government integration or real dispatch")
        return self.store.insert_document(doc)

    def view(self, doc, actor):
        c = self.access(doc, actor)
        result = copy.deepcopy(doc)
        out = result["coordination"]
        out.pop("replays", None)
        # Each account's durable chat remains private even within an organisation.
        out["chat"] = out.pop("chats", {}).get(actor["id"], {"messages": [], "turns": [], "proposals": []})
        # Plans the AI prepared: a person's own from chat, or suggestions for their organisation and role.
        from . import team_plans as plans
        out["plans"] = [{**p, "current": plans.is_current(p, doc), "said": None} for p in plans.visible(out.get("plans", []), actor)]
        # A public requester's phone number is for the lead body and the body asked to help, nobody else.
        from .team_participation import lead_org
        for item in out.get("public_requests", []):
            handlers = {lead_org(c)} | {a["to"] for a in out.get("asks", []) if a.get("public_request_id") == item["id"]}
            if actor["organisation"] not in handlers:
                item["phone"] = None
        # The operational picture, including fictional funding, is shared.
        # Only actions are role-gated; per-persona chat remains private.
        for s in result["sources"]:
            if s.get("attachment"):
                s["attachment"].pop("base64", None)
        for a in out["assets"]:
            a["balance"] = self.balance(c, a)
        out.setdefault("asks", [])
        ask_view(out, actor)
        out["my_queue"] = [o["id"] for o in out["offers"] if o["recipient"] == actor["organisation"] and o["status"] in {"offered", "accepted", "mobilised", "reported_complete"}]
        # Who was asked to acknowledge each report, and who has, is part of the shared picture.
        for s in result["sources"]:
            asked = {}
            for n in out.get("notifications", []):
                if n["source_id"] != s["id"]:
                    continue
                for org in n["recipients"]:
                    ack = n.get("acks", {}).get(org)
                    if org not in asked or ack:
                        asked[org] = {"organisation": org, "requested_by": n["from"], "requested_at": n["at"], "required": bool(n.get("ack_required")),
                                      "acknowledged_at": ack and ack["at"], "acknowledged_by": ack and ack["by"], "note": ack and ack.get("note") or ""}
            s["acknowledgements"] = sorted(asked.values(), key=lambda a: a["organisation"])
        out["notifications"] = [n for n in out.get("notifications", []) if actor["organisation"] in n["recipients"]]
        out["policy"] = "EXERCISE: refresh resource facts within 60 minutes; accepted reservations expire after 30 minutes. No safe route, admission, real dispatch or payment is guaranteed."
        extend_view(result)
        if has(actor, "partner"):
            # Partners see the shared operational picture, never funding.
            out["finance"], out["schemes"] = [], []
            out["asks"] = [a for a in out["asks"] if a["kind"] != "funding"]
        out["picture"] = picture(result)
        # Layer geometry is fetched separately; polling the incident never resends it.
        out["map_layers"] = [{k: v for k, v in x.items() if k != "geojson"} for x in out.get("map_layers", [])]
        out["area_vocabulary"] = vocabulary()
        out["participation_view"] = participation.view(out, actor)
        return result

    def source(self, doc, source_id, *, accepted=True):
        s = find(doc["sources"], source_id)
        require(not accepted or s["status"] == "accepted", "An accepted source is required.")
        return s

    def balance(self, c, asset):
        try:
            facts, shared_offers, shared = resource_snapshot(self.store, c, asset)
        except WorkspaceError as error:
            return {"available": None, "reason": str(error), "shared": True}
        now = business_now(c)
        required = ["capacity", "occupied", "external_pending", "status", "access"]
        if asset["kind"] == "shelter":
            required += ["water", "power"]
        if any(k not in facts for k in required):
            return {"available": None, "reason": "Current capacity, occupancy, external pending and readiness facts must all be confirmed."}
        if any(not 0 <= (now - timestamp(facts[k]["observed_at"])).total_seconds() <= 3600 for k in required):
            return {"available": None, "reason": "Resource evidence is stale or future-dated. Request an owner update."}
        if facts["status"]["value"] != "accepting" or facts["access"]["value"] != "passable" or any(facts[k]["value"] != "working" for k in ("water", "power") if k in required):
            return {"available": None, "reason": "Readiness dependencies are unresolved. Ask the responsible body to confirm them."}
        # Arrival is NOT subtracted from a new occupancy snapshot: newer owner
        # snapshots must explicitly reconcile arrived reservations first.
        local_offers = [o for o in c["offers"] if o.get("asset_id") == asset["id"]]
        held = sum(o["quantity"] for o in local_offers + shared_offers if
                   (o["status"] in {"accepted", "mobilised"} and timestamp(o["expires_at"]) > now or o["status"] in {"reported_complete", "completed"} and not o.get("reconciled")))
        available = facts["capacity"]["value"] - facts["occupied"]["value"] - facts["external_pending"]["value"] - held
        return {"available": max(0, available), "held": held, "shared": shared, "reason": "Exercise planning balance" + (" across owner-linked incidents" if shared else "") + "; owner acceptance is still required."}

    def execute(self, incident_id, actor, version, request_id, action, data):
        clean(request_id, "Request identifier", 100, 8)
        require(action in COMMAND_FIELDS, "Unsupported action.", 422)
        require(isinstance(data, dict) and set(data) <= COMMAND_FIELDS[action], "Unexpected action fields.", 422)
        fingerprint = command_hash(action, data)
        current = self.get(incident_id, actor)
        prior = current["coordination"]["replays"].get(request_id)
        if prior:
            require(prior == {"hash": fingerprint, "actor": actor["id"]}, "This request identifier was already used for a different command.")
            return current
        # Stock is taken from the shared record before the save, and given back if the save fails.
        holds = stock_ledger.prepare(self, current, actor, lambda d: self.apply(d, actor, action, data)) if action in stock_ledger.FIELDS else []
        changes = {}
        def apply(doc):
            self.access(doc, actor)
            saved = doc["coordination"]["replays"].get(request_id)
            if saved:
                require(saved == {"hash": fingerprint, "actor": actor["id"]}, "This request identifier was already used for a different command.")
                return
            detail = self.apply(doc, actor, action, data)
            stock_ledger.attach(doc, holds)
            changes.update(stock_ledger.follow_requests(self, doc))
            doc["coordination"]["replays"][request_id] = {"hash": fingerprint, "actor": actor["id"]}
            record_event(doc, actor["id"], action, detail or f"{actor['organisation']} · Exercise only")
        if action in {"read_notification", "acknowledge"}:
            # Reading or acknowledging a shared report depends only on that
            # notification, never on the rest of the incident. Background tabs may
            # hold an older business version, so re-read and retry only these
            # actor-scoped operations; never silently retry an allocation or
            # approval against a changed snapshot. A read is metadata; an
            # acknowledgement is a recorded event and advances the version.
            for attempt in range(3):
                current = self.get(incident_id, actor)
                try:
                    return self.store.mutate(incident_id, current["version"], actor["id"], apply, metadata=action == "read_notification")
                except WorkspaceError as exc:
                    if exc.status != 409 or attempt == 2:
                        raise
        token = stock_ledger.TAKEN.set(holds)
        try:
            result = self.store.mutate(incident_id, version, actor["id"], apply)
        except Exception:
            if holds:
                stock_ledger.release(self, holds)
            raise
        finally:
            stock_ledger.TAKEN.reset(token)
        if any(changes.values()):
            stock_ledger.settle(self, changes)
        if holds and getattr(self, "on_stock_taken", None):
            self.on_stock_taken({h["stock_id"] for h in holds}, incident_id)
        return result

    def apply(self, doc, actor, action, d):
        c = self.access(doc, actor)
        c.setdefault("incident_id", doc["id"])
        require(action in COMMAND_FIELDS and set(d) <= COMMAND_FIELDS[action], "Unexpected action fields.", 422)
        require(doc["status"] != "closed" or action in {"reopen", "read_notification"}, "This incident is closed. Reopen it with a reason before making changes.")
        if action != "read_notification":
            require(participation.is_active(c, actor["organisation"]), "Your organisation has stood down from this incident. A lead can add it again.", 403)
        if action in participation.PARTICIPATION_FIELDS:
            return participation.apply_participation(doc, actor, action, d)
        if action in EXTRA_FIELDS:
            apply_extra(self, doc, actor, action, d)
            return
        if action in ASK_FIELDS:
            return apply_ask(doc, actor, action, d)
        if action in AREA_FIELDS:
            return apply_area(doc, actor, action, d)
        if action in messages.MESSAGE_FIELDS:
            return messages.apply_message(self, doc, actor, action, d)
        if action in letters.LETTER_FIELDS:
            return letters.share(self, doc, actor, d)
        if action in stock_ledger.FIELDS:
            require(has(actor, "coordinator", "agency_supervisor"), "A coordinator or supervisor reserves stock.", 403)
            return stock_ledger.apply_reservation(self, doc, actor, d)
        org = actor["organisation"]
        effective_now = business_now(c)
        if action == "contribute":
            require(has(actor, "contributor", "agency_supervisor", "coordinator", "finance_approver"), "Your account cannot submit reports.", 403)
            attached = None
            if d.get("attachment"):
                from .team_documents import attachment
                require(sum(s["attachment"]["bytes"] for s in doc["sources"] if s.get("attachment")) < 8000000, "This incident's attachment limit is reached.", 413)
                attached, extracted = attachment(d["attachment"])
                text = clean(extracted, "Extracted report", 60000, 10)
            else:
                text = d.get("text", "")
                clean(text, "Report text", 60000, 10)
            observed = clean(d.get("observed_at", ""), "Observation time", 50)
            require(timestamp(observed) <= effective_now + timedelta(minutes=5), "Observation time is after the exercise clock.", 422)
            published = d.get("published_at") or observed
            timestamp(published)
            require(not any(s["hash"] == digest(text) and s["organisation"] == org for s in doc["sources"]), "That organisation already submitted this report.")
            source_id = uid("source")
            doc["sources"].append({"id": source_id, "title": clean(d.get("title", "Report"), "Title", 150), "text": text,
                "hash": digest(text), "organisation": org, "author": actor["id"], "observed_at": observed, "published_at": published,
                "received_at": now_iso(), "status": "reported", "attachment": attached,
                "issues": [i.message for i in injection_scan(text)]})
            # Recipients must acknowledge; an explicit empty list shares the report without asking anyone.
            require(isinstance(d.get("recipients", []), list), "Choose participating organisations to notify.", 422)
            recipients = [r for r in d.get("recipients", participation.active_orgs(c)) if r != org]
            if recipients:
                self.notify(c, actor, source_id, recipients, clean(d["message"], "Message", 1000) if d.get("message") else "New shared report: please acknowledge")
        elif action == "notify_report":
            require(has(actor, "contributor", "agency_supervisor", "coordinator", "finance_approver", "communications_reviewer"), "This role cannot notify other bodies.", 403)
            s = self.source(doc, d.get("source_id"), accepted=False)
            self.notify(c, actor, s["id"], d.get("recipients"), clean(d.get("note", "Shared report for your attention"), "Notification note", 1000))
        elif action == "read_notification":
            notification = find(c.get("notifications", []), d.get("notification_id"))
            require(org in notification["recipients"], "This notification belongs to another organisation.", 403)
            if actor["id"] not in notification["read_by"]:
                notification["read_by"].append(actor["id"])
        elif action == "acknowledge":
            notification = find(c.get("notifications", []), d.get("notification_id"))
            require(org in notification["recipients"], "This report was not sent to your organisation.", 403)
            require(has(actor, "contributor", "agency_supervisor", "coordinator", "finance_approver", "communications_reviewer"), "Observers can read reports but not acknowledge them.", 403)
            require(notification.get("ack_required"), "This notification does not ask for an acknowledgement.", 422)
            require(org not in notification.setdefault("acks", {}), "Your organisation already acknowledged this report.")
            notification["acks"][org] = {"by": actor["id"], "at": now_iso(), "note": clean(d["note"], "Note", 1000) if d.get("note") else ""}
            if actor["id"] not in notification["read_by"]:
                notification["read_by"].append(actor["id"])
            return f"{org} acknowledged {self.source(doc, notification['source_id'], accepted=False)['title']}"
        elif action == "review_source":
            require(has(actor, "coordinator", "agency_supervisor"), "A coordinator or agency supervisor reviews sources.", 403)
            s = self.source(doc, d.get("source_id"), accepted=False)
            require(has(actor, "coordinator") or s["organisation"] == org, "Review only your organisation's sources.", 403)
            require(s["author"] != actor["id"], "A different account must review this source.", 403)
            require(s["status"] == "reported" and d.get("decision") in {"accepted", "rejected"}, "Review this pending source as accepted or rejected.")
            require(d["decision"] != "accepted" or not s.get("issues"), "This source contains instruction-like content. Obtain and submit clean evidence before accepting it.")
            s.update(status=d["decision"], reviewed_by=actor["id"], reviewed_at=now_iso())
        elif action == "confirm_asset":
            require(has(actor, "agency_supervisor"), "The resource owner's supervisor confirms resource facts.", 403)
            s = self.source(doc, d.get("source_id"))
            require(s["organisation"] == org, "Resource facts need evidence from the owning organisation.", 403)
            asset = find(c["assets"], d["asset_id"]) if d.get("asset_id") else None
            require(not asset or asset["owner"] == org, "Only the owning organisation can update this resource.", 403)
            kind = d.get("kind", asset["kind"] if asset else "")
            require(kind in KINDS, "Select a supported resource kind.", 422)
            facts = d.get("facts")
            require(isinstance(facts, dict) and bool(facts) and set(facts) <= {"capacity", "occupied", "external_pending", "status", "access", "water", "power"}, "Provide supported resource facts.", 422)
            checked = {}
            for field, fact in facts.items():
                require(isinstance(fact, dict) and set(fact) == {"value", "quote"}, "Each fact needs its value and exact source quote.", 422)
                quote = clean(fact["quote"], "Evidence quote", 2000, 2)
                require(quote in s["text"], "Evidence quote does not appear in the original report.", 422)
                value = fact["value"]
                if field in {"capacity", "occupied", "external_pending"}:
                    count(value)
                    require(bool(re.search(r"(?<!\d)" + str(value) + r"(?!\d)", quote)) and not re.search(r"famil(?:y|ies)|design capacity", quote, re.I), "Use evidenced current person/resource counts; do not convert families or design capacity.", 422)
                else:
                    options = {"status": {"accepting", "paused", "closed", "unknown"}, "access": {"passable", "restricted", "blocked", "unknown"}, "water": {"working", "restricted", "unavailable", "unknown"}, "power": {"working", "restricted", "unavailable", "unknown"}}
                    require(value in options[field] and str(value).casefold() in quote.casefold(), "Status must be supported explicitly by its source quote.", 422)
                checked[field] = {"value": value, "quote": quote, "source_id": s["id"], "observed_at": s["observed_at"], "confirmed_by": actor["id"]}
            if asset:
                require(not d.get("resource_key") or d["resource_key"] == asset.get("resource_key"), "A linked identity cannot be silently changed on an existing resource.")
                require(all(field not in asset["facts"] or timestamp(value["observed_at"]) >= timestamp(asset["facts"][field]["observed_at"]) for field, value in checked.items()), "An older observation cannot replace a newer confirmed resource fact.")
                # Avoid double counting arrivals when owner publishes a new baseline.
                arrivals = [o for o in c["offers"] if o.get("asset_id") == asset["id"] and o["status"] in {"reported_complete", "completed"} and not o.get("reconciled")]
                if arrivals:
                    require(set(d.get("reconcile_offer_ids", [])) == {o["id"] for o in arrivals} and all(o["status"] == "completed" for o in arrivals), "Explicitly reconcile every reviewed arrival into the new owner occupancy snapshot.")
                    require("occupied" in checked and all(timestamp(s["observed_at"]) >= timestamp(o["completed_at"]) for o in arrivals), "Reconciliation needs a new occupancy snapshot observed after the reviewed arrivals.")
                    for offer in arrivals:
                        offer.update(reconciled=True, reconciled_source_id=s["id"], reconciled_by=actor["id"])
                asset["facts"].update(checked)
            else:
                lat, lon = d.get("latitude"), d.get("longitude")
                require((lat is None and lon is None) or (type(lat) in {int, float} and type(lon) in {int, float} and 5.8 <= lat <= 9.9 and 79.4 <= lon <= 82.1), "Use Sri Lankan coordinates or leave both unknown.", 422)
                resource_key = clean(d["resource_key"], "Owner's shared resource ID", 120, 3) if d.get("resource_key") else None
                require(not resource_key or not any(a.get("resource_key") == resource_key and a["owner"] == org for a in c["assets"]), "This shared resource is already linked in this incident.")
                c["assets"].append({"id": uid("asset"), "name": clean(d.get("name", ""), "Resource name", 150), "kind": kind, "owner": org, "resource_key": resource_key, "latitude": lat, "longitude": lon, "facts": checked})
        elif action == "request":
            require(has(actor, "contributor", "agency_supervisor", "coordinator"), "Your account cannot create requests.", 403)
            s = self.source(doc, d.get("source_id"))
            quantity = count(d.get("quantity"), optional=True)
            require(d.get("kind") in KINDS, "Select a supported request kind.", 422)
            if quantity is not None:
                require(quantity > 0 and bool(re.search(r"(?<!\d)" + str(quantity) + r"(?!\d)", s["text"])), "The requested quantity must be supported by the source.", 422)
            unit = clean(d.get("unit", ""), "Unit", 40)
            require(d["kind"] != "shelter" or unit == "people", "Shelter commitments use people; family totals need clarification.", 422)
            if d["kind"] == "shelter" and quantity is not None:
                require(bool(re.search(r"(?<!\d)" + str(quantity) + r"\s*(?:people|persons?)\b", s["text"], re.I)), "An explicit person count is required; family counts must be clarified.", 422)
            require(d["kind"] != "transport" or unit == "vehicles", "Transport reservations use vehicles; passenger-seat planning needs separate verification.", 422)
            c["requests"].append({"id": uid("request"), "title": clean(d.get("title", ""), "Request title", 200), "kind": d["kind"], "quantity": quantity, "unit": unit, "source_id": s["id"], "organisation": org, "created_by": actor["id"], "status": "open"})
        elif action == "offer":
            require(has(actor, "coordinator"), "A coordinator reviews an assignment proposal.", 403)
            request = find(c["requests"], d.get("request_id"))
            require(request.get("status") not in {"withdrawn", "transfer_pending", "transferred_unresolved"}, "This request is withdrawn or transferred.")
            asset = find(c["assets"], d["asset_id"]) if d.get("asset_id") else None
            quantity = count(d.get("quantity"))
            require(quantity > 0 and request["quantity"] is not None, "Clarify the requested quantity before allocating.")
            require(quantity <= request["quantity"], "An offer cannot exceed the supported request quantity.")
            received = sum(r["quantity"] for r in c.get("receipts", []) if r["request_id"] == request["id"])
            require(quantity + received <= request["quantity"], "An offer cannot exceed the outstanding need after receipts.")
            recipient = asset["owner"] if asset else d.get("recipient")
            require(recipient in c["participants"], "Receiving organisation must participate in this incident.", 422)
            if request["kind"] in {"shelter", "transport"}:
                require(asset is not None and asset["kind"] == request["kind"], "Accommodation and transport offers need a matching owner-confirmed resource.")
            if asset:
                balance = self.balance(c, asset)
                require(balance["available"] is not None and quantity <= balance["available"], balance["reason"] + " Requested quantity exceeds confirmed availability.")
            c["offers"].append({"id": uid("offer"), "request_id": request["id"], "asset_id": asset["id"] if asset else None,
                "quantity": quantity, "recipient": recipient, "status": "offered", "proposed_by": actor["id"], "created_at": now_iso(),
                "expires_at": (effective_now + timedelta(minutes=30)).isoformat(), "note": clean(d.get("note") or "Exercise assignment", "Note", 1000)})
        elif action == "respond":
            offer = find(c["offers"], d.get("offer_id"))
            require(org == offer["recipient"] and has(actor, "agency_supervisor") and actor["id"] != offer["proposed_by"], "The receiving organisation's supervisor must respond under a different account.", 403)
            require(offer["status"] == "offered" and timestamp(offer["expires_at"]) > effective_now, "This offer is no longer pending or has expired.")
            decision = d.get("decision")
            require(decision in {"accepted", "rejected", "clarification"}, "Choose accepted, rejected or clarification.", 422)
            if decision == "accepted":
                require(not pending_dependencies(c, offer), "Review the outcome of every dependency before accepting this assignment.")
                request = find(c["requests"], offer["request_id"])
                now = effective_now
                held = sum(o["quantity"] for o in c["offers"] if o["request_id"] == request["id"] and (o["status"] in {"accepted", "mobilised"} and timestamp(o["expires_at"]) > now or o["status"] == "reported_complete"))
                completed = sum(o["quantity"] for o in c["offers"] if o["request_id"] == request["id"] and o["status"] == "completed")
                received = sum(r["quantity"] for r in c.get("receipts", []) if r["request_id"] == request["id"])
                # Delivery outcome and its goods receipt must not be added twice.
                committed = held + max(completed, received)
                require(committed + offer["quantity"] <= request["quantity"], "This request is already committed elsewhere; acceptance would over-allocate it.")
                if offer["asset_id"]:
                    balance = self.balance(c, find(c["assets"], offer["asset_id"]))
                    require(balance["available"] is not None and offer["quantity"] <= balance["available"], "Resource conditions changed or acceptance would exceed available capacity.")
                offer["expires_at"] = (now + timedelta(minutes=30)).isoformat()
            offer.update(status=decision, responded_by=actor["id"], responded_at=effective_now.isoformat(), response_note=clean(d.get("note") or decision, "Response note", 1000))
        elif action == "progress":
            offer = find(c["offers"], d.get("offer_id"))
            state = d.get("state")
            require(state in {"mobilised", "reported_complete", "cancelled"}, "Select a supported progress state.", 422)
            require((org == offer["recipient"] and has(actor, "agency_supervisor")) or (state == "cancelled" and has(actor, "coordinator")), "Only the receiving supervisor records progress; a coordinator may cancel.", 403)
            require(offer["status"] in {"offered", "accepted", "mobilised"} and (state == "cancelled" or offer["status"] in {"accepted", "mobilised"} and timestamp(offer["expires_at"]) > effective_now), "An active accepted reservation is required.")
            if state == "reported_complete":
                s = self.source(doc, d.get("source_id"))
                require(s["organisation"] == org and s["id"] != find(c["requests"], offer["request_id"])["source_id"] and timestamp(s["observed_at"]) >= timestamp(offer["responded_at"]), "Completion needs new, accepted receiving-body evidence observed after acceptance.")
                offer["outcome_source_id"] = s["id"]
            if state != "cancelled":
                require(not pending_dependencies(c, offer), "Dependencies are not reviewed complete.")
            offer.update(status=state, progress_by=actor["id"], progress_at=effective_now.isoformat())
        elif action == "review_outcome":
            require(has(actor, "coordinator"), "A coordinator reviews outcome evidence.", 403)
            offer = find(c["offers"], d.get("offer_id"))
            require(offer["status"] == "reported_complete" and actor["id"] != offer["progress_by"], "Review a reported outcome under a different account.")
            require(d.get("source_id") == offer.get("outcome_source_id"), "Review the exact submitted outcome evidence.")
            self.source(doc, d["source_id"])
            offer.update(status="completed", completed_by=actor["id"], completed_at=effective_now.isoformat())
        elif action == "finance_request":
            require(has(actor, "contributor", "agency_supervisor", "coordinator"), "Your account cannot request exercise funding.", 403)
            self.source(doc, d.get("source_id"))
            scheme = find(c["schemes"], d.get("scheme_id"))
            require(d.get("head") == scheme["head"], "Use the fictional scheme's expenditure head.", 422)
            c["finance"].append({"id": uid("finance"), "purpose": clean(d.get("purpose", ""), "Purpose", 500), "requested": str(money(d.get("amount"))),
                "approved": None, "committed": None, "released": None, "spent": None, "source_id": d["source_id"], "organisation": org,
                "requester": actor["id"], "scheme_id": scheme["id"], "scheme_version": "1", "currency": "LKR", "head": scheme["head"], "status": "requested", "evidence": []})
        elif action == "finance_action":
            f = find(c["finance"], d.get("finance_id"))
            scheme = find(c["schemes"], f["scheme_id"])
            state = d.get("state")
            require(state in {"recommended", "approved", "committed", "released", "spent"}, "Unsupported finance state.", 422)
            if state == "recommended":
                require(has(actor, "coordinator") and actor["id"] != f["requester"] and f["status"] == "requested", "A different coordinator recommends a pending request.", 403)
                f.update(status=state, recommender=actor["id"])
            elif state == "approved":
                require(has(actor, "finance_approver") and org == scheme["owner"] and actor["id"] not in {f["requester"], f.get("recommender")} and f["status"] == "recommended", "Only a separate fictional-scheme finance delegate can approve a recommendation.", 403)
                amount = money(d.get("amount"))
                total = pool_approved(self.store, c, scheme)
                require(amount <= money(f["requested"]) and amount <= money(scheme["limit"]) and total + amount <= money(scheme["budget"]), "Approval exceeds the request, fictional delegation limit or available scheme budget.")
                f.update(status=state, approved=str(amount), approver=actor["id"], approved_at=now_iso())
            else:
                require(f["approved"] is not None, "Approve the fictional request first.")
                require(f["status"] == "approved", "This financial record is closed or cancelled.")
                require((state == "released" and has(actor, "finance_approver") and org == scheme["owner"]) or (state in {"committed", "spent"} and org == f["organisation"] and has(actor, "agency_supervisor")), "This financial evidence state belongs to another exercise role.", 403)
                s = self.source(doc, d.get("source_id"))
                require(s["organisation"] == org and s["id"] != f["source_id"] and not any(e["source_id"] == s["id"] for record in c["finance"] for e in record["evidence"]), "Use new accepted evidence from your organisation; do not reuse a voucher in any funding record.")
                amount = money(d.get("amount"))
                ceiling = Decimal(f["released"] or "0") if state == "spent" else Decimal(f["approved"])
                total = Decimal(f[state] or "0") + amount
                require(total <= ceiling, "Evidence total exceeds the approved amount or released funds.")
                require(exact_amount(s["text"], amount), "The financial evidence must explicitly support this exact LKR amount.", 422)
                f[state] = str(total)
                f["evidence"].append({"state": state, "amount": str(amount), "source_id": s["id"], "actor": actor["id"], "at": effective_now.isoformat()})
        elif action == "document" and d.get("kind") == "letter":
            # An official letter to other bodies: issued by the person who applies it; no public reviewer.
            letter = letters.draft(self, doc, actor, d, lambda template_id, kind: messages.template(self, template_id, kind))
            c["documents"].append(letter)
            return f"Letter {letter['reference']} issued to {letter['audience']}"
        elif action == "document":
            require(has(actor, "coordinator", "communications_reviewer"), "A coordinator or communications reviewer prepares documents.", 403)
            kind = d.get("kind")
            require(kind in {"situation", "press_release", "handover", "sms", "email"}, "Choose a supported exercise document.", 422)
            require("to" not in d, "Only letters are addressed to organisations.", 422)
            if kind in messages.MESSAGE_KINDS and (d.get("text") or d.get("template_id")):
                # A public message from a template or typed text, sent later to chosen areas or media.
                c["documents"].append(messages.draft(self, doc, actor, next((o["name"] for o in ORGANISATIONS if o["id"] == org), org), d, effective_now))
                return
            require(not set(d) & messages.DOCUMENT_EXTRA, "Templates, text and recipients apply to SMS, email and press releases.", 422)
            require(d.get("language", "en") == "en", "Only English exercise documents are reviewed in this build.", 422)
            audience = clean(d.get("audience", "Exercise participants"), "Audience", 200)
            body = self.document(doc, kind, audience)
            c["documents"].append({"id": uid("document"), "kind": kind, "audience": audience, "language": "en", "issuer": org,
                "cutoff": effective_now.isoformat(), "incident_version": doc["version"] + 1, "body": body, "hash": digest(body), "status": "draft", "prepared_by": actor["id"], "delivery": "not_sent_exercise_artifact"})
        elif action == "approve_document":
            require(has(actor, "communications_reviewer"), "A communications reviewer approves exercise documents.", 403)
            item = find(c["documents"], d.get("document_id"))
            require(item["issuer"] == org, "The issuing organisation's communications reviewer approves this artifact.", 403)
            # Reports and handovers are snapshots of the incident and go stale; a public message is its own text.
            require(item["status"] == "draft" and item["hash"] == d.get("hash") and (item["kind"] in messages.MESSAGE_KINDS and "text" in item or item["incident_version"] == doc["version"]), "The exact document snapshot is stale, changed or already reviewed. Generate a fresh version.")
            item.update(status="approved_exercise", approved_by=actor["id"], approved_at=now_iso())
        elif action == "accept_handover":
            require(has(actor, "coordinator", "agency_supervisor"), "A receiving coordinator or supervisor accepts a handover.", 403)
            item = find(c["documents"], d.get("document_id"))
            require(item["kind"] == "handover" and item["status"] == "approved_exercise" and item["hash"] == d.get("hash") and actor["id"] != item["prepared_by"], "A different receiving account must accept the exact approved handover.")
            require(doc["version"] == item["incident_version"] + 1, "The incident changed after this handover was approved. Prepare a fresh snapshot.")
            item.update(status="handover_accepted", accepted_by=actor["id"], accepted_at=now_iso())

    def notify(self, c, actor, source_id, recipients, note):
        require(isinstance(recipients, list) and bool(recipients) and len(recipients) <= len(ORGANISATIONS) and all(isinstance(r, str) and r in c["participants"] for r in recipients), "Choose participating organisations to notify.", 422)
        c.setdefault("notifications", []).append({"id": uid("notification"), "source_id": source_id, "recipients": sorted(set(recipients)), "note": note, "from": actor["organisation"], "actor": actor["id"], "at": now_iso(), "read_by": [], "ack_required": True, "acks": {}})

    def document(self, doc, kind, audience):
        c = doc["coordination"]
        lines = ["EXERCISE — NOT AN OFFICIAL WARNING / NO REAL DISPATCH OR PAYMENT", f"{kind.replace('_', ' ').title()}: {doc['title']}", f"District: {c['district']} · Audience: {audience}", f"Evidence cutoff: {business_now(c).isoformat()} · Incident version: {doc['version']}", "", "Accepted contributions:"]
        for s in doc["sources"]:
            if s["status"] == "accepted":
                lines += [f"[{s['id']}] {s['organisation']} · {s['title']} · observed {s['observed_at']}", s["text"][:700]]
        lines += ["", "Resources and unresolved readiness:"]
        for a in c["assets"]:
            b = self.balance(c, a)
            lines.append(f"{a['name']} ({a['owner']}): {b['available'] if b['available'] is not None else 'Unknown'} available. {b['reason']}")
        lines += ["", "Requests and outcomes:"]
        for r in c["requests"]:
            lines.append(f"[{r['id']}] {r['title']}: {r['quantity'] if r['quantity'] is not None else 'Unknown'} {r['unit']} · evidence {r['source_id']}")
        for o in c["offers"]:
            lines.append(f"[{o['id']}] {o['quantity']} → {o['recipient']}: {o['status']}; evidence {o.get('outcome_source_id', 'outcome unconfirmed')}")
        lines += ["", "Unreviewed contributions: " + str(sum(s["status"] == "reported" for s in doc["sources"])), "No live route safety, guaranteed admission, actual delivery or financial transfer is established."]
        lines += document_sections(doc)
        if kind == "sms":
            # Keep channel limits explicit; no AI assertion of a sent message.
            return f"EXERCISE ONLY: {doc['title']}. {c['district']}. Updated reports require coordinator review. No evacuation instruction. Check the exercise workspace."
        return "\n".join(lines)
