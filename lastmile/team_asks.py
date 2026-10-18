"""Organisation-to-organisation requests: the simple collaboration flow.

One organisation asks another for something. The receiving lead accepts or
declines, the receiving organisation delivers, and the asking side confirms.
Funding requests add one step: a coordinator recommends, then the scheme
owner's finance approver approves and releases. Each step is attributed and
kept in the request's history. Nothing here dispatches, pays or sends anything.

Priority sets how long the receiving body has to answer. A request that is not
answered in time, or is past its needed-by time, escalates up the ladder
Division -> District -> DMC; the asker can also escalate by hand. The body it
reaches can redirect it to another organisation.
"""
from __future__ import annotations

import re
from decimal import Decimal

from . import team_participation as participation
from .team_accounts import require
from .team_areas import DIVISIONS
from .team_clock import business_now
from .workspace import now_iso, timestamp, uid

ASK_FIELDS = {
    "ask": {"to", "kind", "title", "quantity", "unit", "amount", "due_at", "note", "source_id", "priority", "delivery_area", "delivery_place", "place_key", "public_request_id"},
    "ask_update": {"ask_id", "step", "note", "to"},
}
ASK_KINDS = {"supplies", "water", "shelter", "transport", "team", "road", "power", "other", "funding"}
OPEN = {"asked", "recommended", "accepted", "in_progress", "done"}
CLOSED = {"confirmed", "declined", "cancelled"}
ACTING = ("contributor", "agency_supervisor", "coordinator", "finance_approver")
# Hours the receiving body has to answer before the request moves one level up.
PRIORITY_HOURS = {"urgent": 1, "high": 4, "normal": 12}
LADDER = ["division", "district", "dmc"]


