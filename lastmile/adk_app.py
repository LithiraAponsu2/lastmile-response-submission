"""The same pipeline, expressed as Google Agent Development Kit (ADK) agents.

    SequentialAgent
      ├─ Ingest            (code)  injection scan, capture the bulletin
      ├─ Intake            (LlmAgent, output_schema=Facts)
      ├─ FactsGate         (code)  intake guards, place resolution, level rules, supersede/cancel, plugins
      ├─ [your agents]     (any ADK agent, local or RemoteA2aAgent) – write findings to state["plugin_issues"]
      ├─ DraftLoop         LoopAgent(max_iterations=3)
      │     ├─ Localizers  ParallelAgent[ one LlmAgent per language ]  (drafting="templates": a code step)
      │     ├─ CodeCheck   (code)  deterministic checks → which languages are clean
      │     ├─ Verifiers   ParallelAgent[ one independent LlmAgent per language ]
      │     └─ Collect     (code)  merge findings, feedback for the next round, escalate when all clean
      └─ Finalize          (code)  status ready|blocked; nothing is published here

Nothing in this graph can publish; publishing stays in publish.py behind a named human reviewer.
All the checks are the very same functions the plain pipeline uses (rules.py / checks.py / templates.py).

Requires `pip install "google-adk~=1.0"`. For offline runs use FakeAdkModel (wraps FakeLLM).
"""

from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Callable, Optional

from google.adk.agents import BaseAgent, LlmAgent, LoopAgent, ParallelAgent, SequentialAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from . import checks, prompts, rules
from .llm import DEFAULT_MODEL_FAST, DEFAULT_MODEL_VERIFIER, FakeLLM, _VerifyOut, answer_prompt
from .packs import Pack, PackRegistry
from .plugins import Plugin, PluginContext, run_plugins
from .schemas import Derived, Facts, Issue, LangDraft
from .store import Store
from .templates import DRAFTING_MODES, TemplateError, render_cancel, render_draft, template_draft

MAX_ROUNDS = 3
APP_NAME = "lastmile"


# --------------------------------------------------------------- helpers
def _issues(state_list: Optional[list]) -> list[Issue]:
    return [Issue.model_validate(i) for i in (state_list or [])]


def _dump(issues: list[Issue]) -> list[dict]:
    return [i.model_dump() for i in issues]


def _blocking(issues: list[Issue]) -> bool:
    return any(i.severity == "block" for i in issues)


def _trace(state: dict, step: str, actor: str, summary: str) -> list[dict]:
    return list(state.get("trace", [])) + [{"step": step, "actor": actor, "summary": summary}]


class CodeStep(BaseAgent):
    """A deterministic step inside the agent graph. `fn(state) -> (state_delta, escalate)`."""

    model_config = {"arbitrary_types_allowed": True}
    fn: Callable[[dict], tuple[dict, bool]]

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        state = dict(ctx.session.state)
        t0 = time.perf_counter()
        delta, escalate = self.fn(state)
        ms = round((time.perf_counter() - t0) * 1000, 2)
        before = len(state.get("trace", []))
        for t in (delta.get("trace") or [])[before:]:  # trace lines this step added: how long it took
            t.setdefault("ms", ms)
        yield Event(
            author=self.name, invocation_id=ctx.invocation_id, branch=ctx.branch,
            actions=EventActions(state_delta=delta, escalate=True if escalate else None),
        )


class FakeAdkModel(BaseLlm):
    """Offline stand-in for Gemini inside ADK: answers every role by delegating to FakeLLM."""

    model: str = "fake-adk"
    model_config = {"arbitrary_types_allowed": True}
    pack: Pack
    fake: FakeLLM

    @classmethod
    def supported_models(cls) -> list[str]:
        return [r"fake-adk"]

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False
                                     ) -> AsyncGenerator[LlmResponse, None]:
        si = llm_request.config.system_instruction if llm_request.config else ""
        system = si if isinstance(si, str) else "".join(p.text or "" for p in getattr(si, "parts", []) or [])
        user = "\n".join(p.text or "" for c in (llm_request.contents or []) for p in (c.parts or []))
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=self._answer(system, user))]))

    def _answer(self, system: str, user: str) -> str:
        return answer_prompt(self.fake, self.pack, system, user)


