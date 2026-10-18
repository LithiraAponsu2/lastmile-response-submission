"""Build the reference seed in data/reference/: the places, stock and standards LastMile starts with.

Run explicitly with network access:  python scripts/build_reference_seed.py
The application never downloads anything at startup; it copies these files into its data directory
(and the private bucket when one is configured) the first time it runs.

Real public data, kept with its licence and retrieval details:
  * hospitals from OpenStreetMap (HOT export listed on HDX, ODbL);
  * district population, 2023 projection (HDX COD-PS from the Department of Census and Statistics, CC BY-IGO);
  * river gauging stations and their alert / minor / major flood levels, transcribed from the Irrigation
    Department "Water Level & Rainfall" bulletin of 8 August 2026; station positions are the nearest
    town from OpenStreetMap Nominatim, not surveyed gauge locations.
  * GN divisions (14,000+) with Sinhala names and centre points (HDX COD-AB, Survey Department / OCHA, CC BY-IGO);
  * historical disaster events per DS division since 2000 (DesInventar Sri Lanka, maintained by the DMC);
  * lifeline and emergency sites and homes for elders and children (OpenStreetMap points of interest, ODbL).
Exercise data is fictional and every record says so: safety centres, stock and equipment, office desks,
forces' assets and partner pledges.
"""
import hashlib
import io
import json
import random
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from collections import Counter, defaultdict
from datetime import date
from difflib import get_close_matches
from xml.etree.ElementTree import iterparse
from pathlib import Path

from shapely.geometry import Point, shape

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "reference"
TODAY = date.today().isoformat()
UA = "LastMile-Prototype/1.0 (reference seed build; https://github.com/)"

HEALTH_URL = "https://production-raw-data-api.s3.amazonaws.com/ISO3/LKA/health_facilities/hotosm_lka_health_facilities_osm_geojson.zip"
HEALTH_PAGE = "https://data.humdata.org/dataset/hotosm_lka_health_facilities"
POP_URL = "https://data.humdata.org/dataset/e982aec3-478e-4910-ae5c-072ae2cf6933/resource/7ccd7053-1cf8-4abb-876c-d6a486c8a743/download/lka_admpop_2023.xlsx"
POP_PAGE = "https://data.humdata.org/dataset/cod-ps-lka"
GAUGE_PAGE = "http://www.drrweb.dmc.gov.lk/images/dmcreports/Water_level_&_Rainfall_2026__1786215485.pdf"
GAUGE_AS_OF = "2026-08-08"
CODAB_URL = "https://data.humdata.org/dataset/0bedcaf3-88cd-4591-b9d5-5d3220e26abf/resource/89f5e8ba-4385-48ad-a328-2006ab717ab3/download/lka_admin_boundaries.xlsx"
CODAB_PAGE = "https://data.humdata.org/dataset/cod-ab-lka"
POP_PLACES_URL = "https://production-raw-data-api.s3.amazonaws.com/ISO3/LKA/populated_places/hotosm_lka_populated_places_osm_geojson.zip"
POI_URL = "https://production-raw-data-api.s3.amazonaws.com/ISO3/LKA/points_of_interest/hotosm_lka_points_of_interest_osm_geojson.zip"
POI_PAGE = "https://data.humdata.org/organization/hot"
DESINVENTAR_URL = "https://www.desinventar.net/DesInventar/download/DI_export_lka.zip"
DESINVENTAR_PAGE = "https://www.desinventar.net/DesInventar/profiletab.jsp?countrycode=lka"
_CACHE = {}


def cached(url):
    if url not in _CACHE:
        _CACHE[url] = fetch(url)
    return _CACHE[url]


def norm(word):
    """Spelling-tolerant key: letters only, common transliteration differences folded."""
    w = re.sub(r"[^a-z]", "", (word or "").lower())
    for a, b in (("th", "t"), ("dh", "d"), ("w", "v"), ("aa", "a"), ("ee", "i"), ("oo", "u")):
        w = w.replace(a, b)
    return w


def fetch(url, headers=None):
    request = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read()


class Areas:
    def __init__(self):
        geo = json.loads((ROOT / "web" / "data" / "sri-lanka-areas.geojson").read_text(encoding="utf-8"))
        self.items = [(f["properties"]["id"], f["properties"]["district"], shape(f["geometry"])) for f in geo["features"]]
        self.ids = {i for i, _, _ in self.items}

    def at(self, lat, lon):
        point = Point(lon, lat)
        for area_id, district, geometry in self.items:
            if geometry.contains(point):
                return area_id, district
        # Coastal points fall just outside the simplified outlines; take the nearest division within about 3 km.
        area_id, district, geometry = min(self.items, key=lambda x: x[2].distance(point))
        return (area_id, district) if geometry.distance(point) < 0.03 else (None, None)

    def point_in(self, area_id, seed):
        geometry = next(g for i, _, g in self.items if i == area_id)
        rng, (x0, y0, x1, y1) = random.Random(seed), geometry.bounds
        inner = geometry.buffer(-0.01) if not geometry.buffer(-0.01).is_empty else geometry
        while True:
            p = Point(rng.uniform(x0, x1), rng.uniform(y0, y1))
            if inner.contains(p):
                return round(p.y, 4), round(p.x, 4)


def district_id(name):
    return "district-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def dataset(name, records, provenance, fictional):
    return {"dataset": name, "version": 1, "updated_at": f"{TODAY}T00:00:00+05:30", "updated_by": "seed", "fictional": fictional,
            "provenance": provenance, "records": records}


# ---------------------------------------------------------------- real: hospitals
def hospital_type(name):
    for pattern, label in [(r"teaching", "teaching"), (r"national hospital", "national"), (r"general hospital|\bdgh\b|district general", "general"),
                           (r"base hospital|\bbh\b", "base"), (r"divisional hospital|\bdh\b", "divisional"), (r"district hospital", "divisional"),
                           (r"primary medical care|\bpmcu\b|\bpmc\b", "primary_care"), (r"\bmoh\b|medical officer of health", "moh_office"),
                           (r"ayurved", "ayurveda")]:
        if re.search(pattern, name, re.I):
            return label
    return "hospital"


