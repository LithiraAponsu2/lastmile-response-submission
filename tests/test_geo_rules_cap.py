from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import pytest
from lxml import etree

from lastmile import cap, geo, rules
from lastmile.schemas import Facts, Measurement, Place, PublishedAlert


# =================================================================== geo
@pytest.mark.parametrize("text,hint,status,pid", [
    ("Passara", "Badulla", "exact", "LK-BD-PAS"),
    ("passara", None, "exact", "LK-BD-PAS"),
    ("Passra", "Badulla", "fuzzy", "LK-BD-PAS"),
    ("Haldumulla", None, "exact", "LK-BD-HAL"),          # alias
    ("Bandarawella", "Badulla", "exact", "LK-BD-BAN"),
    ("පස්සර", None, "exact", "LK-BD-PAS"),                 # Sinhala
    ("பசறை", None, "exact", "LK-BD-PAS"),                  # Tamil
    ("கண்டி", None, "exact", "LK-KY"),
    ("Matara", None, "exact", "LK-MA"),                     # must not collide with Matale
    ("Matale", None, "exact", "LK-MT"),
    ("Passara", "Kandy", "conflict", "LK-BD-PAS"),
    ("Foobarville", None, "unresolved", None),
    ("", None, "unresolved", None),
])
def test_resolve(lk, text, hint, status, pid):
    r = geo.resolve(lk, text, hint)
    assert r.status == status
    assert r.place_id == pid


def test_resolve_does_not_confuse_similar_districts(lk):
    for a, b in [("Matara", "LK-MA"), ("Matale", "LK-MT"), ("Kandy", "LK-KY"), ("Kegalle", "LK-KE")]:
        assert geo.resolve(lk, a).place_id == b


