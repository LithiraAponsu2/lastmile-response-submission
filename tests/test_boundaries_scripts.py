from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from lastmile import boundaries, geo

ROOT = Path(__file__).resolve().parent.parent


def square(lon, lat, d=0.05):
    return {"type": "Polygon", "coordinates": [[[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]]]}


def write_fc(path, feats):
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": p, "geometry": g} for p, g in feats]}))


def test_build_boundaries_matches_by_name_and_reports_rest(lk, tmp_path):
    d2 = tmp_path / "adm2.geojson"
    d3 = tmp_path / "adm3.geojson"
    write_fc(d2, [({"ADM2_EN": "Badulla"}, square(81.0, 6.9)), ({"ADM2_EN": "Atlantis"}, square(0, 0))])
    write_fc(d3, [({"ADM3_EN": "Passara", "ADM2_EN": "Badulla"}, square(81.1, 6.9)),
                  ({"ADM3_EN": "Passara", "ADM2_EN": "Badulla"}, square(81.15, 6.9)),     # two parts -> unioned
                  ({"ADM3_EN": "Haldumulla", "ADM2_EN": "Badulla"}, square(80.8, 6.7)),   # alias
                  ({"ADM3_EN": "Passara", "ADM2_EN": "Kandy"}, square(80.6, 7.2))])       # wrong parent -> unmatched
    fc, rep = boundaries.build(lk, [(d2, "district"), (d3, "ds")])
    ids = {f["properties"]["id"] for f in fc["features"]}
    assert ids == {"LK-BD", "LK-BD-PAS", "LK-BD-HAL"}
    assert any(u["name"] == "Atlantis" for u in rep["unmatched"])
    assert any("conflict" in u["why"] for u in rep["unmatched"])
    assert "LK-KY" in rep["missing"]
    pas = next(f for f in fc["features"] if f["properties"]["id"] == "LK-BD-PAS")
    assert pas["geometry"]["type"] in ("Polygon", "MultiPolygon")


def test_boundaries_flow_into_cap_polygon(lk, tmp_path, monkeypatch):
    f = tmp_path / "lk.geojson"
    write_fc(f, [({"id": "LK-BD-PAS"}, square(81.1, 6.9))])
    monkeypatch.setenv("LASTMILE_BOUNDARIES_DIR", str(tmp_path))
    geo._boundary_cache.clear()
    try:
        assert geo.cap_geometry(lk, "LK-BD-PAS")[0] == "polygon"
        assert geo.cap_geometry(lk, "LK-BD-HAL")[0] == "circle"          # no boundary -> circle fallback
    finally:
        geo._boundary_cache.clear()


def test_resolve_kind_filter(lk):
    assert geo.resolve(lk, "Badulla", kind="district").place_id == "LK-BD"
    assert geo.resolve(lk, "Badulla", kind="ds").status == "unresolved"


def test_build_boundaries_cli(tmp_path):
    src = tmp_path / "adm2.geojson"
    write_fc(src, [({"ADM2_EN": "Kandy"}, square(80.6, 7.2))])
    out = tmp_path / "out.geojson"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_boundaries.py"), "--pack", "lk-landslide",
                        "--input", f"{src}:district", "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(out.read_text())["features"][0]["properties"]["id"] == "LK-KY"
    bad = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_boundaries.py"), "--pack", "lk-landslide",
                          "--input", f"{src}:planet"], capture_output=True, text=True)
    assert bad.returncode != 0


def test_live_smoke_dry_run_with_fake_model():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "live_smoke.py"), "--llm", "fake"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "READY" in r.stdout and "1/1 runs reached READY" in r.stdout
