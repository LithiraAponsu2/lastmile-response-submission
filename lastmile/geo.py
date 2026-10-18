"""Place resolution and geometry. Pure code — no AI.

* resolve(): free-text place -> gazetteer id (exact / fuzzy / ambiguous / conflict / unresolved)
* geometry: real boundaries if present in data/boundaries/<region>.geojson,
  otherwise a circle around the gazetteer centroid (clearly marked as approximate)
* GeoJSON + dependency-free SVG map for the console
"""

from __future__ import annotations

import heapq
import json
import math
import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape

from shapely.geometry import Polygon, mapping, shape

from .packs import Pack

DEFAULT_BOUNDARIES_DIR = Path(__file__).resolve().parent.parent / "data" / "boundaries"
FUZZY_MIN = 0.86
FUZZY_MARGIN = 0.06

LEVEL_COLOURS = {"yellow": "#f2c230", "amber": "#f08a24", "orange": "#f08a24", "red": "#d93025"}
DEFAULT_COLOUR = "#7a869a"


# ------------------------------------------------------------ name matching


@lru_cache(maxsize=16384)  # pure; the same gazetteer names are normalised on every resolution
def norm_name(s: str) -> str:
    t = unicodedata.normalize("NFKC", s or "").casefold()
    decomposed = unicodedata.normalize("NFKD", t)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    if stripped.isascii():  # Latin: drop accents; Indic scripts keep their vowel signs
        t = stripped
    return re.sub(r"[\s\-_'’.,()/]", "", t)


@lru_cache(maxsize=16384)
def _collapse(s: str) -> str:
    return re.sub(r"(.)\1+", r"\1", s)


@lru_cache(maxsize=16384)
def _letters(s: str) -> Counter:
    return Counter(s)


def _quick(a: str, b: str) -> float:
    """Upper bound of SequenceMatcher(None, a, b).ratio(): letters in common, ignoring order (difflib's
    quick_ratio, with the letter counts cached). Same formula, so the bound also holds in floating point."""
    total = len(a) + len(b)
    if not total:
        return 1.0
    ca, cb = _letters(a), _letters(b)
    if len(ca) > len(cb):
        ca, cb = cb, ca
    return 2.0 * sum(min(n, cb[ch]) for ch, n in ca.items() if ch in cb) / total


@lru_cache(maxsize=65536)
def _bound(a: str, b: str) -> float:
    """Cheap upper bound of _score(a, b)."""
    if a == b:
        return 1.0
    return max(_quick(a, b), _quick(_collapse(a), _collapse(b)) * 0.995)


@lru_cache(maxsize=65536)  # pure; each query is compared with every gazetteer name, often several times a run
def _score(a: str, b: str) -> float:
    if a == b:
        return 1.0
    return max(SequenceMatcher(None, a, b).ratio(),
               SequenceMatcher(None, _collapse(a), _collapse(b)).ratio() * 0.995)


@dataclass
class Resolution:
    status: str  # exact | fuzzy | ambiguous | conflict | unresolved
    place_id: Optional[str] = None
    score: float = 0.0
    candidates: list[tuple[str, float]] = field(default_factory=list)
    message: str = ""


def _entry_names(g) -> list[str]:
    return [n for n in (norm_name(x) for x in list(g.names.values()) + list(g.aliases)) if n]


def _entry_score(g, q: str) -> float:
    best = 0.0
    for n in _entry_names(g):
        best = max(best, _score(q, n))
    return best


def _ranked(entries: list, q: str, keep: int = 3) -> list[tuple[str, float]]:
    """Gazetteer entries by similarity to q, best first (ties in gazetteer order), exactly as scoring every
    entry and sorting would give for every entry scoring at least the keep-th best. Entries are scored in order
    of a cheap upper bound, and scoring stops once no remaining entry can reach the keep-th best score; the
    remaining ones are left out (callers only look at the top few and at ties with the top)."""
    bounds = sorted(((max((_bound(q, n) for n in _entry_names(g)), default=0.0), i) for i, g in enumerate(entries)),
                    key=lambda t: -t[0])
    exact: list[tuple[float, int]] = []
    best: list[float] = []  # min-heap of the `keep` best exact scores so far
    for ub, i in bounds:
        if len(best) >= keep and ub < best[0]:
            break
        sc = _entry_score(entries[i], q)
        exact.append((sc, i))
        if len(best) < keep:
            heapq.heappush(best, sc)
        elif sc > best[0]:
            heapq.heapreplace(best, sc)
    exact.sort(key=lambda t: (-t[0], t[1]))
    return [(entries[i].id, sc) for sc, i in exact]


