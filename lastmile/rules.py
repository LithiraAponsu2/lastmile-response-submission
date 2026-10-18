"""Safety rules that must never depend on a language model.

* place resolution against the gazetteer
* hazard level -> CAP urgency/severity/certainty/responseType (from the pack)
* contradictions between the stated level and the stated advice
* conflict / supersede logic against alerts that are already active
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from . import geo
from .checks import parse_dt
from .packs import Pack
from .schemas import Derived, Facts, Issue, Place, PublishedAlert


def resolve_places(facts: Facts, pack: Pack) -> tuple[Facts, list[Issue]]:
    """Fill Place.canonical using the gazetteer. Returns a new Facts (input untouched)."""
    issues: list[Issue] = []
    out = facts.model_copy(deep=True)
    for p in out.places:
        res = geo.resolve(pack, p.as_written, p.parent_hint)
        if res.status in ("exact", "fuzzy"):
            p.canonical = res.place_id
            if res.status == "fuzzy":
                issues.append(Issue(code="place_fuzzy_matched", severity="warn", message=res.message))
        elif res.status == "conflict":
            p.canonical = None
            issues.append(Issue(code="place_parent_conflict", message=res.message))
        elif res.status == "ambiguous":
            issues.append(Issue(code="ambiguous_place", message=res.message))
        else:
            issues.append(Issue(code="unresolved_place",
                                message=f"{res.message}. Add it to the gazetteer or correct the source."))
        if p.canonical:
            missing = [lg for lg in pack.langs if not pack.place_name(p.canonical, lg)]
            if missing:
                issues.append(Issue(code="gazetteer_name_missing",
                                    message=f"{p.canonical} has no name in {missing}"))
            if not pack.place(p.canonical).verified:
                pass  # surfaced once per run as a pack-level warning, not per place
    return out, issues


def derive(facts: Facts, pack: Pack) -> tuple[Derived, list[Issue]]:
    issues: list[Issue] = []
    if facts.hazard and not pack.hazard.matches(facts.hazard):
        issues.append(Issue(code="hazard_mismatch",
                            message=f"Source looks like hazard '{facts.hazard}', but this pack is '{pack.hazard.id}'"))
    if not facts.places and facts.doc_type != "cancel":
        issues.append(Issue(code="no_places", message="No affected areas were found in the source"))

    by_place: dict[str, int] = {}
    for p in facts.places:
        if p.level is None:
            issues.append(Issue(code="missing_level", message=f"No level stated for {p.as_written}"))
            continue
        if pack.level_def(p.level) is None:
            issues.append(Issue(code="unknown_level",
                                message=f"Level {p.level} for {p.as_written} is not on this pack's scale {pack.levels}"))
            continue
        if not p.canonical:
            continue
        if p.canonical in by_place and by_place[p.canonical] != p.level:
            issues.append(Issue(code="duplicate_place_conflict",
                                message=f"{p.as_written} appears with two different levels"))
        by_place[p.canonical] = max(p.level, by_place.get(p.canonical, 0))

    levels = sorted(set(by_place.values()), reverse=True)
    d = Derived(levels=levels, cap_category=pack.hazard.cap_category)
    if levels:
        top = pack.level_def(levels[0])
        assert top is not None
        d.max_level = levels[0]
        d.urgency, d.severity, d.certainty = top.urgency, top.severity, top.certainty
        d.expected_intent = top.intent
        rt: list[str] = []
        for lv in levels:
            for r in pack.level_def(lv).response_types:  # type: ignore[union-attr]
                if r not in rt:
                    rt.append(r)
        d.response_types = rt
        for pid, lv in by_place.items():
            d.places_by_level.setdefault(lv, []).append(pid)
        for lv in d.places_by_level:
            d.places_by_level[lv].sort()

    # contradiction between the stated level and the stated advice
    if d.max_level is not None and facts.intents:
        intents = set(facts.intents)
        if "evacuate" in intents and d.expected_intent != "evacuate":
            issues.append(Issue(code="advice_level_mismatch",
                                message=(f"Source advises evacuation but the highest level is {d.max_level} "
                                         f"({d.expected_intent}). Possible mislabelled level — check the source.")))
        if d.expected_intent == "evacuate" and "evacuate" not in intents:
            issues.append(Issue(code="advice_level_mismatch",
                                message=(f"Highest level is {d.max_level} (evacuate) but the advice in the source "
                                         "does not tell people to evacuate. Check the source.")))
    # the AI noticed conflicting/unclear content — a human must resolve it in the source
    for a in facts.ambiguities:
        issues.append(Issue(code="source_ambiguity", source="ai", message=a))

    # nested areas
    ids = set(by_place)
    for pid in ids:
        if any(a in ids for a in pack.ancestors(pid)):
            issues.append(Issue(code="overlapping_areas", severity="warn",
                                message=f"{pack.place_name(pid, 'en')} is listed together with its parent area"))
            break
    limit = float(pack.channels.cap.get("max_area_km2") or 0)
    if limit and by_place:
        km2 = geo.area_km2(pack, list(by_place))
        if km2 > limit:
            issues.append(Issue(code="large_target_area", severity="warn",
                                message=(f"The target area is about {km2:,.0f} km² (this channel profile expects at "
                                         f"most {limit:,.0f}). Check that every area is meant to receive this alert.")))
    exp = parse_dt(facts.expires_iso, pack)
    if exp is None and facts.doc_type != "cancel":
        issues.append(Issue(code="no_expiry", message="Source gives no expiry time; this MVP requires one"))
    return d, issues


# ------------------------------------------------------- supersede / cancel


def cap_status(mode: str, allow_actual: bool) -> str:
    """CAP status a run would carry: Actual only for live runs on a deployment that explicitly allows it."""
    return "Actual" if (mode == "live" and allow_actual) else "Exercise"


def level_changes(derived: Derived, active: list[PublishedAlert], superseded: list[str]) -> dict[str, list[int]]:
    """For an Update: each area whose level differs from the alert(s) it replaces -> [old level or 0 if new, new]."""
    old: dict[str, int] = {}
    for a in active:
        if a.identifier in superseded:
            for pid, lv in a.place_levels.items():
                old[pid] = max(lv, old.get(pid, 0))
    new = {pid: lv for lv, pids in derived.places_by_level.items() for pid in pids}
    return {pid: [old.get(pid, 0), lv] for pid, lv in sorted(new.items()) if old.get(pid, 0) != lv}


def cancelled_places(active: list[PublishedAlert], superseded: list[str]) -> list[str]:
    """The areas a cancellation closes: everything the cancelled alert(s) covered."""
    return sorted({pid for a in active if a.identifier in superseded for pid in a.place_levels})


def is_active(a: PublishedAlert, at: datetime, pack: Pack) -> bool:
    if a.cancelled or a.msg_type == "Cancel":
        return False
    sent = parse_dt(a.sent, pack)
    exp = parse_dt(a.expires, pack)
    if sent and sent > at:
        return False
    return exp is None or exp > at


def reconcile(facts: Facts, derived: Derived, pack: Pack, active: list[PublishedAlert],
              at: datetime, status: str = "Exercise",
              target: Optional[str] = None) -> tuple[str, list[str], list[str], list[Issue]]:
    """-> (msg_type, references, superseded identifiers, issues).

    Only alerts with the same CAP `status` are considered: an Exercise can never update, cancel or
    reference an Actual alert, and an Actual alert never treats an Exercise as something to supersede.
    `target` restricts a cancellation to one alert identifier (a retraction of one alert sent in error).
    """
    issues: list[Issue] = []
    new_levels = {pid: lv for lv, pids in derived.places_by_level.items() for pid in pids}
    new_ids = set(new_levels)

    def overlaps(a: PublishedAlert) -> bool:
        if not facts.places:  # cancel with no places -> cancels everything active for this pack
            return True
        for pid in new_ids:
            for q in a.place_levels:
                if pid == q or q in pack.ancestors(pid) or pid in pack.ancestors(q):
                    return True
        return False

    live = [a for a in active
            if a.pack_id == pack.id and a.status == status and is_active(a, at, pack) and overlaps(a)
            and (target is None or a.identifier == target)]
    refs = [f"{a.sender},{a.identifier},{a.sent}" for a in live]

    if facts.doc_type == "cancel":
        if not live:
            issues.append(Issue(code="cancel_without_target",
                                message="No active alert covers these areas, so there is nothing to cancel"))
            return "Alert", [], [], issues
        return "Cancel", refs, [a.identifier for a in live], issues

    if live:
        changes = []
        for a in live:
            for pid, lv in new_levels.items():
                old = a.place_levels.get(pid)
                if old is not None and old != lv:
                    changes.append(f"{pack.place_name(pid, 'en')} L{old}→L{lv}")
        msg = f"Supersedes {', '.join(a.identifier for a in live)}"
        if changes:
            msg += f" (level changes: {', '.join(changes)})"
        issues.append(Issue(code="supersedes_active", severity="warn", message=msg))

        # areas the old alert(s) covered that this update leaves out stop being under any alert
        def covered(q: str) -> bool:
            return any(pid == q or q in pack.ancestors(pid) or pid in pack.ancestors(q) for pid in new_ids)

        dropped = sorted({q for a in live for q in a.place_levels if not covered(q)})
        if dropped:
            names = ", ".join(f"{pack.place_name(q, 'en') or q} (L{max(a.place_levels.get(q, 0) for a in live)})"
                              for q in dropped)
            issues.append(Issue(code="areas_dropped_from_active", severity="warn",
                                message=(f"This update no longer covers {names}; once published they are under no "
                                         "active alert. Confirm with the source that they are safe.")))
        if any(a.place_levels == new_levels and a.expires == facts.expires_iso for a in live):
            issues.append(Issue(code="duplicate_of_active", severity="warn",
                                message="Same areas, levels and expiry as an alert already in force: this re-sends it"))
        return "Update", refs, [a.identifier for a in live], issues

    if facts.doc_type == "update":
        issues.append(Issue(code="update_without_target", severity="warn",
                            message="Marked as an update, but no active alert covers these areas; sending as a new alert"))
    return "Alert", [], [], issues
