"""Which organisations take part in an incident, and how that changes.

Any team can open an incident; its organisation becomes the lead and invites the
bodies it needs. Only participating organisations can enter. Others still see that
the incident exists (title, district, lead, bodies involved) and can ask to join.
A lead of any participating body (its coordinator or supervisor) can add a body,
approve or decline a request to join, and stand a body down when its work is done.
A stood-down body keeps read access and its history, but can no longer act; adding
it again restores it. Nothing is ever deleted.
"""
from __future__ import annotations

from .team_accounts import ACTIVE, ORGANISATIONS, require
from .workspace import now_iso, uid

PARTICIPATION_FIELDS = {
    "participant": {"organisation", "reason"},
    "join_decision": {"request_id", "decision", "reason"},
    "stand_down": {"organisation", "reason"},
}
ACTION_HELP = {
    "participant": "A lead (coordinator or agency supervisor) of a participating body adds organisation ID to the incident, or brings back one that stood down; optional reason. Grants no account role.",
    "join_decision": "A lead of a participating body answers a pending join request: request_id, decision approve/decline; decline needs a reason.",
    "stand_down": "Stand down a participating organisation whose work is done: organisation and reason. Its own lead, a lead of the lead organisation or a coordinator may do it. The lead organisation cannot stand down. History stays.",
}
ACTING = ("contributor", "agency_supervisor", "coordinator", "finance_approver", "communications_reviewer")
LEADS = ("coordinator", "agency_supervisor")
# Bodies usually needed first, by hazard. A suggestion only; the creator decides.
SUGGESTED = {
    "landslide": ["dmc", "district", "division", "ndrsc", "rda"],
    "flood": ["dmc", "district", "division", "ndrsc", "water"],
    "cyclone": ["dmc", "district", "division", "ndrsc", "water", "rda"],
    "drought": ["dmc", "district", "division", "ndrsc", "water"],
    "other": ["dmc", "district", "division"],
}
HAZARDS = {"landslide": "Landslide", "flood": "Flood", "cyclone": "Cyclone or storm", "drought": "Drought", "other": "Other"}


def _has(actor, *roles):
    return bool(set(actor["roles"]) & set(roles))


def _text(value, label, maximum, minimum=1):
    require(isinstance(value, str) and minimum <= len(value.strip()) <= maximum, f"{label}: enter {minimum}–{maximum} characters.", 422)
    return value.strip()


def participation(c):
    """Per-organisation participation, backfilled for incidents saved before it existed."""
    record = c.setdefault("participation", {})
    for org in c["participants"]:
        record.setdefault(org, {"via": "original", "status": "active", "at": None, "by": None, "by_org": None, "reason": ""})
    c.setdefault("join_requests", [])
    return record


def lead_org(c):
    return c.get("lead") or ("dmc" if "dmc" in c["participants"] else c["participants"][0])


def active_orgs(c):
    record = participation(c)
    return [o for o in c["participants"] if record[o]["status"] == "active"]


def is_active(c, org):
    return org in c["participants"] and participation(c)[org]["status"] == "active"


def can_manage(c, actor):
    """Leads of any active participating body manage participation."""
    return is_active(c, actor["organisation"]) and _has(actor, *LEADS) and not _has(actor, "partner")


def start(c, actor, participants, hazard=None):
    """Participation for a new incident: the creator's body leads, the rest are invited."""
    org, at = actor["organisation"], now_iso()
    c["lead"], c["hazard"] = org, hazard
    c["participation"] = {o: {"via": "created" if o == org else "invited", "status": "active", "at": at, "by": actor["id"], "by_org": org, "reason": ""}
                          for o in c["participants"]}
    c["join_requests"] = []


def add(c, actor, org, via, reason=""):
    """Add a body, or bring back one that stood down. Returns True when something changed."""
    record = participation(c)
    entry = {"via": via, "status": "active", "at": now_iso(), "by": actor["id"], "by_org": actor["organisation"], "reason": reason}
    if org in c["participants"]:
        if record[org]["status"] == "active":
            return False
        record[org] = {**record[org], **entry, "via": "returned"}
    else:
        c["participants"].append(org)
        record[org] = entry
    for request in c["join_requests"]:
        if request["organisation"] == org and request["status"] == "pending":
            request.update(status="approved", decided_by=actor["id"], decided_org=actor["organisation"], decided_at=entry["at"], reason=reason or "Added to the incident")
    return True


def known(org, c):
    require(org in {o["id"] for o in ORGANISATIONS}, "Unknown organisation.", 422)
    require(not c.get("demo") or org in ACTIVE, "This organisation is not active in this demo.", 422)
    return org