def health(areas):
    body = fetch(HEALTH_URL)
    archive = zipfile.ZipFile(io.BytesIO(body))
    features = json.loads(archive.read("health_facilities.geojson"))["features"]
    records, seen = [], set()
    for f in features:
        p = f["properties"]
        if p.get("amenity") != "hospital" and p.get("healthcare") != "hospital":
            continue
        name = (p.get("name_en") or p.get("name") or p.get("name_latin") or "").strip()
        if not name or p["id"] in seen:
            continue
        seen.add(p["id"])
        c = shape(f["geometry"]).representative_point() if f["geometry"]["type"] != "Point" else shape(f["geometry"])
        lat, lon = round(c.y, 5), round(c.x, 5)
        area, _ = areas.at(lat, lon)
        beds = p.get("capacity_persons")
        records.append({"id": "health-" + p["id"].replace("/", "-"), "name": name[:150], "type": hospital_type(name), "area": area,
                        "latitude": lat, "longitude": lon, "beds": int(beds) if str(beds or "").isdigit() else None,
                        "emergency_unit": None, "osm_id": p["id"], "source": "OpenStreetMap contributors via HOT export", "checked_at": None})
    records.sort(key=lambda r: (r["area"] or "", r["name"]))
    return dataset("health", records, {"title": "Hospitals of Sri Lanka from OpenStreetMap", "page": HEALTH_PAGE, "download": HEALTH_URL,
                   "sha256": hashlib.sha256(body).hexdigest(), "retrieved_on": TODAY, "licence": "Open Database License (ODbL) 1.0",
                   "attribution": "© OpenStreetMap contributors", "note": "Community-mapped; types are read from names, beds are rarely recorded. Not a Ministry of Health register."}, False)


# ---------------------------------------------------------------- real: population
def xlsx_rows(body, sheet_name):
    archive = zipfile.ZipFile(io.BytesIO(body))
    shared = re.findall(r"<si>(.*?)</si>", archive.read("xl/sharedStrings.xml").decode("utf-8"), re.S)
    shared = ["".join(re.findall(r"<t[^>]*>(.*?)</t>", s, re.S)) for s in shared]
    sheets = re.findall(r'<sheet [^>]*name="([^"]+)"[^>]*r:id="([^"]+)"', archive.read("xl/workbook.xml").decode())
    rels = dict(re.findall(r'Id="([^"]+)"[^>]*Target="([^"]+)"', archive.read("xl/_rels/workbook.xml.rels").decode()))
    rels.update({k: v for v, k in re.findall(r'Target="([^"]+)"[^>]*Id="([^"]+)"', archive.read("xl/_rels/workbook.xml.rels").decode())})
    target = rels[dict(sheets)[sheet_name]].lstrip("/").removeprefix("xl/")
    xml = archive.read("xl/" + target).decode("utf-8")
    rows = []
    for row in re.findall(r"<row[^>]*>(.*?)</row>", xml, re.S):
        cells = {}
        for ref, attrs, inner in re.findall(r'<c r="([A-Z]+)\d+"([^>]*)>(.*?)</c>', row, re.S):
            value = re.search(r"<v>(.*?)</v>", inner, re.S)
            text = re.search(r"<t[^>]*>(.*?)</t>", inner, re.S)
            cells[ref] = shared[int(value.group(1))] if 't="s"' in attrs and value else (value.group(1) if value else text.group(1) if text else "")
        rows.append(cells)
    header = rows[0]
    return [{header[k]: v for k, v in r.items() if k in header} for r in rows[1:]]


ALIASES = {"moneragala": "Monaragala", "mulativu": "Mullaitivu", "nuwaraeliya": "Nuwara Eliya"}


def population():
    from lastmile.team_accounts import DISTRICTS
    body = fetch(POP_URL)
    rows = xlsx_rows(body, "lka_admpop_adm2_2023")
    names = {re.sub(r"[^a-z]", "", d.lower()): d for d in DISTRICTS}
    records = []
    for r in rows:
        raw = r.get("ADM2_NAME") or r.get("ADM2_EN", "")
        key = re.sub(r"[^a-z]", "", raw.lower())
        name = names.get(key) or ALIASES.get(key)
        if not name:
            raise SystemExit(f"Unmatched district in population data: {raw!r}")
        n = lambda k: round(float(r[k])) if r.get(k) else None
        older = sum(n(k) or 0 for k in ("T_65_69", "T_70_74", "T_75_79", "T_80Plus"))
        records.append({"id": district_id(name), "district": name, "name_si": (r.get("ADM2_NAME_SI") or "").strip() or None, "name_ta": (r.get("ADM2_NAME_TA") or "").strip() or None,
                        "pcode": r.get("ADM2_PCODE"), "population": n("T_TL"), "female": n("F_TL"), "male": n("M_TL"),
                        "under_5": n("T_00_04"), "over_65": older or None, "year": 2023,
                        "basis": "projection", "source": "HDX COD-PS (Department of Census and Statistics; UNFPA projection)"})
    assert len(records) == 25, len(records)
    records.sort(key=lambda r: r["district"])
    return dataset("population", records, {"title": "Sri Lanka subnational population statistics, 2023", "page": POP_PAGE, "download": POP_URL,
                   "sha256": hashlib.sha256(body).hexdigest(), "retrieved_on": TODAY, "licence": "CC BY-IGO",
                   "attribution": "Department of Census and Statistics, Sri Lanka; UNFPA; via HDX",
                   "note": "District level, projected from the 2012 census. Divisional figures are not in this release."}, False)


