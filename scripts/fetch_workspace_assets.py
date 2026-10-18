"""Fetch pinned Leaflet assets and attributed Sri Lanka geographic references.

Run explicitly with network access; no downloads occur at application startup.
"""
import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "LastMile-Prototype/1.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read()


def main():
    vendor = ROOT / "web" / "vendor" / "leaflet"
    vendor.mkdir(parents=True, exist_ok=True)
    files = ["leaflet.js", "leaflet.css", "images/marker-icon.png", "images/marker-icon-2x.png", "images/marker-shadow.png", "images/layers.png", "images/layers-2x.png"]
    for name in files:
        target = vendor / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(fetch("https://unpkg.com/leaflet@1.9.4/dist/" + name))
    (vendor / "LICENSE").write_bytes(fetch("https://raw.githubusercontent.com/Leaflet/Leaflet/v1.9.4/LICENSE"))
    target = ROOT / "web" / "data"
    target.mkdir(exist_ok=True)
    provenance = {"retrieved_on": "2026-10-15", "reference_only": True, "layers": []}
    district = None
    divisions = None
    for level in ("ADM2", "ADM3"):
        url = f"https://www.geoboundaries.org/api/current/gbOpen/LKA/{level}/"
        meta = json.loads(fetch(url))
        data_url = meta["simplifiedGeometryGeoJSON"]
        raw_url = data_url.replace("https://github.com/", "https://raw.githubusercontent.com/").replace("/raw/", "/")
        body = fetch(raw_url)
        if body.startswith(b"version https://git-lfs.github.com"):
            body = fetch(raw_url.replace("https://raw.githubusercontent.com/", "https://media.githubusercontent.com/media/"))
        data = json.loads(body)
        assert data["type"] == "FeatureCollection" and data["features"]
        for feature in data["features"]:
            feature["properties"]["reference_only"] = True
            feature["properties"]["name"] = re.sub(r" District$", "", feature["properties"]["shapeName"])
        name = "sri-lanka-districts.geojson" if level == "ADM2" else "sri-lanka-divisions.geojson"
        (target / name).write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        provenance["layers"].append({"metadata_url": url, "download_url": data_url, "sha256_download": hashlib.sha256(body).hexdigest(), "metadata": meta})
        if level == "ADM2": district = target / name
        else: divisions = target / name
        print(level, len(data["features"]), "features")
    (target / "geography-provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    from lastmile.boundaries import build
    from lastmile.packs import PackRegistry
    fc, report = build(PackRegistry().get("lk-landslide"), [(district, "district"), (divisions, "ds")], name_props=["name", "shapeName"])
    out = ROOT / "data" / "boundaries"
    out.mkdir(exist_ok=True)
    (out / "lk.geojson").write_text(json.dumps(fc, separators=(",", ":")), encoding="utf-8")
    (target / "boundary-matches.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Matched warning areas:", len(fc["features"]), "missing:", report["missing"])


if __name__ == "__main__":
    main()
