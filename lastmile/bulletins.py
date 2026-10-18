"""Read official bulletins into reviewable records. Nothing here changes data on its own.

* Irrigation Department "Water Level & Rainfall" bulletins: thresholds and the latest level per gauge.
* DMC situation reports: district figures (affected, deaths, missing, houses, safety centres).
* Area warning texts such as NBRO landslide early warnings: which DS divisions are at which level.

Each reader returns rows for a person to check and confirm; the caller turns confirmed rows into
records with the source attached. Unmatched lines are reported, never guessed.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .team_accounts import DISTRICTS
from .team_areas import DIVISIONS

SL = timezone(timedelta(hours=5, minutes=30))
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
NUMBER = r"-?\d+(?:\.\d+)?"


# ---------------------------------------------------------------- gauges
def parse_gauges(text, gauges):
    """Rows for known stations: unit, three thresholds, previous and latest level, printed status, rain."""
    issued = None
    m = re.search(r"DATE\s*:\s*(\d{1,2})-([A-Za-z]{3})-(\d{4})\s+TIME\s*:\s*(\d{1,2}):(\d{2})\s*([AP]M)", text, re.I)
    if m:
        day, mon, year, hour, minute, half = m.groups()
        hour = int(hour) % 12 + (12 if half.upper() == "PM" else 0)
        issued = datetime(int(year), MONTHS[mon.lower()], int(day), hour, int(minute), tzinfo=SL).isoformat()
    flat = re.sub(r"\s+", " ", text)
    rows, missing = [], []
    for g in gauges:
        pattern = re.escape(g["name"]).replace(r"\ ", r"\s*") + rf"\s+(m|ft)\s+({NUMBER})\s+({NUMBER})\s+({NUMBER})\s+({NUMBER}|-)\s+({NUMBER}|-)\s*([A-Za-z ]*?)\s+({NUMBER}|-)(?=\s|$)"
        hit = re.search(pattern, flat)
        if not hit:
            missing.append(g["name"])
            continue
        unit, alert, minor, major, previous, level, status, rain = hit.groups()
        num = lambda v: None if v == "-" else float(v)
        rows.append({"gauge_id": g["id"], "name": g["name"], "unit": unit, "alert_level": float(alert), "minor_flood": float(minor), "major_flood": float(major),
                     "previous": num(previous), "level": num(level), "printed_status": status.strip() or None, "rain_mm": num(rain)})
    return {"issued_at": issued, "rows": rows, "not_found": missing}


def gauge_state(g, now=None):
    """Warning state from the latest reading and the gauge's own thresholds."""
    r = g.get("reading")
    if not r or r.get("level") is None:
        return {"state": "no_reading"}
    level = r["level"]
    state = "normal"
    for key, name in (("alert_level", "alert"), ("minor_flood", "minor"), ("major_flood", "major")):
        if g.get(key) is not None and level >= g[key]:
            state = name
    trend = None
    if r.get("previous") is not None:
        diff = level - r["previous"]
        trend = "rising" if diff > 0.005 else "falling" if diff < -0.005 else "steady"
    now = now or datetime.now(timezone.utc)
    stale = r.get("at") and now - datetime.fromisoformat(r["at"]) > timedelta(hours=12)
    return {"state": state, "trend": trend, "stale": bool(stale), "level": level, "at": r.get("at")}


GAUGE_WARNING = {"alert": "yellow", "minor": "amber", "major": "red"}


# ---------------------------------------------------------------- situation reports
SITREP_COLUMNS = ["peak_families", "peak_people", "families_affected", "people_affected", "deaths", "missing", "houses_destroyed", "houses_damaged",
                  "peak_centres", "peak_centre_families", "peak_centre_people", "safety_centres", "centre_families", "people_in_centres",
                  "relatives_families", "relatives_people", "idp_families", "idp_people"]
SITREP_FIGURES = ["families_affected", "people_affected", "deaths", "missing", "houses_destroyed", "houses_damaged", "safety_centres", "people_in_centres"]


