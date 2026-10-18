"""AI suggestions: when something happens in an incident, prepare a plan for the role that should act.

Triggers come from a scan of the incident, so a missed or repeated event never doubles a
suggestion: each trigger has a key, recorded once in `coordination.suggested`. A suggestion is
an ordinary plan (origin "event") for an organisation and role; it applies only when a person
in that role approves it, checked as that person. Offline, every suggestion is built by code
(templates, the fixed report readers, comparisons) and labelled so; with Gemini, the wording,
translations and report reading come from the model and are checked by code.
"""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import bulletins
from . import team_messages as messages
from . import team_plans as plans
from .team_accounts import ORGANISATIONS
from .team_areas import BY_DISTRICT, DISTRICT_IDS, DIVISIONS, HAZARDS, IMPACT_FIELDS, ISSUERS, SECTORS
from .team_asks import escalation
from .team_clock import business_now
from .team_participation import lead_org
from .workspace import WorkspaceError, now_iso, timestamp

FIXTURE = "exercise_fixture"
AI_ID = "ai-suggestions"
LEVEL_NAME = {"red": "Red", "amber": "Amber", "yellow": "Yellow"}
LEVEL_WORDS, ACTION_WORDS, HAZARD_WORDS, WARNING_WORD, HELP_WORD = messages.LEVEL_WORDS, messages.ACTION_WORDS, messages.HAZARD_WORDS, messages.WARNING_WORD, messages.HELP_WORD
SMS_TEMPLATE = {"landslide": "tpl-landslide-warning", "flood": "tpl-flood-warning", "rain": "tpl-flood-warning"}
ISSUER_WORDS = {"nbro": ("nbro", "national building research"), "met": ("department of meteorology", "met department", "meteorology"),
                "irrigation": ("irrigation department",), "dmc": ("disaster management centre", "disaster management center", "dmc")}
HAZARD_KEYS = {"landslide": ("landslide", "slope"), "flood": ("flood",), "cyclone": ("cyclone",), "wind": ("strong wind", "wind"), "rain": ("heavy rain", "rain"), "tsunami": ("tsunami",)}
# Who settles a disagreement about an area: the body that owns the sector, else the district office.
SETTLER = {"water": "water", "roads": "rda"}


def org_name(org_id):
    return next((o["name"] for o in ORGANISATIONS if o["id"] == org_id), org_id)


def ai_actor(doc, org, roles):
    """Who a suggestion is checked as while it is prepared. Approval re-checks it as the real person."""
    return {"id": AI_ID, "name": "AI suggestion", "organisation": org, "roles": list(roles), "districts": ["*"],
            "demo": bool(doc["coordination"].get("demo")), "job_title": ""}


def human(record):
    """Made by a person, not by the labelled practice fixture."""
    return FIXTURE not in (record.get("by"), record.get("created_by"), record.get("author")) and not record.get("fixture")


# ---------------------------------------------------------------- triggers
def triggers(doc):
    """Everything in the incident that deserves a suggestion, each with a stable key."""
    c = doc["coordination"]
    found = []
    for w in c.get("warnings", []):
        if human(w) and not w.get("ended_at"):
            found.append({"key": "warning:" + w["id"], "kind": "warning_messages", "warning": w})
    for s in doc["sources"]:
        if s["status"] == "reported" and human(s) and not s.get("fixture") and len(s.get("text") or "") >= 40:
            found.append({"key": "report:" + s["id"], "kind": "report_reading", "source": s})
    now = business_now(c)
    for ask in c.get("asks", []):
        up = escalation(c, ask, now)
        if up and human(ask) and ask["kind"] != "funding" and len(ask.get("escalations", [])) < up["level"]:
            found.append({"key": f"escalation:{ask['id']}:{up['level']}", "kind": "escalation_note", "ask": ask, "up": up})
    for item in c.get("public_requests", []):
        # A filed public request with no request to an owner yet: suggest the owner, team and details. One flagged as
        # possibly the same problem as another waits for a person to decide.
        if item["status"] == "filed" and not item.get("group_of") and not item.get("similar_to") and not any(a.get("public_request_id") == item["id"] for a in c.get("asks", [])):
            found.append({"key": "public:" + item["id"], "kind": "public_request", "item": item})
    for item in disagreements(c):
        found.append({"key": "disagreement:" + item["key"], "kind": "disagreement", "item": item})
    return found


def latest_by_org(records, area, field):
    out = {}
    for r in sorted((r for r in records if r["area"] == area and r.get(field) is not None), key=lambda r: r.get("as_of") or r["at"]):
        out[r["organisation"]] = r
    return out


