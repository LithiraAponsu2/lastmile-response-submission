from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from lastmile.packs import PackError, PackRegistry
from lastmile.llm import FakeLLM
from lastmile.pipeline import Pipeline
from lastmile.store import Store

PACKS = Path(__file__).resolve().parent.parent / "packs"


def test_all_bundles_load(registry):
    ids = registry.bundle_ids()
    assert {"lk-landslide", "np-flood"} <= set(ids)
    for p in registry.all():
        assert p.langs and p.levels == [1, 2, 3]


def test_summary_flags_unverified_packs(lk):
    s = lk.summary()
    assert s["verified"] is False and "verified" in s["verification_note"].lower()


@pytest.mark.parametrize("bad", ["../etc/passwd", "a/b", "", "x y", "nope"])
def test_bad_pack_ids(registry, bad):
    with pytest.raises(PackError):
        registry.get(bad)


def test_hazard_must_cover_region_languages(tmp_path):
    shutil.copytree(PACKS, tmp_path / "packs")
    d = tmp_path / "packs"
    (d / "bundles" / "bad.yaml").write_text("id: bad\nname: bad\nregion: np\nhazard: landslide-nbro\nchannels: default\n")
    with pytest.raises(PackError) as e:
        PackRegistry(d).get("bad")
    assert "ne" in str(e.value)


def test_template_slot_typos_are_caught(tmp_path):
    shutil.copytree(PACKS, tmp_path / "packs")
    f = tmp_path / "packs" / "hazard" / "flood-generic.yaml"
    f.write_text(f.read_text(encoding="utf-8").replace("{top_label}", "{top_lable}", 1), encoding="utf-8")
    with pytest.raises(PackError) as e:
        PackRegistry(tmp_path / "packs").get("np-flood")
    assert "top_lable" in str(e.value)


def test_new_country_is_just_yaml(tmp_path):
    """Add a made-up region 'xx' (English only) and reuse the flood hazard pack: no code changes."""
    shutil.copytree(PACKS, tmp_path / "packs")
    d = tmp_path / "packs"
    region = {
        "id": "xx", "name": "Exampleland", "timezone": "UTC", "utc_offset": "+00:00", "verified": True,
        "authority": {"sender_id": "alerts@example.test"},
        "languages": {"en": {"name": "English", "cap_lang": "en-XX", "script": "Latin"}},
        "gazetteer": [
            {"id": "XX-A", "kind": "district", "names": {"en": "Northvale"}, "lat": 10.0, "lon": 20.0, "radius_km": 15},
            {"id": "XX-B", "kind": "district", "names": {"en": "Southmere"}, "lat": 9.0, "lon": 20.5, "radius_km": 15},
        ],
    }
    (d / "region" / "xx.yaml").write_text(yaml.safe_dump(region))
    (d / "bundles" / "xx-flood.yaml").write_text("id: xx-flood\nname: Exampleland flood\nregion: xx\nhazard: flood-generic\nchannels: default\n")
    reg = PackRegistry(d)
    pack = reg.get("xx-flood")
    pack.hazard.reviewed = True
    text = ("FLOOD WARNING (SYNTHETIC)\nIssued: 2026-01-01 06:00\nValid until: 2026-01-02 06:00\n"
            "Level 2 (WARNING) - Prepare: Northvale, Southmere\nRiver level: 3.4 m\n"
            "Advice: Be ready to move. Follow official announcements.\n")
    run = Pipeline(FakeLLM(), reg, Store()).run(text, "xx-flood")
    assert run.status == "ready", [i.message for i in run.blocking()]
    assert "pack_unverified" not in [i.code for i in run.issues]  # verified region + (we flipped) reviewed hazard
    assert "Level 2 – Warning: Northvale, Southmere" in run.drafts["en"].description


def test_bundle_missing_file(tmp_path):
    shutil.copytree(PACKS, tmp_path / "packs")
    (tmp_path / "packs" / "bundles" / "orphan.yaml").write_text("id: orphan\nregion: zz\nhazard: flood-generic\n")
    with pytest.raises(PackError):
        PackRegistry(tmp_path / "packs").get("orphan")
