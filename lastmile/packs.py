"""Pluggable packs: region (where/languages/sender), hazard (levels/wording), channels (limits).

A *bundle* names one of each. Adding a country or a hazard = adding YAML, no code.
"""

from __future__ import annotations

import os
import re
import string
from datetime import timedelta, timezone
from functools import cached_property
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field, model_validator

DEFAULT_PACKS_DIR = Path(__file__).resolve().parent.parent / "packs"
TEMPLATE_SLOTS = {
    "event", "top_label", "top_places", "top_places_sms", "by_level",
    "measures", "actions", "top_action", "valid_until", "valid_until_sms",
}
TEMPLATE_KEYS = ("headline", "description", "instruction", "sms", "spoken")
UI_DEFAULTS = {
    "map_title": "Danger map",
    "no_alerts": "No warnings are in force for this region at this time.",
    "in_force": "Warnings in force",
    "find_area": "Find your area",
    "your_areas": "Your areas",
    "what_to_do": "What to do",
    "reference": "Reference",
    "check_at": "Check it is genuine at",
    "valid_until": "Valid until",
    "no_warning_here": "No warning for this area.",
    "exercise_tag": "EXERCISE",
    "exercise_banner": "EXERCISE: these are drills, not real warnings.",
    "why_receiving": "You receive this because you subscribed to warnings for these areas.",
    "unsubscribe": "Unsubscribe (deletes your email address)",
    "confirm_subject": "Confirm your warning emails",
    "confirm_intro": "Please confirm that you want warning emails for: {areas}. Open this link to confirm:",
    "consent_label": "What you agree to",
    "signin_subject": "Your sign-in link",
    "signin_intro": "Open this link within 30 minutes to manage your warning emails:",
    "ignore_if_not_you": "If you did not ask for this, ignore this email; nothing will be sent to you.",
}


class PackError(ValueError):
    pass


class LangMeta(BaseModel):
    name: str
    native_name: str = ""   # the language's name in its own script, for language pickers people must recognise
    cap_lang: str
    tts_lang: str = ""
    script: str = "Latin"
    # prefixed to every SMS and voice payload of an alert that is not CAP status Actual (drills look like drills)
    exercise_prefix: str = "EXERCISE - NOT A REAL ALERT."
    # how people read a date and time in this language (alerts must not say "2025-11-28 18:00")
    weekdays: list[str] = Field(default_factory=lambda: [
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])
    months: list[str] = Field(default_factory=lambda: [
        "January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
        "November", "December"])
    datetime_format: str = "{weekday} {day} {month} {year}, {time}"
    sms_datetime_format: str = "{day}/{month_num} {time}"   # SMS has no room for names; day/month is read everywhere
    # short phrases code adds around the model's text
    more_areas: str = "and other areas"          # when a list is cut short (SMS, headline)
    update_word: str = "UPDATE"                  # starts the headline and SMS of an Update
    now_at: str = "{place} is now at {label}"    # one per area whose level changed in an Update
    help_sms: str = "Help: {hotline}"            # appended to the SMS when the region has a hotline
    help_voice: str = "For help, call {hotline}."
    ref_label: str = "Ref"                       # SMS reference, so a screenshot can be checked
    # strings for the public map page and emails; English defaults, override per language in the region pack
    ui: dict[str, str] = Field(default_factory=dict)

    def ui_text(self, key: str) -> str:
        return self.ui.get(key) or UI_DEFAULTS.get(key, key)

    @model_validator(mode="after")
    def _check_calendar(self) -> "LangMeta":
        if len(self.weekdays) != 7 or len(self.months) != 12:
            raise ValueError("weekdays needs 7 names (Monday first) and months 12")
        return self


class Authority(BaseModel):
    sender_id: str
    sender_name: str = ""
    web: str = ""
    sms_sender: str = ""   # short issuer name that starts every SMS (e.g. the registered sender ID)
    hotline: str = ""      # public emergency number added to SMS and voice payloads


class GazEntry(BaseModel):
    id: str
    kind: str = "district"
    parent: Optional[str] = None
    names: dict[str, str]
    aliases: list[str] = Field(default_factory=list)
    lat: float
    lon: float
    radius_km: float = 10.0
    verified: bool = False


class Region(BaseModel):
    id: str
    name: str
    timezone: str
    utc_offset: str
    verified: bool = False
    verification_note: str = ""
    authority: Authority
    languages: dict[str, LangMeta]
    gazetteer: list[GazEntry]

    @model_validator(mode="after")
    def _check(self) -> "Region":
        ids = [g.id for g in self.gazetteer]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate gazetteer ids")
        known = set(ids)
        for g in self.gazetteer:
            if g.parent and g.parent not in known:
                raise ValueError(f"gazetteer {g.id}: unknown parent {g.parent}")
        if not re.fullmatch(r"[+-]\d\d:\d\d", self.utc_offset):
            raise ValueError("utc_offset must look like +05:30")
        return self