def disagreements(c):
    """Figures or service levels for the same area that two bodies report differently."""
    found = []
    figures = [{**r, **r.get("values", {})} if "values" in r else r for r in c.get("area_figures", [])]
    areas = {r["area"] for r in figures}
    for area in sorted(areas):
        for field in ("people_affected", "families_affected", "deaths", "missing"):
            latest = latest_by_org(figures, area, field)
            values = {org: r[field] for org, r in latest.items()}
            if len(set(values.values())) > 1 and any(human(r) for r in latest.values()):
                found.append({"key": f"{area}:{field}:" + ",".join(f"{o}={v}" for o, v in sorted(values.items())), "type": "figures", "area": area, "field": field,
                              "values": values, "as_of": {o: r.get("as_of") or r["at"] for o, r in latest.items()}})
    # A district's total cannot be smaller than what its own divisions report.
    for district_id, district in DISTRICT_IDS.items():
        if district_id not in areas:
            continue
        for field in ("people_affected",):
            totals = latest_by_org(figures, district_id, field)
            parts, made = {}, [*totals.values()]
            for ds in BY_DISTRICT.get(district["name"], []):
                latest = latest_by_org(figures, ds, field)
                if latest:
                    newest = max(latest.values(), key=lambda r: r.get("as_of") or r["at"])
                    parts[ds] = newest[field]
                    made.append(newest)
            summed = sum(parts.values())
            for org, record in totals.items():
                value = record[field]
                if len(parts) >= 2 and summed > value and any(human(r) for r in made):
                    found.append({"key": f"{district_id}:{field}:sum:{org}={value}:{summed}", "type": "sum", "area": district_id, "field": field,
                                  "values": {org: value, "divisions": summed}, "parts": parts, "as_of": {}})
    levels = {"working": 0, "restoring": 1, "disrupted": 2}
    for area in sorted({r["area"] for r in c.get("area_status", [])}):
        for sector in SECTORS:
            latest = {}
            for r in sorted((r for r in c["area_status"] if r["area"] == area and r["sector"] == sector), key=lambda r: r["at"]):
                latest[r["organisation"]] = r
            known = {o: r["level"] for o, r in latest.items() if r["level"] in levels}
            if len(known) > 1 and any(human(r) for r in latest.values()) and max(levels[v] for v in known.values()) - min(levels[v] for v in known.values()) == 2:
                found.append({"key": f"{area}:{sector}:" + ",".join(f"{o}={v}" for o, v in sorted(known.items())), "type": "status", "area": area, "field": sector,
                              "values": known, "as_of": {o: r["at"] for o, r in latest.items()}})
    return found


# ---------------------------------------------------------------- builders: each returns a plan outline or None
def offline_translation(english_values, warning, lang, with_time):
    """Fixed wording from the warning's facts (not a translation of free text)."""
    level, hazard = warning["level"], warning["hazard"]
    when = f" ({english_values['date']} {english_values['time']})" if with_time else ""
    return (f"{HAZARD_WORDS[lang][hazard]} {WARNING_WORD[lang]} · {LEVEL_WORDS[lang][level]}{when}: {english_values['areas']}. "
            f"{ACTION_WORDS[lang][level]}. {HELP_WORD[lang]}: {messages.HOTLINE}")