def resolve(pack: Pack, as_written: str, parent_hint: Optional[str] = None,
            kind: Optional[str] = None) -> Resolution:
    """Match free text to a gazetteer entry. `kind` ('district'/'ds'/…) restricts the candidates."""
    q = norm_name(as_written)
    if not q:
        return Resolution("unresolved", message="empty place name")
    scored = _ranked([g for g in pack.region.gazetteer if kind in (None, g.kind)], q)
    if not scored:
        return Resolution("unresolved", message=f"no gazetteer entries of kind '{kind}'")
    top_id, top = scored[0]
    if top < FUZZY_MIN:
        return Resolution("unresolved", candidates=scored[:3],
                          message=f"'{as_written}' is not in the gazetteer")

    # hint -> district id (only used to disambiguate / cross-check)
    hint_id: Optional[str] = None
    if parent_hint:
        hres = _resolve_hint(pack, parent_hint)
        hint_id = hres

    ties = [pid for pid, s in scored if s >= top - 1e-9 and s >= FUZZY_MIN]
    if len(ties) > 1:
        if hint_id:
            filtered = [p for p in ties if hint_id == p or hint_id in pack.ancestors(p)]
            if len(filtered) == 1:
                top_id = filtered[0]
                ties = filtered
        if len(ties) > 1:
            return Resolution("ambiguous", candidates=[(p, top) for p in ties],
                              message=f"'{as_written}' matches several places: {', '.join(ties)}")

    if len(ties) == 1 and top < 0.999:
        second = next((s for pid, s in scored if pid != top_id), 0.0)
        if top - second < FUZZY_MARGIN:
            return Resolution("ambiguous", candidates=scored[:3],
                              message=f"'{as_written}' is close to several places")

    if hint_id and top_id != hint_id and hint_id not in pack.ancestors(top_id):
        return Resolution(
            "conflict", place_id=top_id, score=top, candidates=scored[:3],
            message=(f"'{as_written}' resolves to {pack.place_name(top_id, 'en')} which is not in "
                     f"'{parent_hint}' — check the source"),
        )
    status = "exact" if top >= 0.999 else "fuzzy"
    msg = ""
    if status == "fuzzy":
        msg = f"'{as_written}' matched to {pack.place_name(top_id, 'en')} (similarity {top:.2f})"
    return Resolution(status, place_id=top_id, score=top, candidates=scored[:3], message=msg)


def _resolve_hint(pack: Pack, hint: str) -> Optional[str]:
    q = norm_name(hint)
    top = _ranked(list(pack.region.gazetteer), q, keep=1)
    return top[0][0] if top and top[0][1] >= FUZZY_MIN else None


# ---------------------------------------------------------------- geometry


def _circle(lat: float, lon: float, radius_km: float, n: int = 40) -> Polygon:
    dlat = radius_km / 110.574
    dlon = radius_km / (111.320 * max(math.cos(math.radians(lat)), 0.2))
    pts = [(lon + dlon * math.cos(2 * math.pi * i / n), lat + dlat * math.sin(2 * math.pi * i / n))
           for i in range(n)]
    return Polygon(pts)


_boundary_cache: dict[tuple[str, str], dict] = {}


def load_boundaries(region_id: str, directory: Optional[os.PathLike | str] = None) -> dict:
    """gazetteer id -> shapely geometry, from data/boundaries/<region>.geojson if present."""
    d = Path(directory or os.environ.get("LASTMILE_BOUNDARIES_DIR") or DEFAULT_BOUNDARIES_DIR)
    key = (str(d), region_id)
    if key in _boundary_cache:
        return _boundary_cache[key]
    out: dict = {}
    f = d / f"{region_id}.geojson"
    if f.is_file():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            for feat in data.get("features", []):
                pid = (feat.get("properties") or {}).get("id")
                if pid and feat.get("geometry"):
                    out[pid] = shape(feat["geometry"])
        except (OSError, ValueError):
            out = {}
    _boundary_cache[key] = out
    return out