# ---------------------------------------------------------------- real: river gauges
# Transcribed from the bulletin above: basin, river, station, unit, alert, minor flood, major flood,
# and the district(s) a geocoded town must fall in before its pin is kept.
GAUGES = """Kelani Ganga|Kelani Ganga|Nagalagam Street|ft|4.00|5.00|7.00|Colombo
Kelani Ganga|Kelani Ganga|Hanwella|m|6.50|7.50|9.50|Colombo
Kelani Ganga|Kelani Ganga|Glencourse|m|15.00|15.50|19.00|Kegalle
Kelani Ganga|Kelani Ganga|Kithulgala|m|3.00|4.00|6.00|Kegalle
Kelani Ganga|Gurugoda Oya|Holombuwa|m|3.00|4.00|6.00|Kegalle
Kelani Ganga|Seethawaka Ganga|Deraniyagala|m|4.80|5.80|7.00|Kegalle
Kelani Ganga|Kehelgamu Oya|Norwood|m|1.50|3.00|4.50|Nuwara Eliya
Kalu Ganga|Kalu Ganga|Putupaula|m|3.00|4.00|5.00|Kalutara
Kalu Ganga|Kalu Ganga|Ellagawa|m|10.00|10.70|12.20|Ratnapura/Kalutara
Kalu Ganga|Kalu Ganga|Rathnapura|m|5.20|7.50|9.50|Ratnapura
Kalu Ganga|Maguru Ganga|Magura|m|4.00|6.00|7.50|Kalutara
Kalu Ganga|Kuda Ganga|Kalawellawa (Millakanda)|m|5.00|6.50|8.00|Kalutara
Gin Ganga|Gin Ganga|Baddegama|m|3.50|4.00|5.00|Galle
Gin Ganga|Gin Ganga|Thawalama|m|4.00|6.00|7.50|Galle
Nilwala Ganga|Nilwala Ganga|Thalgahagoda|m|1.40|1.70|2.80|Matara
Nilwala Ganga|Nilwala Ganga|Panadugama|m|5.00|6.00|7.50|Matara
Nilwala Ganga|Nilwala Ganga|Pitabeddara|m|4.00|5.00|6.50|Matara
Nilwala Ganga|Urubokka Ganga|Urawa|m|2.50|4.00|6.00|Matara/Hambantota
Walawe Ganga|Walawe Ganga|Moraketiya|m|3.00|5.00|7.00|Ratnapura
Kirindi Oya|Kirindi Oya|Thanamalwila|m|4.00|5.00|5.50|Monaragala
Kirindi Oya|Kirindi Oya|Wellawaya|m|4.40|5.40|5.90|Monaragala
Kirindi Oya|Kuda Oya|Kuda Oya|m|6.90|8.40|8.80|Monaragala
Menik Ganga|Menik Ganga|Katharagama|m|4.00|4.60|6.50|Monaragala
Kumbukkan Oya|Kumbukkan Oya|Nakkala|m|5.00|6.00|7.50|Monaragala
Heda Oya|Heda Oya|Siyambalanduwa|m|4.50|6.00|7.00|Monaragala
Maduru Oya|Maduru Oya|Padiyathalawa|m|4.00|4.50|6.00|Ampara
Mahaweli Ganga|Mahaweli Ganga|Manampitiya|m|3.00|4.30|6.00|Polonnaruwa
Mahaweli Ganga|Mahaweli Ganga|Weraganthota|m|5.00|6.00|8.00|Badulla
Mahaweli Ganga|Mahaweli Ganga|Peradeniya|m|5.00|7.00|9.00|Kandy
Mahaweli Ganga|Mahaweli Ganga|Nawalapitiya|m|3.50|5.00|6.00|Kandy
Mahaweli Ganga|Badulu Oya|Thaldena|m|3.00|4.00|5.00|Badulla
Yan Oya|Yan Oya|Horowpothana|m|6.00|7.50|10.50|Anuradhapura
Maa Oya|Mukunu Oya|Yaka Wewa|m|4.00|5.00|6.00|Kurunegala
Malwathu Oya|Malwathu Oya|Thanthirimale|m|5.00|6.80|7.80|Anuradhapura
Mee Oya|Mee Oya|Galgamuwa|m|4.84|5.94|8.00|Kurunegala
Deduru Oya|Deduru Oya|Moragaswewa|m|4.75|6.00|7.00|Kurunegala/Puttalam
Maha Oya|Maha Oya|Badalgama|m|5.00|6.20|9.60|Gampaha
Maha Oya|Maha Oya|Giriulla|m|5.50|6.50|7.50|Kurunegala
Attanagalu Oya|Attanagalu Oya|Dunamale|m|3.30|4.40|5.50|Gampaha"""
GEOCODE = {"Kalawellawa (Millakanda)": "Kalawellawa", "Rathnapura": "Ratnapura", "Katharagama": "Kataragama", "Yaka Wewa": "Yakawewa"}


def geocode(query):
    params = urllib.parse.urlencode({"q": query, "countrycodes": "lk", "format": "jsonv2", "limit": 1})
    time.sleep(1.2)  # Nominatim usage policy: at most one request per second
    found = json.loads(fetch("https://nominatim.openstreetmap.org/search?" + params))
    return found[0] if found else None


def local_places(areas):
    """Named points to look gauge towns up in: GN division centres and OpenStreetMap populated places."""
    out = defaultdict(list)
    for r in xlsx_rows(cached(CODAB_URL), "lka_admin4"):
        if not (r.get("adm4_name") and r.get("center_lat")):
            continue
        out[norm(r["adm4_name"])].append((float(r["center_lat"]), float(r["center_lon"]), f"centre of {r['adm4_name']} GN division (COD-AB)"))
    archive = zipfile.ZipFile(io.BytesIO(cached(POP_PLACES_URL)))
    for f in json.loads(archive.read([n for n in archive.namelist() if n.endswith(".geojson")][0]))["features"]:
        p, c = f["properties"], shape(f["geometry"]).representative_point()
        for name in {p.get("name"), p.get("name_en"), p.get("name_latin")} - {None}:
            out[norm(name)].append((c.y, c.x, f"{name} ({p.get('place') or 'place'}, OpenStreetMap)"))
    return out


def gauges(areas, previous, places=None):
    known = {r["name"]: r for r in previous}
    records = []
    for line in GAUGES.splitlines():
        basin, river, station, unit, alert, minor, major, expected = line.split("|")
        allowed = set(expected.split("/"))
        slug = re.sub(r"[^a-z0-9]+", "-", station.lower()).strip("-")
        old = known.get(station)
        if old and old.get("latitude") is not None and areas.at(old["latitude"], old["longitude"])[1] in allowed:
            lat, lon, note = old["latitude"], old["longitude"], old["location_note"]
        else:
            lat = lon = None
            note = f"Location not confirmed; expected in {' or '.join(sorted(allowed))} district. Add it from the gauge register."
            town = GEOCODE.get(station, station)
            for key in [norm(town), norm(re.sub(r"\(.*?\)", "", station)), norm(re.sub(r".*\((.*?)\).*", r"\1", station))]:
                hits = [(lat_, lon_, label) for lat_, lon_, label in (places or {}).get(key, []) if areas.at(lat_, lon_)[1] in allowed]
                if hits:
                    lat, lon = round(hits[0][0], 4), round(hits[0][1], 4)
                    note = f"Approximate: {hits[0][2]}, not the surveyed gauge."
                    break
            for query in ([] if lat is not None else [f"{town}, {d}, Sri Lanka" for d in sorted(allowed)] + [f"{town}, Sri Lanka"]):
                hit = geocode(query)
                # A town of the same name elsewhere is worse than no pin: keep it only inside the expected district.
                if hit and areas.at(float(hit["lat"]), float(hit["lon"]))[1] in allowed:
                    lat, lon = round(float(hit["lat"]), 4), round(float(hit["lon"]), 4)
                    note = f"Approximate: {hit['display_name'].split(',')[0]} (OpenStreetMap Nominatim), not the surveyed gauge."
                    break
        area = areas.at(lat, lon)[0] if lat is not None else None
        records.append({"id": "gauge-" + slug, "name": station, "river": river, "basin": basin, "unit": unit,
                        "alert_level": float(alert), "minor_flood": float(minor), "major_flood": float(major),
                        "latitude": lat, "longitude": lon, "location_note": note, "area": area, "district": " / ".join(sorted(allowed)),
                        "thresholds_as_of": GAUGE_AS_OF, "source": "Irrigation Department, Hydrology and Disaster Management Division"})
    return dataset("gauges", records, {"title": "River gauging stations and flood levels", "page": GAUGE_PAGE, "retrieved_on": TODAY,
                   "licence": "Government publication; reused as reference with attribution", "attribution": "Irrigation Department of Sri Lanka",
                   "note": f"Thresholds as printed in the bulletin of {GAUGE_AS_OF}; they are revised from time to time, so check the latest bulletin. Positions are town locations, not gauges."}, False)


