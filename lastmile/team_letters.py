"""Official letters between organisations: drafted (often by the AI), issued by a person, shared for acknowledgement.

A letter is internal coordination, not a public message: the person who applies it issues it,
and no communications reviewer is needed. A shared letter appears in the recipients' Reports
with the status "issued". It is never accepted evidence, so it cannot support a quantity,
a resource fact, an area record or a payment.
"""
from __future__ import annotations

import re

from .team_accounts import ORGANISATIONS, require
from .team_clock import business_now
from .workspace import digest, now_iso, uid

LETTER_FIELDS = {"share_document": {"document_id", "recipients", "note"}}
DEFAULT_TEMPLATE = "tpl-official-letter"
ACTING = ("contributor", "agency_supervisor", "coordinator", "communications_reviewer", "finance_approver")


def org_name(org_id):
    return next((o["name"] for o in ORGANISATIONS if o["id"] == org_id), org_id)


def _text(value, label, maximum, minimum=1):
    require(isinstance(value, str) and minimum <= len(value.strip()) <= maximum, f"{label}: enter {minimum}–{maximum} characters.", 422)
    return value.strip()


def reference_number(c, org, at):
    """LM/<ORG>/<year>/<incident>-<n>: numbers this organisation's letters in this incident."""
    count = sum(1 for d in c["documents"] if d["kind"] == "letter" and d["issuer"] == org) + 1
    return f"LM/{org.upper()}/{at.year}/{c['incident_id'][-4:].upper()}-{count:02d}"


def draft(service, doc, actor, d, template_lookup):
    """Issue a letter from a template: letterhead, reference, date, recipients, subject, body, signature."""
    c = doc["coordination"]
    roles = set(actor["roles"])
    require(bool(roles & set(ACTING)) and "partner" not in roles, "Your role cannot issue letters for your organisation.", 403)
    to = d.get("to")
    require(isinstance(to, list) and 1 <= len(to) <= 12 and all(isinstance(o, str) and o in c["participants"] and o != actor["organisation"] for o in to),
            "Address the letter to participating organisations other than your own.", 422)
    subject = _text(d.get("subject", ""), "Subject", 200, 3)
    body = _text(d.get("text", ""), "Letter text", 6000, 20)
    chosen = template_lookup(d.get("template_id") or DEFAULT_TEMPLATE, "letter")
    at = business_now(c)
    issuer = org_name(actor["organisation"])
    reference = reference_number(c, actor["organisation"], at)
    values = {"issuer": issuer, "reference": reference, "date": at.strftime("%d %B %Y"), "to": "; ".join(org_name(o) for o in to),
              "subject": subject, "body": body, "signer": actor["name"], "job_title": actor.get("job_title") or "",
              "incident": doc["title"], "district": c["district"]}
    rendered = re.sub(r"\n{3,}", "\n\n", re.sub(r"\{(\w+)\}", lambda m: values.get(m[1], m[0]), chosen["body"])).strip()
    return {"id": uid("document"), "kind": "letter", "language": "en", "issuer": actor["organisation"], "audience": values["to"], "to": sorted(set(to)),
            "subject": subject, "text": body, "template_id": chosen["id"], "template_name": chosen["name"], "reference": reference,
            "body": rendered, "hash": digest(rendered), "status": "issued", "prepared_by": actor["id"], "issued_by": actor["id"],
            "issued_at": now_iso(), "cutoff": at.isoformat(), "incident_version": doc["version"] + 1,
            "signed_by": {"name": actor["name"], "job_title": actor.get("job_title") or ""}, "shared": [], "delivery": "portal_only"}


def share(service, doc, actor, d):
    """Put an issued letter in the recipients' Reports and ask them to acknowledge it."""
    c = doc["coordination"]
    letter = next((x for x in c["documents"] if x["id"] == d.get("document_id")), None)
    require(letter is not None, "Document not found in this incident.", 404)
    require(letter["kind"] == "letter" and letter["status"] == "issued", "Only an issued letter can be shared this way.", 409)
    require(letter["issuer"] == actor["organisation"] and bool(set(actor["roles"]) & set(ACTING)), "Only the issuing organisation shares its letter.", 403)
    recipients = d.get("recipients")
    require(isinstance(recipients, list) and recipients and all(isinstance(r, str) and r in c["participants"] and r != actor["organisation"] for r in recipients),
            "Share with participating organisations other than your own.", 422)
    note = _text(d["note"], "Note", 1000) if d.get("note") else f"Letter {letter['reference']}: please acknowledge receipt."
    at = now_iso()
    source_id = uid("source")
    # "issued", never "accepted": a letter is a statement by its issuer, not evidence another body has reviewed.
    doc["sources"].append({"id": source_id, "title": f"Letter {letter['reference']}: {letter['subject']}", "text": letter["body"], "hash": letter["hash"],
                           "organisation": actor["organisation"], "author": actor["id"], "observed_at": letter["issued_at"], "published_at": letter["issued_at"],
                           "received_at": at, "status": "issued", "document_id": letter["id"], "attachment": None, "issues": []})
    service.notify(c, actor, source_id, recipients, note)
    letter["shared"].append({"recipients": sorted(set(recipients)), "source_id": source_id, "by": actor["id"], "at": at})
    return f"Letter {letter['reference']} shared with " + ", ".join(org_name(r) for r in sorted(set(recipients)))
