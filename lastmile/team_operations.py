"""Exercise lifecycle, claims, fulfilment and reconciliation commands.

These transitions run inside TeamWorkspace's existing versioned transaction.
They never dispatch, publish, pay or infer official authority.
"""
from datetime import datetime, timezone
from decimal import Decimal
import re

from .team_accounts import ACTIVE, ORGANISATIONS, require
from .workspace import now_iso, timestamp, uid, digest
from .team_clock import business_now

EXTRA_FIELDS = {
    "claim": {"topic", "location", "field", "value", "unit", "source_id", "quote", "valid_until"},
    "resolve_claim": {"claim_id", "supersedes", "reason"},
    "request_update": {"request_id", "quantity", "source_id", "state", "reason", "recipient", "reference"},
    "accept_transfer": {"request_id", "reason"},
    "receipt": {"request_id", "quantity", "unit", "source_id", "quote"},
    "assignment_details": {"offer_id", "due_at", "depends_on", "reason"},
    "escalate": {"offer_id", "reason"},
    "finance_decision": {"finance_id", "decision", "reason", "source_id"},
    "finance_reconcile": {"finance_id", "source_id", "quote", "balance"},
    "revise_document": {"document_id", "body", "source_ids", "reason"},
    "retract_document": {"document_id", "reason"},
    "phase": {"phase", "reason"},
    "close": {"reason"},
    "reopen": {"reason"},
    "finance_scheme": {"id", "name", "budget", "limit", "head", "pool_key", "source_id"},
    "replay_clock": {"as_of", "reason"},
}
ACTION_HELP = {
    "claim": "Contributor records topic weather/flood/landslide/road/power/water/health/displacement/other, location, field, literal value and unit, accepted source_id and exact quote; optional valid_until ISO timezone. Conflicts require explicit resolution.",
    "resolve_claim": "Coordinator selects claim_id, supersedes list of competing claim IDs and reason. Does not erase originals.",
    "request_update": "Coordinator/own supervisor may clarify quantity from accepted new source, withdraw with reason, or propose state transfer_pending with recipient organisation and recovery reference. Receiving supervisor separately accepts.",
    "accept_transfer": "Receiving supervisor accepts a transfer_pending request with reason. Need remains transferred_unresolved, never completed.",
    "receipt": "Receiving request organisation supervisor confirms partial supply receipt: request_id, positive quantity, matching unit, new accepted source_id, exact quote. No reused source or receipt exceeding remaining quantity.",
    "assignment_details": "Coordinator sets offer_id, optional due_at timezone ISO, depends_on list of other offer IDs and reason. Dependencies block acceptance/mobilisation until reviewed complete.",
    "escalate": "Coordinator records an overdue unresolved assignment's escalation with reason; does not send anything.",
    "finance_decision": "Coordinator may return pending request; scheme finance delegate may reject requested/recommended or cancel approved only before any financial evidence. Always give reason.",
    "finance_reconcile": "Separate scheme finance delegate records new accepted scheme-owner evidence, quote and balance decimal string including 0.00. Balance must equal released minus spent; known committed/spent/released amounts required; positive remainder stays unresolved.",
    "revise_document": "Coordinator/communications reviewer creates a new draft from document_id, custom exercise body, source_ids all accepted, and reason. Every supplied source ID must appear in body. Never edit approved historical body. Human review is still required.",
    "retract_document": "Communications reviewer retracts approved exercise document with reason; does not retract an external transmission.",
    "phase": "Coordinator sets monitoring/response/recovery with reason. Does not declare disaster over.",
    "close": "Coordinator closes only after blockers are cleared and exact latest handover is accepted, with reason.",
    "reopen": "Coordinator reopens a closed incident with reason; preserves history and increments business version.",
    "finance_scheme": "Scheme-owner finance role creates an explicitly fictional LKR exercise scheme from its accepted source. ID must start fictional-, title contains fictional. Optional owner-chosen pool_key links budget across incidents under one active writer; budget and limit must match linked schemes.",
    "replay_clock": "Coordinator starts a historical replay BEFORE sources/requests/assets exist or advances the existing clock monotonically. as_of is timezone ISO, not future; original source dates stay unchanged, audit/account time remains real. Replay never sends or pays.",
}