def warning_plan(doc, t, service, writer):
    w = t["warning"]
    c = doc["coordination"]
    org = w["organisation"]
    areas = sorted(set(w["areas"]))
    issuer = ISSUERS.get(w["issuer"], w["issuer"])
    template_id = "tpl-evacuate-now" if w["level"] == "red" else SMS_TEMPLATE.get(w["hazard"], "tpl-flood-warning")
    template = messages.template(service, template_id, "sms")
    details = f"{issuer} {LEVEL_NAME[w['level']]} {HAZARDS[w['hazard']].lower()} warning."
    values = messages.values(doc, org_name(org), details, areas)
    english = messages.fill(template["body"], values)
    with_time = "{time}" in template["body"]
    places = [DIVISIONS[a]["name"] if a in DIVISIONS else DISTRICT_IDS[a]["name"] for a in areas]
    steps = [{"id": "s1", "action": "document", "summary": f"English SMS to {', '.join(messages.area_name(a) for a in areas)}",
              "data": {"kind": "sms", "language": "en", "template_id": template_id, "details": details, "text": english, "areas": areas}, "code_fields": ["text", "details"]}]
    checks, models, locks = [], [], []
    for number, lang in enumerate(("si", "ta"), 2):
        if writer.translate:
            text, model = writer.translate(english, lang, {"level": LEVEL_WORDS[lang][w["level"]], "places": places, "hotline": messages.HOTLINE})
            models.append(model)
            fields = []
        else:
            text, fields = offline_translation(values, w, lang, with_time), ["text"]
        sid = f"s{number}"
        steps.append({"id": sid, "action": "document", "summary": f"{ {'si': 'Sinhala', 'ta': 'Tamil'}[lang]} SMS to the same areas",
                      "data": {"kind": "sms", "language": lang, "text": text, "areas": areas}, "code_fields": fields})
        locks.append({"step": sid, "english": english, "lang": lang, "level": w["level"], "places": places})
    release = messages.template(service, "tpl-media-release-situation", "press_release")
    advice = (w.get("advice") or "").strip()
    rel_details = f"{issuer} has issued a {LEVEL_NAME[w['level']]} {HAZARDS[w['hazard']].lower()} warning for {', '.join(messages.area_name(a) for a in areas)}." + (f" Advice: {advice}" if advice else "")
    rel_values = messages.values(doc, org_name(org), rel_details, areas)
    outlets = [r["id"] for r in service.reference.dataset("media")["records"] if not r.get("retired") and r["language"] in {"en", "all"}]
    steps.append({"id": "s4", "action": "document", "summary": f"Press release to {len(outlets)} media recipients",
                  "data": {"kind": "press_release", "language": "en", "template_id": release["id"], "details": rel_details, "subject": messages.fill(release.get("subject") or "", rel_values),
                           "text": messages.fill(release["body"], rel_values), "outlets": outlets}, "code_fields": ["text", "subject", "details"]})
    return {"title": f"Public messages for the {LEVEL_NAME[w['level']]} {HAZARDS[w['hazard']].lower()} warning",
            "why": f"{issuer} {LEVEL_NAME[w['level']]} warning recorded for {len(areas)} area{'s' if len(areas) != 1 else ''}. English, Sinhala and Tamil SMS and a press release are drafted; a communications reviewer approves each before sending.",
            "prepared_for": {"organisation": org, "roles": ["communications_reviewer", "coordinator"]}, "steps": steps, "checks": checks, "models": models, "extra": english, "locks": locks}


def detect(text, table):
    folded = text.casefold()
    return next((key for key, words in table.items() if any(w in folded for w in words)), None)


def report_plan(doc, t, service, writer):
    """The fixed readers (and, with Gemini, a structured reading) turn a new report into records to check."""
    s = t["source"]
    c = doc["coordination"]
    text = s["text"]
    gauges = [g for g in service.reference.dataset("gauges")["records"] if not g.get("retired")]
    read = bulletins.read_report(text, gauges)
    steps, checks = [], []
    hazard, issuer = detect(text, HAZARD_KEYS), detect(text, ISSUER_WORDS)
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+|\n+", text) if x.strip()]
    for group in read["warnings"]["levels"]:
        if not (hazard and issuer):
            checks.append({"step": None, "field": None, "level": "note", "message": f"{LEVEL_NAME[group['level']]} areas are named, but the {'issuer' if hazard else 'hazard'} is not; record that warning by hand if it is official."})
            continue
        quote = next((x for x in sentences if group["level"] in x.casefold()), sentences[0])[:600]
        steps.append({"id": f"s{len(steps) + 1}", "action": "warning", "summary": f"{ISSUERS[issuer]} {LEVEL_NAME[group['level']]} {HAZARDS[hazard].lower()} warning · {len(group['areas'])} area{'s' if len(group['areas']) != 1 else ''}",
                      "data": {"hazard": hazard, "level": group["level"], "areas": group["areas"], "issuer": issuer, "issued_at": s["observed_at"], "advice": quote, "source_id": s["id"]}, "code_fields": ["advice"]})
    for row in read["sitrep"]["rows"]:
        figures = {k: v for k, v in row["figures"].items() if k in IMPACT_FIELDS}
        steps.append({"id": f"s{len(steps) + 1}", "action": "area_figures", "summary": f"Figures for {row['district']} District: {figures.get('people_affected', 0):,} people affected",
                      "data": {"area": row["area"], "as_of": read["sitrep"]["as_of"] or s["observed_at"], "source_id": s["id"], **figures}})
    for row in read["gauges"]["rows"]:
        if row.get("warning") and row.get("area"):
            steps.append({"id": f"s{len(steps) + 1}", "action": "warning", "summary": f"Irrigation Department {LEVEL_NAME[row['warning']]} flood warning · {messages.area_name(row['area'])} ({row['name']} gauge)",
                          "data": {"hazard": "flood", "level": row["warning"], "areas": [row["area"]], "issuer": "irrigation", "issued_at": s["observed_at"],
                                   "advice": f"{row['name']} ({row['river']}): {row['level']} {row['unit']}", "source_id": s["id"]}, "code_fields": ["advice"]})
    models = []
    if writer.read:
        extra, model, notes = writer.read(text, [st["data"] for st in steps])
        models.append(model)
        checks += notes
        for st in extra:
            steps.append({**st, "id": f"s{len(steps) + 1}"})
    if not steps:
        return None
    return {"title": f"Records from “{s['title']}”", "why": f"New report from {org_name(s['organisation'])}. These records were read from it; check each against the original before applying.",
            "prepared_for": {"organisation": lead_org(c), "roles": ["coordinator"]}, "steps": steps[:plans.MAX_STEPS], "checks": checks, "models": models, "extra": text}