# ---------------------------------------------------------------- real: GN divisions
def gn(areas):
    rows = [r for r in xlsx_rows(cached(CODAB_URL), "lka_admin4") if r.get("adm4_pcode") and r.get("center_lat")]
    records = []
    for r in rows:
        lat, lon = round(float(r["center_lat"]), 5), round(float(r["center_lon"]), 5)
        area, district = areas.at(lat, lon)
        records.append({"id": "gn-" + r["adm4_pcode"].lower(), "name": (r.get("adm4_name") or r["adm4_pcode"]).strip(), "name_si": (r.get("adm4_name1") or "").strip() or None,
                        "area": area, "district": (r.get("adm2_name") or "").strip(), "ds_name": (r.get("adm3_name") or "").strip(), "pcode": r["adm4_pcode"], "latitude": lat, "longitude": lon})
    records.sort(key=lambda r: r["pcode"])
    return dataset("gn", records, {"title": "Grama Niladhari divisions", "page": CODAB_PAGE, "download": CODAB_URL, "sha256": hashlib.sha256(cached(CODAB_URL)).hexdigest(),
                   "retrieved_on": TODAY, "licence": "CC BY-IGO", "attribution": "Survey Department of Sri Lanka; OCHA; via HDX COD-AB",
                   "note": "Names and centre points only; the DS division is the one the centre point falls in on the app's simplified outlines."}, False)


# ---------------------------------------------------------------- real: historical disaster events
HAZARD_GROUPS = {"flood": {"FLOOD", "FLASH FLOOD", "URBAN FLOOD", "CYCLONE & FLOOD"}, "landslide": {"LANDSLIDE", "CUTTING FAILURE", "ROCK FALL", "EARTH SLIP", "LAND SUBSIDENCE", "SUBSIDENCE", "RETAINING WF", "WALL COLLAPSE"},
                 "wind": {"STRONG WIND", "GALE", "CYCLONE", "TORNADO", "STORM"}, "drought": {"DROUGHT"}, "coastal": {"TSUNAMI", "SURGE", "TIDAL WAVE", "COASTLINE", "COASTAL EROSION"}}
DISTRICT_ALIASES = {"moneragala": "Monaragala", "mulative": "Mullaitivu", "mullaittivu": "Mullaitivu"}


def history(areas):
    from lastmile.team_accounts import DISTRICTS
    body = cached(DESINVENTAR_URL)
    index = json.loads((ROOT / "web" / "data" / "sri-lanka-areas.json").read_text(encoding="utf-8"))
    by_district = defaultdict(dict)
    for d in index["divisions"]:
        by_district[d["district"]][norm(d["name"])] = d["id"]
    districts = {d.lower(): d for d in DISTRICTS}

    def match(district_name, ds_name):
        district = districts.get(district_name.strip().lower()) or DISTRICT_ALIASES.get(district_name.strip().lower())
        if not district:
            return None, None
        if not ds_name:
            return district, None
        names = by_district[district]
        key = norm(re.sub(r"\(.*?\)", "", ds_name))
        for candidate in (key, key.replace("korale", ""), key.replace("pattu", "")):
            if candidate in names:
                return district, names[candidate]
        close = get_close_matches(key, list(names), n=1, cutoff=0.78) or [k for k in names if k.startswith(key[:7]) or key.startswith(k[:7])][:1]
        return district, names[close[0]] if close else None

    stats = defaultdict(lambda: Counter())
    years = defaultdict(list)
    unmatched = Counter()
    archive = zipfile.ZipFile(io.BytesIO(body))
    for _, el in iterparse(archive.open("DI_export_lka.xml"), events=("end",)):
        if el.tag != "TR":
            continue
        event = (el.findtext("evento") or "").strip().upper()
        if not event:
            el.clear()
            continue
        group = next((g for g, names in HAZARD_GROUPS.items() if event in names), None)
        year = int(el.findtext("fechano") or 0)
        if group and year >= 2000:
            district, ds = match(el.findtext("name1") or "", (el.findtext("name2") or "").strip())
            if district:
                key = ds or district_id(district)
                s_ = stats[key]
                s_[group + "_events"] += 1
                for field, tag in (("deaths", "muertos"), ("people_affected", "afectados"), ("houses_destroyed", "vivdest"), ("houses_damaged", "vivafec")):
                    try:
                        s_[field] += max(0, int(float(el.findtext(tag) or 0)))
                    except ValueError:
                        pass
                years[key].append(year)
            else:
                unmatched[(el.findtext("name1"), el.findtext("name2"))] += 1
        el.clear()
    records = []
    for key, s_ in stats.items():
        name = areas_name(index, key)
        records.append({"id": "hist-" + key, "area": key, "name": name, **{g + "_events": s_[g + "_events"] for g in HAZARD_GROUPS},
                        "deaths": s_["deaths"], "people_affected": s_["people_affected"], "houses_destroyed": s_["houses_destroyed"], "houses_damaged": s_["houses_damaged"],
                        "first_year": min(years[key]), "last_year": max(years[key]), "level": "district" if key.startswith("district-") else "division"})
    records.sort(key=lambda r: r["area"])
    print(f"history: {sum(unmatched.values())} events could not be placed; most common {unmatched.most_common(5)}")
    return dataset("history", records, {"title": "Disaster events since 2000 (DesInventar)", "page": DESINVENTAR_PAGE, "download": DESINVENTAR_URL,
                   "sha256": hashlib.sha256(body).hexdigest(), "retrieved_on": TODAY, "licence": "Public disaster loss database; reuse with attribution",
                   "attribution": "Disaster Management Centre, Sri Lanka; DesInventar (UNDRR)",
                   "note": "Recorded events, not hazard zoning. Records thin out after 2019. District rows hold events the database places only at district level."}, False)


def areas_name(index, key):
    for d in index["divisions"] + index["districts"]:
        if d["id"] == key:
            return d["name"] + (" DS" if key.startswith("ds-") else " District")
    return key


# ---------------------------------------------------------------- real: lifelines and homes for vulnerable people
LIFELINE_TYPES = {"water_works": "water_works", "wastewater_plant": "wastewater", "communications_tower": "telecom", "bridge": "bridge"}


