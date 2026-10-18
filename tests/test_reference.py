"""Places & stock: the standing registry seeded from data/reference and edited with history."""
import json

from test_teams import team  # noqa: F401  (fixture)
from test_team_demo import choose

from lastmile.reference import ReferenceStore, SEED_DIR, derive, STANDARDS


def people(team, monkeypatch):
    monkeypatch.setenv("LASTMILE_TEAM_DEMO", "true")
    return {name: choose(team, org, job) for name, (org, job) in {
        "duty": ("dmc", "duty_officer"), "ds": ("division", "divisional_secretary"), "relief_officer": ("division", "relief_officer"),
        "water": ("water", "service_supervisor"), "observer": ("district", "observer")}.items()}


def test_seed_is_complete_labelled_and_sourced():
    for name in ("centres", "stock", "items", "contacts", "population", "health", "gauges", "gn", "history", "lifelines", "vulnerable"):
        doc = json.loads((SEED_DIR / f"{name}.json").read_text(encoding="utf-8"))
        assert doc["records"] and doc["provenance"]
    centres = json.loads((SEED_DIR / "centres.json").read_text(encoding="utf-8"))
    # Exercise centres say so in the name and the record; real layers carry their licence.
    assert centres["fictional"] and all(r["fictional"] and "fictional" not in r["name"].lower() for r in centres["records"])
    # Every district has listed centres and stock, so the whole country shows on the map.
    index = json.loads((SEED_DIR.parent.parent / "web" / "data" / "sri-lanka-areas.json").read_text(encoding="utf-8"))
    district = {d["id"]: d["district"] for d in index["divisions"]}
    stock = json.loads((SEED_DIR / "stock.json").read_text(encoding="utf-8"))["records"]
    assert len({district[r["area"]] for r in centres["records"]}) == 25 == len({district[r["area"]] for r in stock})
    assert "ODbL" in json.loads((SEED_DIR / "health.json").read_text(encoding="utf-8"))["provenance"]["licence"]
    population = json.loads((SEED_DIR / "population.json").read_text(encoding="utf-8"))["records"]
    assert len(population) == 25 and sum(r["population"] for r in population) > 20000000
    contacts = json.loads((SEED_DIR / "contacts.json").read_text(encoding="utf-8"))["records"]
    assert all(r["number"] is None for r in contacts if r["channel"] == "desk")  # no invented phone numbers
    gauges = json.loads((SEED_DIR / "gauges.json").read_text(encoding="utf-8"))["records"]
    assert all(g["alert_level"] <= g["minor_flood"] <= g["major_flood"] for g in gauges)


def test_standard_capacity_takes_the_tightest_limit_and_keeps_unknowns_unknown():
    assert derive("centres", {"design_capacity": 250, "floor_area_m2": 700, "toilets": 8}, {}) == {
        "by": {"design": 250, "space": 200, "toilets": 160}, "capacity": 160, "limited_by": "toilets", "complete": True,
        "water_l_day": 160 * STANDARDS["water_l_per_person_day"]["value"]}
    partial = derive("centres", {"design_capacity": 350, "floor_area_m2": None, "toilets": None}, {})
    assert partial["capacity"] == 350 and partial["complete"] is False
    assert derive("centres", {}, {})["capacity"] is None