class ScaleLevel(BaseModel):
    level: int
    colour: str = ""
    urgency: str
    severity: str
    certainty: str
    response_types: list[str]
    intent: str
    label: dict[str, str]


class Measure(BaseModel):
    unit: str
    units_accepted: list[str] = Field(default_factory=list)
    phrase: dict[str, str]


class CancelTemplates(BaseModel):
    headline: str
    sms: str
    spoken: str
    # retraction of an alert that was sent in error (falls back to the plain cancel wording if absent)
    error_headline: Optional[str] = None
    error_sms: Optional[str] = None
    error_spoken: Optional[str] = None


class LangTemplates(BaseModel):
    headline: str
    description: str
    instruction: str
    sms: str
    spoken: str
    cancel: CancelTemplates


class Hazard(BaseModel):
    id: str
    name: str
    reviewed: bool = False
    issuer: str = ""
    cap_category: str = "Geo"
    event: dict[str, str]
    scale: list[ScaleLevel]
    measures: dict[str, Measure] = Field(default_factory=dict)
    actions: dict[str, dict[str, str]]
    templates: dict[str, LangTemplates]
    keywords: list[str] = Field(default_factory=list)  # words that name this hazard (for the hazard_mismatch check)

    def matches(self, observed: str) -> bool:
        """Does the hazard the Intake model saw in the bulletin name this pack's hazard?"""
        obs = " ".join(observed.casefold().replace("-", " ").replace("_", " ").split())
        if not obs:
            return True  # nothing reported: unknown, not a contradiction
        names = {self.id.casefold(), *self.id.casefold().split("-"), *(k.casefold() for k in self.keywords)}
        return obs in names or any(k and (k in obs or obs in k) for k in names if len(k) >= 4)


class Channels(BaseModel):
    id: str
    name: str = ""
    cap: dict = Field(default_factory=lambda: {"headline_max": 160})
    sms: dict = Field(default_factory=lambda: {"max_segments": 3})
    voice: dict = Field(default_factory=lambda: {"max_bytes": 4000})
    approvals: dict = Field(default_factory=lambda: {"required_replay": 1, "required_live": 2})

    @property
    def headline_max(self) -> int:
        return int(self.cap.get("headline_max", 160))

    @property
    def sms_max_segments(self) -> int:
        return int(self.sms.get("max_segments", 3))

    @property
    def voice_max_bytes(self) -> int:
        return int(self.voice.get("max_bytes", 4000))

    def required_approvals(self, mode: str) -> int:
        key = "required_live" if mode == "live" else "required_replay"
        return max(1, int(self.approvals.get(key, 1)))


class Pack(BaseModel):
    """A resolved bundle: one region + one hazard + one channel pack."""

    id: str
    name: str
    region: Region
    hazard: Hazard
    channels: Channels

    # ------------------------------------------------------------ helpers
    @property
    def langs(self) -> list[str]:
        return list(self.region.languages.keys())

    @cached_property
    def tz(self) -> timezone:
        sign = -1 if self.region.utc_offset[0] == "-" else 1
        hh, mm = self.region.utc_offset[1:].split(":")
        return timezone(sign * timedelta(hours=int(hh), minutes=int(mm)))

    @cached_property
    def _gaz(self) -> dict:
        return {g.id: g for g in self.region.gazetteer}

    def place(self, pid: str) -> GazEntry:
        return self._gaz[pid]

    def has_place(self, pid: str) -> bool:
        return pid in self._gaz

    def place_name(self, pid: str, lang: str) -> str:
        return self._gaz[pid].names.get(lang, "")

    def ancestors(self, pid: str) -> list[str]:
        out, cur = [], self._gaz[pid].parent
        while cur:
            out.append(cur)
            cur = self._gaz[cur].parent
        return out

    def level_def(self, level: int) -> Optional[ScaleLevel]:
        for s in self.hazard.scale:
            if s.level == level:
                return s
        return None

    def label(self, level: int, lang: str) -> str:
        s = self.level_def(level)
        return s.label.get(lang, "") if s else ""

    @property
    def levels(self) -> list[int]:
        return [s.level for s in self.hazard.scale]

    @property
    def fingerprint(self) -> str:
        """Short hash of the resolved pack (region + hazard + channels), stamped on every run for the audit."""
        import hashlib
        return hashlib.sha256(self.model_dump_json().encode("utf-8")).hexdigest()[:12]

    def summary(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "region": self.region.name,
            "hazard": self.hazard.name,
            "languages": {k: v.name for k, v in self.region.languages.items()},
            "levels": [
                {"level": s.level, "colour": s.colour, "label": s.label,
                 "intent": s.intent, "response_types": s.response_types}
                for s in self.hazard.scale
            ],
            "places": len(self.region.gazetteer),
            "verified": self.region.verified and self.hazard.reviewed,
            "verification_note": self.region.verification_note,
            "hazard_reviewed": self.hazard.reviewed,
            "approvals": self.channels.approvals,
            "sms_max_segments": self.channels.sms_max_segments,
        }


