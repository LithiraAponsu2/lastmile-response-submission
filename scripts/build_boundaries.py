#!/usr/bin/env python3
"""Build data/boundaries/<region>.geojson from real boundary files (e.g. OCHA COD-AB from HDX).

  python scripts/build_boundaries.py --pack lk-landslide \
      --input adm2.geojson:district --input adm3.geojson:ds

Download COD-AB for Sri Lanka from https://data.humdata.org (licence CC BY-IGO; keep the attribution).
Run this on a machine with internet; the output is a small file you commit or mount.
Property names differ between COD-AB releases; override with --name-prop / --parent-prop if needed.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lastmile import boundaries  # noqa: E402
from lastmile.packs import PackRegistry  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pack", required=True)
    ap.add_argument("--input", action="append", required=True, help="FILE:KIND, KIND is district or ds")
    ap.add_argument("--name-prop", action="append")
    ap.add_argument("--parent-prop", action="append")
    ap.add_argument("--out", help="default: data/boundaries/<region>.geojson")
    a = ap.parse_args()
    pack = PackRegistry().get(a.pack)
    inputs = []
    for spec in a.input:
        path, _, kind = spec.rpartition(":")
        if not path or kind not in {"district", "ds"}:
            ap.error(f"--input must look like FILE:district or FILE:ds, got {spec!r}")
        inputs.append((Path(path), kind))
    fc, rep = boundaries.build(pack, inputs, a.name_prop, a.parent_prop)
    out = Path(a.out or Path(__file__).resolve().parent.parent / "data" / "boundaries" / f"{pack.region.id}.geojson")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fc), encoding="utf-8")
    print(f"wrote {out} with {len(fc['features'])} areas")
    print(f"matched {len(rep['matched'])} exact, {len(rep['fuzzy'])} fuzzy; {len(rep['unmatched'])} unmatched features")
    for u in rep["unmatched"][:25]:
        print("  unmatched:", u)
    print(f"gazetteer entries still without a boundary (drawn as circles): {rep['missing']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