def escalation_plan(doc, t, service, writer):
    ask, up = t["ask"], t["up"]
    c = doc["coordination"]
    waited = round((business_now(c) - timestamp(ask.get("redirected_at") or ask["created_at"])).total_seconds() / 3600, 1)
    amount = f" ({ask['quantity']} {ask['unit']})" if ask.get("quantity") else ""
    facts = (f"Escalated to {org_name(up['to'])}: “{ask['title']}”{amount}, {ask.get('priority', 'normal')} priority, was asked of {org_name(ask['to'])} "
             f"{waited:g} h ago. Reason: {up['reason']}.")
    if writer.note:
        note, model = writer.note("escalation", facts)
        models, fields = [model], []
    else:
        note, models, fields = facts + " Please step in, or redirect it to a body that can help.", [], ["note"]
    step = {"id": "s1", "action": "ask_update", "summary": f"Escalate “{ask['title']}” to {org_name(up['to'])} with a note",
            "data": {"ask_id": ask["id"], "step": "escalate", "note": note[:1000]}, "code_fields": fields}
    return {"title": f"Escalate “{ask['title']}”", "why": f"No answer from {org_name(ask['to'])} in time ({up['reason']}). The note tells {org_name(up['to'])} what is needed.",
            "prepared_for": {"organisation": ask["from"], "roles": ["agency_supervisor", "coordinator"]}, "steps": [step], "checks": [], "models": models, "extra": facts}


def disagreement_plan(doc, t, service, writer):
    item = t["item"]
    c = doc["coordination"]
    area = messages.area_name(item["area"])
    label = item["field"].replace("_", " ") if item["type"] != "status" else SECTORS.get(item["field"], item["field"]).lower()
    if item["type"] == "sum":
        org, value = next((o, v) for o, v in item["values"].items() if o != "divisions")
        facts = (f"{org_name(org)} records {value:,} {label} for {area}, but its divisions report {item['values']['divisions']:,} together "
                 f"({'; '.join(f'{DIVISIONS[d]['name']} {v:,}' for d, v in sorted(item['parts'].items()))}). A district total cannot be lower than its divisions.")
        title = f"Settle {label} in {area}: {value:,} vs {item['values']['divisions']:,}"
    else:
        # In the order the bodies reported, earliest first.
        shown = "; ".join(f"{org_name(o)}: {v:,}" if isinstance(v, int) else f"{org_name(o)}: {v}" for o, v in item["values"].items())
        times = "; ".join(f"{org_name(o)} as at {at[:16].replace('T', ' ')}" for o, at in sorted(item["as_of"].items()) if at)
        facts = f"Two bodies report different {label} for {area}. {shown}." + (f" Times: {times}." if times else "")
        title = f"Settle {label} in {area}: " + " vs ".join(f"{v:,}" if isinstance(v, int) else str(v) for v in item["values"].values())
    settler = SETTLER.get(item["field"]) if item["type"] == "status" else None
    lead = lead_org(c)
    if not (settler in c["participants"] and settler != lead):
        settler = next((o for o in ("district", "division") if o in c["participants"] and o != lead), None)
    if not settler:
        return None
    if writer.note:
        note, model = writer.note("disagreement", facts)
        models, fields = [model], []
    else:
        note, models, fields = facts + " Please confirm the correct figure with its source, and record it.", [], ["note"]
    step = {"id": "s1", "action": "ask", "summary": f"Ask {org_name(settler)} to settle it", "code_fields": ["title", *fields],
            "data": {"to": settler, "kind": "other", "title": title[:200], "priority": "high", "note": note[:1000]}}
    return {"title": title, "why": facts, "prepared_for": {"organisation": lead, "roles": ["coordinator"]}, "steps": [step], "checks": [], "models": models, "extra": facts}