def poi(areas):
    body = cached(POI_URL)
    archive = zipfile.ZipFile(io.BytesIO(body))
    features = json.loads(archive.read([n for n in archive.namelist() if n.endswith(".geojson")][0]))["features"]
    lifelines, vulnerable = [], []
    for f in features:
        p = f["properties"]
        name = (p.get("name_en") or p.get("name") or p.get("name_latin") or "").strip()
        kind = LIFELINE_TYPES.get(p.get("man_made")) or {"police": "police", "fire_station": "fire_station"}.get(p.get("amenity"))
        home = p.get("amenity") in {"social_facility", "childcare"}
        if not (kind or home):
            continue
        if kind == "bridge" and not name:
            continue  # unnamed bridge outlines are too many to be useful here
        c = shape(f["geometry"]).representative_point()
        lat, lon = round(c.y, 5), round(c.x, 5)
        area, _ = areas.at(lat, lon)
        base = {"id": ("life-" if kind else "home-") + p["id"].replace("/", "-"), "name": (name or "Unnamed")[:150], "area": area,
                "latitude": lat, "longitude": lon, "osm_id": p["id"], "source": "OpenStreetMap contributors via HOT export"}
        if kind:
            lifelines.append({**base, "type": kind, "operator": None, "status_note": None})
        else:
            text = name.lower()
            group = "elders" if re.search(r"elder|aged|senior|wadihiti|elders", text) else "children" if re.search(r"child|orphan|children|lama|pre.?school|montessori|day care|daycare", text) or p.get("amenity") == "childcare" else \
                "disability" if re.search(r"disab|blind|deaf|special", text) else "other"
            vulnerable.append({**base, "type": group, "residents": int(p["beds"]) if str(p.get("beds") or "").isdigit() else None, "evacuation_note": None})
    prov = {"page": POI_PAGE, "download": POI_URL, "sha256": hashlib.sha256(body).hexdigest(), "retrieved_on": TODAY,
            "licence": "Open Database License (ODbL) 1.0", "attribution": "© OpenStreetMap contributors"}
    return (dataset("lifelines", sorted(lifelines, key=lambda r: (r["type"], r["name"])), {**prov, "title": "Lifeline and emergency sites from OpenStreetMap",
                    "note": "Water works, wastewater plants, telecom towers, named bridges, police and fire stations as mapped by volunteers. Power substations are not in this export."}, False),
            dataset("vulnerable", sorted(vulnerable, key=lambda r: (r["type"], r["name"])), {**prov, "title": "Homes for elders and children from OpenStreetMap",
                    "note": "Volunteer-mapped social facilities and childcare; types are read from names. Residents are rarely mapped."}, False))


# ---------------------------------------------------------------- fictional: centres, stock, desks
CENTRES = [
    # name, type, area, owner, design, floor m², toilets, water, power, kitchen, step-free, accessible toilet, women & children space, flood, landslide
    ("Kotmale practice hall", "community_hall", "ds-nuwara-eliya-kothmale", "division", 120, 300, 4, "piped", "grid", True, True, False, True, "low", "medium"),
    ("Walapane exercise school hall", "school", "ds-nuwara-eliya-walapane", "division", 250, 700, 8, "well", "grid_and_generator", True, False, False, True, "low", "high"),
    ("Hanguranketa exercise temple hall", "religious", "ds-nuwara-eliya-hanguranketa", "division", 150, 420, 5, "piped", "grid", True, True, False, False, "low", "medium"),
    ("Nuwara Eliya exercise community centre", "community_hall", "ds-nuwara-eliya-nuwara-eliya", "district", 200, 600, 10, "piped", "grid_and_generator", True, True, True, True, "low", "low"),
    ("Udunuwara exercise school", "school", "ds-kandy-udunuwara", "division", 300, 900, 12, "piped", "grid", True, True, False, True, "low", "medium"),
    ("Doluwa exercise kovil hall", "religious", "ds-kandy-doluwa", "division", 80, None, 2, "well", "grid", False, False, False, False, "low", "unknown"),
    ("Kuruvita exercise school hall", "school", "ds-ratnapura-kuruvita", "division", 220, 650, 6, "piped", "grid", True, False, False, True, "high", "low"),
    ("Ratnapura exercise evacuation centre", "purpose_built", "ds-ratnapura-ratnapura", "district", 400, 1500, 20, "piped", "grid_and_generator", True, True, True, True, "medium", "low"),
    ("Eheliyagoda exercise church hall", "religious", "ds-ratnapura-eheliyagoda", "division", 100, 280, 3, "bowser", "grid", False, True, False, False, "medium", "low"),
    ("Kolonnawa exercise school", "school", "ds-colombo-kolonnawa", "division", 500, 1200, 16, "piped", "grid", True, True, False, True, "high", "low"),
    ("Kaduwela exercise community hall", "community_hall", "ds-colombo-kaduwela", "division", 180, 500, 6, "piped", "grid", True, True, True, False, "medium", "low"),
    ("Wattala exercise mosque hall", "religious", "ds-gampaha-wattala", "division", 150, 450, 6, "piped", "grid", True, True, False, True, "medium", "low"),
    ("Ja-Ela exercise school", "school", "ds-gampaha-ja-ela", "division", 350, None, None, "unknown", "unknown", None, None, None, None, "unknown", "low"),
    ("Bulathsinhala exercise school", "school", "ds-kalutara-bulathsinhala", "division", 260, 800, 9, "well", "grid", True, False, False, True, "medium", "medium"),
    ("Baddegama exercise temple hall", "religious", "ds-galle-baddegama", "division", 120, 380, 4, "piped", "grid", True, True, False, False, "high", "low"),
    ("Akuressa exercise school", "school", "ds-matara-akuressa", "division", 240, 720, 8, "piped", "grid_and_generator", True, True, True, True, "medium", "medium"),
]


HALL = {"school": "School Hall", "religious": "Temple Hall", "community_hall": "Community Hall", "purpose_built": "Evacuation Centre", "other": "Safety Centre"}


