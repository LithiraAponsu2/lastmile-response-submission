#!/usr/bin/env python3
"""Sample model check: offline by default; --llm gemini explicitly opts into billable calls.

  export LASTMILE_LLM=gemini GOOGLE_GENAI_USE_VERTEXAI=TRUE GOOGLE_CLOUD_PROJECT=<id> GOOGLE_CLOUD_LOCATION=global
  python scripts/live_smoke.py                    # offline clean sample
  python scripts/live_smoke.py --case lk-typo-place --repeat 3
  python scripts/live_smoke.py --llm gemini --list-models      # which Gemini model names does this project see?

Read the drafts with a native Sinhala/Tamil reader. The back-translations printed below come from the
independent verifier model and are a sanity aid, not a substitute for a human reader.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lastmile.checks import sms_segments  # noqa: E402
from lastmile.eval import load_corpus  # noqa: E402
from lastmile.llm import GeminiLLM, make_llm  # noqa: E402
from lastmile.packs import PackRegistry  # noqa: E402
from lastmile.pipeline import Pipeline  # noqa: E402
from lastmile.store import Store  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case", default="lk-clean-multilevel")
    ap.add_argument("--llm", default="fake", help="fake (default); gemini is an explicit billable opt-in")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--list-models", action="store_true")
    a = ap.parse_args()

    llm = make_llm(a.llm)
    if a.list_models:
        if not isinstance(llm, GeminiLLM):
            print("--list-models needs --llm gemini")
            return 2
        names = sorted(m.name for m in llm.client.models.list() if "gemini" in (m.name or "").lower())
        print("\n".join(names))
        return 0

    case = {c["id"]: c for c in load_corpus()}[a.case]
    registry = PackRegistry()
    pack = registry.get(case["pack"])
    bad = 0
    for n in range(a.repeat):
        run = Pipeline(llm, registry, Store()).run(case["text"], case["pack"])
        print(f"\n=== run {n + 1}/{a.repeat}: {run.status.upper()}  attempts={run.attempts}")
        for t in run.trace:
            print(f"  [{t.actor:>4}] {t.step}: {t.summary}")
        for i in run.issues:
            print(f"  {i.severity.upper():5} {i.code} {i.lang or ''}: {i.message}")
        for lg, d in run.drafts.items():
            enc, units, segs = sms_segments(d.sms)
            print(f"\n--- {pack.region.languages[lg].name} ({lg})  SMS: {segs} segment(s), {enc}")
            print(f"HEADLINE : {d.headline}\nDESCRIPT.: {d.description}\nINSTRUCT.: {d.instruction}\nSMS      : {d.sms}\nVOICE    : {d.spoken}")
            bt = getattr(llm, "back_translations", {}).get(lg)
            if bt:
                print(f"BACK-TRANSLATION (verifier): {bt}")
        bad += run.status != "ready"
    print(f"\n{a.repeat - bad}/{a.repeat} runs reached READY")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