def place_geometry(pack: Pack, pid: str) -> tuple[object, bool]:
    """(geometry, is_approximate). Real boundary if available, else centroid circle."""
    real = load_boundaries(pack.region.id).get(pid)
    if real is not None:
        return real, False
    g = pack.place(pid)
    return _circle(g.lat, g.lon, g.radius_km), True


def area_km2(pack: Pack, place_ids: list[str]) -> float:
    """Approximate area covered by the union of these places (real boundaries if loaded, else centroid circles)."""
    from shapely.ops import unary_union
    geoms = [place_geometry(pack, p)[0] for p in place_ids if pack.has_place(p)]
    if not geoms:
        return 0.0
    u = unary_union(geoms)
    lat = u.centroid.y
    return u.area * 110.574 * 111.320 * max(math.cos(math.radians(lat)), 0.2)


def cap_geometry(pack: Pack, pid: str) -> tuple[str, str]:
    """('polygon'|'circle', CAP string). CAP uses 'lat,lon' order; circle radius in km."""
    geom, approx = place_geometry(pack, pid)
    if approx:
        g = pack.place(pid)
        return "circle", f"{g.lat:.4f},{g.lon:.4f} {g.radius_km:g}"
    geom = geom.simplify(0.001) if geom.geom_type in ("Polygon", "MultiPolygon") else geom
    poly = geom if geom.geom_type == "Polygon" else max(geom.geoms, key=lambda p: p.area)
    coords = list(poly.exterior.coords)
    if coords[0] != coords[-1]:
        coords.append(coords[0])
    return "polygon", " ".join(f"{lat:.5f},{lon:.5f}" for lon, lat in coords)


def level_colour(pack: Pack, level: Optional[int]) -> str:
    s = pack.level_def(level) if level is not None else None
    return LEVEL_COLOURS.get(s.colour if s else "", DEFAULT_COLOUR)


def geojson(pack: Pack, place_levels: dict[str, int]) -> dict:
    feats = []
    for pid, level in place_levels.items():
        geom, approx = place_geometry(pack, pid)
        g = pack.place(pid)
        feats.append({
            "type": "Feature",
            "geometry": mapping(geom),
            "properties": {
                "id": pid, "name": g.names, "kind": g.kind, "level": level,
                "label": {lg: pack.label(level, lg) for lg in pack.langs},
                "colour": level_colour(pack, level), "approximate_geometry": approx,
            },
        })
    return {"type": "FeatureCollection", "features": feats}


