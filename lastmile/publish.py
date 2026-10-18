"""The ONLY place an alert can leave the system. Requires a human approval.

Nothing in pipeline.py imports this module; the AI agents have no route to it.
Rules enforced here, in code:
  * the run must be 'ready' (zero blocking issues, re-verified at this moment)
  * reviewer must be named; the same person cannot approve twice
  * live runs need N distinct approvers (pack channel policy); replay needs 1
  * any edit to a draft clears approvals and re-runs the deterministic checks
  * CAP status is 'Exercise' unless the deployment explicitly allows 'Actual'
  * the CAP XML must validate against the official CAP 1.2 XSD
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from functools import wraps
from typing import Optional

from . import cap, checks, rules
from .config import Settings
from .packs import PackRegistry
from .pipeline import recheck
from .schemas import Approval, Issue, PublishedAlert, Run, TraceEvent
from .store import Store

EDITABLE = ("headline", "description", "instruction", "sms", "spoken")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class PublishError(Exception):
    def __init__(self, message: str, code: str = "bad_request", status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


def _serialized(operation):
    """Edits/reviews share the run-store lock, including content-bound hub approval."""
    @wraps(operation)
    def locked(store, *args, **kwargs):
        with store._lock:
            return operation(store, *args, **kwargs)
    return locked


def _reviewer(name: str) -> str:
    name = re.sub(r"\s+", " ", _CTRL.sub("", (name or ""))).strip()
    if not 2 <= len(name) <= 80:
        raise PublishError("reviewer name (2–80 characters) is required", "reviewer_required", 422)
    return name


def _name_tokens(name: str) -> list[str]:
    return [t for t in re.split(r"[^\w]+", name.casefold()) if t]


def same_reviewer(a: str, b: str) -> bool:
    """Could these two self-declared names be the same person? Errs towards "yes".

    Names are free text until SSO is in front (see README). "Alice", "alice perera" and "A. Perera" may all be
    Alice Perera, so the two-person rule treats them as one: the shorter name's words must each match a word of
    the longer one, where a single letter matches any word with that initial.
    """
    ta, tb = _name_tokens(a), _name_tokens(b)
    if not ta or not tb:
        return a.strip().casefold() == b.strip().casefold()
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    pool = list(long_)
    for t in short:
        hit = next((w for w in pool if w == t or (len(t) == 1 and w.startswith(t)) or (len(w) == 1 and t.startswith(w))),
                   None)
        if hit is None:
            return False
        pool.remove(hit)
    return True


def effective_status(run: Run, settings: Settings) -> str:
    return rules.cap_status(run.mode, settings.allow_actual)


def drafted_by(run: Run) -> str:
    """Truthful provenance for CAP: who or what wrote the text that is going out."""
    codes = {i.code for i in run.issues}
    if run.provenance.get("drafting") == "templates" or "template_draft" in codes:
        how = "Approved pack templates (no AI wrote this text)"
    elif "llm_fallback_template" in codes:
        how = "AI-assisted, partly approved templates (model unavailable for some languages)"
    else:
        how = "AI-assisted"
    if run.editors:
        how += "; edited by a reviewer"
    return how + "; verified by deterministic checks"


def _sent_time(run: Run, pack, now: Optional[datetime] = None) -> datetime:
    """CAP <sent>: the bulletin's issue time when replaying a past event, the real clock for live runs."""
    now = now or datetime.now(timezone.utc)
    assert run.facts
    return (checks.parse_dt(run.facts.issued_iso, pack) or now) if run.mode == "replay" else now


def _refuse(store: Store, run: Run, who: str, issue: Issue, message: str, code: str) -> None:
    """Record a blocking finding on the run (it stays: only a fresh draft clears it) and refuse."""
    run.issues.append(issue)
    run.status = "blocked"
    store.audit(run, who, "approve_refused", issue.message)
    raise PublishError(message, code, 409)


def _approval_time_checks(store: Store, settings: Settings, run: Run, pack, who: str,
                          now: Optional[datetime]) -> None:
    """Things that can change between drafting and approval: the set of active alerts, and the clock."""
    assert run.facts and run.derived
    at = checks.parse_dt(run.facts.issued_iso, pack) or now or datetime.now(timezone.utc)
    msg_type, refs, _sup, _iss = rules.reconcile(run.facts, run.derived, pack, store.active_alerts(), at,
                                                 status=effective_status(run, settings), target=run.cancel_target)
    if msg_type != run.msg_type or sorted(refs) != sorted(run.references):
        was = f"{run.msg_type}" + (f" of {len(run.references)} alert(s)" if run.references else "")
        now_ = f"{msg_type}" + (f" of {len(refs)} alert(s)" if refs else "")
        _refuse(store, run, who, Issue(
            code="active_alerts_changed",
            message=(f"The alerts in force changed since this run was drafted (drafted as {was}, would now be "
                     f"{now_}). Draft again from the source so the text and CAP references match.")),
            "the active alerts changed since this run was drafted; draft it again", "active_alerts_changed")
    for issue in checks.status_wording(run.drafts, effective_status(run, settings)):
        _refuse(store, run, who, issue, issue.message, issue.code)
    expires = checks.parse_dt(run.facts.expires_iso, pack)
    sent = _sent_time(run, pack, now)
    if run.msg_type != "Cancel" and expires is not None and expires <= sent:
        _refuse(store, run, who, Issue(
            code="expired_before_send",
            message=f"This alert expires at {run.facts.expires_iso}, which is not after the send time {cap.cap_time(sent, pack)}"),
            "the alert would already be expired when sent", "expired_before_send")