# ----------------------------------------------------------------- builder
def build_agent(pack: Pack, *, model_fast: Any = DEFAULT_MODEL_FAST, model_verifier: Any = DEFAULT_MODEL_VERIFIER,
                store: Optional[Store] = None, plugins: Optional[list[Plugin]] = None,
                extra_agents: Optional[list[BaseAgent]] = None, name: str = "lastmile",
                drafting: str = "model", cap_status: str = "Exercise") -> SequentialAgent:
    """Build the ADK agent graph for one pack. `extra_agents` run after FactsGate (pluggable agents).

    drafting="templates" swaps the Localizer agents for approved pack templates (template-first mode),
    exactly like Options(drafting="templates") in the plain pipeline.
    """
    if drafting not in DRAFTING_MODES:
        raise ValueError(f"drafting must be one of {DRAFTING_MODES}, got {drafting!r}")
    templates = drafting == "templates"
    plugins = list(plugins or [])
    store = store or Store()
    langs = pack.langs

    # ---- code steps -------------------------------------------------------
    def ingest(state: dict) -> tuple[dict, bool]:
        src = state.get("source_text", "")
        inj = checks.injection_scan(src)
        delta: dict = {"issues": _dump(inj), "blocked": bool(inj), "attempts": {}, "pending": [],
                       "trace": _trace(state, "injection_scan", "code",
                                       "Instruction-like text found; stopping" if inj else "No instruction-like content")}
        if not (pack.region.verified and pack.hazard.reviewed):
            delta["issues"] += _dump([Issue(code="pack_unverified", severity="warn",
                                            message="Gazetteer names, level mapping and language text in this pack are "
                                                    "unverified starter data. A native reader / the issuing authority "
                                                    "must verify them before real use.")])
        return delta, False

    def facts_gate(state: dict) -> tuple[dict, bool]:
        if state.get("blocked"):
            return {}, False
        issues = _issues(state.get("issues"))
        raw = state.get("facts_raw")
        if not raw:
            issues.append(Issue(code="llm_error", source="ai", message="Intake produced no output"))
            return {"issues": _dump(issues), "blocked": True,
                    "trace": _trace(state, "intake", "ai", "no output")}, False
        facts = Facts.model_validate(raw)
        for p in facts.places:
            p.canonical = None
        facts, place_issues = rules.resolve_places(facts, pack)
        issues += checks.check_intake(facts, state["source_text"], pack)
        issues += checks.check_intake_coverage(facts, state["source_text"], pack)
        issues += place_issues
        derived, rule_issues = rules.derive(facts, pack)
        issues += rule_issues
        at = checks.parse_dt(facts.issued_iso, pack) or datetime.now(timezone.utc)
        active = store.active_alerts()
        msg_type, refs, superseded, rec_issues = rules.reconcile(facts, derived, pack, active, at, status=cap_status,
                                                                 target=state.get("cancel_target"))
        derived.msg_type = msg_type
        if msg_type == "Update":
            derived.level_changes = rules.level_changes(derived, active, superseded)
        issues += rec_issues
        tr = _trace(state, "intake", "ai", f"{len(facts.places)} places, {len(facts.measurements)} figures")
        tr = tr + [{"step": "rules", "actor": "code", "summary": f"levels={derived.levels} msgType={msg_type}"}]
        delta: dict = {"facts": facts.model_dump(), "derived": derived.model_dump(), "msg_type": msg_type,
                       "references": refs, "trace": tr}
        pctx = PluginContext("post_facts", pack, state["source_text"], facts, derived, {}, msg_type)
        found, _ = run_plugins(plugins, pctx)
        issues += found
        if _blocking(issues):
            delta.update(issues=_dump(issues), blocked=True)
            delta["trace"] = delta["trace"] + [{"step": "stop", "actor": "code",
                                                "summary": "Facts cannot be trusted yet; the model is not asked to write"}]
            return delta, False
        if msg_type == "Cancel":
            closing = rules.cancelled_places(active, superseded)
            drafts = {lg: render_cancel(pack, lg, closing, error="error" in facts.intents).model_dump() for lg in langs}
            delta.update(drafts=drafts, pending=[], attempts={lg: 0 for lg in langs})
        else:
            delta.update(drafts={}, pending=list(langs), feedback={})
        delta["issues"] = _dump(issues)
        return delta, False

    def code_check(state: dict) -> tuple[dict, bool]:
        if state.get("blocked") or state.get("msg_type") == "Cancel":
            return {"to_verify": []}, False
        facts, derived = Facts.model_validate(state["facts"]), Derived.model_validate(state["derived"])
        drafts, attempts = dict(state.get("drafts", {})), dict(state.get("attempts", {}))
        by_lang = dict(state.get("round_issues", {}))
        notes = dict(state.get("notes", {}))
        delta: dict = {}
        to_verify = []
        for lg in state.get("pending", []):
            attempts[lg] = attempts.get(lg, 0) + 1
            raw = state.get(f"draft_{lg}")
            nlist = list(notes.get(lg, []))
            if state.get(f"template_{lg}"):
                draft = LangDraft.model_validate(raw)
                nlist.append(state[f"template_{lg}"])
            elif raw:
                draft = LangDraft.model_validate(raw)
                if state.get(f"fallback_{lg}"):
                    nlist.append(Issue(code="llm_fallback_template", severity="warn", lang=lg, source="code",
                                       message="Model unavailable; used approved templates instead. Read carefully.").model_dump())
            else:
                try:
                    draft = render_draft(facts, derived, lg, pack)
                    nlist.append(Issue(code="llm_fallback_template", severity="warn", lang=lg, source="code",
                                       message="Model returned nothing; used approved templates instead.").model_dump())
                except TemplateError as e:
                    draft = LangDraft()
                    nlist.append(Issue(code="llm_error", lang=lg, source="ai", message=f"Localizer failed ({e})").model_dump())
            notes[lg] = nlist
            drafts[lg] = draft.model_dump()
            iss = [i for i in _issues(nlist) if i.severity == "block"] or checks.check_draft(facts, derived, draft, lg, pack)
            by_lang[lg] = _dump(iss)
            if not _blocking(iss):
                to_verify.append(lg)
            delta[f"draft_{lg}"] = None
            delta[f"fallback_{lg}"] = None
            delta[f"template_{lg}"] = None
        delta.update(drafts=drafts, attempts=attempts, round_issues=by_lang, notes=notes, to_verify=to_verify,
                     trace=_trace(state, "localize+check", "ai", f"round for {', '.join(state.get('pending', []))}"))
        return delta, False

    def collect(state: dict) -> tuple[dict, bool]:
        if state.get("blocked") or state.get("msg_type") == "Cancel":
            return {}, True
        by_lang = dict(state.get("round_issues", {}))
        notes = dict(state.get("notes", {}))
        delta: dict = {}
        pending, feedback = [], {}
        bts = dict(state.get("back_translations", {}))
        for lg in state.get("to_verify", []):
            raw = state.get(f"verify_{lg}")
            if raw:
                bts[lg] = _VerifyOut.model_validate(raw).back_translation
                delta["back_translations"] = bts
                found = [Issue(code=i.code or "mistranslation", severity="block", source="ai", lang=lg,
                               message=i.message or "Verifier reported a discrepancy")
                         for i in _VerifyOut.model_validate(raw).issues]
                by_lang[lg] = list(by_lang.get(lg, [])) + _dump(found)   # keep the code warnings for this draft
            else:
                notes[lg] = list(notes.get(lg, [])) + [Issue(
                    code="verifier_unavailable", severity="warn", lang=lg, source="ai",
                    message="AI verification unavailable; rely on the human read-back").model_dump()]
                by_lang[lg] = list(by_lang.get(lg, []))
            delta[f"verify_{lg}"] = None
        for lg in state.get("pending", []):
            blocks = [i for i in _issues(by_lang.get(lg)) if i.severity == "block"]
            if blocks:
                pending.append(lg)
                feedback[lg] = _dump(blocks)
        delta.update(round_issues=by_lang, notes=notes, pending=pending, feedback=feedback, to_verify=[],
                     trace=_trace(state, "verify", "code", f"{len(pending)} language(s) need another pass"))
        # approved templates give the same text every time: feedback cannot repair them, so one round only
        return delta, templates or not pending

    def template_drafts(state: dict) -> tuple[dict, bool]:
        if state.get("blocked") or state.get("msg_type") == "Cancel":
            return {}, False
        facts, derived = Facts.model_validate(state["facts"]), Derived.model_validate(state["derived"])
        delta: dict = {}
        for lg in state.get("pending", []):
            draft, note = template_draft(facts, derived, lg, pack)
            delta[f"draft_{lg}"] = draft.model_dump()
            delta[f"template_{lg}"] = note.model_dump()
        delta["trace"] = _trace(state, "template_draft", "code",
                                f"{', '.join(state.get('pending', []))} from approved templates (no AI)")
        return delta, False

    def finalize(state: dict) -> tuple[dict, bool]:
        issues = _issues(state.get("issues"))
        drafts = {lg: LangDraft.model_validate(d) for lg, d in state.get("drafts", {}).items()}
        msg_type = state.get("msg_type", "Alert")
        if not state.get("blocked"):
            if msg_type == "Cancel":
                for lg in langs:
                    for fld in ("headline", "description", "instruction", "sms", "spoken"):
                        if not getattr(drafts[lg], fld).strip():
                            issues.append(Issue(code="empty_field", lang=lg, message=f"{fld} is empty"))
            else:
                pending = set(state.get("pending", []))
                for lg in langs:
                    issues += _issues(state.get("notes", {}).get(lg))
                    iss = _issues(state.get("round_issues", {}).get(lg))
                    code_block = any(i.source == "code" and i.severity == "block" for i in iss)
                    for i in iss:
                        if i.source == "ai" and i.severity == "block" and not code_block and lg in pending:
                            i = i.model_copy(update={"severity": "warn", "message":
                                f"Unresolved after {state['attempts'].get(lg, 0)} attempts — reviewer must read: {i.message}"})
                        issues.append(i)
                facts = Facts.model_validate(state["facts"])
                derived = Derived.model_validate(state["derived"])
                found, _ = run_plugins(plugins, PluginContext("post_draft", pack, state["source_text"], facts,
                                                              derived, drafts, msg_type))
                issues += found
            for raw in state.get("plugin_issues", []):          # findings written by extra (e.g. A2A) agents
                issues.append(Issue(code=str(raw.get("code", "plugin_finding"))[:60], source="plugin",
                                    severity="block" if raw.get("severity") == "block" else "warn",
                                    message=str(raw.get("message", ""))[:500], lang=raw.get("lang")))
        status = "blocked" if _blocking(issues) else "ready"
        result = {
            "provenance": {"llm": "adk", "models": {"fast": str(getattr(model_fast, "model", model_fast)),
                                                     "verifier": str(getattr(model_verifier, "model", model_verifier))},
                           "prompts": prompts.PROMPTS_VERSION, "pack": pack.fingerprint, "drafting": drafting},
            "status": status, "msg_type": msg_type, "references": state.get("references", []),
            "issues": _dump(issues), "drafts": {lg: d.model_dump() for lg, d in drafts.items()},
            "attempts": state.get("attempts", {}), "facts": state.get("facts"), "derived": state.get("derived"),
            "back_translations": state.get("back_translations", {}),
            "trace": _trace(state, "result", "code", f"{status.upper()}"),
        }
        return {"result": result}, False

    # ---- LLM steps ----------------------------------------------------------
    def skip_unless(pred: Callable[[dict], bool]):
        def cb(callback_context) -> Optional[types.Content]:
            st = callback_context.state
            snap = {k: st.get(k) for k in ("blocked", "pending", "to_verify")}
            return None if pred(snap) else types.Content(role="model", parts=[types.Part(text="")])
        return cb

    def set_prompt(build: Callable[[dict], tuple[str, str]]):
        def cb(callback_context, llm_request: LlmRequest) -> Optional[LlmResponse]:
            st = callback_context.state
            snap = {k: st.get(k) for k in ("source_text", "facts", "derived", "feedback", "drafts")}
            system, user = build(snap)
            llm_request.config.system_instruction = system
            llm_request.contents = [types.Content(role="user", parts=[types.Part(text=user)])]
            return None
        return cb

    def intake_cb(_snap: dict) -> tuple[str, str]:
        return prompts.intake_prompt(_snap["source_text"], pack)

    def localize_cb(lg: str):
        def build(snap: dict) -> tuple[str, str]:
            fb = [Issue.model_validate(i) for i in (snap.get("feedback") or {}).get(lg, [])] or None
            return prompts.localize_prompt(Facts.model_validate(snap["facts"]), Derived.model_validate(snap["derived"]),
                                           lg, pack, fb)
        return build

    def verify_cb(lg: str):
        def build(snap: dict) -> tuple[str, str]:
            return prompts.verify_prompt(snap["source_text"], Facts.model_validate(snap["facts"]),
                                         Derived.model_validate(snap["derived"]), lg,
                                         LangDraft.model_validate(snap["drafts"][lg]), pack)
        return build

    def empty_on_error(callback_context, llm_request, error) -> Optional[LlmResponse]:
        return LlmResponse(content=types.Content(role="model", parts=[types.Part(text="")]))

    def template_on_error(lg: str):
        def cb(callback_context, llm_request, error) -> Optional[LlmResponse]:
            st = callback_context.state
            try:
                d = render_draft(Facts.model_validate(st.get("facts")), Derived.model_validate(st.get("derived")), lg, pack)
            except Exception:  # noqa: BLE001
                return LlmResponse(content=types.Content(role="model", parts=[types.Part(text="")]))
            st[f"fallback_{lg}"] = True
            return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=d.model_dump_json())]))
        return cb

    intake = LlmAgent(
        name="intake", model=model_fast, instruction="(set per request)", include_contents="none",
        output_schema=Facts, output_key="facts_raw",
        before_agent_callback=skip_unless(lambda s: not s["blocked"]),
        before_model_callback=set_prompt(intake_cb), on_model_error_callback=empty_on_error,
        description="Reads the bulletin into typed Facts. Extracts only; never decides risk.")

    localizers = ParallelAgent(name="localizers", sub_agents=[
        LlmAgent(
            name=f"localize_{lg}", model=model_fast, instruction="(set per request)", include_contents="none",
            output_schema=LangDraft, output_key=f"draft_{lg}",
            before_agent_callback=skip_unless(lambda s, lg=lg: not s["blocked"] and lg in (s["pending"] or [])),
            before_model_callback=set_prompt(localize_cb(lg)), on_model_error_callback=template_on_error(lg),
            description=f"Writes the {pack.region.languages[lg].name} alert.")
        for lg in langs])

    verifiers = ParallelAgent(name="verifiers", sub_agents=[
        LlmAgent(
            name=f"verify_{lg}", model=model_verifier, instruction="(set per request)", include_contents="none",
            output_schema=_VerifyOut, output_key=f"verify_{lg}",
            before_agent_callback=skip_unless(lambda s, lg=lg: lg in (s["to_verify"] or [])),
            before_model_callback=set_prompt(verify_cb(lg)), on_model_error_callback=empty_on_error,
            description=f"Independent back-translation check of the {pack.region.languages[lg].name} draft.")
        for lg in langs])

    drafters: BaseAgent = (CodeStep(name="template_drafts", fn=template_drafts,
                                    description="Template-first mode: approved pack text, no AI writes.")
                           if templates else localizers)
    loop = LoopAgent(name="draft_loop", max_iterations=MAX_ROUNDS, sub_agents=[
        drafters,
        CodeStep(name="code_check", fn=code_check, description="Deterministic checks on every draft."),
        verifiers,
        CodeStep(name="collect", fn=collect, description="Merge findings; feedback or exit the loop."),
    ])

    return SequentialAgent(name=name, description="LastMile Alert: verified multilingual alert drafting. Never publishes.",
                           sub_agents=[
                               CodeStep(name="ingest", fn=ingest),
                               intake,
                               CodeStep(name="facts_gate", fn=facts_gate),
                               *(extra_agents or []),
                               loop,
                               CodeStep(name="finalize", fn=finalize),
                           ])


