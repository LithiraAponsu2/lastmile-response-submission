"""Build the area index used by the status map and the public status page.

Reads the bundled reference boundaries (web/data/sri-lanka-divisions.geojson and
sri-lanka-districts.geojson), gives every Divisional Secretariat (DS) division a stable
ID and its district, simplifies the outlines for fast drawing, and writes:

  web/data/sri-lanka-areas.geojson  DS outlines with {id, name, district}
  web/data/sri-lanka-areas.json     index of districts and DS divisions (ids, names, centres, bounds)

The district of a division is the district containing most of its area. Run again after
refreshing the source boundaries:  python scripts/build_area_index.py
"""
import json
import re
from pathlib import Path

from shapely.geometry import mapping, shape

DATA = Path(__file__).resolve().parent.parent / "web" / "data"
TOLERANCE = 0.0015  # degrees, roughly 150 m: plenty for a national/district status map


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def rounded(geometry):
    def walk(value):
        if isinstance(value, (list, tuple)):
            if value and isinstance(value[0], (int, float)):
                return [round(value[0], 4), round(value[1], 4)]
            return [walk(v) for v in value]
        return value
    out = mapping(geometry)
    return {"type": out["type"], "coordinates": walk(out["coordinates"])}


def main():
    districts = json.loads((DATA / "sri-lanka-districts.geojson").read_text(encoding="utf-8"))["features"]
    divisions = json.loads((DATA / "sri-lanka-divisions.geojson").read_text(encoding="utf-8"))["features"]
    district_shapes = [(f["properties"]["name"], shape(f["geometry"]).buffer(0)) for f in districts]
    index = {"source": "Derived from bundled geoBoundaries reference boundaries; see README.md. Not certified warning boundaries.",
             "districts": [], "divisions": []}
    for name, geom in sorted(district_shapes):
        point = geom.representative_point()
        index["districts"].append({"id": "district-" + slug(name), "name": name, "centre": [round(point.y, 4), round(point.x, 4)],
                                   "bounds": [round(v, 4) for v in (geom.bounds[1], geom.bounds[0], geom.bounds[3], geom.bounds[2])]})
    features, seen = [], set()
    for f in divisions:
        geom = shape(f["geometry"]).buffer(0)
        name = f["properties"]["shapeName"].strip()
        district = max(district_shapes, key=lambda d: d[1].intersection(geom).area)[0]
        area_id = f"ds-{slug(district)}-{slug(name)}"
        assert area_id not in seen, area_id
        seen.add(area_id)
        point = geom.representative_point()
        simple = geom.simplify(TOLERANCE, preserve_topology=True)
        features.append({"type": "Feature", "properties": {"id": area_id, "name": name, "district": district}, "geometry": rounded(simple)})
        index["divisions"].append({"id": area_id, "name": name, "district": district, "centre": [round(point.y, 4), round(point.x, 4)],
                                   "bounds": [round(v, 4) for v in (geom.bounds[1], geom.bounds[0], geom.bounds[3], geom.bounds[2])]})
    index["divisions"].sort(key=lambda d: (d["district"], d["name"]))
    (DATA / "sri-lanka-areas.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")), encoding="utf-8")
    (DATA / "sri-lanka-areas.json").write_text(json.dumps(index, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"{len(index['districts'])} districts, {len(features)} DS divisions")


if __name__ == "__main__":
    main()
