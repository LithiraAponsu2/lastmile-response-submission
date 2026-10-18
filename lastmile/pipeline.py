"""The drafting pipeline: AI for language and ambiguity, code for safety.

    source ──► injection scan (code)
           ──► Intake (AI) ──► intake guards (code: figures/places must exist in the source)
           ──► place resolution (code) ──► level rules (code) ──► supersede/cancel logic (code)
           ──► Localizers, one per language, in parallel (AI)
           ──► loop ≤ N: deterministic checks (code) + independent Verifier (AI) ──► re-localize with feedback
           ──► status: ready (no block issues) | blocked

This module can only produce a Run in state 'ready' or 'blocked'. It has no import path to
anything that sends or publishes; that lives in publish.py behind a human approval.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from . import checks, prompts, rules
from .llm import LLM
from .packs import Pack, PackRegistry
from .plugins import Plugin, PluginContext, run_plugins
from .schemas import Issue, LangDraft, Run, TraceEvent
from .store import Store
from .templates import DRAFTING_MODES, TemplateError, render_cancel, render_draft, template_draft


@dataclass
class Options:
    max_attempts: int = 3          # localize/verify rounds per language
    verify: bool = True            # run the independent AI verifier
    guards: bool = True            # deterministic checks (False = naive baseline, used by the eval ablation)
    fallback_templates: bool = True  # if the model is down, use approved templates (flagged for review)
    max_source_chars: int = 20000
    allow_actual: bool = False     # deployment allows CAP status Actual (LASTMILE_ALLOW_ACTUAL) for live runs
    drafting: str = "model"        # "model" = AI Localizers; "templates" = approved pack templates, AI only for
                                   # Intake + Verifier (template-first mode, e.g. while a language is weak)

    def __post_init__(self) -> None:
        if self.drafting not in DRAFTING_MODES:
            raise ValueError(f"drafting must be one of {DRAFTING_MODES}, got {self.drafting!r}")


class Pipeline:
    def __init__(self, llm: LLM, registry: PackRegistry, store: Store, options: Optional[Options] = None,
                 plugins: Optional[list[Plugin]] = None):
        self.llm = llm
        self.registry = registry
        self.store = store
        self.opt = options or Options()
        self.plugins = list(plugins or [])

    def _plugins(self, run: Run, pack: Pack, stage: str) -> None:
        if not self.plugins or not self.opt.guards or not (run.facts and run.derived):
            return
        t0 = time.perf_counter()
        ctx = PluginContext(stage=stage, pack=pack, source_text=run.source_text, facts=run.facts,
                            derived=run.derived, drafts=dict(run.drafts), msg_type=run.msg_type)
        issues, notes = run_plugins(self.plugins, ctx)
        run.issues.extend(issues)
        if notes:
            self._t(run, f"plugins:{stage}", "plugin", "; ".join(notes), t0)

    # ------------------------------------------------------------------ util
    def _t(self, run: Run, step: str, actor: str, summary: str, since: Optional[float] = None) -> None:
        """Add a trace line; `since` (a perf_counter() reading) records how long the step took, in ms."""
        ms = round((time.perf_counter() - since) * 1000, 2) if since is not None else None
        run.trace.append(TraceEvent(step=step, actor=actor, summary=summary, ms=ms))  # type: ignore[arg-type]

    def _finish(self, run: Run, extra: Optional[list[Issue]] = None) -> Run:
        if extra:
            run.issues.extend(extra)
        run.status = "blocked" if run.blocking() else "ready"
        self._t(run, "result", "code",
                f"{run.status.upper()}: {len(run.blocking())} blocking, "
                f"{len([i for i in run.issues if i.severity == 'warn'])} warnings")
        return run

    # ------------------------------------------------------------------- run
    def run(self, source_text: str, pack_id: str, mode: str = "replay",
            now: Optional[datetime] = None, cancel_target: Optional[str] = None) -> Run:
        started = time.perf_counter()
        run = self._draft(source_text, pack_id, mode, now, cancel_target)
        run.timings = timing_summary(run, started)
        self.store.put(run)
        return run

    def _draft(self, source_text: str, pack_id: str, mode: str, now: Optional[datetime],
               cancel_target: Optional[str]) -> Run:
        pack = self.registry.get(pack_id)
        if len(source_text) > self.opt.max_source_chars:
            raise ValueError(f"source text longer than {self.opt.max_source_chars} characters")
        now = now or datetime.now(timezone.utc)
        run = Run(id=self.store.new_id(), pack_id=pack.id, mode=mode, source_text=source_text,  # type: ignore[arg-type]
                  provenance=provenance(self.llm, pack, self.opt.drafting), cancel_target=cancel_target)
        self.store.audit(run, "system", "created", f"mode={mode} pack={pack.id} llm={self.llm.name}")

        # pack-level honesty banner
        if not (pack.region.verified and pack.hazard.reviewed):
            run.issues.append(Issue(code="pack_unverified", severity="warn",
                                    message=("Gazetteer names, level mapping and language text in this pack are "
                                             "unverified starter data. A native reader / the issuing authority "
                                             "must verify them before real use.")))

        # 1. injection scan ------------------------------------------------
        t0 = time.perf_counter()
        if self.opt.guards:
            inj = checks.injection_scan(source_text)
            if inj:
                self._t(run, "injection_scan", "code", "Instruction-like text found in the source; stopping", t0)
                return self._finish(run, inj)
        self._t(run, "injection_scan", "code", "No instruction-like content in the source", t0)

        # 2. intake (AI) ---------------------------------------------------
        t0 = time.perf_counter()
        try:
            facts = self.llm.intake(source_text, pack)
        except Exception as e:  # noqa: BLE001 — any model-layer failure must end as a visible blocked run
            _add_model_time(run, t0)
            self._t(run, "intake", "ai", f"model error: {e}", t0)
            return self._finish(run, [Issue(code="llm_error", message=f"Intake failed: {e}", source="ai")])
        _add_model_time(run, t0)
        for p in facts.places:
            p.canonical = None  # never trust a model-supplied id
        self._t(run, "intake", "ai",
                f"{len(facts.places)} places, {len(facts.measurements)} figures, doc_type={facts.doc_type}", t0)

        # 3. resolve places + guards (code) ---------------------------------
        t0 = time.perf_counter()
        facts, place_issues = rules.resolve_places(facts, pack)
        run.facts = facts
        if self.opt.guards:
            guard = checks.check_intake(facts, source_text, pack)
            guard += checks.check_intake_coverage(facts, source_text, pack)
            run.issues.extend(guard + place_issues)
            self._t(run, "intake_guards", "code",
                    f"{len(guard)} intake findings; {sum(1 for p in facts.places if p.canonical)}/{len(facts.places)} places resolved",
                    t0)
        else:
            self._t(run, "place_resolution", "code", "baseline: no guards", t0)

        # 4. rules ----------------------------------------------------------
        t0 = time.perf_counter()
        derived, rule_issues = rules.derive(facts, pack)
        run.derived = derived
        if self.opt.guards:
            run.issues.extend(rule_issues)
        self._t(run, "rules", "code",
                f"levels={derived.levels} → urgency={derived.urgency}, severity={derived.severity}, "
                f"response={derived.response_types}", t0)

        t0 = time.perf_counter()
        at = checks.parse_dt(facts.issued_iso, pack) or now
        active = self.store.active_alerts()
        msg_type, refs, superseded, rec_issues = rules.reconcile(
            facts, derived, pack, active, at, status=rules.cap_status(mode, self.opt.allow_actual),
            target=cancel_target)
        run.msg_type, run.references = msg_type, refs  # type: ignore[assignment]
        derived.msg_type = msg_type
        if msg_type == "Update":
            derived.level_changes = rules.level_changes(derived, active, superseded)
        if self.opt.guards:
            run.issues.extend(rec_issues)
        self._t(run, "supersede_check", "code",
                f"msgType={msg_type}" + (f", references {len(refs)} active alert(s)" if refs else ""), t0)

        self._plugins(run, pack, "post_facts")

        # stop here if the facts themselves cannot be trusted ------------------
        if run.blocking():
            self._t(run, "stop", "code", "Facts cannot be trusted yet; not asking the model to write anything")
            return self._finish(run)

        # 5a. cancellation: pre-approved templates only ------------------------
        if msg_type == "Cancel":
            t0 = time.perf_counter()
            closing = rules.cancelled_places(active, superseded)
            for lang in pack.langs:
                run.drafts[lang] = render_cancel(pack, lang, closing, error="error" in facts.intents)
                run.attempts[lang] = 0
            for lang in pack.langs:
                run.issues.extend(self._check_cancel(run.drafts[lang], lang, pack))
            self._t(run, "cancel_text", "code", "Cancellation text from approved templates (no AI)", t0)
            self._plugins(run, pack, "post_draft")
            return self._finish(run)

        # 5b. localize + verify/repair loop -----------------------------------
        self._localize_all(run, pack)
        self._plugins(run, pack, "post_draft")
        return self._finish(run)

    def _keep_back_translation(self, run: Run, lang: str) -> None:
        bt = getattr(self.llm, "back_translations", {}).get(lang)
        if bt:
            run.back_translations[lang] = bt

    # ------------------------------------------------------------ localize
    def _localize_one(self, run: Run, pack: Pack, lang: str, feedback: Optional[list[Issue]]) -> tuple[LangDraft, Optional[Issue]]:
        assert run.facts and run.derived
        run.attempts[lang] = run.attempts.get(lang, 0) + 1
        if self.opt.drafting == "templates":
            return template_draft(run.facts, run.derived, lang, pack)
        try:
            return self.llm.localize(run.facts, run.derived, lang, pack, feedback), None
        except Exception as e:  # noqa: BLE001
            if not self.opt.fallback_templates:
                return LangDraft(), Issue(code="llm_error", lang=lang, source="ai", message=f"Localizer failed: {e}")
            try:
                d = render_draft(run.facts, run.derived, lang, pack)
            except TemplateError as te:
                return LangDraft(), Issue(code="llm_error", lang=lang, source="ai",
                                          message=f"Localizer failed ({e}) and templates could not cover it ({te})")
            return d, Issue(code="llm_fallback_template", severity="warn", lang=lang, source="code",
                            message=f"Model unavailable ({e}); used approved templates instead. Read carefully.")

    def _localize_all(self, run: Run, pack: Pack) -> None:
        assert run.facts and run.derived
        langs = pack.langs
        feedback: dict[str, Optional[list[Issue]]] = {lg: None for lg in langs}
        pending = list(langs)
        notes: dict[str, list[Issue]] = {lg: [] for lg in langs}
        final_issues: dict[str, list[Issue]] = {lg: [] for lg in langs}
        templates = self.opt.drafting == "templates"
        # approved templates give the same text every time: feedback cannot repair them, so one round only
        rounds = 1 if templates else self.opt.max_attempts

        for attempt in range(1, rounds + 1):
            t0 = time.perf_counter()
            with ThreadPoolExecutor(max_workers=max(1, len(pending))) as ex:
                futs = {lg: ex.submit(self._localize_one, run, pack, lg, feedback[lg]) for lg in pending}
                results = {lg: f.result() for lg, f in futs.items()}
            for lg, (draft, note) in results.items():
                run.drafts[lg] = draft
                if note:
                    notes[lg].append(note)
            if templates:
                self._t(run, "template_draft", "code", f"{', '.join(pending)} from approved templates (no AI)", t0)
            else:
                _add_model_time(run, t0)
                self._t(run, "localize" if attempt == 1 else f"re-localize #{attempt}", "ai",
                        f"{', '.join(pending)} in parallel", t0)

            if not self.opt.guards:
                break

            # verify: deterministic first, then the independent AI verifier on clean drafts (in parallel)
            t0 = time.perf_counter()
            code_issues: dict[str, list[Issue]] = {}
            for lg in pending:
                if not any(n.severity == "block" for n in notes[lg]):
                    code_issues[lg] = checks.check_draft(run.facts, run.derived, run.drafts[lg], lg, pack)
            checks_ms = (time.perf_counter() - t0) * 1000
            # code warnings (e.g. sms_omits_areas) do not stop the independent Verifier; code blocks do
            to_verify = [lg for lg, iss in code_issues.items()
                         if self.opt.verify and not any(i.severity == "block" for i in iss)]
            t1 = time.perf_counter()
            verdicts = self._verify_all(run, pack, to_verify)
            verifier_ms = (time.perf_counter() - t1) * 1000
            _add_model_time(run, t1)
            still: list[str] = []
            for lg in pending:
                if lg not in code_issues:
                    final_issues[lg] = list(notes[lg])
                    still.append(lg)
                    feedback[lg] = None
                    continue
                issues = code_issues[lg]
                if lg in verdicts:
                    found, err = verdicts[lg]
                    if err is None:
                        issues = issues + found
                        self._keep_back_translation(run, lg)
                    else:
                        issues = []
                        notes[lg].append(Issue(code="verifier_unavailable", severity="warn", lang=lg, source="ai",
                                               message=f"AI verification unavailable ({err}); rely on the human read-back"))
                final_issues[lg] = issues
                if any(i.severity == "block" for i in issues):
                    still.append(lg)
                    feedback[lg] = [i for i in issues if i.severity == "block"]
            code_n = sum(1 for lg in pending for i in final_issues[lg] if i.source == "code" and i.severity == "block")
            ai_n = sum(1 for lg in pending for i in final_issues[lg] if i.source == "ai" and i.severity == "block")
            self._t(run, "verify", "code", f"attempt {attempt}: {code_n} code finding(s), {ai_n} verifier finding(s); "
                                           f"{len(still)} language(s) need another pass "
                                           f"(checks {checks_ms:.1f} ms, Verifier {verifier_ms:.1f} ms)", t0)
            pending = still
            if not pending:
                break

        # assemble final issues; demote advisory AI issues if code checks are clean
        for lg in langs:
            run.issues.extend(notes[lg])
            iss = final_issues[lg]
            has_code_block = any(i.source == "code" and i.severity == "block" for i in iss)
            for i in iss:
                if i.source == "ai" and i.severity == "block" and not has_code_block and lg in pending:
                    i = i.model_copy(update={
                        "severity": "warn",
                        "message": f"Unresolved after {run.attempts.get(lg, 0)} attempts — reviewer must read: {i.message}"})
                run.issues.append(i)

    def _verify_all(self, run: Run, pack: Pack, langs: list[str]) -> dict[str, tuple[list[Issue], Optional[Exception]]]:
        """The independent Verifier on each language at once: calls are independent, so the wait is the slowest
        one, not the sum. A failure is returned (not raised) so the caller can flag that language."""
        assert run.facts and run.derived

        def one(lg: str) -> tuple[list[Issue], Optional[Exception]]:
            try:
                return self.llm.verify(run.source_text, run.facts, run.derived, lg, run.drafts[lg], pack), None
            except Exception as e:  # noqa: BLE001 — any model-layer failure is a visible warning, never a crash
                return [], e

        if len(langs) <= 1:
            return {lg: one(lg) for lg in langs}
        with ThreadPoolExecutor(max_workers=len(langs)) as ex:
            futs = {lg: ex.submit(one, lg) for lg in langs}
            return {lg: f.result() for lg, f in futs.items()}

    # -------------------------------------------------------------- cancel
    @staticmethod
    def _check_cancel(d: LangDraft, lang: str, pack: Pack) -> list[Issue]:
        out = []
        for fld in ("headline", "description", "instruction", "sms", "spoken"):
            if not getattr(d, fld).strip():
                out.append(Issue(code="empty_field", lang=lang, message=f"{fld} is empty"))
        if checks.script_share(d.headline + d.spoken, pack.region.languages[lang].script) < 0.6:
            out.append(Issue(code="wrong_language", lang=lang, message="cancellation text is not in the expected script"))
        return out


# Per-language findings that say where a draft came from (not produced by checks.check_draft, so a re-check
# cannot recompute them). They stay visible until a human edits that language.
PROVENANCE_CODES = frozenset({"llm_fallback_template", "template_draft"})


def provenance(llm, pack: Pack, drafting: str) -> dict:
    """What produced a run, for the audit: model names, prompt version, exact pack data, drafting mode."""
    models = {role: getattr(llm, attr) for role, attr in (("fast", "model_fast"), ("verifier", "model_verifier"))
              if isinstance(getattr(llm, attr, None), str)}
    return {"llm": getattr(llm, "name", type(llm).__name__), "models": models, "prompts": prompts.PROMPTS_VERSION,
            "pack": pack.fingerprint, "drafting": drafting}


def timing_summary(run: Run, started: float) -> dict:
    """Milliseconds for the presenter and the audit: total wall time, how much of it was spent waiting for the
    model (Intake, Localizers, Verifier) and how much was our own code."""
    total = (time.perf_counter() - started) * 1000
    model = run.timings.get("model_ms", 0.0)
    return {"draft_ms": round(total, 2), "model_ms": round(model, 2), "code_ms": round(max(total - model, 0.0), 2)}


def _add_model_time(run: Run, since: float) -> None:
    run.timings["model_ms"] = run.timings.get("model_ms", 0.0) + (time.perf_counter() - since) * 1000


def reverify_edit(run: Run, pack: Pack, llm: LLM, lang: str) -> Run:
    """After a human edits one language, ask the independent Verifier about that text again (advisory).

    The deterministic checks already ran (recheck) and stay the hard gate; Verifier findings on human text are
    warnings for the next reviewer, never blocks, and a Verifier outage is a visible warning.
    """
    if not (run.facts and run.derived) or lang not in run.drafts or run.msg_type == "Cancel":
        return run
    if any(i.lang == lang and i.severity == "block" for i in run.issues):
        return run  # fix the code findings first; the Verifier only looks at text that passes them
    try:
        found = llm.verify(run.source_text, run.facts, run.derived, lang, run.drafts[lang], pack)
    except Exception as e:  # noqa: BLE001
        run.issues.append(Issue(code="verifier_unavailable", severity="warn", lang=lang, source="ai",
                                message=f"AI verification of the edited text unavailable ({e}); read it back carefully"))
        return run
    bt = getattr(llm, "back_translations", {}).get(lang)
    if bt:
        run.back_translations[lang] = bt
    for i in found:
        run.issues.append(i.model_copy(update={"severity": "warn",
                                               "message": f"After a human edit — reviewer must read: {i.message}"}))
    run.trace.append(TraceEvent(step=f"reverify_edit:{lang}", actor="ai",
                                summary=f"{len(found)} verifier finding(s) on the edited {lang} text"))
    return run


def recheck(run: Run, pack: Pack, plugins: Optional[list[Plugin]] = None, edited: Optional[str] = None) -> Run:
    """Re-run the deterministic checks (and post_draft plugins) after a human edit, or at approval time.

    Code findings are recomputed for every language. AI findings and provenance flags are retired only for
    the `edited` language (the human has taken responsibility for that text); for every other language they
    stay, so a later reviewer still sees e.g. "verifier disagrees, reviewer must read".
    """
    if not (run.facts and run.derived) or any(lg not in run.drafts for lg in pack.langs):
        run.status = "blocked"  # the run stopped before drafting; nothing a reviewer can approve
        return run
    keep = [i for i in run.issues if i.lang is None and not (i.source == "plugin" and i.stage == "post_draft")]
    keep += [i for i in run.issues if i.lang is not None and i.lang != edited
             and (i.source == "ai" or i.code in PROVENANCE_CODES)]
    for lg in pack.langs:
        if run.msg_type == "Cancel":
            keep.extend(Pipeline._check_cancel(run.drafts[lg], lg, pack))
        else:
            keep.extend(checks.check_draft(run.facts, run.derived, run.drafts[lg], lg, pack))
    if plugins:
        found, _ = run_plugins(plugins, PluginContext(stage="post_draft", pack=pack, source_text=run.source_text,
                                                      facts=run.facts, derived=run.derived, drafts=dict(run.drafts),
                                                      msg_type=run.msg_type))
        keep.extend(found)
    run.issues = keep
    run.status = "blocked" if run.blocking() else "ready"
    return run