def render_svg(pack: Pack, place_levels: dict[str, int], width: int = 560, height: int = 440) -> str:
    """Dependency-free map. Every dynamic string is XML-escaped."""
    margin = 28
    geoms = [(pid, lvl, *place_geometry(pack, pid)) for pid, lvl in place_levels.items()]
    if not geoms:
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">'
                f'<rect width="100%" height="100%" fill="#f4f6f8"/>'
                f'<text x="{width/2}" y="{height/2}" text-anchor="middle" fill="#7a869a" '
                f'font-family="sans-serif" font-size="14">No places resolved yet</text></svg>')
    minx = min(g.bounds[0] for _, _, g, _ in geoms)
    miny = min(g.bounds[1] for _, _, g, _ in geoms)
    maxx = max(g.bounds[2] for _, _, g, _ in geoms)
    maxy = max(g.bounds[3] for _, _, g, _ in geoms)
    pad_x, pad_y = max((maxx - minx) * 0.15, 0.05), max((maxy - miny) * 0.15, 0.05)
    minx, maxx, miny, maxy = minx - pad_x, maxx + pad_x, miny - pad_y, maxy + pad_y
    lat0 = (miny + maxy) / 2
    kx = math.cos(math.radians(lat0))
    span_x, span_y = (maxx - minx) * kx, (maxy - miny)
    scale = min((width - 2 * margin) / span_x, (height - 2 * margin) / span_y)
    ox = (width - span_x * scale) / 2
    oy = (height - span_y * scale) / 2

    def px(lon: float, lat: float) -> tuple[float, float]:
        return (ox + (lon - minx) * kx * scale, oy + (maxy - lat) * scale)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Map of affected areas">',
        f'<rect width="100%" height="100%" fill="#eef2f6" rx="8"/>',
    ]
    # context dots for the rest of the gazetteer
    for g in pack.region.gazetteer:
        if g.id in place_levels:
            continue
        if minx <= g.lon <= maxx and miny <= g.lat <= maxy:
            x, y = px(g.lon, g.lat)
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.5" fill="#b7c0cc"/>')
    # draw larger areas first so small ones stay visible
    for pid, lvl, geom, approx in sorted(geoms, key=lambda t: -t[2].area):
        colour = level_colour(pack, lvl)
        polys = [geom] if geom.geom_type == "Polygon" else list(getattr(geom, "geoms", []))
        for poly in polys:
            pts = " ".join(f"{px(x, y)[0]:.1f},{px(x, y)[1]:.1f}" for x, y in poly.exterior.coords)
            dash = ' stroke-dasharray="4 3"' if approx else ""
            parts.append(f'<polygon points="{pts}" fill="{colour}" fill-opacity="0.45" '
                         f'stroke="{colour}" stroke-width="1.6"{dash}/>')
    for pid, lvl, geom, approx in geoms:
        c = geom.centroid
        x, y = px(c.x, c.y)
        name = escape(pack.place_name(pid, "en") or pid)
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#1d2733"/>')
        parts.append(f'<text x="{x + 6:.1f}" y="{y - 5:.1f}" font-family="sans-serif" font-size="11" '
                     f'fill="#1d2733" stroke="#fff" stroke-width="3" paint-order="stroke">{name} · L{lvl}</text>')
    # legend
    lx, ly = 12, height - 14 - 16 * len(pack.hazard.scale)
    for i, s in enumerate(sorted(pack.hazard.scale, key=lambda s: s.level)):
        col = LEVEL_COLOURS.get(s.colour, DEFAULT_COLOUR)
        y = ly + i * 16
        parts.append(f'<rect x="{lx}" y="{y}" width="12" height="12" fill="{col}" fill-opacity="0.7"/>')
        parts.append(f'<text x="{lx + 18}" y="{y + 10}" font-family="sans-serif" font-size="11" '
                     f'fill="#1d2733">{escape(s.label.get("en", str(s.level)))}</text>')
    if any(a for *_, a in geoms):
        parts.append(f'<text x="{width - 10}" y="{height - 8}" text-anchor="end" font-family="sans-serif" '
                     f'font-size="10" fill="#5b6675">dashed = approximate area (centroid circle)</text>')
    parts.append("</svg>")
    return "".join(parts)