def apply_participation(doc, actor, action, d):
    c = doc["coordination"]
    participation(c)
    names = {o["id"]: o["name"] for o in ORGANISATIONS}
    if action == "participant":
        require(can_manage(c, actor), "A coordinator or supervisor of a participating body adds organisations.", 403)
        org = known(d.get("organisation"), c)
        reason = _text(d["reason"], "Reason", 300) if d.get("reason") else ""
        require(add(c, actor, org, "invited", reason), "That organisation already takes part.")
        return f"{actor['organisation']} added {names[org]}"
    if action == "join_decision":
        require(can_manage(c, actor), "A coordinator or supervisor of a participating body answers join requests.", 403)
        request = next((r for r in c["join_requests"] if r["id"] == d.get("request_id")), None)
        require(request is not None, "Join request not found.", 404)
        require(request["status"] == "pending", "This join request was already answered.")
        require(d.get("decision") in {"approve", "decline"}, "Approve or decline the request.", 422)
        if d["decision"] == "approve":
            add(c, actor, request["organisation"], "joined", _text(d["reason"], "Reason", 300) if d.get("reason") else "")
            return f"{actor['organisation']} approved {names[request['organisation']]} joining"
        request.update(status="declined", decided_by=actor["id"], decided_org=actor["organisation"], decided_at=now_iso(), reason=_text(d.get("reason", ""), "Reason", 300, 4))
        return f"{actor['organisation']} declined {names[request['organisation']]} joining"
    if action == "stand_down":
        org = d.get("organisation")
        require(is_active(c, org), "Only an active participating organisation can stand down.", 422)
        require(org != lead_org(c), "The lead organisation cannot stand down. Close the incident instead.", 422)
        own = actor["organisation"] == org and _has(actor, *LEADS)
        require(own or (actor["organisation"] == lead_org(c) and _has(actor, *LEADS)) or (is_active(c, actor["organisation"]) and _has(actor, "coordinator")),
                "Its own lead, a lead of the lead organisation or a coordinator stands a body down.", 403)
        participation(c)[org].update(status="stood_down", stood_down_at=now_iso(), stood_down_by=actor["id"], reason=_text(d.get("reason", ""), "Reason", 300, 4))
        return f"{names[org]} stood down"
    raise AssertionError(action)


def request_join(doc, actor, note):
    """A non-participating body asks to take part. Runs without incident access."""
    c = doc["coordination"]
    participation(c)
    org = actor["organisation"]
    require(_has(actor, *ACTING), "Observers cannot ask to join an incident.", 403)
    require(not is_active(c, org), "Your organisation already takes part in this incident.")
    require(doc["status"] != "closed", "This incident is closed.")
    require(not any(r["organisation"] == org and r["status"] == "pending" for r in c["join_requests"]), "Your organisation has already asked to join. A lead will answer.")
    known(org, c)
    request = {"id": uid("join"), "organisation": org, "by": actor["id"], "at": now_iso(), "note": _text(note, "Note", 500) if note else "", "status": "pending"}
    c["join_requests"].append(request)
    return request


def summary(doc, actor):
    """What anyone in scope may see about an incident they are not part of."""
    c = doc["coordination"]
    record = participation(c)
    mine = [r for r in c["join_requests"] if r["organisation"] == actor["organisation"]]
    return {"lead": lead_org(c), "hazard": c.get("hazard"), "active_participants": active_orgs(c),
            "stood_down": [o for o in c["participants"] if record[o]["status"] == "stood_down"],
            "member": actor["organisation"] in c["participants"], "acting_member": is_active(c, actor["organisation"]),
            "my_join_request": mine[-1]["status"] if mine else None}


def view(c, actor):
    """Participation details inside the incident, with the actions this person may take."""
    record = participation(c)
    out = {"lead": lead_org(c), "hazard": c.get("hazard"), "organisations": [{"organisation": o, **record[o]} for o in c["participants"]],
           "join_requests": c["join_requests"], "can_manage": can_manage(c, actor), "active": active_orgs(c),
           "can_stand_down": [o for o in active_orgs(c) if o != lead_org(c) and (
               (actor["organisation"] == o and _has(actor, *LEADS)) or (actor["organisation"] == lead_org(c) and _has(actor, *LEADS))
               or (is_active(c, actor["organisation"]) and _has(actor, "coordinator")))]}
    return out


def vocabulary():
    return {"hazards": HAZARDS, "suggested": SUGGESTED}
