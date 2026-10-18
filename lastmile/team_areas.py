"""Area status: what is happening in each place, for the map and the public status page.

Three kinds of record, all attributed and kept as history:

* Service status: for one area and one service (power, water, roads, health,
  phone/internet, safety centres, food/relief): working, being restored, not working
  or not known. Modelled on lifeline status boards (green / amber / red / grey).
* Official warnings: copied from an issuing authority's bulletin (NBRO, Department of
  Meteorology, Irrigation Department, DMC) with its level, areas, issue and expiry
  times and a linked source report. The workspace records warnings; it never issues them.
* Affected figures: DMC situation-report counts per DS division (families and people
  affected, deaths, injured, missing, houses destroyed or damaged, safety centres and
  people in them), with their cutoff time. Unknown stays blank, never zero.

Areas are DS divisions (`ds-<district>-<name>`) or whole districts (`district-<name>`).
A district-wide status or warning applies to each of its divisions unless a later,
division-specific record replaces it. Nothing reaches the public page until a lead
(coordinator, agency supervisor or public information officer) publishes it.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from .team_accounts import require
from .team_clock import business_now
from .workspace import now_iso, timestamp, uid

INDEX = json.loads((Path(__file__).resolve().parent.parent / "web" / "data" / "sri-lanka-areas.json").read_text(encoding="utf-8"))
DIVISIONS = {d["id"]: d for d in INDEX["divisions"]}
DISTRICT_IDS = {d["id"]: d for d in INDEX["districts"]}
BY_DISTRICT = {}
for _d in INDEX["divisions"]:
    BY_DISTRICT.setdefault(_d["district"], []).append(_d["id"])

SECTORS = {"power": "Electricity", "water": "Water supply", "roads": "Roads & access", "health": "Health services",
           "communications": "Phone & internet", "shelter": "Safety centres", "food": "Food & relief"}
LEVELS = {"working": "Working", "restoring": "Being restored", "disrupted": "Not working", "unknown": "Not known"}
SEVERITY = {"working": 0, "unknown": 1, "restoring": 2, "disrupted": 3}
HAZARDS = {"landslide": "Landslide", "flood": "Flood", "rain": "Heavy rain", "wind": "Strong wind", "cyclone": "Cyclone", "tsunami": "Tsunami", "other": "Other hazard"}
WARNING_LEVELS = {"yellow": "Yellow · remain vigilant", "amber": "Amber · be alert, get ready to leave", "red": "Red · evacuate or act now"}
WARNING_RANK = {"yellow": 1, "amber": 2, "red": 3}
ISSUERS = {"nbro": "NBRO", "met": "Department of Meteorology", "irrigation": "Irrigation Department", "dmc": "Disaster Management Centre"}
IMPACT_FIELDS = {"families_affected": "Families affected", "people_affected": "People affected", "deaths": "Deaths", "injured": "Injured",
                 "missing": "Missing", "houses_destroyed": "Houses destroyed", "houses_damaged": "Houses partly damaged",
                 "safety_centres": "Safety centres open", "people_in_centres": "People in safety centres"}
# Administrative bodies that compile situation-report figures.
FIGURE_ORGS = {"dmc", "district", "division", "ndrsc"}
ACTING = ("contributor", "agency_supervisor", "coordinator", "finance_approver", "communications_reviewer")
PUBLISHERS = ("coordinator", "agency_supervisor", "communications_reviewer")
STALE_HOURS = 24

AREA_FIELDS = {
    "area_status": {"area", "sector", "level", "note", "expected_by", "latitude", "longitude", "source_id", "public"},
    "area_figures": {"area", "as_of", "source_id", "public", *IMPACT_FIELDS},
    "warning": {"hazard", "level", "areas", "issuer", "issued_at", "valid_until", "advice", "source_id", "public"},
    "warning_end": {"warning_id", "reason"},
    "publish": {"record_id", "public"},
    "map_layer": {"name", "kind", "issuer", "observed_at", "geojson", "source_id", "public"},
    "map_layer_remove": {"layer_id", "reason"},
}
LAYER_KINDS = {"flood_extent": "Observed flood extent", "road_closure": "Road closures", "landslide_area": "Landslide area", "other": "Other"}
LAYER_GEOMETRIES = {"Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon"}
LAYER_BYTES = 700_000
ACTION_HELP = {
    "area_status": "Any acting role reports a service status for an area: area (DS id like ds-nuwara-eliya-kothmale or district id like district-nuwara-eliya), sector power/water/roads/health/communications/shelter/food, level working/restoring/disrupted/unknown, note (what people should know), optional expected_by ISO time, optional latitude/longitude for a point such as a road blockage, optional source_id. public true only for a lead.",
    "area_figures": "Dmc/district/division/ndrsc roles record DMC-style figures for one DS area: as_of ISO time and any of families_affected, people_affected, deaths, injured, missing, houses_destroyed, houses_damaged, safety_centres, people_in_centres (whole numbers; omit unknown). Optional source_id; public true only for a lead.",
    "warning": "Coordinator records an official warning exactly as published: hazard landslide/flood/rain/wind/cyclone/tsunami/other, level yellow/amber/red, areas list of area ids, issuer nbro/met/irrigation/dmc, issued_at and optional valid_until ISO times, advice copied from the bulletin, source_id of the report holding the bulletin. Never invent a warning.",
    "warning_end": "Coordinator ends a warning the issuer lifted: warning_id and reason.",
    "publish": "A coordinator, agency supervisor or public information officer shows (public true) or hides (false) an area status, figures, warning, shelter or map layer on the public status page: record_id.",
    "map_layer": "Any acting role uploads a map layer someone else produced: name, kind flood_extent/road_closure/landslide_area/other, issuer (e.g. Copernicus EMS, UNOSAT, RDA), observed_at ISO time, geojson FeatureCollection in WGS84, optional source_id, public true only for a lead.",
    "map_layer_remove": "The organisation that added a layer, or a coordinator, removes it with a reason: layer_id, reason.",
}


def _has(actor, *roles):
    return bool(set(actor["roles"]) & set(roles))


def _text(value, label, maximum, minimum=1):
    require(isinstance(value, str) and minimum <= len(value.strip()) <= maximum, f"{label}: enter {minimum}–{maximum} characters.", 422)
    return value.strip()


def _time(value, label):
    try:
        return timestamp(value).isoformat()
    except Exception:
        require(False, f"{label}: enter an ISO date and time with a timezone.", 422)


def _area(value):
    require(value in DIVISIONS or value in DISTRICT_IDS, "Choose a DS division or district from the list.", 422)
    return value


def area_name(area_id):
    if area_id in DIVISIONS:
        d = DIVISIONS[area_id]
        return f"{d['name']} DS, {d['district']}"
    return f"{DISTRICT_IDS[area_id]['name']} District" if area_id in DISTRICT_IDS else area_id


def divisions_of(area_id):
    return BY_DISTRICT.get(DISTRICT_IDS[area_id]["name"], []) if area_id in DISTRICT_IDS else [area_id]


def _coords(geometry):
    stack = [geometry["coordinates"]]
    while stack:
        item = stack.pop()
        if item and isinstance(item[0], (int, float)):
            yield item
        else:
            stack.extend(item)


def clean_geojson(value):
    """A bounded FeatureCollection inside Sri Lanka, rounded to about a metre; properties kept small."""
    import json as _json
    require(isinstance(value, dict) and value.get("type") == "FeatureCollection" and isinstance(value.get("features"), list), "Upload a GeoJSON FeatureCollection.", 422)
    require(0 < len(value["features"]) <= 5000, "A layer holds 1 to 5,000 features.", 422)
    out = []
    for f in value["features"]:
        g = f.get("geometry") if isinstance(f, dict) else None
        require(isinstance(g, dict) and g.get("type") in LAYER_GEOMETRIES and isinstance(g.get("coordinates"), list), "Each feature needs a point, line or polygon geometry.", 422)
        def r(c):
            return [r(x) for x in c] if c and isinstance(c[0], list) else [round(float(c[0]), 5), round(float(c[1]), 5)]
        try:
            coords = r(g["coordinates"])
        except (TypeError, ValueError, IndexError):
            require(False, "A geometry has invalid coordinates.", 422)
        geometry = {"type": g["type"], "coordinates": coords}
        require(all(79.0 <= c[0] <= 82.5 and 5.5 <= c[1] <= 10.5 for c in _coords(geometry)), "Coordinates must be longitude, latitude inside Sri Lanka (WGS84).", 422)
        props = f.get("properties") or {}
        label = next((str(props[k])[:120] for k in ("name", "label", "road", "description", "status") if props.get(k) not in (None, "")), None)
        out.append({"type": "Feature", "properties": {"label": label} if label else {}, "geometry": geometry})
    clean = {"type": "FeatureCollection", "features": out}
    require(len(_json.dumps(clean, separators=(",", ":"))) <= LAYER_BYTES, "This layer is too detailed (over 700 KB once cleaned). Simplify it, or split it by area.", 413)
    return clean


def initialise(c):
    c.setdefault("map_layers", [])
    for key in ("area_status", "area_figures", "warnings"):
        c.setdefault(key, [])


def _public(actor, d):
    if d.get("public") is None:
        return False
    require(isinstance(d["public"], bool), "Public must be true or false.", 422)
    require(not d["public"] or (_has(actor, *PUBLISHERS) and not _has(actor, "partner")), "Only a lead or the public information officer publishes to the public page.", 403)
    return d["public"]


def _source(doc, source_id):
    if not source_id:
        return None
    source = next((s for s in doc["sources"] if s["id"] == source_id), None)
    require(source is not None and source["status"] != "rejected", "Link a report that exists and was not rejected.", 422)
    # A shared letter states its issuer's view; it is not evidence for an area record.
    require(source["status"] != "issued", "A shared letter cannot be the evidence for an area record. Link the original report.", 422)
    return source_id


def _base(actor, d, doc):
    return {"organisation": actor["organisation"], "by": actor["id"], "at": now_iso(), "source_id": _source(doc, d.get("source_id")),
            "published_by": None, "published_at": None}


def apply_area(doc, actor, action, d):
    c = doc["coordination"]
    initialise(c)
    org = actor["organisation"]
    require(_has(actor, *ACTING), "Observers can read the map but not change it.", 403)
    if action == "area_status":
        area = _area(d.get("area"))
        require(d.get("sector") in SECTORS, "Choose a service.", 422)
        require(d.get("level") in LEVELS, "Choose working, being restored, not working or not known.", 422)
        point = None
        if d.get("latitude") is not None or d.get("longitude") is not None:
            lat, lng = d.get("latitude"), d.get("longitude")
            require(isinstance(lat, (int, float)) and isinstance(lng, (int, float)) and 5.7 <= lat <= 10.0 and 79.4 <= lng <= 82.1, "A point must be a latitude and longitude inside Sri Lanka.", 422)
            point = [round(float(lat), 5), round(float(lng), 5)]
        record = {"id": uid("status"), "area": area, "sector": d["sector"], "level": d["level"],
                  "note": _text(d["note"], "Note", 300) if d.get("note") else "",
                  "expected_by": _time(d["expected_by"], "Expected back") if d.get("expected_by") else None,
                  "point": point, **_base(actor, d, doc)}
        record["public"] = _public(actor, d)
        if record["public"]:
            record["published_by"], record["published_at"] = actor["id"], record["at"]
        c["area_status"].append(record)
        return f"{org}: {SECTORS[record['sector']]} in {area_name(area)} · {LEVELS[record['level']]}"
    if action == "area_figures":
        area = d.get("area")
        # A district record is that district's official total (a DMC situation-report row); otherwise districts add up their divisions.
        require(area in DIVISIONS or area in DISTRICT_IDS, "Record figures for a DS division, or a district's official total.", 422)
        require(org in FIGURE_ORGS, "Situation figures come from the DMC, district, divisional or NDRSC offices.", 403)
        values = {}
        for key in IMPACT_FIELDS:
            if d.get(key) is not None:
                require(type(d[key]) is int and 0 <= d[key] <= 10_000_000, f"{IMPACT_FIELDS[key]} must be a whole number; leave it blank if unknown.", 422)
                values[key] = d[key]
        require(values, "Enter at least one figure. Leave unknown figures blank.", 422)
        record = {"id": uid("figures"), "area": area, "as_of": _time(d.get("as_of"), "Figures as of"), "values": values, **_base(actor, d, doc)}
        record["public"] = _public(actor, d)
        if record["public"]:
            record["published_by"], record["published_at"] = actor["id"], record["at"]
        c["area_figures"].append(record)
        return f"{org}: figures for {area_name(area)} as of {record['as_of'][:16]}"
    if action == "warning":
        require(_has(actor, "coordinator"), "A coordinator records official warnings from the issuer's bulletin.", 403)
        require(d.get("hazard") in HAZARDS and d.get("level") in WARNING_LEVELS and d.get("issuer") in ISSUERS, "Choose the hazard, level and issuing authority.", 422)
        areas = d.get("areas")
        require(isinstance(areas, list) and 1 <= len(areas) <= 400 and len(set(areas)) == len(areas), "Choose the areas named in the bulletin.", 422)
        for a in areas:
            _area(a)
        require(d.get("source_id"), "Link the report that holds the original bulletin.", 422)
        issued = _time(d.get("issued_at"), "Issued at")
        until = _time(d["valid_until"], "Valid until") if d.get("valid_until") else None
        require(not until or timestamp(until) > timestamp(issued), "Valid until must be after the issue time.", 422)
        record = {"id": uid("warning"), "hazard": d["hazard"], "level": d["level"], "areas": areas, "issuer": d["issuer"],
                  "issued_at": issued, "valid_until": until, "advice": _text(d.get("advice", ""), "Advice from the bulletin", 600),
                  "ended_at": None, "end_reason": None, **_base(actor, d, doc)}
        # An official warning is already public; recording it publicly is the default.
        record["public"] = True if d.get("public") is None else _public(actor, d)
        if record["public"]:
            record["published_by"], record["published_at"] = actor["id"], record["at"]
        c["warnings"].append(record)
        return f"{ISSUERS[record['issuer']]} {record['level']} {HAZARDS[record['hazard']].lower()} warning · {len(areas)} area{'s' if len(areas) != 1 else ''}"
    if action == "warning_end":
        require(_has(actor, "coordinator"), "A coordinator ends a warning once the issuer lifts it.", 403)
        w = next((x for x in c["warnings"] if x["id"] == d.get("warning_id")), None)
        require(w is not None, "Warning not found.", 404)
        require(not w["ended_at"], "This warning has already ended.")
        w["ended_at"], w["end_reason"] = now_iso(), _text(d.get("reason", ""), "Reason", 300, 4)
        return f"Ended {ISSUERS[w['issuer']]} {w['level']} {HAZARDS[w['hazard']].lower()} warning"
    if action == "publish":
        require(_has(actor, *PUBLISHERS) and not _has(actor, "partner"), "Only a lead or the public information officer publishes to the public page.", 403)
        require(isinstance(d.get("public"), bool), "Public must be true or false.", 422)
        target = None
        for key in ("area_status", "area_figures", "warnings", "assets", "map_layers"):
            target = next((x for x in c.get(key, []) if x["id"] == d.get("record_id")), None)
            if target:
                break
        require(target is not None, "Record not found.", 404)
        if key == "assets":
            require(target.get("kind") == "shelter", "Only safety centres appear on the public page.", 422)
            require(_has(actor, "coordinator") or target["owner"] == org, "Publish only your organisation's safety centres.", 403)
        target["public"] = d["public"]
        target["published_by"], target["published_at"] = (actor["id"], now_iso()) if d["public"] else (None, None)
        return ("Published " if d["public"] else "Unpublished ") + {"area_status": "an area status", "area_figures": "area figures", "warnings": "a warning", "assets": "a safety centre", "map_layers": "a map layer"}[key]
    if action == "map_layer":
        name = d.get("name")
        require(isinstance(name, str) and 3 <= len(name.strip()) <= 120, "Name the layer (3–120 characters).", 422)
        require(d.get("kind") in LAYER_KINDS, "Choose what the layer shows.", 422)
        issuer = (d.get("issuer") or "").strip()
        require(1 <= len(issuer) <= 80, "Say who produced the layer, e.g. Copernicus EMS, UNOSAT, RDA.", 422)
        geojson = clean_geojson(d.get("geojson"))
        record = {"id": uid("layer"), "name": name.strip(), "kind": d["kind"], "issuer": issuer, "observed_at": _time(d.get("observed_at"), "Observed at"),
                  "features": len(geojson["features"]), "geojson": geojson, "removed_at": None, "removed_reason": None, **_base(actor, d, doc)}
        record["public"] = _public(actor, d)
        if record["public"]:
            record["published_by"], record["published_at"] = actor["id"], record["at"]
        c["map_layers"].append(record)
        return f"Map layer added: {record['name']} ({record['features']} features)"
    if action == "map_layer_remove":
        layer = next((x for x in c["map_layers"] if x["id"] == d.get("layer_id")), None)
        require(layer is not None, "Layer not found.", 404)
        require(layer["organisation"] == org or _has(actor, "coordinator"), "The organisation that added a layer, or a coordinator, removes it.", 403)
        require(not layer["removed_at"], "This layer was already removed.")
        reason = (d.get("reason") or "").strip()
        require(3 <= len(reason) <= 300, "Say why the layer is removed (3–300 characters).", 422)
        layer.update(removed_at=now_iso(), removed_by=actor["id"], removed_reason=reason, public=False)
        return f"Map layer removed: {layer['name']}"
    raise AssertionError(action)


def layer_meta(layer, meta):
    return {k: layer[k] for k in ("id", "name", "kind", "issuer", "observed_at", "features", "organisation", "at", "public", "source_id")} | meta


def totals(areas):
    """Totals per figure: a district's own record when it has one, else the sum of its divisions."""
    out = {}
    for key in IMPACT_FIELDS:
        total, found = 0, False
        for district_id, district in DISTRICT_IDS.items():
            own = ((areas.get(district_id) or {}).get("figures") or {}).get("values", {}).get(key)
            if own is not None:
                total, found = total + own, True
                continue
            for division in BY_DISTRICT.get(district["name"], []):
                value = ((areas.get(division) or {}).get("figures") or {}).get("values", {}).get(key)
                if value is not None:
                    total, found = total + value, True
        if found:
            out[key] = total
    return out