def more_centres(areas):
    """Two fictional centres in every district that has fewer, so the whole country is covered."""
    index = json.loads((ROOT / "web" / "data" / "sri-lanka-areas.json").read_text(encoding="utf-8"))
    have = Counter(next(d["district"] for d in index["divisions"] if d["id"] == c[2]) for c in CENTRES)
    rows = []
    for district in sorted({d["district"] for d in index["divisions"]}):
        divisions = sorted((d for d in index["divisions"] if d["district"] == district), key=lambda d: d["id"])
        for n in range(max(0, 2 - have[district])):
            d = divisions[(len(divisions) * (n + 1)) // 3]
            rng = random.Random(d["id"])
            kind = rng.choice(["school", "school", "community_hall", "religious", "purpose_built"])
            floor = rng.randrange(250, 1400, 10)
            toilets = rng.randrange(3, 18)
            design = rng.randrange(80, 450, 10)
            rows.append((f"{d['name']} {HALL[kind]}", kind, d["id"], "division" if n == 0 else "district", design, floor, toilets,
                         rng.choice(["piped", "piped", "well", "bowser"]), rng.choice(["grid", "grid", "grid_and_generator"]),
                         rng.random() > .3, rng.random() > .4, rng.random() > .7, rng.random() > .5,
                         rng.choice(["low", "low", "medium", "high"]), rng.choice(["low", "low", "medium"])))
    return rows


def display(name):
    """Plain centre names: no exercise wording; every word capitalised except short joining words."""
    words = name.replace("exercise ", "").split()
    return " ".join(w if w[0].isupper() or w in {"of", "and"} else w.capitalize() for w in words)


def centres(areas):
    records = []
    for name, kind, area, owner, design, floor, toilets, water, power, kitchen, step_free, acc_toilet, wc_space, flood, landslide in CENTRES + more_centres(areas):
        assert area in areas.ids, area
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower().replace("exercise ", "")).strip("-")
        lat, lon = (7.02, 80.59) if slug == "kotmale-practice-hall" else areas.point_in(area, slug)
        records.append({"id": "centre-" + slug, "name": display(name), "type": kind, "area": area, "gn": None, "latitude": lat, "longitude": lon,
                        "owner": owner, "design_capacity": design, "floor_area_m2": floor, "toilets": toilets, "water_source": water, "power": power,
                        "kitchen": kitchen, "step_free": step_free, "accessible_toilet": acc_toilet, "women_children_space": wc_space,
                        "flood_risk": flood, "landslide_risk": landslide, "access_note": None, "contact_point": "Divisional Secretariat duty desk" if owner == "division" else "District DDMCU desk",
                        "notes": None, "checked_at": "2026-10-01", "source": "Demo data", "fictional": True})
    return dataset("centres", records, {"title": "Safety centres (demo)", "licence": "Demo data", "retrieved_on": TODAY,
                   "note": "Demo centres placed in real DS divisions; not real buildings, capacities or approvals."}, True)


ITEMS = [
    # id, name, category, unit, per person, per-person unit, standard note
    ("drinking-water", "Drinking and domestic water", "water", "litres", 15, "L per day", "Sphere: at least 15 L per person per day for drinking, cooking and hygiene."),
    ("water-bowser", "Water bowser", "water", "bowsers", None, None, "Record the tank size in the stock notes."),
    ("purification-tablets", "Water purification tablets", "water", "tablets", None, None, "Follow the product dose per litre."),
    ("dry-rations", "Dry ration pack", "food", "packs", None, None, "Sphere: 2,100 kcal per person per day. Pack contents follow the issuing agency's list (exercise definition)."),
    ("cooking-set", "Cooking utensil set", "food", "sets", None, None, "One per household or communal kitchen (exercise planning note)."),
    ("tarpaulin", "Tarpaulin", "shelter", "pieces", None, None, "Sphere: covered living space of at least 3.5 m² per person."),
    ("family-tent", "Family tent", "shelter", "tents", None, None, "Sphere: covered living space of at least 3.5 m² per person."),
    ("sleeping-mat", "Sleeping mat", "shelter", "pieces", 1, "per person", "Sphere: a sleeping mat or mattress for each person."),
    ("blanket", "Blanket", "shelter", "pieces", 1, "per person", "Sphere: a blanket for each person; more in cold areas."),
    ("mosquito-net", "Mosquito net", "health", "pieces", None, None, "One per sleeping space where mosquito-borne disease is a risk (exercise planning note)."),
    ("hygiene-kit", "Hygiene kit", "hygiene", "kits", None, None, "Sphere: 250 g bathing soap and 200 g laundry soap per person per month."),
    ("first-aid-kit", "First-aid kit", "health", "kits", None, None, "One per centre at least (exercise planning note)."),
    ("generator", "Generator", "equipment", "generators", None, None, "Record the kVA rating in the stock notes."),
    ("inflatable-boat", "Inflatable rescue boat", "rescue", "boats", None, None, "Crew and life jackets go with each boat."),
    ("life-jacket", "Life jacket", "rescue", "pieces", None, None, None),
    ("excavator", "Excavator", "roads", "machines", None, None, None),
    ("backhoe", "Backhoe loader", "roads", "machines", None, None, None),
    ("chainsaw", "Chainsaw", "roads", "pieces", None, None, None),
    ("tipper", "Tipper truck", "transport", "vehicles", None, None, None),
    ("passenger-bus", "Passenger bus", "transport", "vehicles", None, None, "Record the seat count in the stock notes."),
    ("helicopter", "Helicopter", "rescue", "aircraft", None, None, "Record type, seats and hoist in the stock notes; tasking stays with the Air Force."),
    ("rescue-team", "Search and rescue team", "rescue", "teams", None, None, "Record team size and skills (swift water, collapse, medical) in the notes."),
]


def items():
    records = [{"id": i, "name": n, "category": c, "unit": u, "per_person": p, "per_person_unit": pu, "standard": s} for i, n, c, u, p, pu, s in ITEMS]
    return dataset("items", records, {"title": "Relief items and standards", "licence": "Item names are the exercise's own; standards summarise the Sphere Handbook",
                   "attribution": "Sphere Association, The Sphere Handbook (2018)", "retrieved_on": TODAY,
                   "note": "Sphere indicators are minimums for planning, read alongside the full handbook."}, False)


