"""Public messages: SMS, email and press releases drafted from templates, approved, then sent.

Templates and media recipients are standing lists in the reference registry (Communications page).
Sending is recorded, never transmitted: no SMS gateway, mail server or news wire is connected, and the
record says so. An SMS goes to areas (districts or DS divisions); an email or press release goes to
listed media desks.
"""
import re

from .team_accounts import require
from .team_areas import DISTRICT_IDS, DIVISIONS
from .team_clock import business_now
from .workspace import digest, now_iso, uid

MESSAGE_KINDS = {"sms", "email", "press_release"}
MESSAGE_FIELDS = {"send_document": {"document_id", "hash", "areas", "outlets"}}
DOCUMENT_EXTRA = {"template_id", "details", "text", "subject", "areas", "outlets"}
SMS_MAX = 480  # three parts in English; Sinhala and Tamil fit fewer characters per part
TEXT_MAX = 6000
HOTLINE = "117"


def sms_parts(text):
    """Parts a phone network would bill: 160/153 characters in plain text, 70/67 with Sinhala or Tamil."""
    single, multi = (70, 67) if any(ord(ch) > 127 for ch in text) else (160, 153)
    return 1 if len(text) <= single else -(-len(text) // multi)


def area_name(area):
    if area in DIVISIONS:
        return DIVISIONS[area]["name"] + " DS"
    return DISTRICT_IDS[area]["name"] + " District" if area in DISTRICT_IDS else area


def clean_areas(value, required=False):
    value = value or []
    require(isinstance(value, list) and len(value) <= 400 and all(isinstance(a, str) and (a in DIVISIONS or a in DISTRICT_IDS) for a in value), "Choose listed districts or DS divisions.", 422)
    require(bool(value) or not required, "Choose at least one area to send the SMS to.", 422)
    return sorted(set(value))


def clean_outlets(service, value, required=False):
    value = value or []
    require(isinstance(value, list) and len(value) <= 200 and all(isinstance(o, str) and len(o) <= 80 for o in value), "Choose listed media recipients.", 422)
    reference = getattr(service, "reference", None)
    if reference is not None and value:
        known = {r["id"] for r in reference.dataset("media")["records"] if not r.get("retired")}
        require(set(value) <= known, "Choose recipients from the Communications recipient list.", 422)
    require(bool(value) or not required, "Choose at least one recipient.", 422)
    return sorted(set(value))


def template(service, template_id, kind):
    reference = getattr(service, "reference", None)
    require(reference is not None, "Message templates are not available here.", 422)
    found = next((r for r in reference.dataset("templates")["records"] if r["id"] == template_id and not r.get("retired")), None)
    require(found is not None, "Choose a template from the Communications page.", 422)
    require(found["channel"] == kind, "That template is for another kind of message.", 422)
    return found


def fill(text, values):
    """Replace {placeholders}; an unknown one stays visible so the reviewer sees it."""
    return re.sub(r" {2,}", " ", re.sub(r"\{(\w+)\}", lambda m: values.get(m[1], m[0]), text or "")).strip()


def values(doc, issuer, details, areas):
    c = doc["coordination"]
    at = business_now(c)
    return {"incident": doc["title"], "district": c["district"], "areas": ", ".join(area_name(a) for a in areas) or f"{c['district']} District",
            "details": details, "date": at.strftime("%d %b %Y"), "time": at.strftime("%H:%M"), "hotline": HOTLINE, "issuer": issuer}


def draft(service, doc, actor, org_name, d, at):
    """A message draft for review. `text` (as typed or edited) wins over the template's body."""
    kind = d["kind"]
    language = d.get("language", "en")
    require(language in {"en", "si", "ta"}, "Choose English, Sinhala or Tamil.", 422)
    details = d.get("details") or ""
    require(isinstance(details, str) and len(details) <= 1000, "Details: up to 1,000 characters.", 422)
    areas = clean_areas(d.get("areas"))
    outlets = clean_outlets(service, d.get("outlets"))
    chosen = template(service, d["template_id"], kind) if d.get("template_id") else None
    fills = values(doc, org_name, details.strip(), areas)
    text = d.get("text") or fill(chosen["body"] if chosen else "", fills)
    subject = d.get("subject") or (fill(chosen.get("subject"), fills) if chosen else "") or None
    limit = SMS_MAX if kind == "sms" else TEXT_MAX
    require(isinstance(text, str) and 1 <= len(text.strip()) <= limit, f"Message: 1–{limit:,} characters.", 422)
    require(subject is None or isinstance(subject, str) and len(subject) <= 200, "Subject: up to 200 characters.", 422)
    text = text.strip()
    body = (f"Subject: {subject}\n\n" if subject else "") + text
    audience = d.get("audience") or ("Public in " + fills["areas"] if kind == "sms" else "News media")
    return {"id": uid("document"), "kind": kind, "audience": audience[:200], "language": language, "issuer": actor["organisation"],
            "cutoff": at.isoformat(), "incident_version": doc["version"] + 1, "body": body, "text": text, "subject": subject,
            "template_id": chosen["id"] if chosen else None, "template_name": chosen["name"] if chosen else None,
            "planned": {"areas": areas, "outlets": outlets}, "hash": digest(body), "status": "draft", "prepared_by": actor["id"],
            "delivery": "not_sent_exercise_artifact"}


def reach(service, areas):
    """People living in the chosen areas, from the Population list; None when an area has no figure."""
    reference = getattr(service, "reference", None)
    if reference is None:
        return None
    # District rows carry the district id as their record id; DS rows imported from the census name their area.
    figures = {r.get("area") or r["id"]: r["population"] for r in reference.dataset("population")["records"] if not r.get("retired")}
    whole = {DISTRICT_IDS[a]["name"] for a in areas if a in DISTRICT_IDS}
    covered = [a for a in areas if not (a in DIVISIONS and DIVISIONS[a]["district"] in whole)]  # a DS inside a chosen district counts once
    if not all(a in figures for a in covered):
        return None
    return sum(figures[a] for a in covered)


def apply_message(service, doc, actor, action, d):
    from .team_workspace import find, has
    c, org = doc["coordination"], actor["organisation"]
    if action == "send_document":
        require(has(actor, "coordinator", "communications_reviewer"), "A coordinator or communications reviewer sends messages.", 403)
        item = find(c["documents"], d.get("document_id"))
        require(item["kind"] in MESSAGE_KINDS, "Only SMS, email and press releases are sent from here.", 422)
        require(item["issuer"] == org, "The issuing organisation sends its own messages.", 403)
        require(item["status"] == "approved_exercise" and item["hash"] == d.get("hash"), "Send the exact approved message. It is not approved yet, was changed or was already sent.")
        if item["kind"] == "sms":
            areas = clean_areas(d.get("areas"), required=True)
            item.update(sent_to={"areas": areas, "outlets": []}, reach=reach(service, areas))
        else:
            outlets = clean_outlets(service, d.get("outlets"), required=True)
            item.update(sent_to={"areas": [], "outlets": outlets}, reach=None)
        item.update(status="sent", sent_by=actor["id"], sent_at=now_iso(), delivery="simulated_no_transmission")


# Fixed Sinhala and Tamil wording, the same words the public status page uses. Prototype translation.
LEVEL_WORDS = {"si": {"red": "රතු", "amber": "තැඹිලි", "yellow": "කහ"}, "ta": {"red": "சிவப்பு", "amber": "செம்மஞ்சள்", "yellow": "மஞ்சள்"}}
ACTION_WORDS = {"si": {"red": "වහාම ඉවත් වන්න", "amber": "ඉවත් වීමට සූදානම් වන්න", "yellow": "සුපරීක්ෂාකාරී වන්න"},
                "ta": {"red": "உடனடியாக வெளியேறுங்கள்", "amber": "வெளியேறத் தயாராகுங்கள்", "yellow": "விழிப்புடன் இருங்கள்"}}
HAZARD_WORDS = {"si": {"landslide": "නායයෑම්", "flood": "ගංවතුර", "rain": "තද වැසි", "wind": "තද සුළං", "cyclone": "සුළි කුණාටුව", "tsunami": "සුනාමි", "other": "අනතුරු"},
                "ta": {"landslide": "மண்சரிவு", "flood": "வெள்ளம்", "rain": "கடும் மழை", "wind": "பலத்த காற்று", "cyclone": "சூறாவளி", "tsunami": "சுனாமி", "other": "ஆபத்து"}}
WARNING_WORD = {"si": "අනතුරු ඇඟවීම", "ta": "எச்சரிக்கை"}
HELP_WORD = {"si": "උදව්", "ta": "உதவி"}


def locked_checks(step_id, english, text, lang, level, places):
    """A translation keeps the facts: every number, the hotline, the warning level and each place name."""
    problems = []
    en, other = _numbers(english), _numbers(text)
    if en - other:
        problems.append("drops " + ", ".join(sorted(en - other)))
    if other - en:
        problems.append("adds " + ", ".join(sorted(other - en)))
    if LEVEL_WORDS[lang][level] not in text:
        problems.append(f"does not say the level ({LEVEL_WORDS[lang][level]})")
    if HOTLINE not in text:
        problems.append("drops the hotline " + HOTLINE)
    missing = [p for p in places if p not in text]
    if missing:
        problems.append("drops " + ", ".join(missing))
    name = {"si": "Sinhala", "ta": "Tamil"}[lang]
    return [{"step": step_id, "field": "text", "level": "block", "message": f"The {name} SMS {'; '.join(problems)}. Edit it to match the English."}] if problems else []


def _numbers(text):
    return {n.replace(",", "") for n in re.findall(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])", text or "")}