def preview_cap(run: Run, registry: PackRegistry, settings: Settings) -> bytes:
    """CAP with status=Draft — for looking at, never for sending."""
    pack = registry.get(run.pack_id)
    if not run.facts or not run.derived or not run.drafts:
        raise PublishError("nothing to preview yet", "no_draft", 409)
    sent = checks.parse_dt(run.facts.issued_iso, pack) or datetime.now(timezone.utc)
    return cap.build_cap(
        pack, identifier=cap.make_identifier(pack, sent, run.id), sent=sent, status="Draft",
        msg_type=run.msg_type, facts=run.facts, derived=run.derived, drafts=run.drafts,
        references=run.references, note="DRAFT — not approved, not for distribution",
        approval_count=len({a.reviewer for a in run.approvals if a.decision == "approve"}),
        drafted_by=drafted_by(run),
    )


@_serialized
def edit_draft(store: Store, registry: PackRegistry, run_id: str, lang: str,
               fields: dict[str, str], reviewer: str, plugins: Optional[list] = None) -> Run:
    run = _get(store, run_id)
    if run.status in ("published", "rejected"):
        raise PublishError(f"run is {run.status}; it can no longer be edited", "not_editable", 409)
    pack = registry.get(run.pack_id)
    if lang not in pack.langs or lang not in run.drafts:
        raise PublishError(f"unknown language '{lang}'", "bad_lang", 422)
    who = _reviewer(reviewer)
    for k in fields:
        if k not in EDITABLE:
            raise PublishError(f"field '{k}' is not editable", "bad_field", 422)
    if not run.original_drafts:  # keep exactly what the pipeline produced, before any human touched it
        run.original_drafts = {lg: d.model_copy() for lg, d in run.drafts.items()}
    changes = []
    for k, v in fields.items():
        v = _CTRL.sub("", str(v))[:4000]
        before = getattr(run.drafts[lang], k)
        if v != before:
            setattr(run.drafts[lang], k, v)
            changes.append({"lang": lang, "field": k, "before": before, "after": v})
    if not changes:
        return run
    had_approvals = bool(run.approvals)
    run.approvals = [a for a in run.approvals if a.decision != "approve"]  # an edit voids approvals
    if not any(same_reviewer(who, e) for e in run.editors):
        run.editors.append(who)
    recheck(run, pack, plugins, edited=lang)
    store.audit(run, who, "edit_draft",
                f"{lang}: {', '.join(c['field'] for c in changes)}" + ("; approvals cleared" if had_approvals else ""),
                data={"changes": changes})
    return run