def test_geometry_falls_back_to_circle_and_uses_boundaries_when_present(lk, tmp_path, monkeypatch):
    monkeypatch.setenv("LASTMILE_BOUNDARIES_DIR", str(tmp_path))
    geo._boundary_cache.clear()
    geom, approx = geo.place_geometry(lk, "LK-BD-PAS")
    assert approx and geom.is_valid
    kind, val = geo.cap_geometry(lk, "LK-BD-PAS")
    assert kind == "circle" and re.fullmatch(r"-?\d+\.\d+,-?\d+\.\d+ \d+(\.\d+)?", val)

    square = {"type": "Polygon", "coordinates": [[[81.1, 6.9], [81.2, 6.9], [81.2, 7.0], [81.1, 7.0], [81.1, 6.9]]]}
    (tmp_path / "lk.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"id": "LK-BD-PAS"}, "geometry": square}]}))
    monkeypatch.setenv("LASTMILE_BOUNDARIES_DIR", str(tmp_path))
    geo._boundary_cache.clear()
    kind, val = geo.cap_geometry(lk, "LK-BD-PAS")
    assert kind == "polygon" and val.split()[0] == val.split()[-1]   # closed ring, "lat,lon" pairs
    assert geo.place_geometry(lk, "LK-BD-PAS")[1] is False
    geo._boundary_cache.clear()


def test_geojson_and_svg(lk):
    gj = geo.geojson(lk, {"LK-BD-PAS": 3, "LK-NE-HAN": 1})
    assert len(gj["features"]) == 2 and gj["features"][0]["properties"]["colour"]
    svg = geo.render_svg(lk, {"LK-BD-PAS": 3})
    root = etree.fromstring(svg.encode())                 # well-formed XML
    assert root.tag.endswith("svg")
    assert "No places" in geo.render_svg(lk, {})


def test_svg_escapes_names(registry):
    pack = registry.get("lk-landslide").model_copy(deep=True)
    pack.region.gazetteer[0].names["en"] = "<script>alert(1)</script> & co"
    svg = geo.render_svg(pack, {pack.region.gazetteer[0].id: 2})
    etree.fromstring(svg.encode())
    assert "<script>" not in svg


# ================================================================ rules
def facts_for(places, **kw):
    return Facts(hazard="landslide-nbro", places=places, expires_iso="2025-11-28T18:00:00+05:30",
                 issued_iso="2025-11-27T18:00:00+05:30", **kw)


def test_derive_maps_levels_to_cap_fields(lk):
    f, _ = rules.resolve_places(facts_for([Place(as_written="Passara", level=3), Place(as_written="Kandy", level=1)]), lk)
    d, issues = rules.derive(f, lk)
    assert (d.max_level, d.urgency, d.severity, d.certainty) == (3, "Immediate", "Severe", "Likely")
    assert d.response_types == ["Evacuate", "Monitor"]
    assert d.places_by_level == {3: ["LK-BD-PAS"], 1: ["LK-KY"]}


def test_ai_cannot_set_risk_fields(lk):
    # Facts has no urgency/severity field at all: extra keys from a model are simply not part of the contract
    f = Facts.model_validate({"places": [], "urgency": "Immediate", "severity": "Extreme"})
    assert not hasattr(f, "urgency")


def test_advice_level_mismatch_both_directions(lk):
    f, _ = rules.resolve_places(facts_for([Place(as_written="Passara", level=1)], intents=["evacuate"]), lk)
    _, iss = rules.derive(f, lk)
    assert "advice_level_mismatch" in [i.code for i in iss]
    f, _ = rules.resolve_places(facts_for([Place(as_written="Passara", level=3)], intents=["monitor"]), lk)
    _, iss = rules.derive(f, lk)
    assert "advice_level_mismatch" in [i.code for i in iss]


def test_missing_unknown_level_and_ambiguity(lk):
    f, _ = rules.resolve_places(facts_for(
        [Place(as_written="Passara", level=None), Place(as_written="Kandy", level=7)],
        ambiguities=["two different rainfall figures"]), lk)
    codes = [i.code for i in rules.derive(f, lk)[1]]
    assert {"missing_level", "unknown_level", "source_ambiguity"} <= set(codes)


def test_hazard_mismatch_and_nested_areas(lk):
    f, _ = rules.resolve_places(Facts(hazard="tsunami", places=[Place(as_written="Badulla", level=2),
                                                                Place(as_written="Passara", level=2)],
                                      expires_iso="2025-11-28T18:00:00+05:30"), lk)
    codes = [i.code for i in rules.derive(f, lk)[1]]
    assert "hazard_mismatch" in codes and "overlapping_areas" in codes


def active(pid_levels, ident="LMA-1", sent="2025-11-27T18:00:00+05:30", exp="2025-11-28T18:00:00+05:30", **kw):
    return PublishedAlert(identifier=ident, run_id="r", pack_id="lk-landslide", sender="s@x", sent=sent,
                          expires=exp, msg_type="Alert", status="Exercise", place_levels=pid_levels, **kw)


def test_reconcile_update_cancel_and_expiry(lk):
    f, _ = rules.resolve_places(facts_for([Place(as_written="Passara", level=3)]), lk)
    d, _ = rules.derive(f, lk)
    at = datetime(2025, 11, 27, 20, 0, tzinfo=lk.tz)
    mt, refs, sup, iss = rules.reconcile(f, d, lk, [active({"LK-BD-PAS": 2})], at)
    assert mt == "Update" and refs == ["s@x,LMA-1,2025-11-27T18:00:00+05:30"] and sup == ["LMA-1"]
    assert "L2→L3" in iss[0].message
    # parent-district alert overlaps a DS-level one
    assert rules.reconcile(f, d, lk, [active({"LK-BD": 1})], at)[0] == "Update"
    # unrelated area, expired alert, cancelled alert, other pack -> new Alert
    assert rules.reconcile(f, d, lk, [active({"LK-KY": 2})], at)[0] == "Alert"
    assert rules.reconcile(f, d, lk, [active({"LK-BD-PAS": 2})], datetime(2025, 11, 29, tzinfo=lk.tz))[0] == "Alert"
    assert rules.reconcile(f, d, lk, [active({"LK-BD-PAS": 2}, cancelled=True)], at)[0] == "Alert"
    other = active({"LK-BD-PAS": 2}); other.pack_id = "np-flood"
    assert rules.reconcile(f, d, lk, [other], at)[0] == "Alert"
    # cancel
    fc = Facts(doc_type="cancel", hazard="landslide-nbro")
    dc, _ = rules.derive(fc, lk)
    mt, refs, _, iss = rules.reconcile(fc, dc, lk, [active({"LK-BD-PAS": 2})], at)
    assert mt == "Cancel" and refs
    assert rules.reconcile(fc, dc, lk, [], at)[3][0].code == "cancel_without_target"


# ================================================================== CAP
@pytest.fixture
def cap_xml(clean_run, lk):
    sent = datetime(2025, 11, 27, 18, 0, tzinfo=lk.tz)
    return cap.build_cap(lk, identifier="LMA-TEST-1", sent=sent, status="Exercise", msg_type="Alert",
                         facts=clean_run.facts, derived=clean_run.derived, drafts=clean_run.drafts,
                         note="test", approval_count=1)


def test_cap_validates_against_official_xsd(cap_xml):
    assert cap.validate(cap_xml) == []


def test_cap_structure(cap_xml):
    ns = {"c": cap.CAP_NS}
    root = etree.fromstring(cap_xml)
    assert [e.findtext("c:language", namespaces=ns) for e in root.findall("c:info", ns)] == ["en-LK", "si-LK", "ta-LK"]
    for t in root.iter(f"{{{cap.CAP_NS}}}sent", f"{{{cap.CAP_NS}}}effective", f"{{{cap.CAP_NS}}}expires"):
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+05:30", t.text), t.text   # explicit offset, never Z
    info = root.find("c:info", ns)
    assert info.findtext("c:urgency", namespaces=ns) == "Immediate"
    assert [r.text for r in info.findall("c:responseType", ns)] == ["Evacuate", "Prepare", "Monitor"]
    areas = info.findall("c:area", ns)
    assert len(areas) == 4 and areas[0].find("c:polygon", ns) is not None
    assert "Sinhala" not in cap_xml.decode()             # sanity: no stray labels
    assert "පස්සර" in etree.tostring(root.findall("c:info", ns)[1], encoding="unicode")


def test_cap_rejects_bad_enums_and_missing_draft(clean_run, lk):
    sent = datetime(2025, 11, 27, tzinfo=lk.tz)
    kw = dict(identifier="x", sent=sent, facts=clean_run.facts, derived=clean_run.derived, drafts=clean_run.drafts)
    with pytest.raises(cap.CapError):
        cap.build_cap(lk, status="Real", msg_type="Alert", **kw)
    with pytest.raises(cap.CapError):
        cap.build_cap(lk, status="Exercise", msg_type="Nope", **kw)
    kw["drafts"] = {"en": clean_run.drafts["en"]}
    with pytest.raises(cap.CapError):
        cap.build_cap(lk, status="Exercise", msg_type="Alert", **kw)


def test_xsd_catches_broken_documents(cap_xml):
    assert cap.validate(cap_xml.replace(b"<sender>lastmile-demo@example.org</sender>", b""))
    assert cap.validate(cap_xml.replace(b"+05:30</sent>", b"Z</sent>"))
    assert cap.validate(cap_xml.replace(b"<status>Exercise", b"<status>Bogus"))
    assert cap.validate(b"<alert")


def test_cap_escapes_and_strips_control_chars(clean_run, lk):
    drafts = {k: v.model_copy() for k, v in clean_run.drafts.items()}
    drafts["en"].headline = "A & B <b>x</b>\x00\x07 ok"
    xml = cap.build_cap(lk, identifier="LMA-X", sent=datetime(2025, 11, 27, tzinfo=lk.tz), status="Exercise",
                        msg_type="Alert", facts=clean_run.facts, derived=clean_run.derived, drafts=drafts)
    assert cap.validate(xml) == []
    headline = etree.fromstring(xml).find(f".//{{{cap.CAP_NS}}}headline").text
    assert headline == "A & B <b>x</b> ok"
    assert b"&lt;b&gt;" in xml


def test_cancel_cap_has_references_and_no_info(clean_run, lk):
    xml = cap.build_cap(lk, identifier="LMA-C", sent=datetime(2025, 11, 28, 9, tzinfo=lk.tz), status="Exercise",
                        msg_type="Cancel", facts=Facts(doc_type="cancel"), derived=clean_run.derived, drafts={},
                        references=["s@x,LMA-1,2025-11-27T18:00:00+05:30"])
    assert cap.validate(xml) == []
    root = etree.fromstring(xml)
    assert root.find(f"{{{cap.CAP_NS}}}info") is None
    assert root.findtext(f"{{{cap.CAP_NS}}}references") == "s@x,LMA-1,2025-11-27T18:00:00+05:30"


def test_identifier_is_cap_safe(lk):
    ident = cap.make_identifier(lk, datetime(2025, 11, 27, 18, tzinfo=timezone.utc), "run_ab-12 34!")
    assert re.fullmatch(r"[A-Za-z0-9.\-]+", ident)
    assert ident.startswith("LMA-LK-20251127233000")      # converted to local time (+05:30)