STOCK = [
    # owner, item, quantity, store name, area, status
    ("water", "water-bowser", 6, "NWSDB Nuwara Eliya regional yard", "ds-nuwara-eliya-nuwara-eliya", "ready"),
    ("water", "water-bowser", 4, "NWSDB Ratnapura regional yard", "ds-ratnapura-ratnapura", "ready"),
    ("water", "water-bowser", 3, "NWSDB Kolonnawa depot", "ds-colombo-kolonnawa", "in_use"),
    ("water", "purification-tablets", 20000, "NWSDB Kandy store", "ds-kandy-gangawata-korale", "ready"),
    ("ndrsc", "dry-rations", 500, "NDRSC Kandy district store", "ds-kandy-gangawata-korale", "ready"),
    ("ndrsc", "dry-rations", 300, "NDRSC Ratnapura district store", "ds-ratnapura-ratnapura", "ready"),
    ("ndrsc", "sleeping-mat", 800, "NDRSC Kandy district store", "ds-kandy-gangawata-korale", "ready"),
    ("ndrsc", "blanket", 600, "NDRSC Nuwara Eliya district store", "ds-nuwara-eliya-nuwara-eliya", "ready"),
    ("ndrsc", "hygiene-kit", 600, "NDRSC Colombo central store", "ds-colombo-colombo", "ready"),
    ("ndrsc", "tarpaulin", 400, "NDRSC Colombo central store", "ds-colombo-colombo", "ready"),
    ("ndrsc", "mosquito-net", 500, "NDRSC Galle district store", "ds-galle-galle4gravets", "ready"),
    ("ndrsc", "drinking-water", 12000, "NDRSC Colombo central store", "ds-colombo-colombo", "ready"),
    ("rda", "excavator", 2, "RDA Kandy maintenance yard", "ds-kandy-gangawata-korale", "ready"),
    ("rda", "backhoe", 3, "RDA Nuwara Eliya maintenance yard", "ds-nuwara-eliya-nuwara-eliya", "ready"),
    ("rda", "backhoe", 1, "RDA Ratnapura maintenance yard", "ds-ratnapura-ratnapura", "repair"),
    ("rda", "chainsaw", 10, "RDA Kandy maintenance yard", "ds-kandy-gangawata-korale", "ready"),
    ("rda", "tipper", 5, "RDA Kandy maintenance yard", "ds-kandy-gangawata-korale", "ready"),
    ("dmc", "inflatable-boat", 6, "DMC Colombo emergency store", "ds-colombo-colombo", "ready"),
    ("dmc", "life-jacket", 120, "DMC Colombo emergency store", "ds-colombo-colombo", "ready"),
    ("dmc", "generator", 4, "DMC Colombo emergency store", "ds-colombo-colombo", "ready"),
    ("district", "family-tent", 50, "Nuwara Eliya DDMCU store", "ds-nuwara-eliya-nuwara-eliya", "ready"),
    ("district", "generator", 2, "Ratnapura DDMCU store", "ds-ratnapura-ratnapura", "ready"),
    ("division", "cooking-set", 40, "Kotmale DS store", "ds-nuwara-eliya-kothmale", "ready"),
    ("division", "first-aid-kit", 25, "Kotmale DS store", "ds-nuwara-eliya-kothmale", "ready"),
    # Forces and partners: coordinators keep these; a pledge is not stock until it arrives.
    ("navy", "inflatable-boat", 12, "Navy western command boat store", "ds-colombo-colombo", "ready"),
    ("navy", "life-jacket", 300, "Navy western command boat store", "ds-colombo-colombo", "ready"),
    ("army", "rescue-team", 4, "Army engineer regiment, Kandy", "ds-kandy-gangawata-korale", "ready"),
    ("airforce", "helicopter", 2, "Air Force base helicopter flight", "ds-ratnapura-ratnapura", "ready"),
    ("partner", "hygiene-kit", 500, "Partner NGO warehouse (pledge)", "ds-kandy-gangawata-korale", "pledged"),
    ("partner", "dry-rations", 200, "Private company pledge, Colombo", "ds-colombo-colombo", "pledged"),
]


PROVINCE_HUBS = {"Western": "Colombo", "Central": "Kandy", "Southern": "Galle", "Northern": "Jaffna", "Eastern": "Trincomalee",
                 "North Western": "Kurunegala", "North Central": "Anuradhapura", "Uva": "Badulla", "Sabaragamuwa": "Ratnapura"}


def more_stock(areas):
    """An NDRSC store in every district, and NWSDB and RDA yards in every province's main district."""
    index = json.loads((ROOT / "web" / "data" / "sri-lanka-areas.json").read_text(encoding="utf-8"))
    by_district = defaultdict(list)
    for d in index["divisions"]:
        by_district[d["district"]].append(d)
    def seat(district):
        divisions = by_district[district]
        same = [d for d in divisions if d["name"].lower().startswith(district.lower()[:5])]
        return (same or sorted(divisions, key=lambda d: d["id"]))[0]["id"]
    stored = {(o, a) for o, _, _, _, a, _ in STOCK}
    rows = []
    for district in sorted(by_district):
        rng = random.Random(district)
        area = seat(district)
        if ("ndrsc", area) not in stored:
            rows.append(("ndrsc", "dry-rations", rng.randrange(100, 600, 10), f"NDRSC {district} district store", area, "ready"))
            rows.append(("ndrsc", "sleeping-mat", rng.randrange(150, 900, 10), f"NDRSC {district} district store", area, "ready"))
    for province, district in PROVINCE_HUBS.items():
        rng = random.Random(province)
        area = seat(district)
        if not any(o == "water" and a == area for o, _, _, _, a, _ in STOCK):
            rows.append(("water", "water-bowser", rng.randrange(2, 7), f"NWSDB {province} regional yard", area, "ready"))
        if not any(o == "rda" and a == area for o, _, _, _, a, _ in STOCK):
            rows.append(("rda", "backhoe", rng.randrange(1, 4), f"RDA {province} maintenance yard", area, "ready"))
    return rows


def stock(areas):
    records = []
    for n, (owner, item, quantity, place, area, status) in enumerate(STOCK + more_stock(areas), 1):
        assert area in areas.ids, area
        lat, lon = areas.point_in(area, place)
        holder = {"partner": "Partner relief organisation"}.get(owner)
        records.append({"id": f"stock-{n:03d}", "owner": owner, "holder_name": holder, "item": item, "quantity": quantity, "location_name": place, "area": area,
                        "latitude": lat, "longitude": lon, "status": status, "counted_at": "2026-10-01", "notes": None, "fictional": True})
    return dataset("stock", records, {"title": "Stock and equipment (demo)", "licence": "Demo data", "retrieved_on": TODAY,
                   "note": "Demo quantities and stores; not real agency holdings."}, True)


CONTACTS = [
    # id, organisation, label, area, channel, number, hours, public, fictional, notes
    ("hotline-117", "dmc", "DMC emergency hotline", "national", "hotline", "117", "24 hours", True, False, "Disaster Management Centre public hotline."),
    ("hotline-119", "police", "Police emergency", "national", "hotline", "119", "24 hours", True, False, None),
    ("hotline-1990", "ambulance", "Suwa Seriya ambulance", "national", "hotline", "1990", "24 hours", True, False, "Free pre-hospital ambulance service."),
    ("hotline-110", "fire", "Fire and rescue", "national", "hotline", "110", "24 hours", True, False, None),
    ("desk-dmc-eoc", "dmc", "EOC duty desk", "national", "desk", None, "24 hours during incidents", False, True, "Use LastMile Requests to reach this desk."),
    ("desk-district-nuwara-eliya", "district", "Nuwara Eliya DDMCU desk", "district-nuwara-eliya", "desk", None, "Office hours; 24 hours when activated", False, True, None),
    ("desk-district-ratnapura", "district", "Ratnapura DDMCU desk", "district-ratnapura", "desk", None, "Office hours; 24 hours when activated", False, True, None),
    ("desk-division-kotmale", "division", "Kotmale DS duty desk", "ds-nuwara-eliya-kothmale", "desk", None, "Office hours; 24 hours when activated", False, True, None),
    ("desk-ndrsc-relief", "ndrsc", "NDRSC relief desk", "national", "desk", None, "Office hours", False, True, None),
    ("desk-water-central", "water", "NWSDB Central regional desk", "district-kandy", "desk", None, "24 hours", False, True, None),
    ("desk-rda-central", "rda", "RDA Central maintenance desk", "district-kandy", "desk", None, "Office hours; on call", False, True, None),
]


