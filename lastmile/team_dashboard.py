"""Cross-incident home dashboard built only from actor-scoped incident views.

Every figure is derived from saved exercise records the actor may already open.
Unknown capacity stays unknown: it is counted separately and never estimated.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from .team_asks import CLOSED as ASK_CLOSED, allowed as ask_steps, escalation as ask_escalation
from .team_clock import business_now
from .team_notifications import name as org_name
from .team_workspace import has
from .workspace import now_iso, timestamp

EVENT_LABELS = {
    "contribute": "Report contributed", "review_source": "Report reviewed", "notify_report": "Bodies notified",
    "read_notification": "Notification read", "acknowledge": "Report acknowledged", "confirm_asset": "Resource facts confirmed", "request": "Request recorded",
    "offer": "Assignment proposed", "respond": "Assignment answered", "progress": "Assignment progress",
    "review_outcome": "Outcome reviewed", "finance_request": "Funding requested", "finance_action": "Funding updated",
    "ask": "Request sent", "ask_update": "Request updated",
    "participant": "Organisation added", "join_request": "Asked to join", "join_decision": "Join request answered", "stand_down": "Organisation stood down",
    "document": "Document prepared", "approve_document": "Document approved", "accept_handover": "Handover received",
    "send_document": "Message sent", "retract_document": "Document retracted", "area_status": "Area status updated", "warning": "Warning recorded",
    "area_figures": "Figures recorded", "publish_area": "Published to the public page", "map_layer": "Map layer added",
}


def reported_free(capacity, occupied, pending):
    """Free places at the owner's last report: capacity less occupied and expected arrivals. Not a confirmed balance."""
    if capacity is None or occupied is None:
        return None
    return max(0, capacity - occupied - (pending or 0))
# Lower rank is shown first.
PRIORITY = {"ask": 0, "assignment": 0, "join": 1, "ack": 1, "proposal": 1, "report": 2, "resource": 3, "finance": 4, "request": 5, "notification": 6}


def _value(facts, key):
    fact = facts.get(key)
    return fact.get("value") if isinstance(fact, dict) else None


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _money(value):
    try:
        return Decimal(str(value)) if value is not None else None
    except InvalidOperation:
        return None


