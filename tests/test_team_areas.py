"""Area status, official warnings and affected figures: the map and the public status page."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from test_teams import team  # noqa: F401  (fixture)
from test_team_demo import act, choose
from lastmile.team_areas import DIVISIONS, merge
from lastmile.workspace import now_iso

KOTHMALE, WALAPANE, DISTRICT = "ds-nuwara-eliya-kothmale", "ds-nuwara-eliya-walapane", "district-nuwara-eliya"


def setup(team, monkeypatch):
    monkeypatch.setenv("LASTMILE_TEAM_DEMO", "true")
    people = {name: choose(team, org, job) for name, (org, job) in {
        "duty": ("dmc", "duty_officer"), "info": ("dmc", "information_officer"), "engineer": ("water", "field_engineer"),
        "supervisor": ("water", "service_supervisor"), "relief": ("division", "relief_officer"), "observer": ("dmc", "observer"),
        "roads": ("rda", "technical_officer"),
    }.items()}
    doc = people["duty"].post("/api/teams/incidents", json={"title": "Areas exercise", "district": "Nuwara Eliya", "participants": ["dmc", "water", "division", "rda"]}).json()
    base = "/api/teams/incidents/" + doc["id"]
    source = act(people["duty"], base, "contribute", {"title": "Bulletin copy", "text": "EXERCISE bulletin: amber landslide warning for Kothmale.", "observed_at": now_iso(), "recipients": []})["sources"][-1]
    return people, base, source["id"]


def test_area_index_covers_every_division_with_its_district():
    assert len(DIVISIONS) == 330
    assert DIVISIONS[KOTHMALE] == {**DIVISIONS[KOTHMALE], "name": "Kothmale", "district": "Nuwara Eliya"}


def test_services_status_district_rollup_and_permissions(team, monkeypatch):
    p, base, _ = setup(team, monkeypatch)
    act(p["duty"], base, "area_status", {"area": DISTRICT, "sector": "power", "level": "working"})
    later = act(p["engineer"], base, "area_status", {"area": KOTHMALE, "sector": "water", "level": "disrupted", "note": "Mains broken"})
    act(p["duty"], base, "area_status", {"area": KOTHMALE, "sector": "power", "level": "restoring", "expected_by": (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(),
                                         "latitude": 7.04, "longitude": 80.62})
    areas = p["relief"].get(base).json()["coordination"]["picture"]["areas"]
    # A district-wide record applies to each division until a newer division record replaces it.
    assert areas[WALAPANE]["services"]["power"]["level"] == "working"
    assert areas[KOTHMALE]["services"]["power"]["level"] == "restoring" and areas[KOTHMALE]["services"]["power"]["point"] == [7.04, 80.62]
    assert areas[KOTHMALE]["services"]["water"] == {**areas[KOTHMALE]["services"]["water"], "organisation": "water", "note": "Mains broken", "public": False, "stale": False}
    assert later["events"][-1]["detail"] == "water: Water supply in Kothmale DS, Nuwara Eliya · Not working"
    act(p["observer"], base, "area_status", {"area": KOTHMALE, "sector": "water", "level": "working"}, 403)
    act(p["engineer"], base, "area_status", {"area": KOTHMALE, "sector": "water", "level": "working", "public": True}, 403)  # only leads publish
    act(p["duty"], base, "area_status", {"area": "ds-nowhere", "sector": "water", "level": "working"}, 422)
    act(p["duty"], base, "area_status", {"area": KOTHMALE, "sector": "water", "level": "fine"}, 422)
    act(p["duty"], base, "area_status", {"area": KOTHMALE, "sector": "roads", "level": "disrupted", "latitude": 51.5, "longitude": 0.1}, 422)


def test_official_warnings_need_a_coordinator_a_source_and_expire(team, monkeypatch):
    p, base, source = setup(team, monkeypatch)
    now = datetime.now(timezone.utc)
    warning = {"hazard": "landslide", "level": "amber", "areas": [KOTHMALE, WALAPANE], "issuer": "nbro", "issued_at": now.isoformat(),
               "valid_until": (now + timedelta(hours=12)).isoformat(), "advice": "Be alert and ready to move.", "source_id": source}
    act(p["supervisor"], base, "warning", warning, 403)
    act(p["duty"], base, "warning", {**warning, "source_id": None}, 422)
    act(p["duty"], base, "warning", {**warning, "valid_until": (now - timedelta(hours=1)).isoformat()}, 422)
    act(p["duty"], base, "warning", warning)
    act(p["duty"], base, "warning", {**warning, "hazard": "rain", "level": "red", "areas": [DISTRICT], "issuer": "met", "valid_until": None})
    doc = p["relief"].get(base).json()
    kothmale = doc["coordination"]["picture"]["areas"][KOTHMALE]["warnings"]
    assert [w["level"] for w in kothmale] == ["red", "amber"]  # highest first; district warnings reach every division
    assert doc["coordination"]["picture"]["areas"]["ds-nuwara-eliya-hanguranketa"]["warnings"][0]["issuer"] == "met"
    rain = next(w for w in doc["coordination"]["picture"]["warnings"] if w["hazard"] == "rain")
    assert rain["public"]  # an official warning is public by default
    act(p["duty"], base, "warning_end", {"warning_id": rain["id"], "reason": "Met lifted the warning"})
    act(p["duty"], base, "warning_end", {"warning_id": rain["id"], "reason": "Again"}, 409)
    assert [w["level"] for w in p["relief"].get(base).json()["coordination"]["picture"]["areas"][KOTHMALE]["warnings"]] == ["amber"]


def test_figures_come_from_administrative_bodies_and_add_up(team, monkeypatch):
    p, base, source = setup(team, monkeypatch)
    act(p["relief"], base, "area_figures", {"area": KOTHMALE, "as_of": now_iso(), "families_affected": 10, "people_affected": 40, "source_id": source})
    act(p["relief"], base, "area_figures", {"area": KOTHMALE, "as_of": now_iso(), "people_affected": 55, "deaths": 0})
    act(p["engineer"], base, "area_figures", {"area": KOTHMALE, "as_of": now_iso(), "people_affected": 1}, 403)
    act(p["relief"], base, "area_figures", {"area": "ds-nowhere", "as_of": now_iso(), "people_affected": 1}, 422)
    act(p["relief"], base, "area_figures", {"area": KOTHMALE, "as_of": now_iso()}, 422)
    act(p["relief"], base, "area_figures", {"area": KOTHMALE, "as_of": now_iso(), "people_affected": -1}, 422)
    figures = p["duty"].get(base).json()["coordination"]["picture"]["areas"][KOTHMALE]["figures"]
    assert figures["values"] == {"families_affected": 10, "people_affected": 55, "deaths": 0}  # newest value per field; unknown stays absent
    assert p["duty"].get(base).json()["coordination"]["picture"]["totals"] == {"families_affected": 10, "people_affected": 55, "deaths": 0}
    # A district's official total (a situation-report row) replaces the sum of its divisions, so nothing counts twice.
    act(p["relief"], base, "area_figures", {"area": DISTRICT, "as_of": now_iso(), "people_affected": 900, "source_id": source})
    assert p["duty"].get(base).json()["coordination"]["picture"]["totals"] == {"families_affected": 10, "people_affected": 900, "deaths": 0}
    a = {"as_of": "2026-01-01T00:00:00+00:00", "centres": [], "warnings": [], "areas": {KOTHMALE: {"services": {}, "warnings": [], "figures": {"values": {"people_affected": 5}, "as_of": {"people_affected": "x"}, "by": {}}}}}
    assert merge([a, a])["areas"][KOTHMALE]["figures"]["values"] == {"people_affected": 10}


def test_public_page_shows_only_published_records(team, monkeypatch):
    p, base, source = setup(team, monkeypatch)
    internal = act(p["engineer"], base, "area_status", {"area": KOTHMALE, "sector": "water", "level": "disrupted", "note": "Internal crew note"})
    act(p["duty"], base, "warning", {"hazard": "landslide", "level": "red", "areas": [KOTHMALE], "issuer": "nbro", "issued_at": now_iso(), "advice": "Evacuate now.", "source_id": source, "public": False})
    public = TestClient(team["app"])
    assert public.get("/api/public/status").json()["areas"] == {}
    status_id = internal["coordination"]["picture"]["areas"][KOTHMALE]["services"]["water"]["id"]
    act(p["engineer"], base, "publish", {"record_id": status_id, "public": True}, 403)
    act(p["info"], base, "publish", {"record_id": status_id, "public": True})  # the public information officer approves what the public sees
    out = public.get("/api/public/status").json()
    assert out["exercise"] and out["incidents"][0]["title"] == "Areas exercise"
    assert out["areas"][KOTHMALE]["services"]["water"]["level"] == "disrupted" and out["areas"][KOTHMALE]["warnings"] == []
    assert out["vocabulary"]["sectors"]["water"] == "Water supply"
    assert public.get("/public").status_code == 200 and public.get("/status", follow_redirects=False).headers["location"] == "/public"


def test_practice_case_paints_a_district_picture(team, monkeypatch):
    monkeypatch.setenv("LASTMILE_TEAM_DEMO", "true")
    duty = choose(team, "dmc", "duty_officer")
    doc = duty.post("/api/teams/practice", json={}).json()
    picture = doc["coordination"]["picture"]
    assert {w["issuer"] for w in picture["warnings"]} == {"nbro", "met"}
    assert picture["areas"][KOTHMALE]["services"]["water"]["level"] == "disrupted" and picture["areas"][KOTHMALE]["figures"]["values"]["people_affected"] == 410
    assert picture["centres"][0]["public"] and picture["centres"][0]["capacity"] == 100
    board = duty.get("/api/teams/dashboard").json()
    assert KOTHMALE in board["picture"]["areas"] and board["vocabulary"]["levels"]["restoring"] == "Being restored"
    out = TestClient(team["app"]).get("/api/public/status").json()
    assert "food" not in out["areas"][KOTHMALE]["services"]  # left unpublished in the fixture
    assert out["centres"][0]["name"] == "Kotmale Practice Hall"


def test_map_layers_upload_publish_and_remove(team, monkeypatch):
    p, base, source = setup(team, monkeypatch)
    extent = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"name": "Exercise flood extent"},
              "geometry": {"type": "Polygon", "coordinates": [[[80.58, 7.01], [80.60, 7.01], [80.60, 7.03], [80.58, 7.01]]]}}]}
    layer = {"name": "Kotmale exercise flood extent", "kind": "flood_extent", "issuer": "Copernicus EMS (exercise copy)", "observed_at": now_iso(), "geojson": extent, "source_id": source}
    act(p["relief"], base, "map_layer", {**layer, "geojson": {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [2.35, 48.85]}}]}}, 422)  # outside Sri Lanka
    act(p["relief"], base, "map_layer", {**layer, "kind": "rumour"}, 422)
    doc = act(p["relief"], base, "map_layer", layer)
    meta = doc["coordination"]["picture"]["layers"][0]
    assert meta["features"] == 1 and "geojson" not in meta and all("geojson" not in x for x in doc["coordination"]["map_layers"])
    geometry = p["duty"].get(f"{base}/layers/{meta['id']}").json()
    assert geometry["features"][0]["properties"] == {"label": "Exercise flood extent"}
    public = TestClient(team["app"])
    assert public.get(f"/api/public/layers/{meta['id']}").status_code == 404  # not published yet
    act(p["info"], base, "publish", {"record_id": meta["id"], "public": True})
    assert public.get(f"/api/public/layers/{meta['id']}").status_code == 200
    assert public.get("/api/public/status").json()["layers"][0]["name"] == "Kotmale exercise flood extent"
    act(p["engineer"], base, "map_layer_remove", {"layer_id": meta["id"], "reason": "Superseded"}, 403)
    act(p["relief"], base, "map_layer_remove", {"layer_id": meta["id"], "reason": "Superseded by a newer extent"})
    assert public.get(f"/api/public/layers/{meta['id']}").status_code == 404