def contacts():
    from lastmile.team_accounts import DISTRICTS
    have = {c[0] for c in CONTACTS}
    rows = list(CONTACTS) + [(f"desk-district-{re.sub(r'[^a-z0-9]+', '-', d.lower())}", "district", f"{d} DDMCU desk", district_id(d), "desk", None,
                              "Office hours; 24 hours when activated", False, True, None)
                             for d in DISTRICTS if f"desk-district-{re.sub(r'[^a-z0-9]+', '-', d.lower())}" not in have]
    records = [{"id": i, "owner": o, "label": l, "area": a, "channel": c, "number": n, "hours": h, "public": p, "fictional": f, "notes": t}
               for i, o, l, a, c, n, h, p, f, t in rows]
    return dataset("contacts", records, {"title": "Hotlines and duty desks", "retrieved_on": TODAY, "licence": "Public hotline numbers; demo desks",
                   "note": "Only public national hotlines carry numbers. Desks are demo entries without numbers. Never store personal phone numbers here."}, False)


# ---------------------------------------------------------------- communications: templates and media desks
TEMPLATES = [
    ("Flood warning", "sms", "Rising water.", None,
     "FLOOD WARNING {date} {time}: rising water in {areas}. {details} Move valuables up and be ready to leave. Help: {hotline}"),
    ("Evacuate now", "sms", "Leave now.", None,
     "EVACUATE NOW: {areas}. {details} Go to the nearest safety centre or higher ground. Help: {hotline}"),
    ("Landslide warning", "sms", "Slopes and cuttings.", None,
     "LANDSLIDE WARNING: {areas}. {details} Watch for cracks, tilting trees and muddy springs. Leave slopes now. Help: {hotline}"),
    ("Safety centre open", "sms", "Where to go.", None,
     "SAFETY CENTRE OPEN in {areas}: {details} Bring ID, medicines and drinking water. Help: {hotline}"),
    ("Boil drinking water", "sms", "Water supply affected.", None,
     "WATER SAFETY {areas}: {details} Boil drinking water or use bottled water until further notice. Help: {hotline}"),
    ("All clear", "sms", "Danger has passed.", None,
     "ALL CLEAR {areas} {date}: {details} Return only when officials confirm your road and home are safe. Help: {hotline}"),
    ("Press briefing notice", "email", "Invite the media to a briefing.", "Press briefing: {incident}",
     "Dear editor,\n\n{issuer} will brief the media on {incident} ({district} District) on {date}.\n\n{details}\n\nFor questions, call the media desk through {hotline}.\n\n{issuer}"),
    ("Situation update to media", "email", "Regular update to newsrooms.", "Situation update: {incident}, {date} {time}",
     "Dear editor,\n\nThe latest position on {incident} in {district} District, as of {date} {time}:\n\n{details}\n\nPeople who need help should call {hotline}. Please carry official warnings word for word.\n\n{issuer}"),
    ("Media release: situation", "press_release", "Public statement during the response.", "Media release: {incident}",
     "MEDIA RELEASE · {date} {time}\n\n{incident}\n\n{issuer} reports the following for {areas}:\n\n{details}\n\nSafety centres are open for anyone who needs to leave home. Call {hotline} for help. Follow only official warnings.\n\nEnds."),
    ("Media release: all clear", "press_release", "When the danger has passed.", "All clear: {incident}",
     "MEDIA RELEASE · {date} {time}\n\nAll clear for {areas}\n\n{details}\n\nPeople returning home should check for damage, boil drinking water until supplies are tested, and call {hotline} to report hazards.\n\nEnds."),
    ('Official letter', 'letter', 'Letters to other organisations: situation, request, notice.', '{subject}',
     '{issuer}\nRef: {reference}\nDate: {date}\n\nTo: {to}\n\nSubject: {subject}\n\n{body}\n\nPlease acknowledge receipt of this letter in the coordination portal.\n\n{signer}\n{job_title}, {issuer}\nIncident: {incident} · {district} District'),
    ('Request for assistance', 'letter', 'Asking another organisation for supplies, people or equipment.', 'Request for assistance: {subject}',
     '{issuer}\nRef: {reference}\nDate: {date}\n\nTo: {to}\n\nSubject: Request for assistance: {subject}\n\nWe request your assistance as set out below.\n\n{body}\n\nPlease confirm what you can provide, and by when, through the coordination portal.\n\n{signer}\n{job_title}, {issuer}\nIncident: {incident} · {district} District'),
]
MEDIA = [
    ("National television newsroom", "tv", "all", "national"), ("Sinhala television news desk", "tv", "si", "national"),
    ("Tamil television news desk", "tv", "ta", "national"), ("National radio news", "radio", "all", "national"),
    ("Hill country community radio", "radio", "si", "district-kandy"), ("Eastern community radio", "radio", "ta", "district-batticaloa"),
    ("Northern community radio", "radio", "ta", "district-jaffna"), ("Southern community radio", "radio", "si", "district-galle"),
    ("English daily newspaper", "newspaper", "en", "national"), ("Sinhala daily newspaper", "newspaper", "si", "national"),
    ("Tamil daily newspaper", "newspaper", "ta", "national"), ("Online news portal", "online", "en", "national"),
    ("Sinhala online news", "online", "si", "national"), ("Wire news agency", "agency", "en", "national"),
    ("Government information office", "government", "all", "national"),
]


def templates():
    records = [{"id": "tpl-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"), "name": name, "channel": channel, "language": "en",
                "subject": subject, "body": body, "notes": notes} for name, channel, notes, subject, body in TEMPLATES]
    return dataset("templates", records, {"title": "Message templates (demo)", "note": "Written for this prototype; add Sinhala and Tamil versions in the app."}, True)


def media():
    records = [{"id": "media-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"), "name": name, "type": kind, "language": language, "area": area,
                "email": None, "notes": None, "fictional": True} for name, kind, language, area in MEDIA]
    return dataset("media", records, {"title": "Media recipients (demo)", "note": "Generic desks, not real outlets; no addresses."}, True)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    areas = Areas()
    previous = OUT / "gauges.json"
    old_gauges = json.loads(previous.read_text(encoding="utf-8"))["records"] if previous.exists() else []
    lifelines, vulnerable = poi(areas)
    built = {"centres": centres(areas), "stock": stock(areas), "items": items(), "contacts": contacts(),
             "population": population(), "health": health(areas), "gauges": gauges(areas, old_gauges, local_places(areas)),
             "gn": gn(areas), "history": history(areas), "lifelines": lifelines, "vulnerable": vulnerable, "templates": templates(), "media": media()}
    for name, doc in built.items():
        compact = name in {"gn", "health"}  # large lists: one record per line keeps the file small and diffable
        text = ("{" + ",\n".join(f"{json.dumps(k)}:{json.dumps(v, ensure_ascii=False)}" for k, v in doc.items() if k != "records")
                + ',\n"records":[\n' + ",\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in doc["records"]) + "\n]}\n") if compact else json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
        (OUT / f"{name}.json").write_text(text, encoding="utf-8")
        print(f"{name}: {len(doc['records'])} records")


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    main()