def test_owners_edit_their_own_records_with_history_and_versions(team, monkeypatch):
    p = people(team, monkeypatch)
    view = p["ds"].get("/api/teams/reference/centres").json()
    hall = next(r for r in view["records"] if r["id"] == "centre-kotmale-practice-hall")
    assert hall["_can_edit"] and hall["_derived"]["capacity"] == 80 and view["can_add"]
    water_view = p["water"].get("/api/teams/reference/centres").json()
    assert not any(r["_can_edit"] for r in water_view["records"])  # supervisors edit only their own organisation's
    version = view["version"]
    changed = p["ds"].post("/api/teams/reference/centres", json={"version": version, "record": {"id": hall["id"], "toilets": 6}, "reason": "Two toilets repaired"})
    assert changed.status_code == 200, changed.text
    after = changed.json()
    assert after["version"] == version + 1 and next(r for r in after["records"] if r["id"] == hall["id"])["_derived"]["capacity"] == 85
    assert after["history"][0] | {"fields": ["toilets"], "reason": "Two toilets repaired", "action": "update"} == after["history"][0]
    # A stale version, another organisation, a contributor and an observer are all refused.
    assert p["ds"].post("/api/teams/reference/centres", json={"version": version, "record": {"id": hall["id"], "toilets": 7}}).status_code == 409
    assert p["water"].post("/api/teams/reference/centres", json={"version": version + 1, "record": {"id": hall["id"], "toilets": 7}}).status_code == 403
    assert p["relief_officer"].post("/api/teams/reference/centres", json={"version": version + 1, "record": {"id": hall["id"], "toilets": 7}}).status_code == 403
    assert p["observer"].get("/api/teams/reference/centres").status_code == 200
    assert p["observer"].post("/api/teams/reference/centres", json={"version": version + 1, "record": {"id": hall["id"], "toilets": 7}}).status_code == 403
    # New records: own organisation only, a real division, both coordinates inside Sri Lanka.
    new = {"name": "Second Practice Hall", "type": "community_hall", "area": "ds-nuwara-eliya-kothmale", "owner": "division", "design_capacity": 90}
    assert p["ds"].post("/api/teams/reference/centres", json={"version": version + 1, "record": {**new, "owner": "water"}}).status_code == 403
    assert p["ds"].post("/api/teams/reference/centres", json={"version": version + 1, "record": {**new, "area": "ds-nowhere"}}).status_code == 422
    assert p["ds"].post("/api/teams/reference/centres", json={"version": version + 1, "record": {**new, "latitude": 51.5, "longitude": 80.1}}).status_code == 422
    added = p["ds"].post("/api/teams/reference/centres", json={"version": version + 1, "record": new}).json()
    assert added["saved"].startswith("centre-") and added["history"][0]["action"] == "add"
    # Retiring keeps the record, needs a reason, and can be undone.
    url = f"/api/teams/reference/centres/{added['saved']}/retire"
    assert p["ds"].post(url, json={"version": added["version"]}).status_code == 422
    retired = p["ds"].post(url, json={"version": added["version"], "reason": "Building handed back"})
    assert retired.status_code == 200, retired.text
    retired = retired.json()
    assert next(r for r in retired["records"] if r["id"] == added["saved"])["retired"] is True
    restored = p["ds"].post(url, json={"version": retired["version"], "restore": True}).json()
    assert "retired" not in next(r for r in restored["records"] if r["id"] == added["saved"])


def test_coordinators_edit_shared_lists_and_csv_round_trips(team, monkeypatch):
    p = people(team, monkeypatch)
    gauges = p["duty"].get("/api/teams/reference/gauges").json()
    missing = next(g for g in gauges["records"] if g["latitude"] is None)
    assert p["water"].post("/api/teams/reference/gauges", json={"version": gauges["version"], "record": {"id": missing["id"], "latitude": 7.0, "longitude": 80.2}}).status_code == 403
    bad = p["duty"].post("/api/teams/reference/gauges", json={"version": gauges["version"], "record": {"id": missing["id"], "alert_level": 99.0}})
    assert bad.status_code == 422 and "rise" in bad.json()["detail"]
    csv_text = p["duty"].get("/api/teams/reference/stock/export").text
    header, first = csv_text.splitlines()[:2]
    assert header.startswith("id,item,quantity,owner,holder_name") and first.startswith("stock-001,water-bowser,6,water,")
    stock = p["water"].get("/api/teams/reference/stock").json()
    edited = csv_text.splitlines()[0] + "\n" + first.replace(",6,water", ",5,water") + "\n,water-bowser,2,water,,NWSDB Kandy yard,ds-kandy-gangawata-korale,,,ready,2026-10-10,,yes,\n"
    result = p["water"].post("/api/teams/reference/stock/import", json={"version": stock["version"], "csv": edited, "reason": "Monthly count"})
    assert result.status_code == 200, result.text
    assert result.json()["imported"] == {"added": 1, "updated": 1, "unchanged": 0}
    # One bad row stops the whole file, and rows owned by another body are refused.
    other = csv_text.splitlines()[0] + "\n" + next(line for line in csv_text.splitlines() if ",ndrsc," in line).replace(",500,", ",1,") + "\n"
    refused = p["water"].post("/api/teams/reference/stock/import", json={"version": result.json()["version"], "csv": other})
    assert refused.status_code == 422 and "another organisation" in refused.json()["detail"]
    summary = p["observer"].get("/api/teams/reference").json()
    stock_row = next(d for d in summary["datasets"] if d["id"] == "stock")
    seeded = len(json.loads((SEED_DIR / "stock.json").read_text(encoding="utf-8"))["records"])
    assert stock_row["records"] == seeded + 1 and stock_row["version"] == stock["version"] + 1 and not stock_row["can_add"]