def public_plan(doc, t, service, writer):
    """A public request becomes one request to the body that owns that kind of need, with the details staff need."""
    from . import public_requests as public
    item = t["item"]
    c = doc["coordination"]
    reading = item.get("reading") or {}
    need = reading.get("need") or "other"
    lead = lead_org(c)
    owner = public.OWNER.get(need, "division")
    if owner == lead:
        owner = next((o for o in ("district", "division") if o in c["participants"] and o != lead), None)
    if not owner:
        return None
    summary = (reading.get("summary") or item["text"])[:300]
    title = f"{public.NEED_LABEL[need]}: {summary}"[:200]
    note = " ".join(x for x in (f"Public request {item['code']} ({item['area_label']}).", summary, reading.get("photo_note") or "",
                                f"Asked: {reading['questions'][0]}" if reading.get("questions") else "") if x)[:1000]
    data = {"to": owner, "kind": public.ASK_KIND[need], "title": title, "priority": "urgent" if reading.get("urgency") == "high" else "high",
            "note": note, "public_request_id": item["id"]}
    if reading.get("people"):
        data.update(quantity=int(reading["people"]), unit="people")
    if item["area"] in DIVISIONS:
        data["delivery_area"] = item["area"]
    steps = [{"id": "s1", "action": "ask", "summary": f"Ask {org_name(owner)} to handle it ({public.NEED_LABEL[need].lower()})", "data": data}]
    checks, stock_ids = [], []
    # Supplies come from the shared stock record as it is now: same district first, then nearest; split if needed.
    allocation = service.ledger.suggest(need, reading.get("people"), item["area"]) if getattr(service, "ledger", None) else None
    if allocation:
        unit = allocation["item"].replace("-", " ")
        for pick in allocation["picks"]:
            sid = f"s{len(steps) + 1}"
            where = f"{pick['location']}" + (f", {pick['km']} km" if pick["km"] is not None else "")
            steps.append({"id": sid, "action": "reserve_stock", "summary": f"Reserve {pick['quantity']} {unit} from {where} ({pick['free']} available now)",
                          "data": {"stock_id": pick["stock_id"], "quantity": pick["quantity"], "ask_id": plans.step_ref("s1", "asks"),
                                   "note": f"For {item['code']}: {allocation['rule']}."}, "code_fields": ["note"]})
            stock_ids.append(pick["stock_id"])
        if allocation["short"]:
            checks.append({"step": None, "field": None, "level": "note", "message": f"Short by {allocation['short']} {unit}: no more on record nearby. Ask NDRSC or a partner for the rest."})
        if not allocation["picks"]:
            checks.append({"step": None, "field": None, "level": "note", "message": f"No {unit} on record with any left. Ask NDRSC or a partner."})
    why = f"Public request {item['code']} from {item['area_label']}: {summary}" + (f" Photos: {reading['photo_note']}" if reading.get("photo_note") else "")
    if allocation:
        why += f" Needs {allocation['wanted']} {allocation['item'].replace('-', ' ')} ({allocation['rule']})."
    return {"title": f"{public.NEED_LABEL[need]} for {item['area_label']}", "why": why, "prepared_for": {"organisation": lead, "roles": ["coordinator"]},
            "steps": steps, "checks": checks, "models": [item["model"]] if item.get("model", {}).get("model", "offline-rules") != "offline-rules" else [],
            "extra": item["text"], "stock": stock_ids}


BUILDERS = {"warning_messages": warning_plan, "report_reading": report_plan, "escalation_note": escalation_plan, "disagreement": disagreement_plan, "public_request": public_plan}


class Writer:
    """The AI parts of suggestions. Offline every one is None and code builds the suggestion."""
    def __init__(self, translate=None, read=None, note=None, model="offline-rules"):
        self.translate, self.read, self.note, self.model = translate, read, note, model


