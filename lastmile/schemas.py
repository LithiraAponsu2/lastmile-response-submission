"""Data contracts shared by the AI stages, the deterministic checks and the API.

The AI stages (Intake, Localizer, Verifier) only ever produce/consume these
typed objects. Everything that decides whether an alert may go out is code.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from typing import Literal, Optional

from pydantic import BaseModel, Field


def warning_fingerprint(run) -> str:
    """Bind a review to warning content/state, excluding timing and trace noise."""
    payload = run.model_dump_json(include={"id", "pack_id", "mode", "source_text", "facts", "drafts", "derived", "msg_type", "status", "issues"})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- AI-facing


class Place(BaseModel):
    """A place exactly as the source wrote it, plus what code resolved it to."""

    as_written: str
    parent_hint: Optional[str] = None  # e.g. the district written next to a DS division
    level: Optional[int] = None  # hazard-scale level for THIS place (from the source)
    canonical: Optional[str] = None  # gazetteer id; filled by code, never by the AI


class Measurement(BaseModel):
    """A figure from the source that must survive translation unchanged."""

    kind: str = "other"  # a hazard-pack measure id (e.g. rainfall_24h) or "other"
    label: str = ""  # wording in the source, e.g. "rainfall in the past 24 hours"
    value: str  # the number as written, ASCII digits e.g. "150" or "2.5"
    unit: str = ""


class Facts(BaseModel):
    """What the official source literally says. Produced by the Intake agent.

    Risk classification (urgency/severity/certainty/response type) is NOT here:
    code derives it from the per-place level using the hazard pack.
    """

    doc_type: Literal["new", "update", "cancel"] = "new"
    hazard: str = ""
    places: list[Place] = Field(default_factory=list)
    issued_iso: Optional[str] = None
    onset_iso: Optional[str] = None
    expires_iso: Optional[str] = None
    measurements: list[Measurement] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)  # advice sentences as in the source
    intents: list[str] = Field(default_factory=list)  # advice classified: monitor|prepare|evacuate
    ambiguities: list[str] = Field(default_factory=list)  # conflicts/unclear items the AI noticed


class LangDraft(BaseModel):
    """One language's public-facing text. Produced by a Localizer agent."""

    headline: str = ""
    description: str = ""
    instruction: str = ""
    sms: str = ""
    spoken: str = ""

    def all_text(self) -> str:
        return "\n".join([self.headline, self.description, self.instruction, self.sms, self.spoken])


# ------------------------------------------------------------- code-derived


class Derived(BaseModel):
    """Everything safety-relevant that code (not AI) computed from Facts + pack."""

    levels: list[int] = Field(default_factory=list)
    max_level: Optional[int] = None
    urgency: str = "Unknown"
    severity: str = "Unknown"
    certainty: str = "Unknown"
    response_types: list[str] = Field(default_factory=list)
    cap_category: str = "Geo"
    expected_intent: Optional[str] = None
    places_by_level: dict[int, list[str]] = Field(default_factory=dict)  # level -> canonical ids
    msg_type: str = "Alert"  # set by rules.reconcile: Alert | Update | Cancel
    level_changes: dict[str, list[int]] = Field(default_factory=dict)  # Update: place -> [old level or 0, new]


class Issue(BaseModel):
    code: str
    severity: Literal["block", "warn"] = "block"
    message: str = ""
    lang: Optional[str] = None
    source: Literal["code", "ai", "plugin"] = "code"
    stage: Optional[str] = None  # plugin issues: the stage that produced them


# --------------------------------------------------------------- run record


class TraceEvent(BaseModel):
    step: str
    actor: Literal["ai", "code", "human", "plugin"]
    summary: str
    at: str = Field(default_factory=utcnow_iso)
    ms: Optional[float] = None  # wall-clock time this step took, in milliseconds


class Approval(BaseModel):
    reviewer: str
    decision: Literal["approve", "reject"]
    note: str = ""
    at: str = Field(default_factory=utcnow_iso)


RunStatus = Literal["blocked", "ready", "published", "rejected"]


class Run(BaseModel):
    id: str
    created_at: str = Field(default_factory=utcnow_iso)
    pack_id: str
    mode: Literal["replay", "live"] = "replay"
    source_text: str
    status: RunStatus = "blocked"
    facts: Optional[Facts] = None
    derived: Optional[Derived] = None
    drafts: dict[str, LangDraft] = Field(default_factory=dict)
    issues: list[Issue] = Field(default_factory=list)
    attempts: dict[str, int] = Field(default_factory=dict)  # lang -> localizer calls used
    trace: list[TraceEvent] = Field(default_factory=list)
    approvals: list[Approval] = Field(default_factory=list)
    msg_type: Literal["Alert", "Update", "Cancel"] = "Alert"
    references: list[str] = Field(default_factory=list)  # "sender,identifier,sent"
    cancel_target: Optional[str] = None  # a retraction: the one alert identifier this run cancels
    cap_identifier: Optional[str] = None
    cap_xml: Optional[str] = None  # only set once published
    cap_status: Optional[str] = None
    outbox: dict = Field(default_factory=dict)  # channel payloads, set at publish
    audit: list[dict] = Field(default_factory=list)  # append-only
    provenance: dict = Field(default_factory=dict)  # model(s), prompt version, pack fingerprint, drafting mode
    timings: dict[str, float] = Field(default_factory=dict)  # ms: draft / model / code, approve / publish
    back_translations: dict[str, str] = Field(default_factory=dict)  # lang -> the Verifier's English back-translation
    original_drafts: dict[str, LangDraft] = Field(default_factory=dict)  # drafts as produced, kept on first edit
    editors: list[str] = Field(default_factory=list)  # reviewers who changed any draft text

    def blocking(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "block"]


class PublishedAlert(BaseModel):
    """Compact record of what is currently 'active', used for conflict detection."""

    identifier: str
    run_id: str
    pack_id: str
    sender: str
    sent: str
    expires: Optional[str] = None
    msg_type: str
    status: str
    place_levels: dict[str, int] = Field(default_factory=dict)  # canonical id -> level
    cancelled: bool = False
