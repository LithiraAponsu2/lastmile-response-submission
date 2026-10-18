"""CAP 1.2 (OASIS / ITU-T X.1303) builder + validator.

Output is validated against the official XSD vendored in lastmile/schema/.
"""

from __future__ import annotations

import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Optional

from lxml import etree

from . import geo
from .checks import parse_dt
from .packs import Pack
from .schemas import Derived, Facts, LangDraft

CAP_NS = "urn:oasis:names:tc:emergency:cap:1.2"
XSD_PATH = Path(__file__).resolve().parent / "schema" / "CAP-v1.2.xsd"
_BAD_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]")
RESPONSE_TYPES = {"Shelter", "Evacuate", "Prepare", "Execute", "Avoid", "Monitor", "Assess", "AllClear", "None"}


class CapError(ValueError):
    pass


def _clean(s: str) -> str:
    return _BAD_XML.sub("", s or "")


def cap_time(dt: datetime, pack: Pack) -> str:
    """CAP datetimes carry an explicit offset; 'Z' is not allowed."""
    return dt.astimezone(pack.tz).isoformat(timespec="seconds")


def make_identifier(pack: Pack, sent: datetime, run_id: str) -> str:
    stamp = sent.astimezone(pack.tz).strftime("%Y%m%d%H%M%S")
    suffix = re.sub(r"[^A-Za-z0-9]", "", run_id)[-6:] or "000000"
    return f"LMA-{pack.region.id.upper()}-{stamp}-{suffix}"


def _sub(parent: etree._Element, tag: str, text: Optional[str] = None) -> etree._Element:
    el = etree.SubElement(parent, f"{{{CAP_NS}}}{tag}")
    if text is not None:
        el.text = _clean(text)
    return el


def build_cap(
    pack: Pack,
    *,
    identifier: str,
    sent: datetime,
    status: str,
    msg_type: str,
    facts: Facts,
    derived: Derived,
    drafts: dict[str, LangDraft],
    references: Optional[list[str]] = None,
    note: str = "",
    approval_count: int = 0,
    drafted_by: str = "AI-assisted; verified by deterministic checks",
) -> bytes:
    if status not in {"Actual", "Exercise", "System", "Test", "Draft"}:
        raise CapError(f"bad status {status}")
    if msg_type not in {"Alert", "Update", "Cancel"}:
        raise CapError(f"bad msgType {msg_type}")
    root = etree.Element(f"{{{CAP_NS}}}alert", nsmap={None: CAP_NS})
    _sub(root, "identifier", identifier)
    _sub(root, "sender", pack.region.authority.sender_id)
    _sub(root, "sent", cap_time(sent, pack))
    _sub(root, "status", status)
    _sub(root, "msgType", msg_type)
    _sub(root, "scope", "Public")
    if note:
        _sub(root, "note", note)
    if references:
        _sub(root, "references", " ".join(references))

    if msg_type != "Cancel" and not derived.places_by_level:
        raise CapError("no target area: an alert must say where it applies")
    if msg_type != "Cancel":
        eff = parse_dt(facts.issued_iso, pack) or sent
        onset = parse_dt(facts.onset_iso, pack)
        expires = parse_dt(facts.expires_iso, pack)
        place_levels = {pid: lv for lv, pids in derived.places_by_level.items() for pid in pids}
        for lang in pack.langs:
            d = drafts.get(lang)
            if d is None:
                raise CapError(f"missing draft for language {lang}")
            info = _sub(root, "info")
            _sub(info, "language", pack.region.languages[lang].cap_lang)
            _sub(info, "category", derived.cap_category)
            _sub(info, "event", pack.hazard.event[lang])
            for rt in derived.response_types:
                if rt not in RESPONSE_TYPES:
                    raise CapError(f"bad responseType {rt}")
                _sub(info, "responseType", rt)
            _sub(info, "urgency", derived.urgency)
            _sub(info, "severity", derived.severity)
            _sub(info, "certainty", derived.certainty)
            _sub(info, "effective", cap_time(eff, pack))
            if onset:
                _sub(info, "onset", cap_time(onset, pack))
            if expires:
                _sub(info, "expires", cap_time(expires, pack))
            _sub(info, "senderName", pack.region.authority.sender_name or pack.hazard.issuer)
            _sub(info, "headline", d.headline)
            _sub(info, "description", d.description)
            _sub(info, "instruction", d.instruction)
            if pack.region.authority.web:
                _sub(info, "web", pack.region.authority.web)
            for name, value in (
                ("LastMile-MaxLevel", str(derived.max_level)),
                ("LastMile-DraftedBy", drafted_by),
                ("LastMile-HumanApproved", "true" if approval_count else "false"),
                ("LastMile-ApprovalCount", str(approval_count)),
            ):
                p = _sub(info, "parameter")
                _sub(p, "valueName", name)
                _sub(p, "value", value)
            for pid, lv in sorted(place_levels.items(), key=lambda kv: (-kv[1], kv[0])):
                area = _sub(info, "area")
                _sub(area, "areaDesc", f"{pack.place_name(pid, lang)} ({pack.label(lv, lang)})")
                shape_kind, shape_val = geo.cap_geometry(pack, pid)
                _sub(area, shape_kind, shape_val)
                for gname, gval in (("LastMile-PlaceId", pid), ("LastMile-Level", str(lv))):
                    g = _sub(area, "geocode")
                    _sub(g, "valueName", gname)
                    _sub(g, "value", gval)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


@lru_cache(maxsize=1)
def _schema() -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(str(XSD_PATH)))


def warm_up() -> None:
    """Parse the XSD now (at startup) instead of on the first publish."""
    _schema()


def validate(xml: bytes | str) -> list[str]:
    """Return a list of XSD validation errors (empty list = valid CAP 1.2)."""
    data = xml.encode("utf-8") if isinstance(xml, str) else xml
    try:
        doc = etree.fromstring(data)
    except etree.XMLSyntaxError as e:
        return [f"not well-formed XML: {e}"]
    schema = _schema()
    if schema.validate(doc):
        return []
    return [f"line {e.line}: {e.message}" for e in schema.error_log]
