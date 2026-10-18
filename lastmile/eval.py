"""Evaluation harness: does the safety net actually catch things?

    python -m lastmile.eval                 # fake model, fault injection  (offline)
    python -m lastmile.eval --drafting templates   # the same corpus in template-first mode
    LASTMILE_LLM=gemini python -m lastmile.eval --skip-faults   # real model quality, needs credentials

For every case in data/corpus.yaml it runs
  * PIPELINE  — the full thing (intake guards, rules, deterministic checks, verifier, repair loop)
  * BASELINE  — the naive version: same model, no guards, no verifier, one shot
and reports which one lets defects reach the human approval queue.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from . import checks, publish
from .config import Settings
from .llm import LLM, Fault, FakeLLM, make_llm
from .packs import PackRegistry
from .pipeline import Options, Pipeline
from .schemas import Run
from .store import Store
from .templates import DRAFTING_MODES

CORPUS_PATH = Path(__file__).resolve().parent.parent / "data" / "corpus.yaml"


def load_corpus(path: Optional[Path] = None) -> list[dict]:
    data = yaml.safe_load(Path(path or CORPUS_PATH).read_text(encoding="utf-8"))
    return data["cases"]


def faults_of(case: dict) -> list[Fault]:
    return [Fault(role=f["role"], kind=f["kind"], lang=f.get("lang"), attempts=tuple(f.get("attempts", [0])))
            for f in case.get("faults", [])]


class CountingLLM:
    """Wraps any LLM and counts calls per role."""

    def __init__(self, inner: LLM) -> None:
        self.inner, self.name = inner, inner.name
        self.count: Counter = Counter()

    def intake(self, *a, **k):
        self.count["intake"] += 1
        return self.inner.intake(*a, **k)

    def localize(self, *a, **k):
        self.count["localize"] += 1
        return self.inner.localize(*a, **k)

    def verify(self, *a, **k):
        self.count["verify"] += 1
        return self.inner.verify(*a, **k)


def options_of(case: dict, drafting: Optional[str] = None) -> dict:
    """Per-case pipeline options from the corpus; `drafting` (from --drafting) overrides the case's own."""
    opts = dict(case.get("options") or {})
    if drafting:
        opts["drafting"] = drafting
    return opts


def run_case(case: dict, corpus: dict[str, dict], registry: PackRegistry, *, guards: bool, verify: bool,
             llm_kind: str = "fake", drafting: Optional[str] = None) -> tuple[Run, CountingLLM]:
    store = Store()
    settings = Settings()
    # setup cases: run + approve + publish first, with a clean model
    for sid in case.get("setup", []):
        setup_llm = FakeLLM() if llm_kind == "fake" else make_llm(llm_kind)
        sp = Pipeline(setup_llm, registry, store, Options(guards=True, verify=False))
        sr = sp.run(corpus[sid]["text"], corpus[sid]["pack"])
        if sr.status == "ready":
            publish.review(store, registry, settings, sr.id, "eval-bot", "approve")
    inner: LLM = FakeLLM(faults_of(case)) if llm_kind == "fake" else make_llm(llm_kind)
    llm = CountingLLM(inner)
    pipe = Pipeline(llm, registry, store, Options(guards=guards, verify=verify, **options_of(case, drafting)))
    return pipe.run(case["text"], case["pack"]), llm


def oracle_defects(run: Run, registry: PackRegistry) -> list[str]:
    """Independent post-hoc check of the FINAL drafts (same deterministic rules, applied to the output)."""
    if not (run.facts and run.derived and run.drafts) or run.msg_type == "Cancel":
        return []
    pack = registry.get(run.pack_id)
    out = []
    for lg in pack.langs:
        if lg in run.drafts:
            out += [f"{lg}:{i.code}" for i in checks.check_draft(run.facts, run.derived, run.drafts[lg], lg, pack)]
    return out


@dataclass
class CaseResult:
    id: str
    title: str
    defect: str
    expected: dict
    pipeline_status: str
    baseline_status: str
    pipeline_ok: bool
    problems: list[str] = field(default_factory=list)
    pipeline_codes: list[str] = field(default_factory=list)
    baseline_unsafe: bool = False
    pipeline_unsafe: bool = False
    false_block: bool = False
    repair_rounds: int = 0
    calls_pipeline: dict = field(default_factory=dict)
    calls_baseline: dict = field(default_factory=dict)


