"""Turn a published alert into one email per matching subscriber, in their language, about their areas.

Runs only after publish.py has published (called by the API, never by the pipeline or the agents). An email failure
never un-publishes an alert: it is counted and written to the run's audit.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from .mailer import Mail, Mailer
from .packs import Pack
from .publish import reference
from .schemas import Run
from .store import Store
from .subscriptions import CONSENT_TEXT, Subscriber, SubscriptionStore


def _t(pack: Pack, lang: str, key: str, **kw) -> str:
    return pack.region.languages[lang].ui_text(key).format(**kw)


def _areas_of(run: Run, store: Store) -> dict[str, int]:
    """The areas an email is about: the alert's own areas, or for a cancellation, those of the alerts it closes."""
    if run.msg_type == "Cancel":
        ids = {r.split(",")[1] for r in run.references if r.count(",") >= 2}
        out: dict[str, int] = {}
        for a in store.published:
            if a.identifier in ids:
                for pid, lv in a.place_levels.items():
                    out[pid] = max(lv, out.get(pid, 0))
        return out
    assert run.derived
    return {pid: lv for lv, pids in run.derived.places_by_level.items() for pid in pids}


def compose_alert(pack: Pack, run: Run, sub: Subscriber, mine: dict[str, int], base_url: str,
                  unsubscribe_token: str) -> Mail:
    lang = sub.lang if sub.lang in run.drafts else pack.langs[0]
    d = run.drafts[lang]
    meta = pack.region.languages[lang]
    exercise = run.cap_status != "Actual"
    ref = reference(run.cap_identifier or "")
    lines = [meta.exercise_prefix, ""] if exercise else []
    lines += [d.headline, "", _t(pack, lang, "your_areas") + ":"]
    for pid, lv in sorted(mine.items(), key=lambda kv: (-kv[1], kv[0])):
        name = pack.place_name(pid, lang) or pid
        if run.msg_type == "Cancel":
            lines.append(f"- {name}")
        else:
            level = pack.level_def(lv)
            action = pack.hazard.actions.get(level.intent, {}).get(lang, "") if level else ""
            lines.append(f"- {name}: {pack.label(lv, lang)}. {action}".rstrip())
    lines += ["", d.description, "", f"{_t(pack, lang, 'what_to_do')}: {d.instruction}"]
    if pack.region.authority.hotline:
        lines.append(meta.help_voice.format(hotline=pack.region.authority.hotline))
    lines += ["", f"{_t(pack, lang, 'reference')}: {ref}",
              f"{_t(pack, lang, 'check_at')}: {base_url}/alerts?ref={ref}", "", "--",
              pack.region.authority.sender_name or pack.hazard.issuer,
              _t(pack, lang, "why_receiving"),
              f"{_t(pack, lang, 'unsubscribe')}: {base_url}/alerts?unsubscribe={unsubscribe_token}"]
    subject = (f"[{_t(pack, lang, 'exercise_tag')}] " if exercise else "") + d.headline
    one_click = f"<{base_url}/api/public/unsubscribe?token={unsubscribe_token}>"
    return Mail(to=sub.email, subject=subject[:180], body="\n".join(lines), lang=meta.cap_lang,
                headers={"List-Unsubscribe": one_click, "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
                         "X-LastMile-Reference": ref, "X-LastMile-Status": run.cap_status or "Exercise"})


def compose_confirm(pack: Pack, sub: Subscriber, token: str, base_url: str) -> Mail:
    lang = sub.lang
    areas = ", ".join(pack.place_name(a, lang) or a for a in sub.areas)
    body = "\n".join([_t(pack, lang, "confirm_intro", areas=areas), "", f"{base_url}/alerts?confirm={token}", "",
                      _t(pack, lang, "consent_label") + ":", CONSENT_TEXT, "", _t(pack, lang, "ignore_if_not_you")])
    return Mail(to=sub.email, subject=_t(pack, lang, "confirm_subject"), body=body,
                lang=pack.region.languages[lang].cap_lang)


def compose_signin(pack: Pack, sub: Subscriber, token: str, base_url: str) -> Mail:
    lang = sub.lang
    body = "\n".join([_t(pack, lang, "signin_intro"), "", f"{base_url}/alerts?signin={token}", "",
                      _t(pack, lang, "ignore_if_not_you")])
    return Mail(to=sub.email, subject=_t(pack, lang, "signin_subject"), body=body,
                lang=pack.region.languages[lang].cap_lang)


def prepare(mailer: Optional[Mailer], subs: SubscriptionStore, store: Store, pack: Pack, run: Run,
            base_url: str) -> tuple[dict, list[Mail]]:
    """Who gets an email about a just-published run, and the emails themselves (fast: no network)."""
    summary = {"matched": 0, "sent": 0, "failed": 0, "by_lang": {}, "mailer": getattr(mailer, "name", "off"),
               "status": "done"}
    if mailer is None or run.status != "published":
        return summary, []
    targets = subs.matching(pack, _areas_of(run, store), run.cap_status or "Exercise")
    summary["matched"] = len(targets)
    return summary, [compose_alert(pack, run, s, mine, base_url, subs.unsubscribe_token(s)) for s, mine in targets]


def deliver(mailer: Optional[Mailer], mails: list[Mail], summary: dict, workers: int = 4) -> dict:
    """Send prepared emails; fills in the counts (no addresses). One failure never stops the others."""
    if mailer is None or not mails:
        return summary

    def send(m: Mail) -> Optional[str]:
        try:
            mailer.send(m)
            return None
        except Exception as e:  # noqa: BLE001 — one bad address must not stop the others
            return type(e).__name__

    def send_chunk(chunk: list[Mail]) -> list[Optional[str]]:
        try:
            return mailer.send_batch(chunk)  # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001
            return [type(e).__name__] * len(chunk)

    if mailer.name == "file" or len(mails) <= 1:
        results = [send(m) for m in mails]
    elif hasattr(mailer, "send_batch"):  # a few connections, each carrying many messages
        n = max(1, min(workers, len(mails)))
        chunks = [mails[i::n] for i in range(n)]
        with ThreadPoolExecutor(max_workers=n) as ex:
            per_chunk = list(ex.map(send_chunk, chunks))
        by_mail = {id(m): err for chunk, errs in zip(chunks, per_chunk) for m, err in zip(chunk, errs)}
        results = [by_mail[id(m)] for m in mails]
    else:  # other network mailers: a small pool, so a slow relay does not serialise hundreds of sends
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(mails)))) as ex:
            results = list(ex.map(send, mails))
    for m, err in zip(mails, results):
        if err:
            summary["failed"] += 1
        else:
            summary["sent"] += 1
            summary["by_lang"][m.lang] = summary["by_lang"].get(m.lang, 0) + 1
    return summary


def dispatch(mailer: Optional[Mailer], subs: SubscriptionStore, store: Store, pack: Pack, run: Run,
             base_url: str, workers: int = 4) -> dict:
    """Email every matching subscriber about a just-published run, now. Returns counts only (no addresses)."""
    summary, mails = prepare(mailer, subs, store, pack, run, base_url)
    return deliver(mailer, mails, summary, workers)