def render_region_svg(pack: Pack, area_levels: dict[str, int], lang: str = "en", exercise_label: str = "",
                      width: int = 640, height: int = 760) -> str:
    """Region-wide danger map: every gazetteer area, affected ones coloured by level, labels in `lang`.

    The digital counterpart of the colour-coded maps agencies publish (e.g. NBRO's per-division landslide map).
    Every dynamic string is XML-escaped.
    """
    margin = 24
    all_geoms = {g.id: place_geometry(pack, g.id) for g in pack.region.gazetteer}
    minx = min(gm.bounds[0] for gm, _ in all_geoms.values())
    miny = min(gm.bounds[1] for gm, _ in all_geoms.values())
    maxx = max(gm.bounds[2] for gm, _ in all_geoms.values())
    maxy = max(gm.bounds[3] for gm, _ in all_geoms.values())
    pad = max(maxx - minx, maxy - miny) * 0.06
    minx, maxx, miny, maxy = minx - pad, maxx + pad, miny - pad, maxy + pad
    kx = math.cos(math.radians((miny + maxy) / 2))
    scale = min((width - 2 * margin) / ((maxx - minx) * kx), (height - 2 * margin - 70) / (maxy - miny))
    ox = (width - (maxx - minx) * kx * scale) / 2
    oy = margin

    def px(lon: float, lat: float) -> tuple[float, float]:
        return (ox + (lon - minx) * kx * scale, oy + (maxy - lat) * scale)

    def poly_svg(geom, fill: str, opacity: float, stroke: str, width_: float, dash: str = "") -> list[str]:
        out = []
        polys = [geom] if geom.geom_type == "Polygon" else list(getattr(geom, "geoms", []))
        for poly in polys:
            pts = " ".join(f"{px(x, y)[0]:.1f},{px(x, y)[1]:.1f}" for x, y in poly.exterior.coords)
            d = f' stroke-dasharray="{dash}"' if dash else ""
            out.append(f'<polygon points="{pts}" fill="{fill}" fill-opacity="{opacity}" stroke="{stroke}" '
                       f'stroke-width="{width_}"{d}/>')
        return out

    title = escape(pack.region.languages[lang].ui_text("map_title") + " – " + pack.region.name)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="{title}">',
             f'<rect width="100%" height="100%" fill="#eef2f6" rx="8"/>']
    # base layer: every district, unaffected ones neutral
    districts = [g for g in pack.region.gazetteer if g.parent is None]
    for g in sorted(districts, key=lambda g: -all_geoms[g.id][0].area):
        lv = area_levels.get(g.id)
        fill = level_colour(pack, lv) if lv else "#d5dce4"
        parts += poly_svg(all_geoms[g.id][0], fill, 0.55 if lv else 0.6, "#ffffff", 1.2)
    # divisions on top, affected ones coloured
    for g in [g for g in pack.region.gazetteer if g.parent is not None]:
        lv = area_levels.get(g.id)
        if lv:
            parts += poly_svg(all_geoms[g.id][0], level_colour(pack, lv), 0.8, "#1d2733", 0.8,
                              "3 2" if all_geoms[g.id][1] else "")
        else:
            x, y = px(g.lon, g.lat)
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2" fill="#8794a3"/>')
    # labels: districts quietly, affected areas prominently, in the reader's language
    for g in districts:
        x, y = px(g.lon, g.lat)
        parts.append(f'<text x="{x:.1f}" y="{y + 3:.1f}" text-anchor="middle" font-family="sans-serif" '
                     f'font-size="10" fill="#4a5563">{escape(pack.place_name(g.id, lang) or g.id)}</text>')
    for pid, lv in sorted(area_levels.items(), key=lambda kv: kv[1]):
        g = pack.place(pid)
        if g.parent is None:
            continue
        x, y = px(g.lon, g.lat)
        parts.append(f'<text x="{x + 7:.1f}" y="{y - 6:.1f}" font-family="sans-serif" font-size="12" '
                     f'font-weight="bold" fill="#1d2733" stroke="#fff" stroke-width="3" paint-order="stroke">'
                     f'{escape(pack.place_name(pid, lang) or pid)}</text>')
    # legend in the reader's language
    ly = height - 22 - 18 * len(pack.hazard.scale)
    for i, s in enumerate(sorted(pack.hazard.scale, key=lambda s: -s.level)):
        y = ly + i * 18
        parts.append(f'<rect x="16" y="{y}" width="14" height="14" fill="{LEVEL_COLOURS.get(s.colour, DEFAULT_COLOUR)}" '
                     f'fill-opacity="0.85"/>')
        parts.append(f'<text x="36" y="{y + 11}" font-family="sans-serif" font-size="12" fill="#1d2733">'
                     f'{escape(s.label.get(lang) or s.label.get("en", str(s.level)))}</text>')
    if any(approx for _, approx in all_geoms.values()):
        parts.append(f'<text x="{width - 12}" y="{height - 10}" text-anchor="end" font-family="sans-serif" font-size="10" '
                     f'fill="#5b6675">Areas are approximate circles until official boundaries are loaded</text>')
    if exercise_label:
        parts.append(f'<text x="{width / 2:.0f}" y="{height / 2:.0f}" text-anchor="middle" font-family="sans-serif" '
                     f'font-size="56" font-weight="bold" fill="#b3261e" fill-opacity="0.16" '
                     f'transform="rotate(-30 {width / 2:.0f} {height / 2:.0f})">{escape(exercise_label)}</text>')
    parts.append("</svg>")
    return "".join(parts)