def test_first_start_copies_the_seed_and_mirrors_every_change_to_the_bucket(tmp_path):
    saved = []

    class Cloud:
        directory = tmp_path

        def restore(self, prefix):
            saved.append(("restore", prefix))

        def save(self, path):
            saved.append(("save", path.relative_to(tmp_path).as_posix()))

    store = ReferenceStore(tmp_path, cloud=Cloud())
    assert saved[0] == ("restore", "reference/") and ("save", "reference/centres.json") in saved and ("save", "reference/manifest.json") in saved
    manifest = json.loads((tmp_path / "reference" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["datasets"]["health"]["records"] > 500 and manifest["datasets"]["centres"]["fictional"] is True
    saved.clear()
    duty = {"id": "u1", "name": "Duty officer", "organisation": "dmc", "roles": ["coordinator", "contributor"]}
    store.save("items", duty, 1, {"name": "Solar lantern", "category": "equipment", "unit": "pieces"}, "Added for the exercise")
    assert {"reference/items.json", "reference/items.changes.jsonl", "reference/manifest.json"} == {p for _, p in saved}
    # A restart reads the saved list, not the seed.
    again = ReferenceStore(tmp_path)
    assert again.dataset("items")["version"] == 2 and again.changes("items")[0]["after"]["name"] == "Solar lantern"


def test_seed_upload_is_a_dry_run_by_default_and_never_overwrites_edits():
    import sys
    sys.path.insert(0, str(SEED_DIR.parent.parent / "scripts"))
    import upload_reference_seed as upload

    class Blob:
        def __init__(self, store, name):
            self.store, self.name, self.generation = store, name, store.get(name, (None, 0))[1]

        def exists(self):
            return self.name in self.store

        def reload(self):
            self.generation = self.store[self.name][1]

        def download_as_bytes(self, if_generation_match):
            assert if_generation_match == self.store[self.name][1]
            return self.store[self.name][0]

        def upload_from_string(self, body, content_type, if_generation_match):
            assert if_generation_match == self.store.get(self.name, (None, 0))[1]
            self.store[self.name] = (body, if_generation_match + 1)

    class Bucket:
        def __init__(self):
            self.store = {"state/reference/items.json": (json.dumps({"version": 4, "records": []}).encode(), 9),
                          "state/reference/health.json": (json.dumps({"version": 1, "records": []}).encode(), 3)}

        def blob(self, name):
            return Blob(self.store, name)

    bucket = Bucket()
    steps = {s[0]: s[1] for s in upload.main(["--replace", "items", "health"], bucket=bucket)}
    assert steps == {**steps, "centres": "upload", "items": "refuse", "health": "replace"} and len(bucket.store) == 2  # dry run
    upload.main(["--apply", "--replace", "items", "health"], bucket=bucket)
    assert json.loads(bucket.store["state/reference/items.json"][0])["version"] == 4  # edited copy kept
    assert len(json.loads(bucket.store["state/reference/health.json"][0])["records"]) > 500
    assert "state/reference/centres.json" in bucket.store


# Fictional figures in the published layouts; not real readings.
GAUGE_BULLETIN = """Islandwide Water Level & Rainfall Situation in Major Rivers
DATE : 9-Oct-2026 TIME : 6:30 AM
Kelani Ganga Nagalagam Street ft 4.00 5.00 7.00 2.10 2.40 Normal -
Kelani Ganga Hanwella m 6.50 7.50 9.50 7.60 8.05 Minor Flood 41.0
"""
SITREP = """Disaster Management Center - Situation Report as at 1000hrs on 09th October 2026 - exercise
20 Nuwara Eliya 2100 6900 1800 6100 2 1 5 41 4 120 400 3 90 300 10 40 100 340
"""
NBRO = "EXERCISE. NBRO Level 3 (Red) landslide early warning for Kothmale and Walapane DS divisions. Level 2 (Amber): Hanguranketha."


def test_gauge_bulletin_updates_readings_and_reports_turn_into_rows(team, monkeypatch):
    import base64
    from test_team_demo import act
    p = people(team, monkeypatch)
    gauges = p["duty"].get("/api/teams/reference/gauges").json()
    upload = lambda who, text, version: p[who].post("/api/teams/reference/gauges/bulletin", json={"version": version, "file": {"name": "bulletin.txt", "base64": base64.b64encode(text.encode()).decode()}})
    assert upload("water", GAUGE_BULLETIN, gauges["version"]).status_code == 403
    done = upload("duty", GAUGE_BULLETIN, gauges["version"])
    assert done.status_code == 200, done.text
    out = done.json()
    assert out["bulletin"]["readings"] == 2 and out["bulletin"]["issued_at"] == "2026-10-09T06:30:00+05:30"
    hanwella = next(g for g in out["records"] if g["name"] == "Hanwella")
    assert hanwella["_derived"]["state"] == "minor" and hanwella["_derived"]["trend"] == "rising" and hanwella["reading"]["rain_mm"] == 41.0
    assert out["history"][0]["action"] == "bulletin"
    older = GAUGE_BULLETIN.replace("9-Oct-2026", "1-Oct-2026")
    assert upload("duty", older, out["version"]).status_code == 409  # an older bulletin never replaces a newer reading
    # Editing a gauge keeps its reading.
    kept = p["duty"].post("/api/teams/reference/gauges", json={"version": out["version"], "record": {"id": hanwella["id"], "location_note": "Checked"}}).json()
    assert next(g for g in kept["records"] if g["id"] == hanwella["id"])["reading"]["level"] == 8.05
    # The same documents added to an incident are read into rows a person checks.
    doc = p["duty"].post("/api/teams/incidents", json={"title": "Bulletin exercise", "district": "Nuwara Eliya", "participants": ["dmc", "district", "division"]}).json()
    base = "/api/teams/incidents/" + doc["id"]
    doc = act(p["duty"], base, "contribute", {"title": "Exercise bulletins", "text": GAUGE_BULLETIN + SITREP + NBRO, "observed_at": "2026-10-09T06:45:00+05:30"})
    source = doc["sources"][-1]["id"]
    read = p["ds"].get(f"{base}/sources/{source}/read").json()
    assert read["sitrep"]["as_of"] == "2026-10-09T10:00:00+05:30"
    assert read["sitrep"]["rows"][0]["figures"] == {"families_affected": 1800, "people_affected": 6100, "deaths": 2, "missing": 1, "houses_destroyed": 5,
                                                    "houses_damaged": 41, "safety_centres": 3, "people_in_centres": 300}
    assert read["warnings"]["levels"] == [{"level": "red", "areas": ["ds-nuwara-eliya-kothmale", "ds-nuwara-eliya-walapane"]}, {"level": "amber", "areas": ["ds-nuwara-eliya-hanguranketa"]}]
    flood = next(r for r in read["gauges"]["rows"] if r["name"] == "Hanwella")
    assert (flood["state"], flood["warning"], flood["area"]) == ("minor", "amber", "ds-colombo-seethawaka")
    assert p["water"].get(f"{base}/sources/{source}/read").status_code == 403  # not part of the incident


def test_large_lists_filter_by_area_and_search_and_seed_updates_reach_unedited_lists(team, monkeypatch, tmp_path):
    p = people(team, monkeypatch)
    gn = p["ds"].get("/api/teams/reference/gn").json()
    assert gn["partial"] and len(gn["records"]) == 400 and gn["total"] > 14000
    kotmale = p["ds"].get("/api/teams/reference/gn?area=ds-nuwara-eliya-kothmale").json()["records"]
    assert kotmale and all(r["area"] == "ds-nuwara-eliya-kothmale" for r in kotmale)
    assert p["ds"].get("/api/teams/reference/history?q=walapane").json()["records"][0]["landslide_events"] > 0
    template = p["duty"].get("/api/teams/reference/centres/export?template=true").text
    assert template.strip().splitlines() == [template.strip()] and template.startswith("id,name,type,area")
    # Forces' and partners' lines belong to coordinators; a pledge is not ready stock.
    stock = p["duty"].get("/api/teams/reference/stock").json()
    assert any(r["owner"] == "partner" and r["status"] == "pledged" for r in stock["records"])
    assert p["water"].post("/api/teams/reference/stock", json={"version": stock["version"], "record": {"item": "helicopter", "quantity": 1, "owner": "airforce", "location_name": "x", "area": "ds-colombo-colombo", "status": "ready"}}).status_code == 403
    # An unedited list follows a rebuilt seed; an edited one is kept.
    seed = tmp_path / "seed"
    seed.mkdir()
    for name in ("items", "contacts"):
        (seed / f"{name}.json").write_text((SEED_DIR / f"{name}.json").read_text(encoding="utf-8"), encoding="utf-8")
    store = ReferenceStore(tmp_path / "data", seed_dir=seed)
    duty = {"id": "u1", "name": "Duty officer", "organisation": "dmc", "roles": ["coordinator"]}
    store.save("contacts", duty, 1, {"id": "hotline-110", "hours": "Always"})
    for name in ("items", "contacts"):
        doc = json.loads((seed / f"{name}.json").read_text(encoding="utf-8"))
        doc["records"].append({**doc["records"][0], "id": f"{name}-new"})
        (seed / f"{name}.json").write_text(json.dumps(doc), encoding="utf-8")
    again = ReferenceStore(tmp_path / "data", seed_dir=seed)
    assert any(r["id"] == "items-new" for r in again.dataset("items")["records"])
    assert not any(r["id"] == "contacts-new" for r in again.dataset("contacts")["records"])