def build(views, actor, activity_limit=12):
    """`views` are results of TeamWorkspace.view() for incidents the actor can access."""
    org = actor["organisation"]
    attention, incidents, shelters, activity, seen_shelters = [], [], [], [], set()
    districts = {}
    totals = {"active_incidents": 0, "closed_incidents": 0, "pending_reports": 0, "open_requests": 0, "assignments_in_progress": 0}
    requested, released = Decimal(0), Decimal(0)

    for doc in views:
        c, active = doc["coordination"], doc["status"] != "closed"
        ref = {"incident_id": doc["id"], "incident_title": doc["title"], "district": c["district"]}
        part = c.get("participation_view") or {}
        # A body that stood down keeps reading the incident but has nothing left to do in it.
        acting_here = org in part.get("active", c["participants"])
        def need(kind, title, detail, tab=None, record=None, pane="data", at=None):
            # `at` is when the thing now waiting arrived, so a person can see how long it has waited.
            if (active and acting_here) or kind == "notification":
                attention.append({**ref, "kind": kind, "title": title, "detail": detail, "tab": tab, "record": record, "pane": pane, "at": at})
        if part.get("can_manage"):
            names = {"dmc": "DMC", "district": "District Secretariat", "division": "Divisional Secretariat", "ndrsc": "NDRSC", "water": "NWSDB", "rda": "RDA"}
            for r in part.get("join_requests", []):
                if r["status"] == "pending":
                    need("join", f"{names.get(r['organisation'], r['organisation'])} asks to join", r["note"] or "Approve or decline from Organisations", "overview", r["id"], at=r.get("at"))
        totals["active_incidents" if active else "closed_incidents"] += 1
        if active:
            districts[c["district"]] = districts.get(c["district"], 0) + 1

        pending = [s for s in doc["sources"] if s["status"] == "reported"]
        open_requests = [r for r in c["requests"] if r["status"] not in {"fulfilled", "withdrawn", "transferred_unresolved"}]
        in_progress = [o for o in c["offers"] if o["status"] in {"accepted", "mobilised", "reported_complete"}]
        if active:
            totals["pending_reports"] += len(pending)
            totals["open_requests"] += len(open_requests)
            totals["assignments_in_progress"] += len(in_progress)

        # Same rules the incident actions enforce; the dashboard only points at them.
        reviewable = [s for s in pending if s.get("author") != actor["id"] and (has(actor, "coordinator") or has(actor, "agency_supervisor") and s["organisation"] == org)]
        if reviewable:
            need("report", f"Review {len(reviewable)} incoming report{'s' if len(reviewable) != 1 else ''}", reviewable[0]["title"], "sources", reviewable[0]["id"], at=reviewable[0].get("received_at"))
        now = business_now(c)
        for o in c["offers"]:
            if o["recipient"] == org and has(actor, "agency_supervisor") and o["status"] == "offered" and timestamp(o["expires_at"]) > now and o.get("proposed_by") != actor["id"]:
                need("assignment", "Answer an assignment for your organisation", f"{o['quantity']} · {o.get('note') or 'Receiving-body assignment'}", "requests", o["id"], at=o.get("created_at"))
            elif o["recipient"] == org and has(actor, "agency_supervisor") and o["status"] in {"accepted", "mobilised"} and o.get("overdue"):
                need("assignment", "Assignment is past its due time", o.get("note") or "Record progress or ask the coordinator to cancel it.", "requests", o["id"])
            elif has(actor, "coordinator") and o["status"] == "reported_complete" and o.get("progress_by") != actor["id"]:
                need("assignment", "Review a reported outcome", o.get("note") or "Completion evidence was submitted.", "requests", o["id"])
        proposals = [p for p in c["chat"].get("proposals", []) if p.get("status") == "pending" and p.get("version") == doc["version"] and timestamp(p["expires_at"]) > datetime.now(timezone.utc)]
        if proposals:
            need("proposal", f"{len(proposals)} chat proposal{'s' if len(proposals) != 1 else ''} waiting for you", "Review the exact change before it applies.", pane="chat", at=proposals[-1].get("created_at"))
        if has(actor, "coordinator"):
            unassigned = [r for r in open_requests if r.get("status") in {"open", "needs_clarification"} and not any(o["request_id"] == r["id"] and o["status"] in {"offered", "accepted", "mobilised", "reported_complete"} for o in c["offers"])]
            if unassigned:
                need("request", f"{len(unassigned)} request{'s' if len(unassigned) != 1 else ''} without an assignment", unassigned[0]["title"], "requests", unassigned[0]["id"], at=unassigned[0].get("created_at"))
        for f in c["finance"]:
            scheme = next((s for s in c["schemes"] if s["id"] == f["scheme_id"]), {})
            if has(actor, "coordinator") and f["status"] == "requested":
                need("finance", "Recommend or return a funding request", f["purpose"], "finance", f["id"])
            elif has(actor, "finance_approver") and f["status"] == "recommended" and scheme.get("owner") == org and actor["id"] not in {f.get("requester"), f.get("recommender")}:
                need("finance", "Funding recommendation awaits your decision", f["purpose"], "finance", f["id"])
            if f["status"] not in {"rejected", "cancelled"}:
                requested += _money(f.get("requested")) or 0
                released += _money(f.get("released")) or 0
        # Reports another body asked this organisation to acknowledge come first; other unread pointers follow.
        acting = has(actor, "contributor", "agency_supervisor", "coordinator", "finance_approver", "communications_reviewer")
        pending_acks = [n for n in c.get("notifications", []) if n.get("ack_required") and org not in n.get("acks", {})] if acting else []
        for n in pending_acks:
            title = next((s["title"] for s in doc["sources"] if s["id"] == n["source_id"]), "Shared report")
            need("ack", f"Acknowledge a report from {n['from']}", title, "sources", n["source_id"], at=n["at"])
        # Public requests a person must look at: doubtful ones (with the resident's answer, if any) and possible repeats.
        checks = [p for p in c.get("public_requests", []) if p["status"] == "verify" or p.get("similar_to")]
        if checks and has(actor, "coordinator"):
            answered = [p for p in checks if p.get("answer")]
            need("public", f"{len(checks)} public request{'s' if len(checks) != 1 else ''} to check",
                 f"{answered[-1]['code']} answered: {answered[-1]['answer']['text']}" if answered else (checks[-1].get("reading") or {}).get("summary") or checks[-1]["text"],
                 "overview", "public-requests", at=max((p["answer"]["at"] if p.get("answer") else p["at"] for p in checks), key=timestamp))
        suggested = [p for p in c.get("plans", []) if p["origin"] == "event" and p["status"] == "pending"]
        if suggested:
            need("proposal", f"{len(suggested)} suggestion{'s' if len(suggested) != 1 else ''} to review", suggested[0]["title"], "overview", "ai-suggestions", at=suggested[-1].get("created_at"))
        unread = [n for n in c.get("notifications", []) if actor["id"] not in n.get("read_by", []) and n not in pending_acks]
        if unread:
            need("notification", f"{len(unread)} unread shared report{'s' if len(unread) != 1 else ''}", unread[-1].get("note") or "A body asked for your attention.", "notifications", at=unread[-1]["at"])

        incident_requested, incident_released = Decimal(0), Decimal(0)
        for f in c["finance"]:
            if f["status"] not in {"rejected", "cancelled"}:
                incident_requested += _money(f.get("requested")) or 0
                incident_released += _money(f.get("released")) or 0
        incident_places, incident_unknown, incident_reported = 0, 0, None
        for a in c["assets"]:
            known = a["balance"]["available"]
            if a["owner"] == org and has(actor, "agency_supervisor") and known is None and active:
                need("resource", f"Update {a['name']}", a["balance"]["reason"], "resources", a["id"])
            if a["kind"] != "shelter":
                continue
            if known is None:
                incident_unknown += 1
                last = reported_free(_number(_value(a["facts"], "capacity")), _number(_value(a["facts"], "occupied")), _number(_value(a["facts"], "external_pending")))
                if last is not None:
                    incident_reported = (incident_reported or 0) + last
            else:
                incident_places += known
            # Owner-linked resources appear in several incidents; count the physical shelter once.
            identity = (a["owner"], a["resource_key"]) if a.get("resource_key") else (doc["id"], a["id"])
            if identity in seen_shelters or not active:
                continue
            seen_shelters.add(identity)
            facts = a["facts"]
            observed = [v["observed_at"] for v in facts.values() if isinstance(v, dict) and v.get("observed_at")]
            shelters.append({**ref, "id": a["id"], "name": a["name"], "owner": a["owner"],
                             "capacity": _number(_value(facts, "capacity")), "occupied": _number(_value(facts, "occupied")),
                             "pending": _number(_value(facts, "external_pending")), "held": a["balance"].get("held"), "available": known,
                             "reported_free": None if known is not None else reported_free(_number(_value(facts, "capacity")), _number(_value(facts, "occupied")), _number(_value(facts, "external_pending"))),
                             "status": _value(facts, "status"), "access": _value(facts, "access"), "water": _value(facts, "water"), "power": _value(facts, "power"),
                             "observed_at": max(observed, key=timestamp) if observed else None, "reason": a["balance"]["reason"],
                             "latitude": a.get("latitude"), "longitude": a.get("longitude")})

        # Organisation-to-organisation requests: point each role at the step it can take.
        asks = c.get("asks", [])
        open_asks = [a for a in asks if a["status"] not in ASK_CLOSED]
        if active:
            totals["open_requests"] += len(open_asks)
            totals["assignments_in_progress"] += sum(a["status"] in {"accepted", "in_progress"} for a in asks)
        for a in asks:
            if a["kind"] == "funding" and a["status"] not in {"declined", "cancelled"}:
                amount = _money(a.get("amount")) or 0
                released_now = amount if a["status"] in {"done", "confirmed"} else 0
                requested += amount; released += released_now
                incident_requested += amount; incident_released += released_now
        for a in open_asks:
            steps = set(ask_steps(c, a, actor))
            what = f"{a['title']} · {org_name(a['from'])} → {org_name(a['to'])}"
            if a["kind"] == "funding" and "recommend" in steps:
                need("finance", "Recommend or return a funding request", what, "requests", a["id"], at=a.get("updated_at") or a["created_at"])
            elif a["kind"] == "funding" and a["status"] == "recommended" and "accept" in steps:
                need("finance", "Funding request awaits your approval", what, "requests", a["id"], at=a.get("updated_at") or a["created_at"])
            elif "accept" in steps:
                need("ask", "New request for your organisation", what, "requests", a["id"], at=a.get("updated_at") or a["created_at"])
            elif "confirm" in steps:
                need("ask", "Confirm a delivered request", what, "requests", a["id"], at=a.get("updated_at") or a["created_at"])
            elif "start" in steps or "done" in steps:
                need("ask", "Deliver a request your organisation accepted", what, "requests", a["id"], at=a.get("updated_at") or a["created_at"])
            elif "redirect" in steps:
                up = ask_escalation(c, a) or {}
                need("ask", "Escalated to you: a request is waiting", f"{what} · {up.get('reason', '')}", "requests", a["id"], at=a.get("updated_at") or a["created_at"])
        fulfilled = sum(r["status"] == "fulfilled" for r in c["requests"]) + sum(a["status"] == "confirmed" for a in asks)
        incidents.append({**ref, "id": doc["id"], "title": doc["title"], "status": doc["status"], "phase": c.get("phase", "response"),
                          "updated_at": doc["updated_at"], "version": doc["version"], "reports": len(doc["sources"]), "pending_reports": len(pending),
                          "requests": len(c["requests"]) + len(asks), "requests_fulfilled": fulfilled, "open_requests": len(open_requests) + len(open_asks),
                          "assignments_in_progress": len(in_progress) + sum(a["status"] in {"accepted", "in_progress"} for a in asks), "shelters": sum(a["kind"] == "shelter" for a in c["assets"]),
                          "places_available": incident_places, "shelters_unknown": incident_unknown, "places_reported": incident_reported,
                          "created_at": doc.get("created_at"), "participants": c["participants"],
                          "resources": sum(a["kind"] != "shelter" for a in c["assets"]),
                          "finance": {"requested": str(incident_requested), "released": str(incident_released)},
                          "attention": sum(item["incident_id"] == doc["id"] for item in attention), "latest": None, "next": None})
        for e in doc.get("events", []):
            if e["action"] == "read_notification":
                continue
            item = {**ref, "id": e["id"], "at": e["at"], "label": EVENT_LABELS.get(e["action"], e["action"].replace("_", " ").capitalize()), "detail": e.get("detail") or ""}
            # A card's latest line shows response work, not someone switching demo roles.
            latest = incidents[-1]["latest"]
            if e["action"] != "Demo role selected" and (latest is None or timestamp(e["at"]) >= timestamp(latest["at"])):
                incidents[-1]["latest"] = {"label": item["label"], "at": item["at"]}
            activity.append(item)

    known = [s for s in shelters if s["available"] is not None]
    attention.sort(key=lambda item: (PRIORITY.get(item["kind"], 9), item["incident_title"]))
    for incident in incidents:
        # The most urgent item for this role, so a card can say what to do first.
        incident["next"] = next((item for item in attention if item["incident_id"] == incident["id"]), None)
    incidents.sort(key=lambda i: (i["status"] == "closed", -i["attention"], -timestamp(i["updated_at"]).timestamp()))
    shelters.sort(key=lambda s: (s["available"] is not None, s["available"] or 0))
    activity.sort(key=lambda e: timestamp(e["at"]), reverse=True)
    return {
        "generated_at": now_iso(), "organisation": org, "totals": {**totals, "districts": len(districts)},
        "shelters": {"count": len(shelters), "known": len(known), "unknown": len(shelters) - len(known),
                     "available": sum(s["available"] for s in known),
                     "capacity": sum(s["capacity"] for s in known if s["capacity"] is not None),
                     "occupied": sum(s["occupied"] for s in known if s["occupied"] is not None), "items": shelters,
                     # Shelters whose balance cannot be confirmed: what their owners last reported, shown as such.
                     "reported_free": sum(s["reported_free"] for s in shelters if s["reported_free"] is not None) if any(s["reported_free"] is not None for s in shelters) else None},
        "finance": {"currency": "LKR", "requested": str(requested), "released": str(released)},
        "districts": [{"name": name, "active_incidents": count} for name, count in sorted(districts.items())],
        "attention": attention, "incidents": incidents, "activity": activity[:activity_limit],
    }