def _active(w, now):
    return not w["ended_at"] and timestamp(w["issued_at"]) <= now and (not w["valid_until"] or timestamp(w["valid_until"]) > now)


def picture(doc, *, public_only=False, balances=None):
    """The current state of every area touched by this incident.

    `areas[id]` holds `services` (latest record per sector, district-wide records applied to
    each division), `warnings` in force (highest first) and the latest `figures`.
    """
    c = doc["coordination"]
    initialise(c)
    now = business_now(c)
    keep = (lambda r: r.get("public")) if public_only else (lambda r: True)
    areas = {}
    slot = lambda a: areas.setdefault(a, {"services": {}, "warnings": [], "figures": None})
    meta = {"incident_id": doc["id"], "incident_title": doc["title"]}
    for r in sorted(filter(keep, c["area_status"]), key=lambda r: r["at"]):
        entry = {k: r[k] for k in ("id", "area", "sector", "level", "note", "expected_by", "point", "organisation", "at", "source_id", "public")}
        entry["stale"] = timestamp(r["at"]) < now - timedelta(hours=STALE_HOURS)
        entry.update(meta)
        for a in divisions_of(r["area"]):
            slot(a)["services"][r["sector"]] = entry
    for w in filter(keep, c["warnings"]):
        if not _active(w, now):
            continue
        entry = {k: w[k] for k in ("id", "hazard", "level", "issuer", "issued_at", "valid_until", "advice", "areas", "source_id", "public")}
        entry.update(meta)
        for a in {x for area in w["areas"] for x in divisions_of(area)}:
            slot(a)["warnings"].append(entry)
    for a in areas.values():
        a["warnings"].sort(key=lambda w: -WARNING_RANK[w["level"]])
    for r in sorted(filter(keep, c["area_figures"]), key=lambda r: r["as_of"]):
        current = slot(r["area"])["figures"] or {"values": {}, "as_of": {}, "by": {}, **meta}
        for key, value in r["values"].items():
            current["values"][key], current["as_of"][key], current["by"][key] = value, r["as_of"], r["organisation"]
        current["id"], current["public"] = r["id"], r.get("public")
        slot(r["area"])["figures"] = current
    centres = []
    for asset in c.get("assets", []):
        if asset.get("kind") != "shelter" or asset.get("latitude") is None or not keep(asset):
            continue
        balance = (balances or {}).get(asset["id"], asset.get("balance") or {})
        capacity = (asset.get("facts", {}).get("capacity") or {}).get("value")
        centres.append({"id": asset["id"], "name": asset["name"], "owner": asset["owner"], "latitude": asset["latitude"], "longitude": asset["longitude"],
                        "capacity": capacity, "available": balance.get("available"), "public": bool(asset.get("public")), **meta})
    layers = [layer_meta(x, meta) for x in c.get("map_layers", []) if not x["removed_at"] and keep(x)]
    return {"as_of": now.isoformat(), "areas": areas, "centres": centres, "totals": totals(areas), "layers": layers, "warnings": [
        {k: w[k] for k in ("id", "hazard", "level", "issuer", "issued_at", "valid_until", "advice", "areas", "ended_at", "public", "source_id")} | meta
        for w in filter(keep, c["warnings"])]}


