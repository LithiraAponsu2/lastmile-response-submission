"""The public side: what is in force, where, at a given time. Read-only, no login, no personal data.

Builds the digital version of an agency's colour-coded danger map from published alerts (all hazards of a region
together, each area at its highest level), plus per-area lookup and a CAP Atom feed for aggregators.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from xml.sax.saxutils import escape

from . import checks, geo, rules
from .packs import UI_DEFAULTS, Pack, PackRegistry
from .publish import reference
from .store import Store


def region_packs(registry: PackRegistry, region: str) -> list[Pack]:
    return [p for p in registry.all() if p.region.id == region]


def regions(registry: PackRegistry) -> list[dict]:
    seen: dict[str, dict] = {}
    for p in registry.all():
        r = seen.setdefault(p.region.id, {"id": p.region.id, "name": p.region.name,
                                          "languages": {k: v.native_name or v.name for k, v in p.region.languages.items()},
                                          "hazards": [], "levels": [], "areas": [],
                                          "ui": {lg: {k: m.ui_text(k) for k in UI_DEFAULTS}
                                                 for lg, m in p.region.languages.items()}})
        r["hazards"].append({"pack": p.id, "name": p.hazard.name})
        if not r["areas"]:
            r["levels"] = [{"level": s.level, "colour": geo.level_colour(p, s.level), "label": s.label}
                           for s in sorted(p.hazard.scale, key=lambda s: s.level)]
            r["areas"] = [{"id": g.id, "kind": g.kind, "parent": g.parent, "names": g.names}
                          for g in p.region.gazetteer]
    return list(seen.values())


def situation(registry: PackRegistry, store: Store, region: str, at: datetime, show_exercise: bool) -> dict:
    packs = {p.id: p for p in region_packs(registry, region)}
    if not packs:
        raise KeyError(region)
    any_pack = next(iter(packs.values()))
    alerts, area_levels, exercise = [], {}, False
    for a in store.active_alerts():
        pack = packs.get(a.pack_id)
        if pack is None or not rules.is_active(a, at, pack) or a.msg_type == "Cancel":
            continue
        if a.status != "Actual" and not show_exercise:
            continue
        exercise |= a.status != "Actual"
        run = store.get(a.run_id)
        drafts = run.drafts if run else {}
        for pid, lv in a.place_levels.items():
            area_levels[pid] = max(lv, area_levels.get(pid, 0))
        alerts.append({
            "identifier": a.identifier, "reference": reference(a.identifier), "status": a.status,
            "msg_type": a.msg_type, "pack": a.pack_id, "event": pack.hazard.event, "sent": a.sent,
            "expires": a.expires,
            "expires_text": {lg: checks.fmt_human(a.expires, pack, lg) for lg in pack.langs},
            "place_levels": a.place_levels,
            "headline": {lg: d.headline for lg, d in drafts.items()},
            "instruction": {lg: d.instruction for lg, d in drafts.items()},
        })
    published = [a for a in store.published if a.pack_id in packs]
    return {"region": region, "at": at.isoformat(), "exercise": exercise, "alerts": alerts,
            "area_levels": area_levels,
            "latest_sent": max((a.sent for a in published), default=None),
            "levels": {str(s.level): {"label": s.label, "colour": geo.level_colour(any_pack, s.level),
                                      "advice": any_pack.hazard.actions.get(s.intent, {})}
                       for s in any_pack.hazard.scale}}


def area_status(pack: Pack, sit: dict, area: str) -> dict:
    """Level that applies to `area`: alerts on it or on a parent; plus any affected sub-areas."""
    if not pack.has_place(area):
        raise KeyError(area)
    lv = max([l for pid, l in sit["area_levels"].items() if pid == area or pid in pack.ancestors(area)], default=0)
    subs = {pid: l for pid, l in sit["area_levels"].items() if area in pack.ancestors(pid)}
    refs = [a["reference"] for a in sit["alerts"]
            if any(pid == area or pid in pack.ancestors(area) or area in pack.ancestors(pid) for pid in a["place_levels"])]
    return {"area": area, "level": lv or None, "sub_areas": subs, "references": refs}


def atom_feed(registry: PackRegistry, store: Store, base_url: str, at: datetime, show_exercise: bool) -> str:
    """Atom feed of alerts in force, each linking to its CAP file (the shape alert aggregators poll)."""
    entries = []
    for r in regions(registry):
        sit = situation(registry, store, r["id"], at, show_exercise)
        for a in sit["alerts"]:
            title = next(iter(a["headline"].values()), a["identifier"])
            entries.append(
                f"<entry><id>urn:lastmile:{escape(a['identifier'])}</id><title>{escape(title)}</title>"
                f"<updated>{escape(a['sent'])}</updated>"
                f'<link rel="alternate" type="application/cap+xml" href="{escape(base_url)}/feeds/cap/'
                f'{escape(a["identifier"])}.xml"/><summary>{escape(a["status"])} {escape(a["msg_type"])}'
                f"</summary></entry>")
    return ('<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom">'
            f"<id>{escape(base_url)}/feeds/alerts.atom</id><title>LastMile Alert – warnings in force</title>"
            f"<updated>{escape(at.isoformat())}</updated>" + "".join(entries) + "</feed>")


def find_cap(store: Store, identifier: str, show_exercise: bool) -> Optional[str]:
    a = next((x for x in store.published if x.identifier == identifier), None)
    if a is None or (a.status != "Actual" and not show_exercise):
        return None
    run = store.get(a.run_id)
    return run.cap_xml if run else None
