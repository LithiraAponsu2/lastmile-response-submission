"""Turn real admin boundaries (e.g. OCHA COD-AB GeoJSON) into data/boundaries/<region>.geojson.

Features are matched to gazetteer entries by NAME with the same resolver the pipeline uses, restricted to
the file's kind (district / ds). Only exact or high-confidence fuzzy matches are kept; everything else is
reported so a human can extend the gazetteer. Several features for one place are unioned.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Optional

from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from . import geo
from .packs import Pack

NAME_PROPS = ["ADM3_EN", "admin3Name_en", "ADM2_EN", "admin2Name_en", "ADM1_EN", "admin1Name_en", "name", "NAME"]
PARENT_PROPS = ["ADM2_EN", "admin2Name_en", "ADM1_EN", "admin1Name_en"]


def _first(props: dict, keys: Iterable[str]) -> Optional[str]:
    for k in keys:
        v = props.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def build(pack: Pack, inputs: list[tuple[Path, str]], name_props: Optional[list[str]] = None,
          parent_props: Optional[list[str]] = None) -> tuple[dict, dict]:
    """-> (FeatureCollection, report). `inputs` is [(geojson path, kind)]."""
    geoms: dict[str, list] = defaultdict(list)
    report = {"matched": [], "fuzzy": [], "unmatched": [], "missing": []}
    for path, kind in inputs:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for feat in data.get("features", []):
            props, geom = feat.get("properties") or {}, feat.get("geometry")
            name = _first(props, name_props or NAME_PROPS)
            if not name or not geom:
                report["unmatched"].append({"file": str(path), "name": name, "why": "no name or geometry"})
                continue
            parent = _first(props, parent_props or PARENT_PROPS) if kind == "ds" else None
            res = geo.resolve(pack, name, parent, kind=kind)
            if res.status == "exact" or (res.status == "fuzzy" and res.score >= 0.92):
                geoms[res.place_id].append(shape(geom))  # type: ignore[index]
                (report["matched"] if res.status == "exact" else report["fuzzy"]).append(
                    {"name": name, "id": res.place_id, "score": round(res.score, 3)})
            else:
                report["unmatched"].append({"file": str(path), "name": name, "why": res.status + ": " + res.message})
    feats = []
    for pid, parts in sorted(geoms.items()):
        merged = unary_union(parts)
        if not merged.is_valid:
            merged = merged.buffer(0)
        feats.append({"type": "Feature", "properties": {"id": pid}, "geometry": mapping(merged)})
    have = set(geoms)
    report["missing"] = [g.id for g in pack.region.gazetteer if g.id not in have]
    return {"type": "FeatureCollection", "features": feats}, report