@_serialized
def review(store: Store, registry: PackRegistry, settings: Settings, run_id: str, reviewer: str,
           decision: str, note: str = "", now: Optional[datetime] = None,
           plugins: Optional[list] = None) -> Run:
    run = _get(store, run_id)
    who = _reviewer(reviewer)
    note = _CTRL.sub("", note or "")[:500]
    if run.status == "published":
        raise PublishError("already published", "already_published", 409)
    if run.status == "rejected":
        raise PublishError("run was rejected", "rejected", 409)
    pack = registry.get(run.pack_id)

    if decision == "reject":
        run.approvals.append(Approval(reviewer=who, decision="reject", note=note))
        run.status = "rejected"
        store.audit(run, who, "reject", note)
        return run
    if decision != "approve":
        raise PublishError("decision must be 'approve' or 'reject'", "bad_decision", 422)

    # re-verify at the moment of approval, regardless of what the status field says
    t0 = time.perf_counter()
    recheck(run, pack, plugins)
    if run.status == "ready":
        _approval_time_checks(store, settings, run, pack, who, now)
    run.timings["approve_checks_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    if run.status != "ready":
        store.audit(run, who, "approve_refused", f"{len(run.blocking())} blocking issue(s)")
        raise PublishError("blocking issues remain; fix them (edit the draft or correct the source) first",
                           "blocked", 409)
    approved_by = [a.reviewer for a in run.approvals if a.decision == "approve"]
    if any(same_reviewer(who, a) for a in approved_by):
        raise PublishError("you (or a reviewer with a matching name) already approved this run; a different "
                           "reviewer must give the next approval", "duplicate_approver", 409)
    status = effective_status(run, settings)
    required = pack.channels.required_approvals("live" if status == "Actual" else "replay")
    would_be = approved_by + [who]
    if len(would_be) >= required and run.editors and all(
            any(same_reviewer(r, e) for e in run.editors) for r in would_be):
        store.audit(run, who, "approve_refused", "edited text needs approval from someone who did not edit it")
        raise PublishError("this draft was edited; at least one approver must be someone other than its editor(s) "
                           f"({', '.join(run.editors)})", "editor_cannot_self_approve", 409)
    run.approvals.append(Approval(reviewer=who, decision="approve", note=note))
    approvers = {a.reviewer.casefold() for a in run.approvals if a.decision == "approve"}
    store.audit(run, who, "approve", f"{len(approvers)}/{required}" + (f" — {note}" if note else ""))
    if len(approvers) < required:
        return run
    return _publish(store, registry, settings, run, now)


def _publish(store: Store, registry: PackRegistry, settings: Settings, run: Run,
             now: Optional[datetime]) -> Run:
    t0 = time.perf_counter()
    pack = registry.get(run.pack_id)
    assert run.facts and run.derived
    now = now or datetime.now(timezone.utc)
    status = effective_status(run, settings)
    sent = _sent_time(run, pack, now)
    identifier = cap.make_identifier(pack, sent, run.id)
    note = ("EXERCISE — replay of a past event / demonstration; not a real alert."
            if status == "Exercise" else "")
    xml = cap.build_cap(
        pack, identifier=identifier, sent=sent, status=status, msg_type=run.msg_type,
        facts=run.facts, derived=run.derived, drafts=run.drafts, references=run.references, note=note,
        approval_count=len({a.reviewer for a in run.approvals if a.decision == "approve"}),
        drafted_by=drafted_by(run),
    )
    errors = cap.validate(xml)
    if errors:
        run.issues.append(Issue(code="cap_invalid", message="; ".join(errors[:3])))
        run.status = "blocked"
        store.audit(run, "system", "cap_invalid", "; ".join(errors[:3]))
        raise PublishError("generated CAP failed schema validation: " + errors[0], "cap_invalid", 500)

    place_levels = {pid: lv for lv, pids in run.derived.places_by_level.items() for pid in pids}
    superseded = [r.split(",")[1] for r in run.references if r.count(",") >= 2]
    run.cap_xml = xml.decode("utf-8")
    run.cap_identifier = identifier
    run.cap_status = status
    # what leaves on each channel: drill marker (unless Actual), issuer, hotline and a reference people can check
    drill = status != "Actual"
    ref = reference(identifier)
    sms = {lg: checks.sms_envelope(pack, lg, run.drafts[lg].sms, exercise=drill, ref=ref) for lg in pack.langs}
    run.outbox = {
        "cap": f"{identifier}.xml",
        "status": status,
        "reference": ref,
        "sms": {lg: {"text": sms[lg], "segments": checks.sms_segments(sms[lg])[2],
                     "encoding": checks.sms_segments(sms[lg])[0]} for lg in pack.langs},
        "voice": {lg: {"script": checks.voice_envelope(pack, lg, run.drafts[lg].spoken, exercise=drill),
                       "tts_lang": pack.region.languages[lg].tts_lang} for lg in pack.langs},
        "note": "No live channel is connected in this build; payloads are written for a gateway to pick up.",
    }
    run.status = "published"
    store.write_outbox(run.id, f"{identifier}.xml", xml)
    store.add_published(
        PublishedAlert(identifier=identifier, run_id=run.id, pack_id=pack.id,
                       sender=pack.region.authority.sender_id, sent=cap.cap_time(sent, pack),
                       expires=run.facts.expires_iso, msg_type=run.msg_type, status=status,
                       place_levels=place_levels, cancelled=(run.msg_type == "Cancel")),
        supersedes=superseded,
    )
    ms = round((time.perf_counter() - t0) * 1000, 2)
    run.timings["publish_ms"] = ms
    run.trace.append(TraceEvent(step="publish", actor="code", ms=ms,
                                summary="CAP 1.2 built and validated against the XSD; SMS/voice payloads written"))
    store.audit(run, "system", "published", f"{identifier} status={status} msgType={run.msg_type}")
    return run


def reference(identifier: str) -> str:
    """Short public reference printed in every SMS: the unique tail of the CAP identifier."""
    return identifier.rsplit("-", 1)[-1].upper()


def _get(store: Store, run_id: str) -> Run:
    run = store.get(run_id)
    if run is None:
        raise PublishError("run not found", "not_found", 404)
    return run