def merge(pictures):
    """Combine several incidents: newest service status wins, warnings and centres add up,
    figures from different incidents add up per field (each incident is a separate event)."""
    out = {"as_of": max((p["as_of"] for p in pictures), default=now_iso()), "areas": {}, "centres": [], "warnings": [], "layers": []}
    for p in pictures:
        out["centres"] += p["centres"]
        out["layers"] += p.get("layers", [])
        out["warnings"] += p["warnings"]
        for area, value in p["areas"].items():
            slot = out["areas"].setdefault(area, {"services": {}, "warnings": [], "figures": None})
            for sector, entry in value["services"].items():
                if sector not in slot["services"] or entry["at"] > slot["services"][sector]["at"]:
                    slot["services"][sector] = entry
            slot["warnings"] = sorted(slot["warnings"] + value["warnings"], key=lambda w: -WARNING_RANK[w["level"]])
            if value["figures"]:
                if not slot["figures"]:
                    slot["figures"] = {"values": dict(value["figures"]["values"]), "as_of": dict(value["figures"]["as_of"]), "by": dict(value["figures"]["by"]), "incidents": 1}
                else:
                    for key, v in value["figures"]["values"].items():
                        slot["figures"]["values"][key] = slot["figures"]["values"].get(key, 0) + v
                        slot["figures"]["as_of"][key] = max(slot["figures"]["as_of"].get(key, ""), value["figures"]["as_of"][key])
                    slot["figures"]["incidents"] += 1
    out["totals"] = totals(out["areas"])
    return out