def evaluate(llm_kind: str = "fake", skip_faults: bool = False, registry: Optional[PackRegistry] = None,
             corpus_path: Optional[Path] = None, drafting: Optional[str] = None) -> dict:
    registry = registry or PackRegistry()
    cases = load_corpus(corpus_path)
    by_id = {c["id"]: c for c in cases}
    results: list[CaseResult] = []
    for c in cases:
        if skip_faults and c.get("faults"):
            continue
        # forcing template-first mode on the whole corpus: Localizer faults cannot happen (no AI Localizer),
        # and attempt counts no longer apply (templates get one round)
        forced = drafting == "templates" and c.get("options", {}).get("drafting") != "templates"
        if forced and any(f["role"] == "localize" for f in c.get("faults", [])):
            continue
        prun, pllm = run_case(c, by_id, registry, guards=True, verify=True, llm_kind=llm_kind, drafting=drafting)
        brun, bllm = run_case(c, by_id, registry, guards=False, verify=False, llm_kind=llm_kind, drafting=drafting)
        exp = c.get("expect", {})
        problems = []
        if "status" in exp and prun.status != exp["status"]:
            problems.append(f"status {prun.status} != {exp['status']}")
        if "msg_type" in exp and prun.msg_type != exp["msg_type"]:
            problems.append(f"msg_type {prun.msg_type} != {exp['msg_type']}")
        codes = {i.code for i in prun.issues}
        for code in exp.get("codes", []):
            if code not in codes:
                problems.append(f"missing issue code {code}")
        for lg, n in ({} if forced else exp.get("attempts", {})).items():
            if prun.attempts.get(lg) != n:
                problems.append(f"attempts[{lg}]={prun.attempts.get(lg)} != {n}")

        defect = c.get("defect", "none")

        def unsafe(run: Run) -> bool:
            if run.status != "ready":
                return False
            if defect in ("source", "intake"):
                return True
            if defect == "draft":
                return bool(oracle_defects(run, registry))
            return False

        rounds = sum(max(0, n - 1) for n in prun.attempts.values())
        results.append(CaseResult(
            id=c["id"], title=c["title"], defect=defect, expected=exp,
            pipeline_status=prun.status, baseline_status=brun.status,
            pipeline_ok=not problems, problems=problems, pipeline_codes=sorted(codes),
            baseline_unsafe=unsafe(brun), pipeline_unsafe=unsafe(prun),
            false_block=(defect == "none" and exp.get("status") == "ready" and prun.status != "ready"),
            repair_rounds=rounds, calls_pipeline=dict(pllm.count), calls_baseline=dict(bllm.count),
        ))
    return summarize(results, llm_kind, drafting or "per case")


def summarize(results: list[CaseResult], llm_kind: str, drafting: str = "per case") -> dict:
    risky = [r for r in results if r.defect in ("source", "intake", "draft")]
    clean = [r for r in results if r.defect == "none" and r.expected.get("status") == "ready"]
    residual = [r for r in results if r.defect == "residual"]
    return {
        "llm": llm_kind,
        "drafting": drafting,
        "cases": len(results),
        "pipeline_matches_expectations": sum(r.pipeline_ok for r in results),
        "risky_cases": len(risky),
        "baseline_unsafe_in_queue": sum(r.baseline_unsafe for r in risky),
        "pipeline_unsafe_in_queue": sum(r.pipeline_unsafe for r in risky),
        "clean_cases": len(clean),
        "pipeline_false_blocks": sum(r.false_block for r in clean),
        "repair_rounds_total": sum(r.repair_rounds for r in results),
        "residual_cases": [r.id for r in residual],
        "model_calls_pipeline": dict(sum((Counter(r.calls_pipeline) for r in results), Counter())),
        "model_calls_baseline": dict(sum((Counter(r.calls_baseline) for r in results), Counter())),
        "results": [r.__dict__ for r in results],
    }


def to_markdown(rep: dict) -> str:
    L = []
    mode = "" if rep.get("drafting", "per case") == "per case" else f", drafting: `{rep['drafting']}`"
    L.append(f"# LastMile Alert — safety-net evaluation (model: `{rep['llm']}`{mode})\n")
    L.append(f"* Cases run: **{rep['cases']}**; pipeline behaved as expected in "
             f"**{rep['pipeline_matches_expectations']}/{rep['cases']}**")
    L.append(f"* Defective alerts reaching the human approval queue — baseline (no guards): "
             f"**{rep['baseline_unsafe_in_queue']}/{rep['risky_cases']}**, pipeline: "
             f"**{rep['pipeline_unsafe_in_queue']}/{rep['risky_cases']}**")
    L.append(f"* Clean bulletins wrongly blocked by the pipeline: **{rep['pipeline_false_blocks']}/{rep['clean_cases']}**")
    L.append(f"* Repair rounds used (extra localizer passes): {rep['repair_rounds_total']}; "
             f"model calls pipeline {rep['model_calls_pipeline']} vs baseline {rep['model_calls_baseline']}")
    if rep["residual_cases"]:
        L.append(f"* Known residual risks (code cannot see; verifier AI + human reviewer must): "
                 f"{', '.join(rep['residual_cases'])}")
    L.append("\n| case | defect | baseline | pipeline | as expected | notes |")
    L.append("|---|---|---|---|---|---|")
    for r in rep["results"]:
        note = "; ".join(r["problems"]) or ", ".join(c for c in r["pipeline_codes"] if c != "pack_unverified")[:80]
        L.append(f"| {r['id']} | {r['defect']} | {r['baseline_status']}{' ⚠' if r['baseline_unsafe'] else ''} | "
                 f"{r['pipeline_status']}{' ⚠' if r['pipeline_unsafe'] else ''} | "
                 f"{'yes' if r['pipeline_ok'] else '**NO**'} | {note} |")
    L.append("\n⚠ = a defective alert reached the approval queue. Fake-model results measure the safety net, "
             "not Gemini's language quality; run with `LASTMILE_LLM=gemini` for that.")
    return "\n".join(L) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="LastMile Alert evaluation harness")
    ap.add_argument("--llm", default=None, help="fake | gemini (default: $LASTMILE_LLM or fake)")
    ap.add_argument("--skip-faults", action="store_true", help="skip fault-injection cases (use with gemini)")
    ap.add_argument("--out", default=None, help="write the markdown report here")
    ap.add_argument("--json", action="store_true", help="print JSON instead of markdown")
    ap.add_argument("--drafting", choices=DRAFTING_MODES, default=None,
                    help="force a drafting mode on every case (default: each case's own, usually model)")
    args = ap.parse_args(argv)
    kind = (args.llm or Settings.from_env().llm).lower()
    rep = evaluate(kind, skip_faults=args.skip_faults or kind != "fake", drafting=args.drafting)
    md = to_markdown(rep)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=1) if args.json else md)
    return 0 if rep["pipeline_matches_expectations"] == rep["cases"] else 1


if __name__ == "__main__":
    sys.exit(main())
