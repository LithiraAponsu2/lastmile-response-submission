"""Plans: several linked workspace commands, prepared by the AI, applied together by a person.

A plan is metadata on the incident until a person approves it. Every step is an
existing command; code checks each one as the approving person, in order, inside
one store transaction, so a plan applies completely or not at all. A step may use a
record an earlier step creates: {"$step": "s1", "record": "documents"}.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timedelta, timezone

from . import team_messages as messages
from .team_accounts import ORGANISATIONS, require
from .team_areas import SECTORS
from .workspace import WorkspaceError, digest, now_iso, record_event, timestamp, uid

PLAN_MINUTES = 30
MAX_STEPS = 8
# Free-text fields the AI may write. Numbers in them must trace to the person's words or the records.
TEXT_FIELDS = {"text", "subject", "details", "body", "title", "note", "reason", "message", "advice", "purpose"}
# Keys whose values are machinery, not facts: their digits never ground a number.
MACHINE_KEYS = re.compile(r"(?:^id$|_id$|_ids$|^hash$|_at$|^at$|^version$|^cutoff$|^expires|^request_id$|^replays$|^chats$|^plans$|^base64$|^bounds$|^centre$|^latitude$|^longitude$)")
NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])")


def org_name(org_id):
    return next((o["name"] for o in ORGANISATIONS if o["id"] == org_id), org_id)


def describe(action, data):
    """A plain-language line for a plan step."""
    org = org_name
    if action == "ask":
        amount = f"{data['quantity']} {data.get('unit') or 'units'} · " if data.get("quantity") else ""
        return f"Request to {org(data.get('to'))}: {amount}{data.get('title', '')} ({data.get('priority') or 'normal'} priority)"
    if action == "document" and data.get("kind") in messages.MESSAGE_KINDS:
        kind = {"sms": "SMS", "email": "Email", "press_release": "Press release"}[data["kind"]]
        to = ", ".join(messages.area_name(a) for a in data.get("areas", [])) if data["kind"] == "sms" else f"{len(data.get('outlets', []))} media recipients"
        return f"{kind} draft for {to}"
    if action == "document" and data.get("kind") == "letter":
        return f"Letter to {', '.join(org(o) for o in data.get('to', []))}: {data.get('subject', '')}"
    if action == "share_document":
        letter = f"the letter from step {data['document_id']['$step'][1:]}" if isinstance(data.get("document_id"), dict) else "the letter"
        return f"Share {letter} with {', '.join(org(o) for o in data.get('recipients', []))} and ask them to acknowledge"
    if action == "area_status":
        return f"{SECTORS.get(data.get('sector'), data.get('sector'))} in {messages.area_name(data.get('area', ''))}: {str(data.get('level', '')).replace('_', ' ')}"
    return action.replace("_", " ").capitalize()


def step_ref(step_id, record):
    return {"$step": step_id, "record": record}


def _is_ref(value):
    return isinstance(value, dict) and set(value) == {"$step", "record"}


def _record_ids(doc):
    """IDs of every record list in the incident, by list name."""
    found = {"sources": {s["id"] for s in doc.get("sources", [])}}
    for key, value in doc.get("coordination", {}).items():
        if isinstance(value, list) and value and all(isinstance(x, dict) and "id" in x for x in value):
            found[key] = {x["id"] for x in value}
    return found


def resolve(data, created):
    """Replace step references with the IDs those steps created."""
    if _is_ref(data):
        made = created.get(data["$step"], {}).get(data["record"], [])
        require(len(made) == 1, f"Step {data['$step']} did not create exactly one {data['record'].rstrip('s')} to use here.", 422)
        return made[0]
    if isinstance(data, dict):
        return {k: resolve(v, created) for k, v in data.items()}
    if isinstance(data, list):
        return [resolve(v, created) for v in data]
    return data


def references(data):
    if _is_ref(data):
        return {data["$step"]}
    if isinstance(data, dict):
        return set().union(*(references(v) for v in data.values())) if data else set()
    if isinstance(data, list):
        return set().union(*(references(v) for v in data)) if data else set()
    return set()


def run_steps(service, doc, actor, steps, *, record=False, plan=None):
    """Apply steps in order to doc (mutating it). Returns {step_id: {list name: [new IDs]}}."""
    created = {}
    for number, step in enumerate(steps, 1):
        before = _record_ids(doc)
        try:
            data = resolve(step["data"], created)
            detail = service.apply(doc, actor, step["action"], data)
        except WorkspaceError as error:
            # Keep the kind of refusal (e.g. a stock shortage) so callers can re-plan rather than retry.
            raise type(error)(f"Step {number} ({step['summary']}): {error}", error.status) from None
        except (KeyError, TypeError, AttributeError):
            raise WorkspaceError(f"Step {number} ({step['summary']}) is missing a field or has a wrong field type.", 422) from None
        after = _record_ids(doc)
        created[step["id"]] = {k: sorted(v - before.get(k, set())) for k, v in after.items() if v - before.get(k, set())}
        if record:
            how = "prepared with AI" if not plan or plan.get("prepared_by_ai", True) else "prepared automatically"
            note = f"{detail or step['summary']} · {how}, approved by {actor['name']}"
            record_event(doc, actor["id"], step["action"], note)
    if record and plan:
        how = "prepared with AI" if plan.get("prepared_by_ai", True) else "prepared automatically"
        record_event(doc, actor["id"], "plan_applied", f"{plan['title']} · {len(steps)} step{'s' if len(steps) != 1 else ''}, {how}")
    return created


def dry_run(service, doc, actor, steps):
    """Check every step against a copy of the incident, as this person."""
    return run_steps(service, copy.deepcopy(doc), actor, steps)


def plan_hash(steps):
    return digest(json.dumps([[s["id"], s["action"], s["data"]] for s in steps], sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def _numbers(text):
    return {n.replace(",", "") for n in NUMBER.findall(text or "")}


def _record_numbers(value, key=""):
    if MACHINE_KEYS.search(key):
        return set()
    if isinstance(value, dict):
        return set().union(*(_record_numbers(v, k) for k, v in value.items())) if value else set()
    if isinstance(value, list):
        return set().union(*(_record_numbers(v, key) for v in value)) if value else set()
    if isinstance(value, bool) or value is None:
        return set()
    if isinstance(value, (int, float)):
        return {str(value)}
    return _numbers(str(value))


def number_checks(steps, said, doc, extra=""):
    """Numbers the AI wrote that appear neither in the person's words nor in the records."""
    allowed = _numbers(said) | _numbers(extra) | _record_numbers(doc)
    checks = []
    for number, step in enumerate(steps, 1):
        mine = set(step.get("human_fields", [])) | set(step.get("code_fields", []))
        for field, value in step["data"].items():
            if field in TEXT_FIELDS and field not in mine and isinstance(value, str):
                unknown = sorted(n for n in _numbers(value) - allowed if not re.fullmatch(r"0*[0-9]", n))
                if unknown:
                    checks.append({"step": step["id"], "field": field, "level": "block",
                                   "message": f"Step {number}: {', '.join(unknown)} is not in your message or the incident records. Check it or edit it."})
    return checks