def vocabulary():
    return {"sectors": SECTORS, "levels": LEVELS, "hazards": HAZARDS, "warning_levels": WARNING_LEVELS, "issuers": ISSUERS,
            "figures": IMPACT_FIELDS, "stale_hours": STALE_HOURS}


def practice_fixture(d, at, bulletin_source, report_source):
    """Fictional map picture for the labelled practice case (Nuwara Eliya). Not agency data."""
    c = d["coordination"]
    initialise(c)
    later = lambda hours: (timestamp(at) + timedelta(hours=hours)).isoformat()
    base = lambda org, public, source=None: {"organisation": org, "by": "exercise_fixture", "at": at, "source_id": source, "public": public,
                                             "published_by": "exercise_fixture" if public else None, "published_at": at if public else None}
    for area, sector, level, org, note, extra, public in [
        ("district-nuwara-eliya", "communications", "working", "dmc", "Mobile networks working across the district.", {}, True),
        ("ds-nuwara-eliya-kothmale", "water", "disrupted", "water", "Piped supply off at the practice hall; bowsers requested.", {}, True),
        ("ds-nuwara-eliya-kothmale", "power", "restoring", "dmc", "Crews restoring the feeder line.", {"expected_by": later(6)}, True),
        ("ds-nuwara-eliya-kothmale", "roads", "disrupted", "rda", "Landslide blocks the access road to the practice hall.", {"point": [7.045, 80.62]}, True),
        ("ds-nuwara-eliya-kothmale", "health", "working", "district", "Divisional hospital open.", {}, True),
        ("ds-nuwara-eliya-kothmale", "shelter", "working", "division", "One safety centre open with free places.", {}, True),
        ("ds-nuwara-eliya-kothmale", "food", "restoring", "ndrsc", "Dry rations dispatched; arrival not yet confirmed.", {}, False),
        ("ds-nuwara-eliya-kothmale", "communications", "restoring", "dmc", "Weak signal near the hall after the landslide.", {}, False),
        ("ds-nuwara-eliya-walapane", "roads", "restoring", "rda", "One lane open; clearing continues.", {"point": [7.1, 80.86]}, True),
        ("ds-nuwara-eliya-walapane", "power", "disrupted", "dmc", "Outage after fallen trees.", {}, True),
        ("ds-nuwara-eliya-walapane", "water", "working", "water", "", {}, True),
        ("ds-nuwara-eliya-hanguranketa", "power", "working", "dmc", "", {}, True),
        ("ds-nuwara-eliya-hanguranketa", "water", "working", "water", "", {}, True),
        ("ds-nuwara-eliya-nuwara-eliya", "roads", "working", "rda", "", {}, True),
    ]:
        c["area_status"].append({"id": uid("status"), "area": area, "sector": sector, "level": level, "note": note, "expected_by": extra.get("expected_by"),
                                 "point": extra.get("point"), **base(org, public, report_source)})
    for hazard, level, areas, issuer, advice in [
        ("landslide", "amber", ["ds-nuwara-eliya-kothmale", "ds-nuwara-eliya-walapane"], "nbro", "Be alert to cracks, tilting trees and muddy water on slopes; be ready to move to a safety centre (fictional exercise wording)."),
        ("landslide", "yellow", ["ds-nuwara-eliya-hanguranketa"], "nbro", "Remain vigilant if rain continues (fictional exercise wording)."),
        ("rain", "amber", ["district-nuwara-eliya"], "met", "Heavy showers above 100 mm likely; avoid travel on hill roads (fictional exercise wording)."),
    ]:
        c["warnings"].append({"id": uid("warning"), "hazard": hazard, "level": level, "areas": areas, "issuer": issuer, "issued_at": at, "valid_until": later(24),
                              "advice": advice, "ended_at": None, "end_reason": None, **base("dmc", True, bulletin_source)})
    for area, values in [
        ("ds-nuwara-eliya-kothmale", {"families_affected": 120, "people_affected": 410, "deaths": 0, "injured": 3, "missing": 0, "houses_destroyed": 2, "houses_damaged": 23, "safety_centres": 1, "people_in_centres": 40}),
        ("ds-nuwara-eliya-walapane", {"families_affected": 45, "people_affected": 160, "houses_damaged": 9}),
        ("ds-nuwara-eliya-hanguranketa", {"families_affected": 6, "people_affected": 21}),
    ]:
        c["area_figures"].append({"id": uid("figures"), "area": area, "as_of": at, "values": values, **base("district", True, report_source)})
    for asset in c["assets"]:
        if asset.get("kind") == "shelter":
            asset.update(public=True, published_by="exercise_fixture", published_at=at)