def escalation(c, ask, now=None):
    """Who should step in now, if anyone: computed from priority, waiting time, the needed-by time and manual escalations."""
    if ask["status"] not in OPEN - {"done"}:
        return None
    now = now or business_now(c)
    hours = PRIORITY_HOURS[ask.get("priority", "normal")]
    auto, reasons = 0, []
    if ask["status"] in {"asked", "recommended"}:
        waited = (now - timestamp(ask.get("redirected_at") or ask["created_at"])).total_seconds() / 3600
        if waited >= hours:
            auto = int(waited // hours)
            reasons.append(f"no answer in {hours} h ({ask.get('priority', 'normal')} priority)")
    if ask.get("due_at") and timestamp(ask["due_at"]) < now:
        auto = max(auto, 1)
        reasons.append("past its needed-by time")
    manual = len(ask.get("escalations", []))
    if manual:
        reasons.append("escalated by " + ", ".join(e["organisation"] for e in ask["escalations"]))
    level = max(auto, manual)
    if not level:
        return None
    start = LADDER.index(ask["from"]) + 1 if ask["from"] in LADDER else 1
    chain = [o for o in LADDER[start:] if o not in {ask["to"]}] or ["dmc"]
    return {"level": level, "to": chain[min(level, len(chain)) - 1], "chain": chain[:level], "reason": "; ".join(reasons)}


def _has(actor, *roles):
    return bool(set(actor["roles"]) & set(roles))


def lead(actor, org):
    """The organisation's decision-maker: its supervisor, or a coordinator for central bodies."""
    return actor["organisation"] == org and _has(actor, "agency_supervisor", "coordinator")


def member(actor, org):
    return actor["organisation"] == org and _has(actor, *ACTING)


def _text(value, label, maximum, minimum=1):
    require(isinstance(value, str) and minimum <= len(value.strip()) <= maximum, f"{label}: enter {minimum}–{maximum} characters.", 422)
    return value.strip()


def _money(value):
    require(isinstance(value, str) and bool(re.fullmatch(r"\d{1,10}(?:\.\d{1,2})?", value)) and Decimal(value) > 0, "Amount must be a positive LKR amount, with at most two decimal places.", 422)
    return Decimal(value).quantize(Decimal("0.01"))


def allowed(c, ask, actor):
    """Steps this actor may take on this request now. The UI shows exactly these."""
    status, funding, frm, to = ask["status"], ask["kind"] == "funding", ask["from"], ask["to"]
    approver = actor["organisation"] == to and _has(actor, "finance_approver") and actor["id"] not in {ask["created_by"], ask.get("recommended_by")}
    steps = []
    if funding:
        if status == "asked" and _has(actor, "coordinator") and actor["id"] != ask["created_by"]:
            steps += ["recommend", "decline"]
        if status == "recommended" and approver:
            steps += ["accept", "decline"]
        if status == "accepted" and actor["organisation"] == to and _has(actor, "finance_approver"):
            steps.append("done")
    else:
        if status == "asked" and lead(actor, to):
            steps += ["accept", "decline"]
        if status == "accepted" and member(actor, to):
            steps.append("start")
        if status in {"accepted", "in_progress"} and member(actor, to):
            steps.append("done")
    if status == "done" and (lead(actor, frm) or actor["id"] == ask["created_by"]):
        steps += ["confirm", "reopen"]
    if status in OPEN - {"done"} and (lead(actor, frm) or actor["id"] == ask["created_by"] or _has(actor, "coordinator")):
        steps.append("cancel")
    if status in OPEN - {"done"} and (lead(actor, frm) or actor["id"] == ask["created_by"]):
        steps.append("escalate")
    up = escalation(c, ask)
    if not funding and status == "asked" and up and (lead(actor, up["to"]) or _has(actor, "coordinator") and actor["organisation"] == "dmc"):
        steps.append("redirect")
    if status in OPEN and _has(actor, *ACTING) and participation.is_active(c, actor["organisation"]):
        steps.append("comment")
    return steps


NEXT = {"recommend": "recommended", "accept": "accepted", "decline": "declined", "start": "in_progress",
        "done": "done", "confirm": "confirmed", "reopen": "in_progress", "cancel": "cancelled"}
NEEDS_NOTE = {"decline", "reopen", "cancel", "comment", "escalate", "redirect"}


def approved_total(c, scheme_id):
    return sum((Decimal(a["amount"]) for a in c.get("asks", []) if a["kind"] == "funding" and a.get("scheme_id") == scheme_id and a["status"] in {"accepted", "done", "confirmed"}), Decimal(0)) + \
        sum((Decimal(f["approved"]) for f in c.get("finance", []) if f.get("scheme_id") == scheme_id and f.get("approved") and f["status"] not in {"rejected", "cancelled"}), Decimal(0))


def apply_ask(doc, actor, action, d):
    c = doc["coordination"]
    asks = c.setdefault("asks", [])
    org = actor["organisation"]
    if action == "ask":
        require(_has(actor, "contributor", "agency_supervisor", "coordinator"), "Your role can read requests but not send them.", 403)
        kind = d.get("kind")
        require(kind in ASK_KINDS, "Choose what kind of help is needed.", 422)
        record = {"id": uid("ask"), "from": org, "kind": kind, "title": _text(d.get("title", ""), "What is needed", 200, 3),
                  "note": _text(d["note"], "Note", 1000) if d.get("note") else "", "created_by": actor["id"], "created_at": now_iso(),
                  "status": "asked", "quantity": None, "unit": "", "amount": None, "due_at": None, "source_id": None,
                  "priority": d.get("priority") or "normal", "delivery_area": None, "delivery_place": "", "place_key": None, "escalations": []}
        require(record["priority"] in PRIORITY_HOURS, "Priority is urgent, high or normal.", 422)
        if d.get("delivery_area"):
            require(d["delivery_area"] in DIVISIONS, "Deliver to a DS division from the list.", 422)
            record["delivery_area"] = d["delivery_area"]
        if d.get("delivery_place"):
            record["delivery_place"] = _text(d["delivery_place"], "Delivery place", 150)
        if d.get("place_key"):
            require(bool(re.fullmatch(r"centre-[a-z0-9-]{3,80}", d["place_key"])), "Choose a registered safety centre.", 422)
            record["place_key"] = d["place_key"]
        if kind == "funding":
            require(not _has(actor, "partner"), "Partners do not request funding through the portal.", 403)
            scheme = c["schemes"][0]
            require(d.get("to") in (None, "", scheme["owner"]), "Funding requests go to the relief scheme owner.", 422)
            record.update(to=scheme["owner"], amount=str(_money(d.get("amount"))), scheme_id=scheme["id"], currency=scheme["currency"])
            require(Decimal(record["amount"]) <= Decimal(scheme["limit"]), f"A single request can be at most LKR {scheme['limit']} under this scheme.", 422)
        else:
            to = d.get("to")
            require(to != org, "Choose another organisation.", 422)
            if not participation.is_active(c, to):
                # A lead can bring the body in by asking it; others ask a lead to add it first.
                require(participation.can_manage(c, actor), "That organisation is not in this incident yet. Ask a coordinator or supervisor to add it, or send the request as a lead.", 422)
                participation.add(c, actor, participation.known(to, c), "request", f"Added to receive a request: {record['title']}"[:300])
            record["to"] = to
            if d.get("quantity") is not None:
                require(type(d["quantity"]) is int and 0 < d["quantity"] <= 1000000, "Quantity must be a whole number, or left blank when unknown.", 422)
                record.update(quantity=d["quantity"], unit=_text(d.get("unit") or "units", "Unit", 40))
        if d.get("due_at"):
            timestamp(_text(d["due_at"], "Needed by", 50))
            record["due_at"] = d["due_at"]
        if d.get("source_id"):
            require(any(s["id"] == d["source_id"] for s in doc["sources"]), "Linked report not found in this incident.", 404)
            record["source_id"] = d["source_id"]
        if d.get("public_request_id"):
            # The public tracker follows this request: its status is what the person who asked sees.
            require(any(p["id"] == d["public_request_id"] for p in c.get("public_requests", [])), "Public request not found in this incident.", 404)
            require(not any(a.get("public_request_id") == d["public_request_id"] and a["status"] not in CLOSED for a in asks), "This public request already has an open request.", 409)
            record["public_request_id"] = d["public_request_id"]
        record["history"] = [{"at": record["created_at"], "actor": actor["id"], "organisation": org, "step": "asked", "note": record["note"]}]
        asks.append(record)
        return f"{org} → {record['to']}: {record['title']}"
    ask = next((a for a in asks if a["id"] == d.get("ask_id")), None)
    require(ask is not None, "Request not found in this incident.", 404)
    step = d.get("step")
    require(step in NEXT or step in {"comment", "escalate", "redirect"}, "Unsupported request step.", 422)
    require(step in allowed(c, ask, actor), "Your organisation and role cannot take this step on this request now.", 403)
    note = _text(d.get("note", ""), "Reason" if step != "comment" else "Comment", 1000, 3) if step in NEEDS_NOTE else (d.get("note") or "").strip()[:1000]
    if step == "accept" and ask["kind"] == "funding":
        scheme = next(s for s in c["schemes"] if s["id"] == ask["scheme_id"])
        require(approved_total(c, scheme["id"]) + Decimal(ask["amount"]) <= Decimal(scheme["budget"]), "Approving this would exceed the scheme budget.")
    if step == "escalate":
        ask.setdefault("escalations", []).append({"at": now_iso(), "actor": actor["id"], "organisation": org, "note": note})
    elif step == "redirect":
        new = d.get("to")
        require(new not in {None, ask["to"], ask["from"]} and participation.is_active(c, new), "Redirect to another organisation taking part in this incident.", 422)
        note = f"{note} (from {ask['to']} to {new})"
        ask["to"], ask["redirected_at"], ask["escalations"] = new, now_iso(), []
    elif step != "comment":
        ask["status"] = NEXT[step]
        ask[{"recommend": "recommended_by", "accept": "accepted_by", "decline": "declined_by", "done": "done_by", "confirm": "confirmed_by"}.get(step, step + "_by")] = actor["id"]
    ask["updated_at"] = now_iso()
    ask["history"].append({"at": ask["updated_at"], "actor": actor["id"], "organisation": org, "step": step, "note": note})
    return f"{ask['title']}: {step}"


def ask_view(c, actor):
    """Attach the steps this actor can take and any escalation, so the UI never guesses."""
    now = business_now(c)
    for ask in c.get("asks", []):
        ask.setdefault("priority", "normal")
        ask["escalation"] = escalation(c, ask, now)
        ask["can"] = allowed(c, ask, actor)