def initialise(c):
    for key in ("claims", "receipts", "escalations", "lifecycle"):
        c.setdefault(key, [])
    c.setdefault("phase", "response")


def quantity_state(c, request):
    now = business_now(c)
    received = sum(r["quantity"] for r in c.get("receipts", []) if r["request_id"] == request["id"])
    completed = sum(o["quantity"] for o in c["offers"] if o["request_id"] == request["id"] and o["status"] == "completed")
    # Supplies are fulfilled by goods receipts, not also by completing a delivery task.
    fulfilled = received if request["kind"] == "supplies" else completed
    held = sum(o["quantity"] for o in c["offers"] if o["request_id"] == request["id"] and
               (o["status"] in {"accepted", "mobilised"} and timestamp(o["expires_at"]) > now or o["status"] == "reported_complete"))
    quantity = request["quantity"]
    state = request.get("status", "open")
    if state not in {"withdrawn", "transfer_pending", "transferred_unresolved"}:
        state = "needs_clarification" if quantity is None else "fulfilled" if fulfilled >= quantity else "in_progress" if held or fulfilled else "open"
    return {"status": state, "fulfilled": fulfilled, "received": received, "held": held,
            "remaining": None if quantity is None else max(0, quantity - fulfilled)}


def pending_dependencies(c, offer):
    by_id = {o["id"]: o for o in c["offers"]}
    return [key for key in offer.get("depends_on", []) if by_id.get(key, {}).get("status") != "completed"]


def blockers(doc):
    c = doc["coordination"]
    result = []
    def add(kind, record_id, note):
        result.append({"kind": kind, "id": record_id, "note": note})
    for source in doc["sources"]:
        if source["status"] == "reported":
            add("source", source["id"], "Contribution still needs review.")
    for request in c["requests"]:
        if quantity_state(c, request)["status"] not in {"fulfilled", "withdrawn", "transferred_unresolved"}:
            add("request", request["id"], "Need remains unresolved or transfer is not accepted.")
    now = business_now(c)
    for offer in c["offers"]:
        if offer["status"] == "reported_complete" or offer["status"] in {"offered", "accepted", "mobilised"} and timestamp(offer["expires_at"]) > now:
            add("assignment", offer["id"], "Assignment needs cancellation or reviewed outcome.")
        if offer.get("asset_id") and offer["status"] == "completed" and not offer.get("reconciled"):
            add("resource", offer["id"], "Arrival/resource return is not reconciled into an owner snapshot.")
    for claim in c.get("claims", []):
        if claim["status"] == "disputed":
            add("claim", claim["id"], "Conflicting claims need explicit resolution.")
    for record in c["finance"]:
        if record["status"] not in {"reconciled", "rejected", "cancelled"}:
            add("finance", record["id"], "Financial evidence/reconciliation is unfinished.")
    return result


def extend_view(doc):
    c = doc["coordination"]
    initialise(c)
    for request in c["requests"]:
        request.update(quantity_state(c, request))
    for offer in c["offers"]:
        offer["blocked_by"] = pending_dependencies(c, offer)
        offer["overdue"] = bool(offer.get("due_at") and timestamp(offer["due_at"]) < business_now(c) and offer["status"] not in {"completed", "cancelled", "rejected"})
    for claim in c["claims"]:
        claim["expired"] = bool(claim.get("valid_until") and timestamp(claim["valid_until"]) < business_now(c))
    c["closure_blockers"] = blockers(doc)
    c["required_handover"] = "Generate, approve and receive a current handover after clearing blockers."


