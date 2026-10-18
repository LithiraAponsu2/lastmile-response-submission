"""Send the reference seed (data/reference/*.json) to the deployment's private bucket.

You rarely need this: a deployed app copies any missing list into the bucket on its first start.
Use it to load a rebuilt seed before deploying, or to replace one list that nobody has edited yet.

  python scripts/upload_reference_seed.py --bucket NAME            # dry run: shows what would happen
  python scripts/upload_reference_seed.py --bucket NAME --apply    # upload lists the bucket does not have
  python scripts/upload_reference_seed.py --bucket NAME --apply --replace health
                                                                    # also replace 'health', only if still unedited

Edited lists are never overwritten: the bucket copy keeps every change and its history. The script
uses Application Default Credentials and prints no credential material.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "data" / "reference"
PREFIX = "state/reference/"


def plan(bucket, names, replace):
    steps = []
    for name in names:
        body = (SEED / f"{name}.json").read_bytes()
        seed = json.loads(body)
        blob = bucket.blob(PREFIX + f"{name}.json")
        if not blob.exists():
            steps.append((name, "upload", f"new list, {len(seed['records'])} records", blob, body, 0))
            continue
        blob.reload()
        current = json.loads(blob.download_as_bytes(if_generation_match=blob.generation))
        if name not in replace:
            steps.append((name, "keep", f"bucket has version {current['version']} with {len(current['records'])} records", blob, None, None))
        elif current["version"] > 1:
            steps.append((name, "refuse", f"bucket copy was edited (version {current['version']}); export it, merge, and import instead", blob, None, None))
        else:
            steps.append((name, "replace", f"unedited bucket copy replaced with {len(seed['records'])} records", blob, body, blob.generation))
    return steps


def main(argv=None, bucket=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bucket", default=os.environ.get("LASTMILE_STORAGE_BUCKET"), help="Bucket name (default: LASTMILE_STORAGE_BUCKET)")
    parser.add_argument("--apply", action="store_true", help="Upload. Without it, only show the plan.")
    parser.add_argument("--replace", nargs="*", default=[], help="Lists to replace if the bucket copy is still the unedited seed")
    args = parser.parse_args(argv)
    names = sorted(p.stem for p in SEED.glob("*.json"))
    unknown = set(args.replace) - set(names)
    if unknown:
        parser.error(f"unknown list(s): {', '.join(sorted(unknown))}")
    if bucket is None:
        if not args.bucket:
            parser.error("give --bucket or set LASTMILE_STORAGE_BUCKET")
        from google.cloud import storage
        bucket = storage.Client().bucket(args.bucket)
    steps = plan(bucket, names, set(args.replace))
    for name, action, detail, *_ in steps:
        print(f"{name:<11} {action:<8} {detail}")
    if not args.apply:
        print("Dry run. Add --apply to upload.")
        return steps
    for name, action, _, blob, body, generation in steps:
        if action in {"upload", "replace"}:
            # Create-only or exact-generation writes: a copy changed by the running app is never clobbered.
            blob.upload_from_string(body, content_type="application/json", if_generation_match=generation)
            print(f"{name}: uploaded")
    return steps


if __name__ == "__main__":
    sys.exit(0 if main() is not None else 1)
