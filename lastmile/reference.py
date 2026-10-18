"""Standing reference data that exists before and between incidents: places, stock and standards.

Seed files live in data/reference/ (built by scripts/build_reference_seed.py). On first start they are copied
into <data dir>/reference/, and into the private bucket under state/reference/ when one is configured, so the
deployed application keeps every later edit. Edits never overwrite history: each one bumps the dataset
version and appends who, when, why, and the record before and after to <dataset>.changes.jsonl.

Incidents keep their own evidence-backed facts (current capacity, occupancy). The registry holds what is true
of a place or a store regardless of any incident, and an incident resource links to it by resource_key.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import threading
from datetime import date
from pathlib import Path

from . import bulletins
from .team_accounts import ACTIVE, ORGANISATIONS, require
from .team_areas import DISTRICT_IDS, DIVISIONS
from .workspace import now_iso, uid

SEED_DIR = Path(__file__).resolve().parent.parent / "data" / "reference"

# Minimum standards used for planning arithmetic. Sphere Handbook (2018) indicators; read with the full handbook.
STANDARDS = {
    "space_m2_per_person": {"value": 3.5, "label": "Covered living space", "unit": "m² per person", "source": "Sphere shelter standard 3"},
    "people_per_toilet": {"value": 20, "label": "People per toilet", "unit": "people", "source": "Sphere WASH excreta management standard"},
    "water_l_per_person_day": {"value": 15, "label": "Water", "unit": "L per person per day", "source": "Sphere WASH water supply standard"},
    "kcal_per_person_day": {"value": 2100, "label": "Food energy", "unit": "kcal per person per day", "source": "Sphere food security standard"},
}

ORG_NAMES = {o["id"]: o["name"] for o in ORGANISATIONS}
OTHER_SERVICES = {"police": "Sri Lanka Police", "ambulance": "Suwa Seriya ambulance", "fire": "Fire and rescue services"}
# Holders of stock outside the six portal organisations; coordinators keep their lines.
OTHER_HOLDERS = {"navy": "Sri Lanka Navy", "army": "Sri Lanka Army", "airforce": "Sri Lanka Air Force"}
RISK = {"low": "Low", "medium": "Medium", "high": "High", "unknown": "Not known"}


def F(key, label, kind, **extra):
    return {"key": key, "label": label, "type": kind, **extra}


DATASETS = {
    "centres": {
        "label": "Safety centres", "singular": "safety centre", "owned": True, "map": True,
        "about": "Buildings used as safety centres: what each can hold and what it has. Live occupancy stays in the incident.",
        "fields": [
            F("name", "Name", "text", required=True, list=True),
            F("type", "Building type", "choice", required=True, list=True, options={"school": "School", "religious": "Religious site", "community_hall": "Community hall", "purpose_built": "Purpose-built centre", "other": "Other"}),
            F("area", "DS division", "area", required=True, list=True),
            F("gn", "GN division", "text", max=80),
            F("latitude", "Latitude", "lat"), F("longitude", "Longitude", "lon"),
            F("owner", "Managed by", "org", required=True, list=True),
            F("design_capacity", "Design capacity", "int", unit="people", list=True, max=20000, hint="What the building was planned to hold. Not today's free places."),
            F("floor_area_m2", "Usable floor area", "int", unit="m²", max=100000),
            F("toilets", "Working toilets", "int", max=2000),
            F("water_source", "Water source", "choice", options={"piped": "Piped supply", "well": "Well", "bowser": "Bowser only", "none": "None", "unknown": "Not known"}),
            F("power", "Power", "choice", options={"grid": "Grid", "generator": "Generator", "grid_and_generator": "Grid and generator", "none": "None", "unknown": "Not known"}),
            F("kitchen", "Kitchen", "bool"), F("step_free", "Step-free entrance", "bool"), F("accessible_toilet", "Accessible toilet", "bool"),
            F("women_children_space", "Separate space for women and children", "bool"),
            F("flood_risk", "Flood risk at the site", "choice", options=RISK), F("landslide_risk", "Landslide risk at the site", "choice", options=RISK),
            F("access_note", "Access", "text", max=300, hint="Road access, vehicle limits, seasonal problems."),
            F("contact_point", "Contact point", "text", max=120, hint="A desk or post, never a personal phone number."),
            F("notes", "Notes", "text", max=500),
            F("checked_at", "Last checked", "date", list=True),
            F("source", "Source", "text", max=200),
            F("fictional", "Demo record", "bool"),
        ]},
    "stock": {
        "label": "Stock & equipment", "singular": "stock line", "owned": True, "map": True,
        "about": "What each organisation holds and where, so a request can be checked against stock.",
        "fields": [
            F("item", "Item", "item", required=True, list=True),
            F("quantity", "Quantity", "int", required=True, list=True, max=10000000),
            F("owner", "Held by", "holder", required=True, list=True),
            F("holder_name", "Partner or unit name", "text", max=120, hint="For forces and partners: the unit, NGO or company."),
            F("location_name", "Store or yard", "text", required=True, max=150, list=True),
            F("area", "DS division", "area", required=True),
            F("latitude", "Latitude", "lat"), F("longitude", "Longitude", "lon"),
            F("status", "Status", "choice", required=True, list=True, options={"ready": "Ready", "in_use": "In use", "repair": "Under repair", "pledged": "Pledged, not yet received"}),
            F("counted_at", "Last counted", "date", list=True),
            F("notes", "Notes", "text", max=500),
            F("fictional", "Demo record", "bool"),
        ]},
    "health": {
        "label": "Health facilities", "singular": "health facility", "owned": False, "map": True,
        "about": "Hospitals mapped in OpenStreetMap. Types are read from names; check before relying on them.",
        "fields": [
            F("name", "Name", "text", required=True, list=True),
            F("type", "Type", "choice", required=True, list=True, options={"national": "National hospital", "teaching": "Teaching hospital", "general": "General hospital", "base": "Base hospital", "divisional": "Divisional hospital", "primary_care": "Primary medical care unit", "moh_office": "MOH office", "ayurveda": "Ayurveda hospital", "hospital": "Hospital (type not recorded)"}),
            F("area", "DS division", "area", list=True),
            F("latitude", "Latitude", "lat"), F("longitude", "Longitude", "lon"),
            F("beds", "Beds", "int", max=10000, list=True),
            F("emergency_unit", "Emergency unit", "bool"),
            F("verified", "Checked with the hospital or RDHS", "bool", list=True, hint="Mark only after the type, beds or emergency unit were confirmed."),
            F("osm_id", "OpenStreetMap ID", "text", max=40),
            F("source", "Source", "text", max=200),
            F("checked_at", "Last checked", "date"),
        ]},
    "gauges": {
        "label": "River gauges", "singular": "river gauge", "owned": False, "map": True,
        "about": "Irrigation Department gauging stations with the levels that trigger each warning.",
        "fields": [
            F("name", "Station", "text", required=True, list=True),
            F("river", "River", "text", required=True, max=80, list=True),
            F("basin", "River basin", "text", max=80),
            F("unit", "Unit", "choice", required=True, options={"m": "metres", "ft": "feet"}),
            F("alert_level", "Alert level", "number", list=True), F("minor_flood", "Minor flood level", "number", list=True), F("major_flood", "Major flood level", "number", list=True),
            F("latitude", "Latitude", "lat"), F("longitude", "Longitude", "lon"),
            F("location_note", "Location note", "text", max=200),
            F("area", "DS division", "area"),
            F("district", "District", "text", max=80),
            F("thresholds_as_of", "Levels as of", "date", list=True),
            F("source", "Source", "text", max=200),
        ]},
    "population": {
        "label": "Population", "singular": "district population", "owned": False, "map": False,
        "about": "People per district, for affected shares and planning totals.",
        "fields": [
            F("district", "Area name", "text", required=True, list=True),
            F("area", "Area", "area", hint="A district, or a DS division for rows from the census divisional tables."),
            F("name_si", "Name (Sinhala)", "text", max=80), F("name_ta", "Name (Tamil)", "text", max=80),
            F("pcode", "P-code", "text", max=20),
            F("population", "Population", "int", required=True, max=50000000, list=True),
            F("female", "Female", "int", max=50000000), F("male", "Male", "int", max=50000000),
            F("under_5", "Children under 5", "int", max=50000000, list=True), F("over_65", "People 65 and over", "int", max=50000000, list=True),
            F("year", "Year", "int", required=True, max=2100, list=True),
            F("basis", "Basis", "choice", options={"census": "Census", "projection": "Projection", "estimate": "Estimate"}),
            F("source", "Source", "text", max=200),
        ]},
    "contacts": {
        "label": "Hotlines & desks", "singular": "contact point", "owned": True, "map": False,
        "about": "Public hotlines and organisation duty desks by area. Never personal phone numbers.",
        "fields": [
            F("label", "Name", "text", required=True, list=True, max=120),
            F("owner", "Organisation", "contact_org", required=True, list=True),
            F("area", "Area served", "area", national=True, list=True),
            F("channel", "Channel", "choice", required=True, list=True, options={"hotline": "Hotline", "desk": "Duty desk", "radio": "Radio", "email": "Email"}),
            F("number", "Number", "text", max=40, list=True, pattern=r"^[0-9 +()-]{3,20}$", hint="Public or office numbers only."),
            F("hours", "Hours", "text", max=80),
            F("public", "Can be shown to the public", "bool"),
            F("notes", "Notes", "text", max=300),
            F("fictional", "Demo record", "bool"),
        ]},
    "gn": {
        "label": "GN divisions", "singular": "GN division", "owned": False, "map": False,
        "about": "Grama Niladhari divisions with Sinhala names and centre points, for locating places and needs below DS level.",
        "fields": [
            F("name", "GN division", "text", required=True, list=True, max=120),
            F("name_si", "Name (Sinhala)", "text", max=120),
            F("area", "DS division", "area", list=True),
            F("district", "District", "text", max=60, list=True),
            F("ds_name", "DS name in the source", "text", max=120),
            F("pcode", "P-code", "text", max=20, list=True),
            F("latitude", "Latitude", "lat"), F("longitude", "Longitude", "lon"),
        ]},
    "history": {
        "label": "Disaster history", "singular": "area history", "owned": False, "map": False,
        "about": "Disaster events recorded since 2000 per DS division (or district, where that is all the record says). Recorded events, not hazard zoning.",
        "fields": [
            F("name", "Area", "text", required=True, list=True, max=120),
            F("area", "Area code", "area", required=True),
            F("level", "Recorded at", "choice", options={"division": "DS division", "district": "District only"}),
            F("flood_events", "Floods", "int", list=True, max=100000), F("landslide_events", "Landslides", "int", list=True, max=100000),
            F("wind_events", "Wind and cyclones", "int", list=True, max=100000), F("drought_events", "Droughts", "int", list=True, max=100000),
            F("coastal_events", "Coastal events", "int", max=100000),
            F("deaths", "Deaths", "int", list=True, max=10000000), F("people_affected", "People affected", "int", list=True, max=100000000),
            F("houses_destroyed", "Houses destroyed", "int", max=10000000), F("houses_damaged", "Houses damaged", "int", max=10000000),
            F("first_year", "First year", "int", max=2100), F("last_year", "Last year", "int", list=True, max=2100),
        ]},
    "lifelines": {
        "label": "Lifeline sites", "singular": "lifeline site", "owned": False, "map": True,
        "about": "Water works, wastewater plants, telecom towers, named bridges, police and fire stations: what an outage or blockage hits.",
        "fields": [
            F("name", "Name", "text", required=True, list=True),
            F("type", "Type", "choice", required=True, list=True, options={"water_works": "Water treatment works", "wastewater": "Wastewater plant", "telecom": "Telecom tower", "bridge": "Bridge", "police": "Police station", "fire_station": "Fire station", "substation": "Power substation", "other": "Other"}),
            F("area", "DS division", "area", list=True),
            F("latitude", "Latitude", "lat"), F("longitude", "Longitude", "lon"),
            F("operator", "Operator", "text", max=120, list=True),
            F("status_note", "Status note", "text", max=300),
            F("osm_id", "OpenStreetMap ID", "text", max=40),
            F("source", "Source", "text", max=200),
        ]},
    "vulnerable": {
        "label": "Homes (elders, children)", "singular": "home", "owned": False, "map": True,
        "about": "Homes for elders and children, and other care homes, to check and move first. Volunteer-mapped; types are read from names.",
        "fields": [
            F("name", "Name", "text", required=True, list=True),
            F("type", "Type", "choice", required=True, list=True, options={"elders": "Home for elders", "children": "Children's home or childcare", "disability": "Disability care", "other": "Other care home"}),
            F("area", "DS division", "area", list=True),
            F("latitude", "Latitude", "lat"), F("longitude", "Longitude", "lon"),
            F("residents", "Residents", "int", max=10000, list=True),
            F("evacuation_note", "Evacuation note", "text", max=300, hint="Mobility needs, transport, a partner centre. Never residents' names."),
            F("osm_id", "OpenStreetMap ID", "text", max=40),
            F("source", "Source", "text", max=200),
        ]},
    "items": {
        "label": "Items & standards", "singular": "item", "owned": False, "map": False,
        "about": "The shared list of relief items and units, with the minimum standard each one serves.",
        "fields": [
            F("name", "Item", "text", required=True, list=True),
            F("category", "Category", "choice", required=True, list=True, options={"water": "Water", "food": "Food", "shelter": "Shelter", "hygiene": "Hygiene", "health": "Health", "equipment": "Equipment", "rescue": "Rescue", "roads": "Roads", "transport": "Transport"}),
            F("unit", "Unit", "text", required=True, max=30, list=True),
            F("per_person", "Amount per person", "number", list=True),
            F("per_person_unit", "Per-person unit", "text", max=40),
            F("standard", "Standard", "text", max=300, list=True),
        ]},
    # Communications page lists: what a message says, and the media desks an email or press release goes to.
    "templates": {
        "label": "Message templates", "singular": "message template", "owned": False, "map": False, "group": "communications", "editors": ["communications_reviewer"],
        "about": "Starting text for SMS, emails, press releases and official letters. {incident}, {district}, {areas}, {details}, {date}, {time}, {hotline} and {issuer} are filled in from the incident; letters also use {reference}, {to}, {subject}, {body}, {signer} and {job_title}.",
        "fields": [
            F("name", "Name", "text", required=True, list=True, max=120),
            F("channel", "Channel", "choice", required=True, list=True, options={"sms": "SMS", "email": "Email", "press_release": "Press / media release", "letter": "Official letter"}),
            F("language", "Language", "choice", required=True, list=True, options={"en": "English", "si": "Sinhala", "ta": "Tamil"}),
            F("subject", "Subject", "text", max=200, hint="Emails and press releases."),
            F("body", "Message", "text", required=True, max=6000, multiline=True, hint="SMS: keep to 160 characters (70 in Sinhala or Tamil) for one message."),
            F("notes", "When to use it", "text", max=300, list=True),
        ]},
    "media": {
        "label": "Media recipients", "singular": "media recipient", "owned": False, "map": False, "group": "communications", "editors": ["communications_reviewer"],
        "about": "News desks, papers, radio and TV that emails and press releases can be sent to.",
        "fields": [
            F("name", "Name", "text", required=True, list=True, max=120),
            F("type", "Type", "choice", required=True, list=True, options={"tv": "Television", "radio": "Radio", "newspaper": "Newspaper", "online": "Online news", "agency": "News agency", "government": "Government information"}),
            F("language", "Language", "choice", required=True, list=True, options={"en": "English", "si": "Sinhala", "ta": "Tamil", "all": "All three"}),
            F("area", "Coverage", "area", national=True, list=True),
            F("email", "Email", "text", max=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", pattern_hint="enter an email address such as newsdesk@example.org", hint="A newsroom address, never a personal one."),
            F("notes", "Notes", "text", max=300),
            F("fictional", "Demo record", "bool"),
        ]},
}
PREFIX = {"centres": "centre", "stock": "stock", "health": "health", "gauges": "gauge", "population": "pop", "contacts": "contact", "items": "item",
          "gn": "gn", "history": "hist", "lifelines": "life", "vulnerable": "home", "templates": "tpl", "media": "media"}
GN_PAGE = 400  # GN divisions are sent one area or search at a time; this many without a filter
MAX_IMPORT = 3000


def spec_view():
    """Field definitions for the client, with choices resolved."""
    out = {}
    for name, d in DATASETS.items():
        fields = []
        for f in d["fields"]:
            f = dict(f)
            if f["type"] == "org":
                f["options"] = {o: ORG_NAMES[o] for o in ACTIVE}
            elif f["type"] == "contact_org":
                f["options"] = {**{o: ORG_NAMES[o] for o in ACTIVE}, **OTHER_SERVICES}
            elif f["type"] == "holder":
                f["options"] = {**{o: ORG_NAMES[o] for o in ACTIVE}, **OTHER_HOLDERS}
            fields.append(f)
        out[name] = {k: v for k, v in d.items() if k != "fields"} | {"fields": fields}
    return out


def derive(name, record, items):
    """Planning figures computed from stored facts; never stored and never a guarantee of admission."""
    if name == "centres":
        s = STANDARDS
        by = {"design": record.get("design_capacity"),
              "space": math.floor(record["floor_area_m2"] / s["space_m2_per_person"]["value"]) if record.get("floor_area_m2") else None,
              "toilets": record["toilets"] * s["people_per_toilet"]["value"] if record.get("toilets") else None}
        known = {k: v for k, v in by.items() if v is not None}
        if not known:
            return {"by": by, "capacity": None, "limited_by": None, "water_l_day": None}
        limit = min(known, key=known.get)
        capacity = known[limit]
        return {"by": by, "capacity": capacity, "limited_by": limit, "complete": len(known) == 3,
                "water_l_day": capacity * s["water_l_per_person_day"]["value"]}
    if name == "stock":
        item = items.get(record.get("item")) or {}
        return {"item_name": item.get("name", record.get("item")), "unit": item.get("unit", "")}
    if name == "gauges":
        return bulletins.gauge_state(record)
    return {}


def _coerce_csv(field, value):
    value = (value or "").strip()
    if value == "":
        return None
    kind = field["type"]
    if kind == "int":
        require(re.fullmatch(r"-?\d+", value.replace(",", "")) is not None, f"{field['label']}: enter a whole number.", 422)
        return int(value.replace(",", ""))
    if kind in {"number", "lat", "lon"}:
        try:
            return float(value)
        except ValueError:
            raise_field(field, "enter a number")
    if kind == "bool":
        lowered = value.lower()
        require(lowered in {"yes", "no", "true", "false", "1", "0", "y", "n"}, f"{field['label']}: use yes or no.", 422)
        return lowered in {"yes", "true", "1", "y"}
    return value


def raise_field(field, message):
    require(False, f"{field['label']}: {message}.", 422)


class ReferenceStore:
    def __init__(self, directory, seed_dir=SEED_DIR, cloud=None):
        self.dir = Path(directory) / "reference"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cloud, self.lock, self.docs = cloud, threading.RLock(), {}
        if cloud:
            cloud.restore("reference/")
        for name in DATASETS:
            path, seed = self.dir / f"{name}.json", Path(seed_dir) / f"{name}.json"
            current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
            fresh = json.loads(seed.read_text(encoding="utf-8")) if seed.exists() else None
            # A list nobody has edited follows a rebuilt seed; an edited list is never replaced.
            if current is None or (fresh and current["version"] == 1 and current.get("updated_by") == "seed" and current["records"] != fresh["records"]):
                doc = fresh or {"dataset": name, "version": 1, "updated_at": now_iso(), "updated_by": "seed", "fictional": False, "provenance": {}, "records": []}
                self._write(path, doc)
                current = doc
            self.docs[name] = current
        self._write_manifest()

    # ------------------------------------------------------------ files
    def _write(self, path, doc):
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
        if self.cloud:
            self.cloud.save(path)

    def _append_change(self, name, change):
        path = self.dir / f"{name}.changes.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(change, ensure_ascii=False, separators=(",", ":")) + "\n")
        if self.cloud:
            self.cloud.save(path)

    def _write_manifest(self):
        manifest = {"generated_at": now_iso(), "datasets": {}}
        for name, doc in self.docs.items():
            body = json.dumps(doc["records"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            manifest["datasets"][name] = {"version": doc["version"], "updated_at": doc["updated_at"], "records": len(doc["records"]),
                                          "active": sum(not r.get("retired") for r in doc["records"]), "fictional": doc.get("fictional", False),
                                          "licence": doc.get("provenance", {}).get("licence"), "sha256": hashlib.sha256(body).hexdigest()}
        self._write(self.dir / "manifest.json", manifest)
        self.manifest = manifest

    def changes(self, name, limit=40):
        path = self.dir / f"{name}.changes.jsonl"
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
        return [json.loads(line) for line in reversed(lines)]

    # ------------------------------------------------------------ reads
    def dataset(self, name):
        require(name in DATASETS, "Unknown reference dataset.", 404)
        return self.docs[name]

    def items(self):
        return {r["id"]: r for r in self.docs["items"]["records"] if not r.get("retired")}

    def can_edit(self, actor, name, record=None):
        roles = set(actor["roles"])
        if "coordinator" in roles or roles & set(DATASETS[name].get("editors", [])):
            return True
        if not DATASETS[name]["owned"] or "agency_supervisor" not in roles:
            return False
        return record is None or record.get("owner") == actor["organisation"]

    def view(self, name, actor, area=None, query=None):
        doc, items = self.dataset(name), self.items()
        rows = doc["records"]
        if area:
            district = DISTRICT_IDS.get(area, {}).get("name")
            rows = [r for r in rows if r.get("area") == area or (district and (r.get("district") == district or DIVISIONS.get(r.get("area") or "", {}).get("district") == district))]
        if query:
            q = query.strip().lower()
            rows = [r for r in rows if q in json.dumps(r, ensure_ascii=False).lower()]
        limited = name == "gn" and not (area or query)
        if limited:
            rows = rows[:GN_PAGE]
        records = [{**r, "_derived": derive(name, r, items), "_can_edit": self.can_edit(actor, name, r)} for r in rows]
        history = [{k: v for k, v in c.items() if k not in {"before", "after"}} for c in self.changes(name)]
        return {"dataset": name, "spec": spec_view()[name], "version": doc["version"], "updated_at": doc["updated_at"], "updated_by": doc.get("updated_by"),
                "fictional": doc.get("fictional", False), "provenance": doc.get("provenance", {}), "records": records, "total": len(doc["records"]),
                "partial": bool(area or query or limited),
                "can_add": self.can_edit(actor, name), "history": history, "standards": STANDARDS}

    def summary(self, actor):
        spec = spec_view()
        return {"standards": STANDARDS, "datasets": [{"id": name, "label": spec[name]["label"], "about": spec[name]["about"], "group": spec[name].get("group", "places"), "can_add": self.can_edit(actor, name),
                                                       **self.manifest["datasets"][name], "provenance": self.docs[name].get("provenance", {})} for name in DATASETS]}

    def export_csv(self, name, template=False):
        doc = self.dataset(name)
        keys = ["id"] + [f["key"] for f in DATASETS[name]["fields"]] + ["retired"]
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=keys, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for r in ([] if template else doc["records"]):
            writer.writerow({k: ("yes" if r.get(k) is True else "no" if r.get(k) is False else "" if r.get(k) is None else r.get(k)) for k in keys})
        return out.getvalue()

    # ------------------------------------------------------------ validation
    def _clean(self, name, data, actor, existing=None):
        fields = {f["key"]: f for f in DATASETS[name]["fields"]}
        unknown = set(data) - set(fields) - {"id"}
        require(not unknown, f"Unknown fields: {', '.join(sorted(unknown))}.", 422)
        merged = {**(existing or {}), **data}
        out = {"id": merged.get("id")}
        items = self.items()
        for key, f in fields.items():
            value = merged.get(key)
            if isinstance(value, str):
                value = value.strip() or None
            kind = f["type"]
            if value is None:
                require(not f.get("required"), f"{f['label']} is required.", 422)
                out[key] = None
                continue
            if kind == "text":
                require(isinstance(value, str) and len(value) <= f.get("max", 150), f"{f['label']}: enter up to {f.get('max', 150)} characters.", 422)
                if f.get("pattern"):
                    require(re.fullmatch(f["pattern"], value) is not None, f"{f['label']}: {f.get('pattern_hint', 'use digits, spaces, +, ( ) and - only')}.", 422)
            elif kind == "int":
                require(type(value) is int and 0 <= value <= f.get("max", 1000000), f"{f['label']}: enter a whole number from 0 to {f.get('max', 1000000):,}.", 422)
            elif kind == "number":
                require(type(value) in {int, float} and math.isfinite(value) and value >= -100, f"{f['label']}: enter a number.", 422)
                value = float(value)
            elif kind == "bool":
                require(type(value) is bool, f"{f['label']}: choose yes or no.", 422)
            elif kind == "choice":
                require(value in f["options"], f"{f['label']}: choose one of the listed options.", 422)
            elif kind == "org":
                require(value in ACTIVE, f"{f['label']}: choose a participating organisation.", 422)
            elif kind == "contact_org":
                require(value in ACTIVE or value in OTHER_SERVICES, f"{f['label']}: choose a listed organisation.", 422)
            elif kind == "holder":
                require(value in ACTIVE or value in OTHER_HOLDERS, f"{f['label']}: choose a listed organisation.", 422)
            elif kind == "item":
                require(value in items, f"{f['label']}: choose an item from Items & standards.", 422)
            elif kind == "area":
                require(value in DIVISIONS or value in DISTRICT_IDS or (f.get("national") and value == "national"), f"{f['label']}: choose a listed area.", 422)
                if name in {"centres", "stock", "lifelines", "vulnerable"}:
                    require(value in DIVISIONS, f"{f['label']}: choose a DS division.", 422)
            elif kind == "date":
                try:
                    require(isinstance(value, str) and date.fromisoformat(value[:10]) <= date.today(), f"{f['label']}: a date cannot be in the future.", 422)
                except ValueError:
                    raise_field(f, "use the date format YYYY-MM-DD")
                value = value[:10]
            elif kind == "lat":
                require(type(value) in {int, float} and 5.7 <= value <= 10.1, "Latitude must be inside Sri Lanka (5.7 to 10.1).", 422)
                value = round(float(value), 6)
            elif kind == "lon":
                require(type(value) in {int, float} and 79.4 <= value <= 82.1, "Longitude must be inside Sri Lanka (79.4 to 82.1).", 422)
                value = round(float(value), 6)
            out[key] = value
        if "latitude" in fields:
            require((out["latitude"] is None) == (out["longitude"] is None), "Give both latitude and longitude, or neither.", 422)
        if DATASETS[name]["owned"] and not has_role(actor, "coordinator"):
            require(out.get("owner") == actor["organisation"], "You can add and change only your own organisation's records.", 403)
        if name == "gauges" and None not in (out["alert_level"], out["minor_flood"], out["major_flood"]):
            require(out["alert_level"] <= out["minor_flood"] <= out["major_flood"], "Levels must rise from alert to minor flood to major flood.", 422)
        if name == "population" and None not in (out["female"], out["male"]):
            require(out["female"] + out["male"] <= out["population"] + 5, "Female and male counts cannot exceed the total.", 422)
        for key in ("retired", "retired_at", "retired_by", "retired_reason", "reading"):
            if existing and key in existing:
                out[key] = existing[key]
        return out

    def _check(self, name, actor, version):
        doc = self.dataset(name)
        require(self.can_edit(actor, name), "Your role cannot change this list. Coordinators, and supervisors for their own organisation's records, can.", 403)
        require(version == doc["version"], "This list changed since you opened it. Refresh and try again.")
        return doc

    def _commit(self, name, doc, actor, change):
        doc["version"] += 1
        doc["updated_at"] = now_iso()
        doc["updated_by"] = actor.get("name") or actor["id"]
        self._write(self.dir / f"{name}.json", doc)
        self._append_change(name, {"version": doc["version"], "at": doc["updated_at"], "by": actor["id"], "by_name": actor.get("name"),
                                   "organisation": actor["organisation"], **change})
        self._write_manifest()

    # ------------------------------------------------------------ writes
    def save(self, name, actor, version, data, reason=""):
        with self.lock:
            doc = self._check(name, actor, version)
            require(isinstance(data, dict), "Send the record's fields.", 422)
            reason = (reason or "").strip()[:300]
            record_id = data.get("id")
            existing = next((r for r in doc["records"] if r["id"] == record_id), None) if record_id else None
            require(not record_id or existing is not None, "Record not found. Refresh the list.", 404)
            if existing:
                require(self.can_edit(actor, name, existing), "Only the owning organisation or a coordinator can change this record.", 403)
                require(not existing.get("retired"), "Restore this record before changing it.")
            record = self._clean(name, data, actor, existing)
            if existing:
                changed = sorted(k for k in record if record.get(k) != existing.get(k))
                if not changed:
                    return self.view(name, actor) | {"saved": existing["id"], "unchanged": True}
                doc["records"][doc["records"].index(existing)] = record
            else:
                record["id"] = f"{PREFIX[name]}-{uid('r')[2:10]}"
                doc["records"].append(record)
                changed = sorted(k for k, v in record.items() if v is not None and k != "id")
            self._commit(name, doc, actor, {"action": "update" if existing else "add", "record_id": record["id"], "record_name": label_of(name, record),
                                            "fields": changed, "reason": reason, "before": existing, "after": record})
            return self.view(name, actor) | {"saved": record["id"]}

    def retire(self, name, actor, version, record_id, reason, restore=False):
        with self.lock:
            doc = self._check(name, actor, version)
            record = next((r for r in doc["records"] if r["id"] == record_id), None)
            require(record is not None, "Record not found. Refresh the list.", 404)
            require(self.can_edit(actor, name, record), "Only the owning organisation or a coordinator can change this record.", 403)
            reason = (reason or "").strip()
            require(restore or 3 <= len(reason) <= 300, "Say why this record is retired (3–300 characters).", 422)
            require(bool(record.get("retired")) == restore, "This record is not retired." if restore else "This record is already retired.")
            before = dict(record)
            if restore:
                for key in ("retired", "retired_at", "retired_by", "retired_reason"):
                    record.pop(key, None)
            else:
                record.update({"retired": True, "retired_at": now_iso(), "retired_by": actor.get("name") or actor["id"], "retired_reason": reason})
            self._commit(name, doc, actor, {"action": "restore" if restore else "retire", "record_id": record_id, "record_name": label_of(name, record),
                                            "fields": ["retired"], "reason": reason, "before": before, "after": record})
            return self.view(name, actor)

    def import_gauge_bulletin(self, actor, version, text, source):
        """Latest levels (and any revised thresholds) from an Irrigation Department bulletin."""
        with self.lock:
            doc = self._check("gauges", actor, version)
            require(has_role(actor, "coordinator"), "Coordinators import gauge bulletins.", 403)
            parsed = bulletins.parse_gauges(text, [g for g in doc["records"] if not g.get("retired")])
            require(parsed["rows"], "No gauge rows were found. Use the Irrigation Department 'Water Level & Rainfall' bulletin.", 422)
            require(parsed["issued_at"], "The bulletin's DATE and TIME line was not found.", 422)
            by_id = {g["id"]: g for g in doc["records"]}
            revised = []
            for row in parsed["rows"]:
                g = by_id[row["gauge_id"]]
                old = g.get("reading") or {}
                require(not old.get("at") or old["at"] <= parsed["issued_at"], f"{g['name']} already has a newer reading ({old.get('at')}).", 409)
                changed = [k for k in ("unit", "alert_level", "minor_flood", "major_flood") if g.get(k) != row[k]]
                if changed:
                    revised.append(g["name"])
                    g.update({k: row[k] for k in changed})
                    g["thresholds_as_of"] = parsed["issued_at"][:10]
                g["reading"] = {"level": row["level"], "previous": row["previous"], "at": parsed["issued_at"], "printed_status": row["printed_status"],
                                "rain_mm": row["rain_mm"], "source": source}
            self._commit("gauges", doc, actor, {"action": "bulletin", "record_id": None, "record_name": f"Bulletin of {parsed['issued_at'][:16].replace('T', ' ')}",
                                                "fields": ["reading"] + (["alert_level", "minor_flood", "major_flood"] if revised else []),
                                                "reason": f"{len(parsed['rows'])} readings" + (f"; levels revised for {', '.join(revised)}" if revised else ""),
                                                "ids": [r["gauge_id"] for r in parsed["rows"]], "source": source})
            return self.view("gauges", actor) | {"bulletin": {"issued_at": parsed["issued_at"], "readings": len(parsed["rows"]), "revised": revised, "not_found": parsed["not_found"]}}

    def import_csv(self, name, actor, version, text, reason=""):
        with self.lock:
            doc = self._check(name, actor, version)
            require(isinstance(text, str) and text.strip(), "Choose a CSV file with a header row.", 422)
            rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
            require(0 < len(rows) <= MAX_IMPORT, f"A CSV import holds 1 to {MAX_IMPORT:,} rows.", 422)
            fields = {f["key"]: f for f in DATASETS[name]["fields"]}
            header = set(rows[0]) - {"id", "retired", None}
            unknown = header - set(fields)
            require(not unknown, f"Unknown columns: {', '.join(sorted(unknown))}. Export the list to see the expected columns.", 422)
            by_id = {r["id"]: r for r in doc["records"]}
            added, updated, unchanged, errors, seen = [], [], 0, [], set()
            staged = list(doc["records"])
            for number, row in enumerate(rows, start=2):
                try:
                    data = {k: _coerce_csv(fields[k], v) for k, v in row.items() if k in fields}
                    record_id = (row.get("id") or "").strip()
                    require(record_id not in seen, "This id appears twice in the file.", 422)
                    existing = by_id.get(record_id) if record_id else None
                    if existing:
                        seen.add(record_id)
                        require(self.can_edit(actor, name, existing), "You cannot change this record; it belongs to another organisation.", 403)
                        require(not existing.get("retired"), "This record is retired; restore it first.")
                        record = self._clean(name, data, actor, existing)
                        if any(record.get(k) != existing.get(k) for k in record):
                            staged[staged.index(existing)] = record
                            updated.append(record)
                        else:
                            unchanged += 1
                    else:
                        require(not record_id or re.fullmatch(r"[a-z0-9][a-z0-9-]{2,79}", record_id), "Use a lowercase id with letters, digits and dashes, or leave it blank.", 422)
                        record = self._clean(name, data, actor)
                        record["id"] = record_id or f"{PREFIX[name]}-{uid('r')[2:10]}"
                        seen.add(record["id"])
                        staged.append(record)
                        added.append(record)
                except Exception as error:  # noqa: BLE001 - report every bad row at once
                    errors.append(f"Row {number}: {error}")
                    if len(errors) >= 10:
                        break
            require(not errors, "Nothing was imported. " + " ".join(errors), 422)
            if not added and not updated:
                return self.view(name, actor) | {"imported": {"added": 0, "updated": 0, "unchanged": unchanged}}
            doc["records"] = staged
            self._commit(name, doc, actor, {"action": "import", "record_id": None, "record_name": f"{len(added)} added, {len(updated)} updated",
                                            "fields": sorted({k for r in added + updated for k in r}), "reason": (reason or "").strip()[:300],
                                            "ids": [r["id"] for r in added + updated][:200]})
            return self.view(name, actor) | {"imported": {"added": len(added), "updated": len(updated), "unchanged": unchanged}}


def has_role(actor, role):
    return role in actor["roles"]


def label_of(name, record):
    return record.get("name") or record.get("label") or record.get("district") or record.get("location_name") or record.get("id")