# ----------------------------------------------------------------- loading


# libyaml's C parser when PyYAML was built with it (the pip wheels are): same safe subset, several times faster
_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def _read(path: Path) -> dict:
    try:
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=_YAML_LOADER)
    except Exception as e:  # noqa: BLE001
        raise PackError(f"cannot read {path.name}: {e}") from e
    if not isinstance(data, dict):
        raise PackError(f"{path.name}: expected a mapping at top level")
    return data


def _validate_hazard_for_region(h: Hazard, r: Region) -> None:
    langs = set(r.languages)
    problems: list[str] = []

    def need(label: str, have) -> None:
        missing = langs - set(have)
        if missing:
            problems.append(f"{label} missing languages {sorted(missing)}")

    need("hazard.event", h.event)
    need("hazard.templates", h.templates)
    intents = {s.intent for s in h.scale}
    for s in h.scale:
        need(f"scale level {s.level} label", s.label)
    for intent in intents:
        if intent not in h.actions:
            problems.append(f"hazard.actions missing intent '{intent}'")
        else:
            need(f"hazard.actions.{intent}", h.actions[intent])
    for mid, m in h.measures.items():
        need(f"measure {mid} phrase", m.phrase)
    fmt = string.Formatter()
    for lang, t in h.templates.items():
        for key in TEMPLATE_KEYS:
            for _, field, _, _ in fmt.parse(getattr(t, key)):
                if field and field not in TEMPLATE_SLOTS:
                    problems.append(f"templates.{lang}.{key}: unknown slot {{{field}}}")
        for key in ("headline", "sms", "spoken", "error_headline", "error_sms", "error_spoken"):
            for _, field, _, _ in fmt.parse(getattr(t.cancel, key) or ""):
                if field and field not in {"event", "areas"}:
                    problems.append(f"templates.{lang}.cancel.{key}: unknown slot {{{field}}}")
    levels = [s.level for s in h.scale]
    if len(levels) != len(set(levels)):
        problems.append("duplicate scale levels")
    if problems:
        raise PackError(
            f"hazard '{h.id}' cannot serve region '{r.id}': " + "; ".join(problems)
        )


class PackRegistry:
    def __init__(self, packs_dir: Optional[os.PathLike | str] = None) -> None:
        self.dir = Path(packs_dir or os.environ.get("LASTMILE_PACKS_DIR") or DEFAULT_PACKS_DIR)
        self._cache: dict[str, Pack] = {}

    def bundle_ids(self) -> list[str]:
        d = self.dir / "bundles"
        return sorted(p.stem for p in d.glob("*.yaml")) if d.is_dir() else []

    def get(self, bundle_id: str) -> Pack:
        if bundle_id in self._cache:
            return self._cache[bundle_id]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", bundle_id or ""):
            raise PackError(f"invalid pack id {bundle_id!r}")
        bpath = self.dir / "bundles" / f"{bundle_id}.yaml"
        if not bpath.is_file():
            raise PackError(f"unknown pack '{bundle_id}'")
        b = _read(bpath)
        try:
            region = Region.model_validate(_read(self.dir / "region" / f"{b['region']}.yaml"))
            hazard = Hazard.model_validate(_read(self.dir / "hazard" / f"{b['hazard']}.yaml"))
            channels = Channels.model_validate(
                _read(self.dir / "channels" / f"{b.get('channels', 'default')}.yaml")
            )
        except KeyError as e:
            raise PackError(f"bundle '{bundle_id}' is missing key {e}") from e
        except FileNotFoundError as e:
            raise PackError(f"bundle '{bundle_id}': {e.filename} not found") from e
        except ValueError as e:
            raise PackError(f"bundle '{bundle_id}' invalid: {e}") from e
        _validate_hazard_for_region(hazard, region)
        pack = Pack(id=b.get("id", bundle_id), name=b.get("name", bundle_id),
                    region=region, hazard=hazard, channels=channels)
        self._cache[bundle_id] = pack
        return pack

    def all(self) -> list[Pack]:
        return [self.get(i) for i in self.bundle_ids()]