def apply_extra(service, doc, actor, action, d):
    from .team_workspace import clean, count, find, has, money, exact_amount
    c, org = doc["coordination"], actor["organisation"]
    initialise(c)
    if action == "replay_clock":
        require(has(actor, "coordinator"), "A coordinator controls the exercise replay clock.", 403)
        clock = timestamp(d.get("as_of"))
        require(clock <= datetime.now(timezone.utc), "Replay time cannot be in the future.", 422)
        require(c.get("replay_at") or not (doc["sources"] or c["requests"] or c["assets"] or c["finance"]), "Start replay before loading operational evidence.")
        require(not c.get("replay_at") or clock >= timestamp(c["replay_at"]), "Replay cannot move backwards; start a separate exercise.")
        c["replay_at"] = clock.isoformat()
        c["lifecycle"].append({"action": action, "as_of": c["replay_at"], "reason": clean(d.get("reason", ""), "Replay reason", 1000, 8), "actor": actor["id"], "at": now_iso()})
    elif action == "finance_scheme":
        require(has(actor, "finance_approver"), "Only the exercise scheme owner's finance role registers its scheme.", 403)
        source = service.source(doc, d.get("source_id"))
        require(source["organisation"] == org, "Use accepted scheme-owner evidence.", 403)
        scheme_id = clean(d.get("id", ""), "Fictional scheme ID", 100, 12)
        name = clean(d.get("name", ""), "Scheme name", 180, 8)
        require(scheme_id.startswith("fictional-") and "fictional" in name.casefold(), "Only explicitly fictional schemes are enabled.", 422)
        require(not any(s["id"] == scheme_id for s in c["schemes"]), "Scheme ID already exists in this incident.")
        budget, limit = money(d.get("budget")), money(d.get("limit"))
        require(limit <= budget, "Delegation limit cannot exceed the pool budget.", 422)
        require(exact_amount(source["text"], budget) and exact_amount(source["text"], limit), "Evidence must state the fictional budget and limit amounts.", 422)
        scheme = {"id": scheme_id, "name": name, "currency": "LKR", "budget": str(budget), "limit": str(limit), "owner": org, "policy": "exercise_only", "head": clean(d.get("head", ""), "Exercise expenditure head", 80), "pool_key": clean(d["pool_key"], "Shared pool ID", 120, 3) if d.get("pool_key") else None, "source_id": source["id"], "registered_by": actor["id"]}
        require(not scheme["pool_key"] or not any(s.get("pool_key") == scheme["pool_key"] and s["owner"] == org for s in c["schemes"]), "This owner's shared pool already has a scheme in this incident; reuse it.")
        from .team_shared import pool_approved
        require(pool_approved(service.store, c, scheme) <= budget, "Linked pool is already over budget.")
        c["schemes"].append(scheme)
    elif action == "claim":
        require(has(actor, "contributor", "agency_supervisor", "coordinator"), "Your role cannot record a claim.", 403)
        source = service.source(doc, d.get("source_id"))
        require(source["organisation"] == org or has(actor, "coordinator"), "Record claims from your own organisation's evidence.", 403)
        topic = d.get("topic")
        require(topic in {"weather", "flood", "landslide", "road", "power", "water", "health", "displacement", "other"}, "Choose a supported topic.", 422)
        quote = clean(d.get("quote", ""), "Quote", 2000, 2)
        value, unit = clean(d.get("value", ""), "Literal value", 300), clean(d.get("unit", "unknown"), "Unit", 50)
        require(quote in source["text"] and value.casefold() in quote.casefold(), "The literal value must be supported by an exact source quote.", 422)
        valid_until = d.get("valid_until")
        if valid_until:
            require(timestamp(valid_until) > timestamp(source["observed_at"]), "Validity must end after observation.", 422)
        claim = {"id": uid("claim"), "topic": topic, "location": clean(d.get("location", ""), "Location", 160),
                 "field": clean(d.get("field", ""), "Field", 80), "value": value, "unit": unit, "source_id": source["id"],
                 "quote": quote, "observed_at": source["observed_at"], "valid_until": valid_until, "organisation": source["organisation"],
                 "recorded_by": actor["id"], "status": "recorded"}
        require(claim["location"].casefold() in source["text"].casefold(), "The location must occur in the original evidence.", 422)
        require(unit in {"status", "unknown", "text"} or unit.casefold() in quote.casefold(), "Do not invent or convert the source unit.", 422)
        conflicts = [x for x in c["claims"] if all(x[k].casefold() == claim[k].casefold() for k in ("topic", "location", "field", "unit")) and x["status"] in {"recorded", "current", "disputed"} and x["value"].casefold() != value.casefold()]
        if conflicts:
            claim["status"] = "disputed"
            for previous in conflicts:
                previous["status"] = "disputed"
        c["claims"].append(claim)
    elif action == "resolve_claim":
        require(has(actor, "coordinator"), "A coordinator resolves conflicting evidence.", 403)
        chosen = find(c["claims"], d.get("claim_id"))
        supersedes = d.get("supersedes", [])
        require(isinstance(supersedes, list) and chosen["id"] not in supersedes and chosen["status"] in {"recorded", "disputed", "current"}, "Select a current candidate and competing claim IDs.", 422)
        reason = clean(d.get("reason", ""), "Resolution reason", 1000, 8)
        candidates = [find(c["claims"], key) for key in supersedes]
        require(all(all(x[k].casefold() == chosen[k].casefold() for k in ("topic", "location", "field", "unit")) for x in candidates), "Only resolve the same location, field and unit together.")
        competitors = {x["id"] for x in c["claims"] if x["id"] != chosen["id"] and x["status"] in {"recorded", "current", "disputed"} and all(x[k].casefold() == chosen[k].casefold() for k in ("topic", "location", "field", "unit")) and x["value"] != chosen["value"]}
        require(competitors <= set(supersedes), "Include all unresolved competing claims in the decision.")
        for previous in candidates:
            previous.update(status="superseded", superseded_by=chosen["id"])
        chosen.update(status="current", resolution=reason, resolved_by=actor["id"], resolved_at=now_iso())
    elif action == "request_update":
        request = find(c["requests"], d.get("request_id"))
        require(has(actor, "coordinator") or has(actor, "agency_supervisor") and request["organisation"] == org, "Only a coordinator or requesting supervisor updates this need.", 403)
        require(quantity_state(c, request)["status"] not in {"fulfilled", "withdrawn", "transferred_unresolved"}, "This need is already settled.")
        require(not any(o["request_id"] == request["id"] and o["status"] not in {"completed", "cancelled", "rejected", "clarification"} for o in c["offers"]), "Cancel or finish related offers before changing a request.")
        reason = clean(d.get("reason", ""), "Reason", 1000, 8)
        state = d.get("state", "open")
        require(state in {"open", "withdrawn", "transfer_pending"}, "Choose open, withdrawn or transfer_pending.", 422)
        if state == "open":
            source = service.source(doc, d.get("source_id"))
            quantity = count(d.get("quantity"))
            require(quantity > 0 and re.search(r"(?<!\d)" + str(quantity) + r"(?!\d)", source["text"]), "Clarified quantity needs new supporting evidence.", 422)
            if request["kind"] == "shelter":
                require(re.search(r"(?<!\d)" + str(quantity) + r"\s*(?:people|persons?)\b", source["text"], re.I), "Clarify people; do not convert families.", 422)
            require(quantity >= quantity_state(c, request)["fulfilled"], "Quantity cannot be less than reviewed fulfilment.")
            request.update(quantity=quantity, source_id=source["id"])
        elif state == "transfer_pending":
            require(d.get("recipient") in c["participants"] and d["recipient"] != request["organisation"], "Choose a different participating receiving organisation.", 422)
            request.update(transfer_recipient=d["recipient"], transfer_reference=clean(d.get("reference", ""), "Recovery follow-up reference", 200, 3), transfer_by=actor["id"])
        request.update(status=state, update_reason=reason, updated_by=actor["id"])
    elif action == "accept_transfer":
        request = find(c["requests"], d.get("request_id"))
        require(has(actor, "agency_supervisor") and org == request.get("transfer_recipient") and actor["id"] != request.get("transfer_by"), "The separate receiving supervisor must accept ownership.", 403)
        require(request["status"] == "transfer_pending", "No pending transfer.")
        request.update(status="transferred_unresolved", transfer_accepted_by=actor["id"], transfer_accepted_at=now_iso(), transfer_acceptance=clean(d.get("reason", ""), "Acceptance note", 1000, 8))
    elif action == "receipt":
        request = find(c["requests"], d.get("request_id"))
        require(has(actor, "agency_supervisor") and org == request["organisation"], "The receiving request organisation's supervisor confirms receipt.", 403)
        require(request["kind"] == "supplies" and quantity_state(c, request)["status"] in {"open", "in_progress"}, "Record a receipt against an open supply request.")
        source = service.source(doc, d.get("source_id"))
        quantity, quote = count(d.get("quantity")), clean(d.get("quote", ""), "Receipt quote", 2000, 2)
        require(source["organisation"] == org and source["id"] != request["source_id"] and not any(r["source_id"] == source["id"] for r in c["receipts"]), "Use new receiving-body evidence; a receipt source can be counted only once.")
        require(quote in source["text"] and d.get("unit") == request["unit"] and request["unit"].casefold() in quote.casefold() and re.search(r"(?<!\d)" + str(quantity) + r"(?!\d)", quote), "Receipt quantity and matching unit need an exact source quote.", 422)
        require(quantity > 0 and request["quantity"] is not None and quantity <= quantity_state(c, request)["remaining"], "Receipt exceeds the outstanding need or quantity is unknown.")
        c["receipts"].append({"id": uid("receipt"), "request_id": request["id"], "quantity": quantity, "unit": request["unit"], "source_id": source["id"], "quote": quote, "actor": actor["id"], "at": now_iso()})
    elif action == "assignment_details":
        require(has(actor, "coordinator"), "A coordinator sets dependencies and deadlines.", 403)
        offer = find(c["offers"], d.get("offer_id"))
        require(offer["status"] in {"offered", "clarification"}, "Material assignment changes require a fresh offer before acceptance.")
        keys = d.get("depends_on", [])
        require(isinstance(keys, list) and len(keys) <= 20 and offer["id"] not in keys, "Invalid dependency list.", 422)
        for key in keys:
            dependency = find(c["offers"], key)
            # Traverse the entire graph so adding A→B cannot close a longer cycle.
            todo, seen = list(dependency.get("depends_on", [])), set()
            while todo:
                other = todo.pop()
                require(other != offer["id"], "Dependency would introduce a cycle.")
                if other not in seen:
                    seen.add(other)
                    todo.extend(find(c["offers"], other).get("depends_on", []))
        due_at = d.get("due_at")
        if due_at:
            require(timestamp(due_at) > business_now(c), "Set a deadline after the exercise clock.", 422)
        offer.update(depends_on=list(dict.fromkeys(keys)), due_at=due_at, coordination_reason=clean(d.get("reason", ""), "Reason", 1000, 8))
    elif action == "escalate":
        require(has(actor, "coordinator"), "A coordinator records escalation.", 403)
        offer = find(c["offers"], d.get("offer_id"))
        require(offer.get("due_at") and timestamp(offer["due_at"]) < business_now(c) and offer["status"] not in {"completed", "cancelled", "rejected"}, "Only unresolved overdue work can be escalated.")
        c["escalations"].append({"id": uid("escalation"), "offer_id": offer["id"], "reason": clean(d.get("reason", ""), "Reason", 1000, 8), "actor": actor["id"], "at": now_iso(), "delivery": "not_sent_exercise_record"})
    elif action == "finance_decision":
        record = find(c["finance"], d.get("finance_id"))
        scheme = find(c["schemes"], record["scheme_id"])
        decision = d.get("decision")
        require(decision in {"returned", "rejected", "cancelled"}, "Choose returned, rejected or cancelled.", 422)
        if decision == "returned":
            require(has(actor, "coordinator") and actor["id"] != record["requester"] and record["status"] in {"requested", "recommended"}, "A separate coordinator returns pending requests.", 403)
        else:
            require(has(actor, "finance_approver") and org == scheme["owner"] and actor["id"] != record["requester"], "Only the separate fictional-scheme finance delegate decides.", 403)
            require(record["status"] in ({"requested", "recommended", "returned"} if decision == "rejected" else {"approved"}), "This financial decision is not applicable.")
            require(not record["evidence"], "Funding with financial movements cannot be cancelled without a reconciliation/reversal process.")
        record.update(status=decision, decision_reason=clean(d.get("reason", ""), "Reason", 1000, 8), decision_by=actor["id"], decision_at=now_iso())
    elif action == "finance_reconcile":
        record = find(c["finance"], d.get("finance_id"))
        scheme = find(c["schemes"], record["scheme_id"])
        require(has(actor, "finance_approver") and org == scheme["owner"] and actor["id"] != record["requester"], "The separate scheme finance delegate reviews reconciliation.", 403)
        require(record["status"] == "approved" and all(record.get(k) is not None for k in ("committed", "released", "spent")), "Known commitment, release and expenditure are required before reconciliation.")
        source = service.source(doc, d.get("source_id"))
        require(source["organisation"] == org and source["id"] != record["source_id"] and not any(e["source_id"] == source["id"] for f in c["finance"] for e in f["evidence"]), "Use a new accepted scheme-owner reconciliation statement.")
        quote = clean(d.get("quote", ""), "Statement quote", 2000, 2)
        require(all(timestamp(source["observed_at"]) >= timestamp(e["at"]) for e in record["evidence"]), "Reconciliation must be observed after the financial movements.")
        balance = d.get("balance")
        require(isinstance(balance, str) and re.fullmatch(r"\d{1,10}(?:\.\d{1,2})?", balance), "Balance must be a nonnegative LKR decimal string.", 422)
        balance = Decimal(balance).quantize(Decimal("0.01"))
        number = str(int(balance)) + r"(?:\.00)?" if balance == balance.to_integral_value() else re.escape(str(balance))
        require(quote in source["text"] and re.search(r"(?<![\d.])" + number + r"(?![\d.])", quote.replace(",", "")), "The statement must explicitly support this exact balance.", 422)
        require(balance == Decimal(record["released"]) - Decimal(record["spent"]), "Statement balance disagrees with released minus spent.")
        require(Decimal(record["committed"]) == Decimal(record["spent"]), "Outstanding commitments require resolution before final reconciliation.")
        require(balance == 0, "A positive cash remainder needs a verified return/reversal process; keep the record open.")
        record.update(status="reconciled", reconciliation={"source_id": source["id"], "quote": quote, "balance": str(balance), "reviewer": actor["id"], "at": now_iso()})
        record["evidence"].append({"state": "reconciled", "amount": str(balance), "source_id": source["id"], "actor": actor["id"], "at": now_iso()})
    elif action == "revise_document":
        require(has(actor, "coordinator", "communications_reviewer"), "A coordinator or communications reviewer prepares revisions.", 403)
        previous = find(c["documents"], d.get("document_id"))
        require(previous["issuer"] == org, "Only the issuing organisation can prepare its revised artifact.", 403)
        require(previous["kind"] != "handover", "Handovers must be generated from the current saved-state snapshot.")
        text = clean(d.get("body", ""), "Exercise draft", 12000, 20)
        require(text.startswith("EXERCISE") and not re.search(r"this is not a drill", text, re.I), "Revised text must start EXERCISE and remain clearly an exercise.", 422)
        from .checks import injection_scan
        require(not injection_scan(text), "Instruction-like content cannot be a reviewed draft.")
        source_ids = d.get("source_ids")
        require(isinstance(source_ids, list) and 1 <= len(source_ids) <= 30, "Cite the supporting source IDs.", 422)
        for key in source_ids:
            service.source(doc, key)
            require(key in text, "Every cited source ID must appear in the draft.", 422)
        revision = {k: previous[k] for k in ("kind", "audience", "language", "issuer")}
        revision.update(id=uid("document"), cutoff=now_iso(), incident_version=doc["version"] + 1, body=text, hash=digest(text), status="draft", prepared_by=actor["id"],
                        delivery="not_sent_exercise_artifact", replaces=previous["id"], source_ids=list(dict.fromkeys(source_ids)), revision_reason=clean(d.get("reason", ""), "Revision reason", 1000, 8))
        c["documents"].append(revision)
    elif action == "retract_document":
        require(has(actor, "communications_reviewer"), "A communications reviewer retracts an exercise artifact.", 403)
        item = find(c["documents"], d.get("document_id"))
        require(item["issuer"] == org, "Only the issuing organisation can retract its artifact.", 403)
        require(item["status"] == "approved_exercise" and item["kind"] != "handover", "Only an approved non-handover artifact can be retracted.")
        item.update(status="retracted_exercise", retracted_by=actor["id"], retracted_at=now_iso(), retraction_reason=clean(d.get("reason", ""), "Reason", 1000, 8))
    elif action in {"phase", "close", "reopen"}:
        require(has(actor, "coordinator"), "A coordinator changes the incident lifecycle.", 403)
        reason = clean(d.get("reason", ""), "Lifecycle reason", 1000, 8)
        if action == "reopen":
            require(doc["status"] == "closed", "Only a closed incident can be reopened.")
            doc["status"], c["phase"] = "active", "response"
        elif action == "phase":
            phase = d.get("phase")
            require(phase in {"monitoring", "response", "recovery"}, "Choose monitoring, response or recovery.", 422)
            c["phase"] = phase
        else:
            pending = blockers(doc)
            require(not pending, "Cannot close: " + "; ".join(f"{x['id']}: {x['note']}" for x in pending[:8]))
            require(c["phase"] == "recovery", "Move to recovery and reconcile work before closure.")
            accepted = [x for x in c["documents"] if x["kind"] == "handover" and x["status"] == "handover_accepted"]
            require(accepted and doc["version"] == accepted[-1]["incident_version"] + 2, "An exact current approved and received handover is required before closure.")
            doc["status"], c["phase"] = "closed", "closed"
        c["lifecycle"].append({"action": action, "phase": c["phase"], "reason": reason, "actor": actor["id"], "at": now_iso(), "version": doc["version"] + 1})