def build_root_agent(pack_id: Optional[str] = None) -> SequentialAgent:
    """Used by `adk web adk_agents` (see adk_agents/lastmile/agent.py). Env-driven like the rest of the app."""
    registry = PackRegistry()
    pack = registry.get(pack_id or os.environ.get("LASTMILE_PACK", "lk-landslide"))
    drafting = os.environ.get("LASTMILE_DRAFTING", "model").strip().lower() or "model"
    if os.environ.get("LASTMILE_LLM", "fake").lower() == "gemini":
        return build_agent(pack, model_fast=os.environ.get("LASTMILE_MODEL_FAST", DEFAULT_MODEL_FAST),
                           model_verifier=os.environ.get("LASTMILE_MODEL_VERIFIER", DEFAULT_MODEL_VERIFIER),
                           drafting=drafting)
    fake = FakeAdkModel(pack=pack, fake=FakeLLM())
    return build_agent(pack, model_fast=fake, model_verifier=fake, drafting=drafting)


# ----------------------------------------------------------------- running
async def run_adk_async(agent: SequentialAgent, source_text: str) -> dict:
    svc = InMemorySessionService()
    uid, sid = "reviewer", uuid.uuid4().hex
    await svc.create_session(app_name=APP_NAME, user_id=uid, session_id=sid, state={"source_text": source_text})
    runner = Runner(agent=agent, app_name=APP_NAME, session_service=svc)
    msg = types.Content(role="user", parts=[types.Part(text="Draft the alert for the bulletin in the session.")])
    async for _event in runner.run_async(user_id=uid, session_id=sid, new_message=msg):
        pass
    session = await svc.get_session(app_name=APP_NAME, user_id=uid, session_id=sid)
    return session.state["result"]


def run_adk(agent: SequentialAgent, source_text: str) -> dict:
    """Synchronous helper: returns the `result` dict (status, issues, drafts, attempts, trace…)."""
    return asyncio.run(run_adk_async(agent, source_text))
