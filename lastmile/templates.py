"""Deterministic 'fast path' text from pre-approved pack templates. No AI.

Used (1) for cancellations, (2) as the offline stand-in for the Localizer in tests,
(3) as the degraded mode if the model is unavailable (flagged for review), and (4) as the
whole drafting step in template-first mode (LASTMILE_DRAFTING=templates; AI only reads and verifies).
"""

from __future__ import annotations

from typing import Optional

from .checks import REF_PLACEHOLDER, fmt_human, sms_envelope, sms_segments
from .packs import Pack
from .schemas import Derived, Facts, Issue, LangDraft


DRAFTING_MODES = ("model", "templates")


class TemplateError(ValueError):
    pass


def _display(pack: Pack, pid: str, lang: str, with_parent: bool = True) -> str:
    name = pack.place_name(pid, lang)
    if with_parent:
        parent = pack.place(pid).parent
        if parent and pack.place_name(parent, lang):
            return f"{name} ({pack.place_name(parent, lang)})"
    return name


def _limit(names: list[str], n: int, more: str) -> str:
    return ", ".join(names[:n]) + (f" {more}" if len(names) > n else "")


def render_draft(facts: Facts, derived: Derived, lang: str, pack: Pack) -> LangDraft:
    if derived.max_level is None:
        raise TemplateError("no levels to render")
    t = pack.hazard.templates[lang]
    event = pack.hazard.event[lang]
    by_level_parts, top_names, top_plain = [], [], []
    for lv in derived.levels:
        names = [_display(pack, p, lang) for p in derived.places_by_level[lv]]
        by_level_parts.append(f"{pack.label(lv, lang)}: {', '.join(names)}.")
        if lv == derived.max_level:
            top_names = names
            top_plain = [pack.place_name(p, lang) for p in derived.places_by_level[lv]]

    measures = []
    for m in facts.measurements:
        spec = pack.hazard.measures.get(m.kind)
        if spec and lang in spec.phrase:
            measures.append(spec.phrase[lang].format(value=m.value))
        else:
            measures.append(f"{m.label}: {m.value} {m.unit}.".strip())

    # advice per level, naming that level's areas: a Level 1 resident must not read "evacuate" as meant for them
    actions = []
    for lv in derived.levels:
        intent = pack.level_def(lv).intent  # type: ignore[union-attr]
        names = ", ".join(pack.place_name(p, lang) for p in derived.places_by_level[lv])
        actions.append(f"{pack.label(lv, lang)} ({names}): {pack.hazard.actions[intent][lang].strip()}")
    top_intent = pack.level_def(derived.max_level).intent  # type: ignore[union-attr]

    meta = pack.region.languages[lang]
    exp = fmt_human(facts.expires_iso, pack, lang)
    if not exp:
        raise TemplateError("expiry time required")
    slots = {
        "event": event,
        "top_label": pack.label(derived.max_level, lang),
        "top_places": _limit(top_names, 3, meta.more_areas),
        "top_places_sms": _limit(top_plain, 4, meta.more_areas),
        "by_level": " ".join(by_level_parts),
        "measures": " ".join(measures),
        "actions": " ".join(actions),
        "top_action": pack.hazard.actions[top_intent][lang],
        "valid_until": exp,
        "valid_until_sms": fmt_human(facts.expires_iso, pack, lang, sms=True),
    }
    out = LangDraft(**{k: getattr(t, k).format_map(slots).replace("  ", " ").strip()
                       for k in ("headline", "description", "instruction", "sms", "spoken")})
    if derived.msg_type == "Update":
        line = update_line(derived, lang, pack)
        out.headline = f"{meta.update_word} – {out.headline}"
        out.description = f"{line} {out.description}".strip()
        out.spoken = f"{line} {out.spoken}".strip()

    def sms_with(n: int) -> str:
        sms = t.sms.format_map({**slots, "top_places_sms": _limit(top_plain, n, meta.more_areas)})
        sms = sms.replace("  ", " ").strip()
        if derived.msg_type == "Update":
            sms = f"{meta.update_word}: {sms}"
        return sms.replace("–", "-").replace("—", "-") if meta.script == "Latin" else sms  # Latin: stay GSM-7

    # name as many of the highest-level areas as fit the channel once sender, hotline, reference and the drill
    # marker are added (check_draft warns sms_omits_areas when some are left to the description and voice)
    for n in range(min(4, len(top_plain)), 0, -1):
        out.sms = sms_with(n)
        envelope = sms_envelope(pack, lang, out.sms, exercise=True, ref=REF_PLACEHOLDER)
        if sms_segments(envelope)[2] <= pack.channels.sms_max_segments:
            break
    return out


def update_line(derived: Derived, lang: str, pack: Pack) -> str:
    """'UPDATE: Haldummulla is now at Level 3 – Red.' from the code-computed level changes (empty for an Alert)."""
    if derived.msg_type != "Update":
        return ""
    meta = pack.region.languages[lang]
    changes = [meta.now_at.format(place=pack.place_name(pid, lang), label=pack.label(new, lang))
               for pid, (_old, new) in sorted(derived.level_changes.items())]
    return f"{meta.update_word}: {'; '.join(changes)}." if changes else f"{meta.update_word}."


def render_cancel(pack: Pack, lang: str, place_ids: Optional[list[str]] = None, error: bool = False) -> LangDraft:
    """Cancellation text naming the areas of the alert(s) being cancelled; `error` = retraction of a false alarm."""
    c = pack.hazard.templates[lang].cancel
    event = pack.hazard.event[lang]
    names = [pack.place_name(p, lang) for p in (place_ids or []) if pack.has_place(p) and pack.place_name(p, lang)]
    areas = _limit(names, 4, pack.region.languages[lang].more_areas) or "-"
    use_error = error and c.error_headline and c.error_sms and c.error_spoken
    headline = (c.error_headline if use_error else c.headline).format(event=event, areas=areas)
    sms = (c.error_sms if use_error else c.sms).format(event=event, areas=areas)
    if pack.region.languages[lang].script == "Latin":
        sms = sms.replace("–", "-")
    spoken = (c.error_spoken if use_error else c.spoken).format(event=event, areas=areas)
    return LangDraft(headline=headline, description=headline, instruction=spoken, sms=sms, spoken=spoken)


def template_draft(facts: Facts, derived: Derived, lang: str, pack: Pack) -> tuple[LangDraft, Issue]:
    """Template-first mode (no AI Localizer): the approved draft plus the finding that says so.

    Used by pipeline.py and adk_app.py so both graphs flag it identically.
    """
    try:
        d = render_draft(facts, derived, lang, pack)
    except TemplateError as e:
        return LangDraft(), Issue(code="template_unavailable", lang=lang, source="code",
                                  message=f"Template-first mode: the approved templates cannot cover this alert ({e})")
    return d, Issue(code="template_draft", severity="warn", lang=lang, source="code",
                    message="Template-first mode: drafted from the pack's approved templates; no AI wrote this text. "
                            "Read it against the source.")
