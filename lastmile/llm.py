"""The model layer. Three roles — intake, localize, verify — behind one small interface.

* FakeLLM   : deterministic, offline, with fault injection. Used by tests, the eval harness
              and the demo when no credentials are present.
* GeminiLLM : google-genai client (Vertex AI / Agent Platform or AI Studio key, chosen by the
              standard env vars). Tested offline with a stub client; needs a live run to tune.

Select with LASTMILE_LLM=fake|gemini (see make_llm()).
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional, Protocol

from pydantic import BaseModel, Field

from . import prompts
from .checks import fmt_local, number_tokens
from .packs import Pack
from .schemas import Derived, Facts, Issue, LangDraft, Measurement, Place
from .templates import render_draft


class LLMError(RuntimeError):
    """Model unavailable, timed out, or returned something unusable."""


class LLM(Protocol):
    name: str

    def intake(self, source_text: str, pack: Pack) -> Facts: ...

    def localize(self, facts: Facts, derived: Derived, lang: str, pack: Pack,
                 feedback: Optional[list[Issue]] = None) -> LangDraft: ...

    def verify(self, source_text: str, facts: Facts, derived: Derived, lang: str,
               draft: LangDraft, pack: Pack) -> list[Issue]: ...


# =========================================================== FakeLLM ======


@dataclass
class Fault:
    """Inject a mistake into the fake model's output to test the safety net.

    role: intake | localize | verify;  attempts: which call indices (per role+lang) are affected.
    Use attempts=(0,1,2,3) for a fault that the repair loop can never fix.
    """

    role: str
    kind: str
    lang: Optional[str] = None
    attempts: tuple = (0,)


_FAKE_MEASURES = {
    "rainfall_24h": re.compile(r"rain\w*[^0-9\n]*?(\d+(?:\.\d+)?)\s*(mm|millimet\w+)", re.I),
    "river_level_m": re.compile(r"river[^0-9\n]*?(\d+(?:\.\d+)?)\s*(m|metres|meters)\b", re.I),
}
_HAZARD_WORDS = ("landslide", "flood", "cyclone", "tsunami", "earthquake", "wildfire", "storm surge", "drought")
_LEVEL_LINE = re.compile(r"^\s*Level\s*(\d)\b[^:\n]*:\s*(.+?)\s*$", re.I | re.M)
_TIME = r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})"


def _split_top_level(s: str) -> list[str]:
    items, depth, cur = [], 0, ""
    for ch in s:
        depth += (ch == "(") - (ch == ")")
        depth = max(depth, 0)
        if ch == "," and depth == 0:
            items.append(cur)
            cur = ""
        else:
            cur += ch
    items.append(cur)
    return [i.strip() for i in items if i.strip()]


def _parse_places(chunk: str, level: int) -> list[Place]:
    items = _split_top_level(re.sub(r"\band\b", ",", chunk.rstrip(". ")))
    parsed = []
    for it in items:
        m = re.match(r"^(.+?)\s*(?:\((.+?)\))?$", it)
        parsed.append([m.group(1).strip(), m.group(2)] if m else [it, None])
    hint = None
    for row in reversed(parsed):  # "A, B (District)" -> both get District
        if row[1]:
            hint = row[1]
        elif hint:
            row[1] = hint
    return [Place(as_written=n, parent_hint=h, level=level) for n, h in parsed]


def fake_parse(source_text: str, pack: Pack) -> Facts:
    """A tiny rule-based reader for the synthetic bulletin format (see README)."""
    first = (source_text.strip().splitlines() or [""])[0].upper()
    retraction = "SENT IN ERROR" in first or "FALSE ALARM" in first
    doc_type = "cancel" if ("CANCEL" in first or retraction) else "update" if "UPDATE" in first else "new"
    off = pack.region.utc_offset

    def when(label: str) -> Optional[str]:
        m = re.search(rf"(?:{label})\s*:\s*{_TIME}", source_text, re.I)
        return f"{m.group(1)}T{m.group(2)}:00{off}" if m else None

    places: list[Place] = []
    for m in _LEVEL_LINE.finditer(source_text):
        places.extend(_parse_places(m.group(2), int(m.group(1))))

    measurements: list[Measurement] = []
    ambiguities: list[str] = []
    for kind in pack.hazard.measures:
        pat = _FAKE_MEASURES.get(kind)
        if not pat:
            continue
        found = pat.findall(source_text)
        if not found:
            continue
        values = []
        for v, _unit in found:
            if v not in values:
                values.append(v)
        measurements.append(Measurement(kind=kind, label=kind.replace("_", " "), value=values[0],
                                        unit=pack.hazard.measures[kind].unit))
        if len(values) > 1:
            ambiguities.append(f"Conflicting {kind.replace('_', ' ')} figures in the bulletin: {' vs '.join(values)}")

    heading = " ".join(source_text.strip().splitlines()[:2]).lower()
    hazard = next((w for w in _HAZARD_WORDS if w in heading), pack.hazard.id)
    advice = " ".join(re.findall(r"^\s*Advice\s*:\s*(.+)$", source_text, re.I | re.M))
    low = advice.lower()
    intents = []
    if re.search(r"evacuat|move to higher|leave (the )?area", low):
        intents.append("evacuate")
    if re.search(r"be ready|prepare|be prepared|stand ?by", low):
        intents.append("prepare")
    if re.search(r"alert|aware|monitor|follow official", low):
        intents.append("monitor")
    if retraction:
        intents = ["error"]
    return Facts(
        doc_type=doc_type, hazard=hazard, places=places,
        issued_iso=when("Issued"), onset_iso=when("Onset"),
        expires_iso=when(r"Valid until|Expires"),
        measurements=measurements, actions=[advice] if advice else [], intents=intents,
        ambiguities=ambiguities,
    )


class FakeLLM:
    name = "fake"

    def __init__(self, faults: Optional[list[Fault]] = None) -> None:
        self.faults = list(faults or [])
        self.calls: dict = defaultdict(int)
        self.back_translations: dict[str, str] = {}  # same diagnostic GeminiLLM keeps; the fake cannot translate
        self._lock = threading.Lock()

    def _active(self, role: str, lang: Optional[str]) -> list[Fault]:
        with self._lock:
            idx = self.calls[(role, lang)]
            self.calls[(role, lang)] += 1
        return [f for f in self.faults if f.role == role and f.lang in (None, lang) and idx in f.attempts]

    # ---- roles
    def intake(self, source_text: str, pack: Pack) -> Facts:
        facts = fake_parse(source_text, pack)
        for f in self._active("intake", None):
            if f.kind == "add_number":
                facts.measurements.append(Measurement(kind="other", label="invented", value="999", unit="mm"))
            elif f.kind == "swap_level" and len(facts.places) >= 2:
                a, b = facts.places[0], facts.places[-1]
                a.level, b.level = b.level, a.level
            elif f.kind == "add_place":
                facts.places.append(Place(as_written="Ghostville", level=1))
            elif f.kind == "wrong_hazard":
                facts.hazard = "tsunami"
            elif f.kind == "drop_place" and facts.places:
                facts.places.pop(0)
            elif f.kind == "change_figure" and facts.measurements:
                facts.measurements[0].value = str(int(float(facts.measurements[0].value)) + 50)
        return facts

    def localize(self, facts: Facts, derived: Derived, lang: str, pack: Pack,
                 feedback: Optional[list[Issue]] = None) -> LangDraft:
        d = render_draft(facts, derived, lang, pack)
        for f in self._active("localize", lang):
            k = f.kind
            vals = [m.value for m in facts.measurements]
            if k == "drop_number" and vals:
                d.description = d.description.replace(vals[0], "")
            elif k == "change_number" and vals:
                d.description = d.description.replace(vals[0], str(int(float(vals[0])) + 10))
            elif k == "wrong_level_label":
                low = min(derived.levels)
                if derived.max_level != low:
                    d.headline = d.headline.replace(pack.label(derived.max_level, lang), pack.label(low, lang))  # type: ignore[arg-type]
            elif k == "hallucinate_place":
                extra = next(g for g in pack.region.gazetteer
                             if g.id not in {p.canonical for p in facts.places}
                             and g.id not in {a for p in facts.places if p.canonical for a in pack.ancestors(p.canonical)})
                d.description += f" {extra.names[lang]}"
            elif k == "wrong_language":
                d.description = "Heavy rain is expected. Please stay safe and follow official announcements today."
                d.headline = "Landslide warning for your area"
            elif k == "leak_placeholder":
                d.description += " {places}"
            elif k == "long_sms":
                d.sms += " " + "extra " * 80
            elif k == "drop_place" and derived.max_level is not None:
                pid = derived.places_by_level[derived.levels[-1]][0]
                nm = pack.place_name(pid, lang)
                d.description = d.description.replace(nm, "")
            elif k == "empty_instruction":
                d.instruction = ""
            elif k == "markup":
                d.headline = f"<b>{d.headline}</b>"
            elif k == "drop_update_marker" and derived.msg_type == "Update":
                word = pack.region.languages[lang].update_word
                d.headline = d.headline.replace(f"{word} – ", "")
                d.sms = d.sms.replace(f"{word}: ", "")
            elif k == "weaken_instruction" and derived.expected_intent and derived.expected_intent != "monitor":
                strong = pack.hazard.actions[derived.expected_intent][lang]
                weak = pack.hazard.actions["monitor"][lang]
                d.instruction = d.instruction.replace(strong, weak)
                d.sms = d.sms.replace(strong, weak)
        return d

    def verify(self, source_text: str, facts: Facts, derived: Derived, lang: str,
               draft: LangDraft, pack: Pack) -> list[Issue]:
        out = []
        try:  # stand-in "back-translation": the English rendering of the same facts, clearly labelled
            en = render_draft(facts, derived, "en", pack) if "en" in pack.langs else None
            self.back_translations[lang] = ("[fake model, not a real translation] " + en.description) if en else ""
        except Exception:  # noqa: BLE001
            self.back_translations[lang] = "[fake model, not a real translation]"
        for f in self._active("verify", lang):
            if f.kind == "flag":
                out.append(Issue(code="mistranslation", source="ai", lang=lang,
                                 message="Back-translation says the advice is weaker than the source"))
        return out


def answer_prompt(fake: FakeLLM, pack: Pack, system: str, user: str) -> str:
    """Answer one of OUR role prompts (prompts.py) with the fake model, as the JSON a real model would return.

    Lets code that talks to "a model" by prompt text run offline: the ADK graph (FakeAdkModel) and the local
    Gemini stand-in (mock_gemini.py). Only the fields the prompts carry are used, as a real model would.
    """
    if "Intake agent" in system:
        src = user.split(prompts.SOURCE_OPEN, 1)[1].rsplit(prompts.SOURCE_CLOSE, 1)[0].strip("\n")
        return fake.intake(src, pack).model_dump_json()
    if "Localizer agent" in system:
        lang = next(c for c, m in pack.region.languages.items() if f"Write the alert in {m.name}" in system)
        ctx = json.loads(user.split("FACTS:\n", 1)[1].split("\n\nYour previous draft", 1)[0])
        pbl = {int(k): v for k, v in ctx["places_by_level"].items()}
        levels = sorted(pbl, reverse=True)
        top = pack.level_def(levels[0])
        derived = Derived(levels=levels, max_level=levels[0], places_by_level=pbl,
                          expected_intent=top.intent if top else None, msg_type=ctx.get("MSG_TYPE", "Alert"),
                          level_changes=ctx.get("LEVEL_CHANGES") or {})
        places = [Place(as_written=pid, canonical=pid, level=lv) for lv, pids in pbl.items() for pid in pids]
        facts = Facts(places=places, measurements=ctx["measurements"], expires_iso=ctx["valid_until_local"],
                      issued_iso=ctx["issued_local"])
        return fake.localize(facts, derived, lang, pack).model_dump_json()
    if "independent Verifier" in system:
        lang = next(c for c, m in pack.region.languages.items() if f"DRAFT ({m.name})" in user)
        issues = fake.verify("", Facts(), Derived(), lang, LangDraft(), pack)
        return _VerifyOut(back_translation=fake.back_translations.get(lang) or "(fake)", issues=[
            _VerifyIssue(code=i.code, message=i.message) for i in issues]).model_dump_json()
    raise ValueError("answer_prompt: not one of the LastMile role prompts")


# Demo-only: the console can ask the FAKE model to make one specific mistake, to show the repair loop.
PRESET_FAULTS: dict[str, list[Fault]] = {
    "drop_number": [Fault("localize", "drop_number", "si", (0,))],
    "wrong_level_label": [Fault("localize", "wrong_level_label", "en", (0,))],
    "hallucinate_place": [Fault("localize", "hallucinate_place", "ta", (0,))],
    "wrong_language": [Fault("localize", "wrong_language", "si", (0,))],
    "long_sms": [Fault("localize", "long_sms", "ta", (0,))],
    "verifier_flag": [Fault("verify", "flag", "ta", (0,))],
    "intake_invented_figure": [Fault("intake", "add_number", None, (0,))],
}


# ========================================================== GeminiLLM =====


class _VerifyIssue(BaseModel):
    code: str = "mistranslation"
    message: str = ""


class _VerifyOut(BaseModel):
    back_translation: str = ""
    issues: list[_VerifyIssue] = Field(default_factory=list)


# Vertex AI has no "-latest" aliases; these are stable model names (Oct 2026). Cheapest and fastest pair: the
# verifier is a different, stronger model than the writer, so it stays independent.
DEFAULT_MODEL_FAST = "gemini-3.5-flash-lite"
DEFAULT_MODEL_VERIFIER = "gemini-3.6-flash"  # 2.4-3.7 s per call; 3.7/3.8 Flash had 40-90 s stalls


def thinking_budget(var: str) -> Optional[int]:
    """Thinking-token budget from an env var: an integer (0 = off), or None (unset/blank = the model's default)."""
    raw = os.environ.get(var, "").strip().lower()
    if raw in ("", "default"):
        return None
    try:
        n = int(raw)
    except ValueError:
        raise ValueError(f"{var} must be a whole number of tokens (0 = off) or empty, got {raw!r}") from None
    if n < 0:
        raise ValueError(f"{var} must be 0 or more, got {n}")
    return n


class GeminiLLM:
    """Gemini through google-genai. Vertex AI (Agent Platform) or AI Studio, by env:

        GOOGLE_GENAI_USE_VERTEXAI=TRUE  GOOGLE_CLOUD_PROJECT=...  GOOGLE_CLOUD_LOCATION=global
        (or GOOGLE_API_KEY=... for AI Studio)

    Models: LASTMILE_MODEL_FAST (intake + localizer) and LASTMILE_MODEL_VERIFIER (a different,
    stronger model so the verifier is independent). Check the exact names available in your
    project/region before the live run.

    Thinking: LASTMILE_THINKING_FAST / LASTMILE_THINKING_VERIFIER set a thinking-token budget per role
    (0 = off, the cheapest and fastest; unset = the model's default). Thinking tokens are billed as output:
    with gemini-3.8-flash, off cut one call from 12 s to 4 s and ~1 300 thinking tokens to 0.
    """

    name = "gemini"

    def __init__(self, client=None, model_fast: Optional[str] = None, model_verifier: Optional[str] = None,
                 retries: int = 1, timeout_s: float = 60.0) -> None:
        self._client = client
        self.model_fast = model_fast or os.environ.get("LASTMILE_MODEL_FAST", DEFAULT_MODEL_FAST)
        self.model_verifier = model_verifier or os.environ.get("LASTMILE_MODEL_VERIFIER", DEFAULT_MODEL_VERIFIER)
        self.thinking = {self.model_fast: thinking_budget("LASTMILE_THINKING_FAST"),
                         self.model_verifier: thinking_budget("LASTMILE_THINKING_VERIFIER")}
        self.retries = retries
        self.timeout_s = timeout_s
        self.back_translations: dict[str, str] = {}  # diagnostics: last verifier back-translation per language

    @property
    def client(self):
        if self._client is None:
            self._client = shared_client(self.timeout_s)
        return self._client

    def _config(self, system: str, schema, thinking: Optional[int] = None):
        from google.genai import types

        return types.GenerateContentConfig(
            system_instruction=system, response_mime_type="application/json",
            response_schema=schema, temperature=0.0,
            thinking_config=types.ThinkingConfig(thinking_budget=thinking) if thinking is not None else None,
        )

    def _call(self, model: str, system: str, user: str, schema):
        last: Exception | None = None
        for _ in range(self.retries + 1):
            try:
                resp = self.client.models.generate_content(
                    model=model, contents=user, config=self._config(system, schema, self.thinking.get(model)))
                text = getattr(resp, "text", None)
                if not text:
                    raise LLMError("empty model response (possibly blocked)")
                return schema.model_validate_json(text)
            except Exception as e:  # noqa: BLE001 — wrap everything: pipeline must degrade, not crash
                last = e
        raise LLMError(f"{type(last).__name__}: {last}") from last

    def intake(self, source_text: str, pack: Pack) -> Facts:
        system, user = prompts.intake_prompt(source_text, pack)
        facts: Facts = self._call(self.model_fast, system, user, Facts)
        return facts

    def read_structured(self, system: str, user: str, schema):
        """Read-only workspace proposals through the same configured client and bounded call."""
        return self._call(self.model_fast, system, user, schema)

    def read_parts(self, system: str, parts: list, schema):
        """Multimodal reading (photos plus text) on the fast model, with structured output."""
        return self._call(self.model_fast, system, parts, schema)

    def write_structured(self, system: str, user: str, schema):
        """Drafting (letters, translations) on the stronger model: the planner stays on the fast one."""
        return self._call(self.model_verifier, system, user, schema)

    def localize(self, facts: Facts, derived: Derived, lang: str, pack: Pack,
                 feedback: Optional[list[Issue]] = None) -> LangDraft:
        system, user = prompts.localize_prompt(facts, derived, lang, pack, feedback)
        return self._call(self.model_fast, system, user, LangDraft)

    def verify(self, source_text: str, facts: Facts, derived: Derived, lang: str,
               draft: LangDraft, pack: Pack) -> list[Issue]:
        system, user = prompts.verify_prompt(source_text, facts, derived, lang, draft, pack)
        out: _VerifyOut = self._call(self.model_verifier, system, user, _VerifyOut)
        self.back_translations[lang] = out.back_translation
        return [Issue(code=i.code or "mistranslation", severity="block", source="ai", lang=lang,
                      message=i.message or "Verifier reported a discrepancy") for i in out.issues]


_CLIENT_ENV = ("GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_CLOUD_PROJECT",
               "GOOGLE_CLOUD_LOCATION", "GOOGLE_GEMINI_BASE_URL", "GOOGLE_VERTEX_BASE_URL")
_clients: dict[tuple, object] = {}
_clients_lock = threading.Lock()


def shared_client(timeout_s: float = 60.0):
    """One google-genai client per configuration for the whole process. Each run gets a fresh GeminiLLM (its
    diagnostics are per run) but they share the client, so its HTTP connections (and TLS sessions) to Gemini are
    reused instead of set up again for every alert. Keyed on the env that configures it, so tests can switch."""
    key = (timeout_s,) + tuple(os.environ.get(k, "") for k in _CLIENT_ENV)
    with _clients_lock:
        client = _clients.get(key)
        if client is None:
            try:
                from google import genai
                from google.genai import types
            except ImportError as e:  # pragma: no cover
                raise LLMError("google-genai is not installed (pip install google-genai)") from e
            client = _clients[key] = genai.Client(http_options=types.HttpOptions(timeout=int(timeout_s * 1000)))
        return client


def make_llm(kind: Optional[str] = None) -> LLM:
    kind = (kind or os.environ.get("LASTMILE_LLM", "fake")).lower()
    if kind == "fake":
        return FakeLLM()
    if kind == "gemini":
        return GeminiLLM()
    raise ValueError(f"LASTMILE_LLM must be 'fake' or 'gemini', got {kind!r}")