def document_sections(doc):
    c = doc["coordination"]
    lines = ["", "Incident phase: " + c.get("phase", "response"), "Recorded claims (not independent technical verification):"]
    for claim in c.get("claims", []):
        lines.append(f"[{claim['id']}] {claim['location']} · {claim['field']}: {claim['value']} {claim['unit']} · {claim['status']} · {claim['source_id']}")
    lines += ["", "Fulfilment and receiving ownership:"]
    for request in c["requests"]:
        q = quantity_state(c, request)
        lines.append(f"[{request['id']}] {q['status']} · fulfilled {q['fulfilled']} · remaining {q['remaining'] if q['remaining'] is not None else 'Unknown'} {request['unit']}")
        if request.get("transfer_recipient"):
            lines.append(f"Transferred responsibility: {request['transfer_recipient']} · {request.get('transfer_reference')} · accepted by {request.get('transfer_accepted_by', 'not yet accepted')}; outcome remains unresolved.")
    lines += ["", "Outstanding closure checks:"]
    for record in c["finance"]:
        lines.append(f"Finance [{record['id']}]: {record['status']} · {record['scheme_id']}; this is exercise evidence, not payment execution.")
    lines += [f"[{x['id']}] {x['note']}" for x in blockers(doc)] or ["No work blockers recorded; a current receiving handover is still required."]
    return lines
