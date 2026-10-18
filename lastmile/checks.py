"""Deterministic checks. These — not the AI — decide whether a draft is allowed through.

Everything here is exact-match / arithmetic so it behaves identically for English,
Sinhala, Tamil and Devanagari text. A failed check is an Issue with severity
"block"; the run cannot become 'ready' while any block-issue remains.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from functools import lru_cache
from typing import Optional

from .geo import norm_name
from .packs import Pack
from .schemas import Derived, Facts, Issue, LangDraft

# ------------------------------------------------------------------ numbers

_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")


_DIGIT_RE = re.compile(r"\d")  # in a str pattern \d is exactly the Unicode decimal digits (category Nd)


@lru_cache(maxsize=4096)  # pure; the same source and draft fields are re-checked many times per run
def norm_digits(s: str) -> str:
    """Map any Unicode decimal digit (Sinhala, Tamil, Devanagari, Arabic-Indic…) to ASCII."""
    if s.isascii():
        return s
    return _DIGIT_RE.sub(lambda m: str(unicodedata.decimal(m.group())), s)


def _canon_num(tok: str) -> str:
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", tok):  # 1,200 or 1,200.5
        tok = tok.replace(",", "")
    elif re.fullmatch(r"\d+,\d+", tok):  # 2,5 -> 2.5 (decimal comma)
        tok = tok.replace(",", ".")
    if "." in tok:
        whole, frac = tok.split(".", 1)
        frac = frac.rstrip("0")
        whole = str(int(whole))
        return f"{whole}.{frac}" if frac else whole
    return str(int(tok))


def number_tokens(text: str) -> list[str]:
    out = []
    for m in _NUM_RE.findall(norm_digits(text)):
        try:
            out.append(_canon_num(m))
        except ValueError:
            continue
    return out


# ---------------------------------------------------------------------- SMS


_GSM7 = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")
_GSM7_EXT = set("^{}\\[~]|€")


def sms_segments(text: str) -> tuple[str, int, int]:
    """(encoding, units, segments). GSM-7: 160/153; UCS-2: 70/67 (UTF-16 code units)."""
    if all(c in _GSM7 or c in _GSM7_EXT for c in text):
        units = sum(2 if c in _GSM7_EXT else 1 for c in text)
        single, multi, enc = 160, 153, "GSM-7"
    else:
        units = len(text.encode("utf-16-le")) // 2
        single, multi, enc = 70, 67, "UCS-2"
    if units == 0:
        return enc, 0, 0
    segs = 1 if units <= single else -(-units // multi)
    return enc, units, segs


# ---------------------------------------------------------------- text norm


@lru_cache(maxsize=4096)
def norm_text(s: str) -> str:
    t = unicodedata.normalize("NFKC", s or "")
    t = norm_digits(t)
    t = re.sub(r"[‐‑‒–—―−-]", "-", t)
    t = re.sub(r"\s+", " ", t)
    return t.casefold().strip()


def fmt_local(iso: Optional[str], pack: Pack) -> str:
    dt = parse_dt(iso, pack)
    return dt.astimezone(pack.tz).strftime("%Y-%m-%d %H:%M") if dt else ""


def fmt_human(iso: Optional[str], pack: Pack, lang: str, sms: bool = False) -> str:
    """A date and time as people read it in `lang`, in the region's time zone.

    Long form "Friday 28 November 2025, 18:00" for description and voice; `sms=True` gives "28/11 18:00".
    """
    dt = parse_dt(iso, pack)
    if not dt:
        return ""
    dt, meta = dt.astimezone(pack.tz), pack.region.languages[lang]
    fmt = meta.sms_datetime_format if sms else meta.datetime_format
    return fmt.format(weekday=meta.weekdays[dt.weekday()], day=dt.day, month=meta.months[dt.month - 1],
                      month_num=dt.month, year=dt.year, time=dt.strftime("%H:%M"))


# ------------------------------------------------------- channel envelopes
# Text code adds around a draft when it leaves on a channel: drill marker, issuer, hotline, reference. The model
# never writes these, so they cannot be invented or dropped; the length checks below budget for the worst case.
REF_PLACEHOLDER = "XXXXXX"


def sms_envelope(pack: Pack, lang: str, sms: str, *, exercise: bool, ref: str = "") -> str:
    meta, auth = pack.region.languages[lang], pack.region.authority
    parts = [meta.exercise_prefix] if exercise else []
    parts.append(f"{auth.sms_sender}: {sms}" if auth.sms_sender else sms)
    if auth.hotline:
        parts.append(meta.help_sms.format(hotline=auth.hotline))
    if ref:
        parts.append(f"{meta.ref_label} {ref}")
    out = " ".join(p.strip() for p in parts if p.strip())
    return out.replace("–", "-").replace("—", "-") if meta.script == "Latin" else out


def voice_envelope(pack: Pack, lang: str, spoken: str, *, exercise: bool) -> str:
    meta, auth = pack.region.languages[lang], pack.region.authority
    parts = [meta.exercise_prefix] if exercise else []
    parts.append(spoken)
    if auth.hotline:
        parts.append(meta.help_voice.format(hotline=auth.hotline))
    return " ".join(p.strip() for p in parts if p.strip())


def parse_dt(iso: Optional[str], pack: Pack) -> Optional[datetime]:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=pack.tz)


# ------------------------------------------------------------ script check

_SCRIPTS = {
    "Latin": [(0x41, 0x5A), (0x61, 0x7A), (0xC0, 0x24F)],
    "Sinhala": [(0x0D80, 0x0DFF)],
    "Tamil": [(0x0B80, 0x0BFF)],
    "Devanagari": [(0x0900, 0x097F)],
}


def script_share(text: str, script: str) -> float:
    ranges = _SCRIPTS.get(script)
    letters = [c for c in text if c.isalpha() or unicodedata.category(c).startswith("M")]
    if not ranges or not letters:
        return 1.0
    ok = sum(1 for c in letters if any(a <= ord(c) <= b for a, b in ranges))
    return ok / len(letters)


# ----------------------------------------------------------------- guards

_INJECTION = re.compile(
    r"(ignore|disregard|forget)\s+(all\s+|any\s+|the\s+)?(previous|prior|above|earlier)\s+"
    r"(instructions?|prompts?|rules?)|system\s+prompt|you\s+are\s+now\s+|"
    r"(auto[- ]?)?(approve|publish|send|broadcast)\s+(this\s+)?(alert\s+)?(immediately|now|without)|"
    r"</?(system|assistant|instructions?)>|\bBEGIN\s+(SYSTEM|PROMPT)\b",
    re.I,
)


_SAYS_REAL = re.compile(r"\bnot\s+a\s+drill\b|\bnot\s+an?\s+(exercise|test)\b|\bthis\s+is\s+real\b", re.I)
_SAYS_DRILL = re.compile(r"\bthis\s+is\s+(only\s+)?an?\s+(drill|test|exercise)\b|\bexercise\s+only\b", re.I)


def status_wording(drafts: dict, status: str) -> list[Issue]:
    """Text that contradicts the CAP status: "THIS IS NOT A DRILL" in an Exercise, "this is a test" in an Actual.

    The FCC told alert originators after Hawaii 2018 never to say "this is not a drill" in a drill.
    """
    out = []
    for lang, d in drafts.items():
        text = d.all_text()
        if status != "Actual" and _SAYS_REAL.search(text):
            out.append(Issue(code="real_wording_in_exercise", lang=lang,
                             message="An exercise must not say it is real (e.g. “this is not a drill”)"))
        if status == "Actual" and _SAYS_DRILL.search(text):
            out.append(Issue(code="drill_wording_in_actual", lang=lang,
                             message="A real alert must not say it is a drill, test or exercise"))
    return out


def injection_scan(text: str) -> list[Issue]:
    m = _INJECTION.search(text or "")
    if not m:
        return []
    return [Issue(code="prompt_injection_suspected", severity="block", source="code",
                  message=("Source text contains instruction-like content "
                           f"(“{m.group(0)[:60]}”). It is treated as data and never obeyed; "
                           "remove it or confirm the source is authentic, then resubmit."))]


def check_intake(facts: Facts, source_text: str, pack: Pack) -> list[Issue]:
    """Did the Intake agent invent anything? Compare Facts back to the raw source."""
    issues: list[Issue] = []
    src_tokens = set(number_tokens(source_text))
    for m in facts.measurements:
        vals = number_tokens(m.value)
        if len(vals) != 1:
            issues.append(Issue(code="intake_bad_figure", message=f"Figure '{m.value}' is not a single number"))
            continue
        if vals[0] not in src_tokens:
            issues.append(Issue(code="intake_figure_not_in_source",
                                message=f"Figure {m.value} {m.unit} is not in the source text"))
        spec = pack.hazard.measures.get(m.kind)
        if spec and spec.units_accepted and m.unit and m.unit.lower() not in [u.lower() for u in spec.units_accepted]:
            issues.append(Issue(code="intake_unit_mismatch",
                                message=f"{m.kind} expects {spec.unit}, source gave '{m.unit}' — convert/confirm by hand"))
    words = tokens(source_text)
    for p in facts.places:
        if not _in_source(p.as_written, words):
            issues.append(Issue(code="intake_place_not_in_source",
                                message=f"Place '{p.as_written}' was not found in the source text"))
    for field in ("issued_iso", "onset_iso", "expires_iso"):
        v = getattr(facts, field)
        if v and parse_dt(v, pack) is None:
            issues.append(Issue(code="bad_datetime", message=f"{field} '{v}' is not an ISO date-time"))
    exp, iss = parse_dt(facts.expires_iso, pack), parse_dt(facts.issued_iso or facts.onset_iso, pack)
    if exp and iss and exp <= iss:
        issues.append(Issue(code="expiry_before_issue", message="Expiry is not after the issue time"))
    return issues


def tokens(s: str) -> list[str]:
    """Split on whitespace/punctuation only (Python's \\w would cut Sinhala/Tamil words at vowel signs)."""
    t = unicodedata.normalize("NFKC", s or "").casefold()
    return [w for w in re.split(r"[\s,;:.()\[\]{}\"'/\\|<>!?\-–—]+", t) if w]


def _in_source(place: str, source_words: list[str]) -> bool:
    from difflib import SequenceMatcher

    pw = tokens(place)
    if not pw:
        return False
    target = norm_name("".join(pw))
    n = len(pw)
    for size in {max(1, n - 1), n, n + 1}:
        for i in range(0, max(0, len(source_words) - size + 1)):
            cand = norm_name("".join(source_words[i:i + size]))
            if cand == target or SequenceMatcher(None, cand, target).ratio() >= 0.85:
                return True
    return False


def _level_words(pack: Pack) -> set[str]:
    words: set[str] = set()
    for sc in pack.hazard.scale:
        for lab in sc.label.values():
            for tok in tokens(lab):
                if len(tok) >= 2 and not any(ch.isdigit() for ch in tok):
                    words.add(tok)
    return words


def check_intake_coverage(facts: Facts, source_text: str, pack: Pack) -> list[Issue]:
    """Did Intake DROP something? A gazetteer place named in the source but absent from Facts is flagged.

    BLOCK when the place sits on a line that also carries a level word (e.g. "Level 3 … Passara"):
    that is almost certainly an affected area the model missed. Otherwise WARN (the place may just be
    mentioned, e.g. an issuing office).
    """
    from .geo import resolve

    covered: set[str] = set()
    for p in facts.places:
        if p.canonical:
            covered.add(p.canonical)
            covered.update(pack.ancestors(p.canonical))
        if p.parent_hint:
            r = resolve(pack, p.parent_hint)
            if r.place_id:
                covered.add(r.place_id)
    level_words = _level_words(pack)
    # each line's words and 1–3-word grams, computed once (not once per gazetteer entry)
    lines = []
    for line in source_text.splitlines():
        words = tokens(line)
        grams = {norm_name("".join(words[i:i + n])) for n in (1, 2, 3) for i in range(max(0, len(words) - n + 1))}
        lines.append((set(words), grams))
    issues = []
    for g in pack.region.gazetteer:
        if g.id in covered:
            continue
        variants = {norm_name(v) for v in list(g.names.values()) + list(g.aliases)}
        variants = {v for v in variants if len(v) >= 3}
        hit_lines = [words for words, grams in lines if variants & grams]
        if not hit_lines:
            continue
        on_level_line = any(ws & level_words for ws in hit_lines)
        name = g.names.get("en", g.id)
        if on_level_line:
            issues.append(Issue(code="place_in_source_not_extracted", severity="block",
                                message=(f"{name} appears on a level line of the source but is not in the "
                                         "extracted affected areas. Check the source and resubmit.")))
        else:
            issues.append(Issue(code="place_in_source_not_extracted", severity="warn",
                                message=(f"The source mentions {name} but it is not in the affected-area list. "
                                         "Confirm it is intentionally left out.")))
    return issues[:8]


# ------------------------------------------------------------ draft checks

_PLACEHOLDER = re.compile(r"\{[a-z_]+\}|\bTODO\b|lorem ipsum|\[(?:translat|placeholder)[^\]]*\]", re.I)
_MARKUP = re.compile(r"<\s*/?\s*[a-zA-Z!][^>]*>|\*\*|^#{1,6}\s", re.M)


def allowed_numbers(facts: Facts, derived: Derived, pack: Pack) -> set[str]:
    allowed: set[str] = set()
    allowed.update(str(l) for l in derived.levels)
    allowed.update(number_tokens(pack.region.authority.hotline))  # authority data, e.g. 117
    for s in pack.hazard.scale:
        for lab in s.label.values():
            allowed.update(number_tokens(lab))
    for m in facts.measurements:
        allowed.update(number_tokens(m.value))
        allowed.update(number_tokens(m.label))
        spec = pack.hazard.measures.get(m.kind)
        if spec:
            for ph in spec.phrase.values():
                allowed.update(number_tokens(ph.replace("{value}", "")))
    for iso in (facts.issued_iso, facts.onset_iso, facts.expires_iso):
        allowed.update(number_tokens(fmt_local(iso, pack)))
        for lg in pack.langs:
            allowed.update(number_tokens(fmt_human(iso, pack, lg)))
    for intent in {s.intent for s in pack.hazard.scale if s.level in derived.levels}:
        for ph in pack.hazard.actions.get(intent, {}).values():
            allowed.update(number_tokens(ph))
    return allowed


def check_draft(facts: Facts, derived: Derived, draft: LangDraft, lang: str, pack: Pack) -> list[Issue]:
    issues: list[Issue] = []

    def add(code: str, msg: str) -> None:
        issues.append(Issue(code=code, lang=lang, message=msg, source="code"))

    for fld in ("headline", "description", "instruction", "sms", "spoken"):
        if not getattr(draft, fld).strip():
            add("empty_field", f"{fld} is empty")
    if issues:
        return issues

    body = f"{draft.headline}\n{draft.description}\n{draft.instruction}"
    full = draft.all_text()
    nfull = norm_text(full)

    # leakage / markup
    if _PLACEHOLDER.search(full):
        add("placeholder_leak", "Draft contains an unfilled placeholder or TODO")
    if _MARKUP.search(full):
        add("markup_in_text", "Draft contains HTML/markdown markup; alerts must be plain text")

    # language / script
    meta = pack.region.languages[lang]
    share = script_share(body, meta.script)
    if share < 0.6:
        add("wrong_language", f"Only {share:.0%} of letters are {meta.script} script — text may be in the wrong language")

    # figures
    desc_tokens = set(number_tokens(draft.description))
    spoken_tokens = set(number_tokens(draft.spoken))
    for m in facts.measurements:
        v = number_tokens(m.value)[0] if number_tokens(m.value) else None
        if v and v not in desc_tokens:
            add("missing_number", f"Figure {m.value} is missing from the description")
        if v and v not in spoken_tokens:
            add("missing_number", f"Figure {m.value} is missing from the voice script")
    allowed = allowed_numbers(facts, derived, pack)
    for tok in sorted(set(number_tokens(full)) - allowed):
        add("unsupported_number", f"Number {tok} is not in the official source")

    # levels / labels  (catches Yellow-vs-Red style mislabelling)
    if derived.max_level is not None:
        nl = norm_text
        for lv in derived.levels:
            if nl(pack.label(lv, lang)) not in nl(draft.description):
                add("level_label_missing", f"Description lacks the exact label for level {lv}: “{pack.label(lv, lang)}”")
        top = nl(pack.label(derived.max_level, lang))
        if top not in nl(draft.headline):
            add("level_label_missing", f"Headline lacks the top-level label “{pack.label(derived.max_level, lang)}”")
        if top not in nl(draft.sms):
            add("level_label_missing", f"SMS lacks the top-level label “{pack.label(derived.max_level, lang)}”")
        for lv in pack.levels:
            if lv not in derived.levels and nl(pack.label(lv, lang)) in nfull:
                add("level_label_mismatch", f"Draft mentions “{pack.label(lv, lang)}” but no area is at level {lv}")

    # places
    expected = {p.canonical for p in facts.places if p.canonical}
    nd, ns, nsp = norm_text(draft.description), norm_text(draft.sms), norm_text(draft.spoken)
    for pid in sorted(expected):
        name = pack.place_name(pid, lang)
        if not name:
            add("gazetteer_name_missing", f"No {lang} name for {pid} in the gazetteer")
            continue
        n = norm_text(name)
        if n not in nd:
            add("place_missing", f"{name} is missing from the description")
        if n not in nsp:
            add("place_missing", f"{name} is missing from the voice script")
    if derived.max_level is not None:
        top_ids = derived.places_by_level.get(derived.max_level, [])
        top_names = [norm_text(pack.place_name(p, lang)) for p in top_ids if pack.place_name(p, lang)]
        if top_names and not any(n in ns for n in top_names):
            add("sms_missing_priority_place", "SMS names none of the highest-level areas")
        elif top_names and not all(n in ns for n in top_names):
            issues.append(Issue(code="sms_omits_areas", severity="warn", lang=lang, source="code",
                                message=(f"SMS names {sum(n in ns for n in top_names)} of {len(top_names)} highest-level "
                                         "areas; the rest are only in the description and voice script")))
    if derived.msg_type == "Update":
        word = norm_text(pack.region.languages[lang].update_word)
        for fld, text in (("headline", draft.headline), ("SMS", draft.sms)):
            if word not in norm_text(text):
                add("update_marker_missing",
                    f"This is an Update but the {fld} does not say “{pack.region.languages[lang].update_word}”")
    allowed_ids = set(expected)
    for pid in list(expected):
        allowed_ids.update(pack.ancestors(pid))
    allowed_names = {norm_text(pack.place_name(p, lang)) for p in allowed_ids}
    for g in pack.region.gazetteer:
        if g.id in allowed_ids:
            continue
        for lg in (lang, "en"):
            nm = norm_text(g.names.get(lg, ""))
            if len(nm) >= 3 and nm in nfull and not any(nm in a for a in allowed_names if a):
                add("unexpected_place", f"Draft names {g.names.get(lg)} which is not in the source")
                break

    # instruction
    if (derived.response_types or facts.intents or facts.actions) and len(draft.instruction.strip()) < 5:
        add("missing_instruction", "No instruction (what people should do)")
    # the authority's approved action for the highest level must be there word for word: a paraphrase or an
    # edit can turn "evacuate immediately" into "be careful", and a script cannot judge a paraphrase
    if derived.expected_intent:
        approved = pack.hazard.actions.get(derived.expected_intent, {}).get(lang, "")
        if approved:
            for fld, text in (("instruction", draft.instruction), ("SMS", draft.sms)):
                if norm_text(approved) not in norm_text(text):
                    add("instruction_intent_missing",
                        f"The {fld} lacks the approved {derived.expected_intent} action for level "
                        f"{derived.max_level}: “{approved}”")

    # channel limits
    if len(draft.headline) > pack.channels.headline_max:
        add("headline_too_long", f"Headline is {len(draft.headline)} chars (max {pack.channels.headline_max})")
    # budget for what actually leaves: drill marker, issuer, hotline and reference included (worst case)
    enc, units, segs = sms_segments(sms_envelope(pack, lang, draft.sms, exercise=True, ref=REF_PLACEHOLDER))
    if segs > pack.channels.sms_max_segments:
        add("sms_too_long", f"SMS is {segs} segments ({units} {enc} units, with sender, hotline, reference and drill "
                            f"marker); max {pack.channels.sms_max_segments}")
    vb = len(voice_envelope(pack, lang, draft.spoken, exercise=True).encode("utf-8"))
    if vb > pack.channels.voice_max_bytes:
        add("voice_too_long", f"Voice script is {vb} bytes (max {pack.channels.voice_max_bytes})")
    return issues