class Suggestions:
    def __init__(self, service, store, *, background=False, writer=None, enabled=True):
        self.service, self.store, self.background, self.enabled = service, store, background, enabled
        self.writer = writer or (lambda: Writer())
        self.pool = ThreadPoolExecutor(2, thread_name_prefix="lastmile-suggest") if background else None
        self.futures, self.read_at, self.lock, self.failures = [], {}, threading.Lock(), 0

    def after_change(self, incident_id):
        self._submit(incident_id)

    def on_read(self, incident_id):
        """Time alone can make a request overdue, so an opened incident is re-scanned now and then."""
        now = time.monotonic()
        with self.lock:
            if now - self.read_at.get(incident_id, 0) < 60:
                return
            self.read_at[incident_id] = now
        self._submit(incident_id)

    def drain(self, timeout=60):
        for future in list(self.futures):
            future.result(timeout=timeout)

    def _submit(self, incident_id):
        if not self.enabled:
            return
        if self.pool:
            with self.lock:
                self.futures = [f for f in self.futures if not f.done()] + [self.pool.submit(self.scan, incident_id)]
            return
        try:
            self.scan(incident_id)
        except Exception:  # a suggestion must never fail the change or the read that triggered it
            self.failures += 1

    def _meta(self, incident_id, fn):
        for attempt in range(4):
            doc = self.store.get(incident_id)
            try:
                return self.store.mutate(incident_id, doc["version"], AI_ID, fn, metadata=True)
            except WorkspaceError as exc:
                if exc.status != 409 or attempt == 3:
                    raise

    def scan(self, incident_id):
        try:
            doc = self.store.get(incident_id)
        except WorkspaceError:
            return
        c = doc.get("coordination")
        if not c or doc.get("internal") or doc["status"] == "closed":
            return
        writer = None
        for t in triggers(doc):
            if t["key"] in c.get("suggested", {}):
                continue
            claimed = {}
            def claim(d):
                taken = d["coordination"].setdefault("suggested", {})
                if t["key"] not in taken:
                    taken[t["key"]] = {"kind": t["kind"], "status": "running", "at": now_iso()}
                    claimed["ok"] = True
            self._meta(incident_id, claim)
            if not claimed:
                continue
            writer = writer or self.writer()
            try:
                outline = BUILDERS[t["kind"]](self.store.get(incident_id), t, self.service, writer)
                error = None
            except Exception as exc:  # a failed suggestion is recorded, never fatal to the incident
                outline, error = None, f"{type(exc).__name__}: {exc}"[:300]
            self._meta(incident_id, lambda d: self._finish(d, t, outline, error, writer))

    def replace(self, incident_id, plan_id):
        """Retire a suggestion built on an old picture and build it again from the records as they are now."""
        def retire(d):
            for p in d["coordination"].get("plans", []):
                if p["id"] == plan_id and p["status"] == "pending":
                    p.update(status="replaced", replaced_at=now_iso())
                    d["coordination"].get("suggested", {}).pop(p.get("trigger", {}).get("key"), None)
        self._meta(incident_id, retire)
        self._submit(incident_id)

    def replan_for_stock(self, stock_ids, origin_incident=None):
        """Stock was taken: every pending suggestion counting on those lines, in any incident, is rebuilt with what is left."""
        for doc in self.store.team_documents():
            for p in doc["coordination"].get("plans", []):
                if p["status"] == "pending" and p["origin"] == "event" and set(p.get("stock", [])) & set(stock_ids):
                    self.replace(doc["id"], p["id"])

    def _finish(self, d, t, outline, error, writer):
        mark = d["coordination"]["suggested"][t["key"]]
        if outline is None:
            mark.update(status="failed" if error else "nothing", error=error)
            return
        who = outline["prepared_for"]
        actor = ai_actor(d, who["organisation"], who["roles"])
        steps = [{**s, "summary": s.get("summary") or plans.describe(s["action"], s["data"])} for s in outline["steps"]]
        try:
            plans.dry_run(self.service, d, actor, steps)
        except WorkspaceError as exc:
            mark.update(status="not_applicable", error=str(exc)[:300])
            return
        plan = plans.new_plan(d, actor, steps, title=outline["title"], origin="event", prepared_for=who, checks=outline["checks"])
        plan.update(locks=outline.get("locks", []), extra=outline.get("extra", ""), stock=outline.get("stock", []))
        plan["checks"] = [*outline["checks"], *plans.number_checks(steps, "", d, plan["extra"]), *plans.lock_checks(plan)]
        plan.update(trigger={"kind": t["kind"], "key": t["key"], "why": outline["why"]},
                    models=outline["models"] or [{"role": "suggestion", "model": writer.model}], prepared_by_ai=bool(outline["models"]))
        d["coordination"].setdefault("plans", []).append(plan)
        mark.update(status="ready", plan_id=plan["id"])