def parse_sitrep(text):
    """District rows of a DMC situation report table (18 figures per district, as at the report time)."""
    as_of = None
    m = re.search(r"as at\s*(\d{3,4})\s*hrs\s*on\s*(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})", text, re.I)
    if m:
        hhmm, day, month, year = m.groups()
        hhmm = hhmm.zfill(4)
        if month[:3].lower() in MONTHS:
            as_of = datetime(int(year), MONTHS[month[:3].lower()], int(day), int(hhmm[:2]), int(hhmm[2:]), tzinfo=SL).isoformat()
    names = {d.lower(): d for d in DISTRICTS}
    rows, problems = [], []
    for line in text.splitlines():
        hit = re.match(r"^\s*\d{1,2}\s+([A-Za-z][A-Za-z .]+?)\s+((?:[\d,]+\s+)+[\d,]+)\s*$", line)
        if not hit:
            continue
        district = names.get(re.sub(r"\s+", " ", hit.group(1)).strip().lower())
        if not district:
            problems.append(f"Unknown district: {hit.group(1).strip()}")
            continue
        numbers = [int(n.replace(",", "")) for n in hit.group(2).split()]
        if len(numbers) != len(SITREP_COLUMNS):
            problems.append(f"{district}: {len(numbers)} figures, expected {len(SITREP_COLUMNS)}; check the table layout")
            continue
        values = dict(zip(SITREP_COLUMNS, numbers))
        rows.append({"district": district, "area": "district-" + re.sub(r"[^a-z0-9]+", "-", district.lower()).strip("-"),
                     "figures": {k: values[k] for k in SITREP_FIGURES}, "peak": {k: values[k] for k in ("peak_families", "peak_people")}})
    return {"as_of": as_of, "rows": rows, "problems": problems}


# ---------------------------------------------------------------- area warnings (NBRO landslide, DMC flood notices)
def _norm(word):
    w = re.sub(r"[^a-z]", "", word.lower())
    for a, b in (("th", "t"), ("dh", "d"), ("w", "v"), ("aa", "a"), ("ee", "i"), ("oo", "u")):
        w = w.replace(a, b)
    return w


NAME_INDEX = {}
for _id, _d in DIVISIONS.items():
    NAME_INDEX.setdefault(_norm(_d["name"].replace("&", "")), []).append(_id)
LEVEL_WORDS = [(r"level\s*(?:3|iii|03)\b|\bred\b", "red"), (r"level\s*(?:2|ii|02)\b|\bamber\b", "amber"), (r"level\s*(?:1|i|01)\b|\byellow\b", "yellow")]


def find_divisions(text):
    """DS division ids named in a passage; tolerant of spacing and common transliteration differences."""
    tokens = re.findall(r"[A-Za-z]+", text)
    normed = [_norm(t) for t in tokens]
    found = []
    for i in range(len(tokens)):
        for j in range(min(len(tokens), i + 4), i, -1):
            key = "".join(normed[i:j])
            if key in NAME_INDEX:
                if j < len(tokens) and normed[j] == "district":
                    break  # "Colombo District" names the district, not the division
                found.extend(x for x in NAME_INDEX[key] if x not in found)
                break
    return found


def parse_area_warning(text):
    """Split a warning text at each level heading and list the DS divisions under it."""
    marks = []
    for pattern, level in LEVEL_WORDS:
        marks += [(m.start(), level) for m in re.finditer(pattern, text, re.I)]
    marks.sort()
    levels = {}
    for n, (start, level) in enumerate(marks):
        end = marks[n + 1][0] if n + 1 < len(marks) else len(text)
        for area in find_divisions(text[start:end]):
            levels.setdefault(level, [])
            if area not in levels[level]:
                levels[level].append(area)
    # A division named under two levels keeps the higher one.
    seen = set()
    out = []
    for level in ("red", "amber", "yellow"):
        areas = [a for a in levels.get(level, []) if a not in seen]
        seen.update(areas)
        if areas:
            out.append({"level": level, "areas": areas})
    unplaced = [a for a in find_divisions(text) if a not in seen]
    return {"levels": out, "unplaced": unplaced}


def read_report(text, gauges):
    """Everything the fixed readers find in one report: situation-report figures, area warnings and gauge levels."""
    gauge_rows = parse_gauges(text, gauges)
    by_id = {g["id"]: g for g in gauges}
    for row in gauge_rows["rows"]:
        state = gauge_state({**row, "reading": {"level": row["level"], "previous": row["previous"]}})
        row.update(state=state["state"], trend=state.get("trend"), area=by_id[row["gauge_id"]].get("area"), river=by_id[row["gauge_id"]]["river"], warning=GAUGE_WARNING.get(state["state"]))
    return {"sitrep": parse_sitrep(text), "warnings": parse_area_warning(text), "gauges": gauge_rows}