def lock_checks(plan):
    """Translations stay locked to the facts of their English original, through every edit."""
    by_id = {s["id"]: s for s in plan["steps"]}
    return [check for lock in plan.get("locks", []) if lock["step"] in by_id
            for check in messages.locked_checks(lock["step"], lock["english"], by_id[lock["step"]]["data"].get("text", ""), lock["lang"], lock["level"], lock["places"])]


def new_plan(doc, actor, steps, *, title, origin="chat", said="", checks=(), prepared_for=None):
    require(1 <= len(steps) <= MAX_STEPS, f"A plan has 1 to {MAX_STEPS} steps.", 422)
    now = datetime.now(timezone.utc)
    return {"id": uid("plan"), "title": title, "origin": origin, "steps": steps, "hash": plan_hash(steps), "version": doc["version"],
            "prepared_for": prepared_for or {"person": actor["id"]}, "prepared_by_ai": True, "said": said,
            "status": "pending", "checks": list(checks), "created_at": now.isoformat(), "expires_at": (now + timedelta(minutes=PLAN_MINUTES)).isoformat()}


def may_act(plan, actor):
    """Who a plan is for: one person (chat), or a role in an organisation (an AI suggestion)."""
    who = plan["prepared_for"]
    if "person" in who:
        return who["person"] == actor["id"]
    return actor["organisation"] == who.get("organisation") and bool(set(actor["roles"]) & set(who.get("roles", [])))


def visible(plans, actor):
    return [p for p in plans if may_act(p, actor)]


def blocking(plan):
    return [c for c in plan.get("checks", []) if c["level"] == "block"]


def is_current(plan, doc):
    return plan["status"] == "pending" and plan["version"] == doc["version"] and timestamp(plan["expires_at"]) > datetime.now(timezone.utc)


def edit(service, doc, actor, plan, changes, extra=""):
    """A person changes step fields or removes steps; the plan is checked again and re-signed."""
    require(isinstance(changes, list) and changes, "Say which steps change.", 422)
    steps = copy.deepcopy(plan["steps"])
    by_id = {s["id"]: s for s in steps}
    for change in changes:
        step = by_id.get(change.get("id"))
        require(step is not None, "That step is not in this plan.", 404)
        if change.get("remove"):
            needed = [s["id"] for s in steps if step["id"] in references(s["data"]) and s["id"] != step["id"]]
            require(not needed, f"Step {step['id']} is used by {', '.join(needed)}. Remove that step first.", 409)
            steps.remove(step)
            by_id.pop(step["id"])
            continue
        fields = change.get("data") or {}
        require(isinstance(fields, dict) and fields, "Give the fields to change.", 422)
        for key, value in fields.items():
            require(key in step["data"] or key in service_fields(step["action"]), f"{key} is not a field of this step.", 422)
            require(not _is_ref(step["data"].get(key)), f"{key} comes from an earlier step and cannot be typed in.", 422)
            step["data"][key] = value
            step["summary"] = describe(step["action"], step["data"])
            if isinstance(value, str):
                step.setdefault("human_fields", [])
                if key not in step["human_fields"]:
                    step["human_fields"].append(key)
    require(steps, "A plan needs at least one step. Dismiss it instead.", 422)
    dry_run(service, doc, actor, steps)
    now = datetime.now(timezone.utc)
    plan.update(steps=steps, hash=plan_hash(steps), version=doc["version"], edited_by=actor["id"], edited_at=now.isoformat(),
                expires_at=(now + timedelta(minutes=PLAN_MINUTES)).isoformat())
    plan["checks"] = [*number_checks(steps, plan.get("said", ""), doc, extra + " " + plan.get("extra", "")), *lock_checks(plan)]
    return plan


def service_fields(action):
    from .team_workspace import COMMAND_FIELDS
    return COMMAND_FIELDS.get(action, set())


def refresh(service, doc, actor, plan, extra=""):
    """Re-check a plan against the incident as it is now, e.g. after another change."""
    dry_run(service, doc, actor, plan["steps"])
    now = datetime.now(timezone.utc)
    plan.update(version=doc["version"], expires_at=(now + timedelta(minutes=PLAN_MINUTES)).isoformat(),
                checks=[*number_checks(plan["steps"], plan.get("said", ""), doc, extra + " " + plan.get("extra", "")), *lock_checks(plan)])
    return plan
