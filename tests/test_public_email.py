"""Public danger map and email warnings: who sees what, who gets which email, and what is (not) stored."""

from __future__ import annotations

import ast
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lastmile import geo, notify, public
from lastmile.api import create_app
from lastmile.config import Settings
from lastmile.mailer import FileMailer, Mail, MailError, SmtpMailer, make_mailer
from lastmile.store import Store
from lastmile.subscriptions import SubscriptionError, SubscriptionStore, normalise_email

from clock import live_text

ROOT = Path(__file__).resolve().parent.parent
AT = "2025-11-27T20:00:00+05:30"


@pytest.fixture
def env(registry):
    store, mailer, subs = Store(), FileMailer(), SubscriptionStore()
    app = create_app(Settings(rate_limit_per_min=1000, dev_mailbox=True), store=store, registry=registry,
                     mailer=mailer, subscribers=subs)
    return TestClient(app), store, mailer, subs


def publish(c, text, mode="replay", who="A. Perera"):
    rid = c.post("/api/runs", json={"pack_id": "lk-landslide", "source_text": text, "mode": mode}).json()["id"]
    return c.post(f"/api/runs/{rid}/review", json={"reviewer": who, "decision": "approve"}).json()


def subscribe(c, mailer, email="kumar@example.com", areas=("LK-BD-PAS",), lang="ta", drills=True, min_level=1):
    r = c.post("/api/public/subscribe", json={"email": email, "region": "lk", "areas": list(areas), "lang": lang,
                                              "min_level": min_level, "include_exercises": drills, "consent": True})
    assert r.status_code == 202, r.text
    token = re.search(r"confirm=([\w-]+)", mailer.sent[-1].body).group(1)
    assert c.post("/api/public/confirm", json={"token": token}).status_code == 200


# ----------------------------------------------------------------------------- the public map
def test_map_and_situation_need_no_login_even_with_an_api_key(registry, corpus):
    store = Store()
    c = TestClient(create_app(Settings(api_key="k", rate_limit_per_min=100), store=store, registry=registry,
                              mailer=FileMailer(), subscribers=SubscriptionStore()))
    assert c.post("/api/runs", json={"pack_id": "lk-landslide", "source_text": "x" * 20}).status_code == 401
    for path in ("/public", "/api/public/regions", f"/api/public/situation?at={AT.replace('+', '%2B')}",
                 "/api/public/map.svg?lang=si", "/api/public/map.geojson", "/feeds/alerts.atom"):
        assert c.get(path).status_code == 200, path


def test_situation_takes_the_highest_level_per_area_and_respects_time(env, corpus):
    c, store, *_ = env
    publish(c, corpus["lk-clean-multilevel"]["text"])
    sit = c.get("/api/public/situation", params={"at": AT}).json()
    assert sit["area_levels"] == {"LK-BD-PAS": 3, "LK-BD-HAL": 2, "LK-BD-BAN": 2, "LK-NE-HAN": 1}
    assert sit["exercise"] is True and sit["alerts"][0]["reference"] in sit["alerts"][0]["identifier"].upper()
    assert sit["alerts"][0]["headline"]["si"].startswith("නායයාමේ")
    assert c.get("/api/public/situation").json()["alerts"] == []                # expired by today's clock
    assert c.get("/api/public/situation", params={"at": "2025-11-27T17:00:00+05:30"}).json()["alerts"] == []
    assert sit["latest_sent"].startswith("2025-11-27T18:00")


def test_area_lookup_covers_parents_and_children(env, corpus):
    c, *_ = env
    publish(c, corpus["lk-clean-multilevel"]["text"])
    pas = c.get("/api/public/area", params={"id": "LK-BD-PAS", "at": AT}).json()
    assert pas["level"] == 3 and pas["references"]
    district = c.get("/api/public/area", params={"id": "LK-BD", "at": AT}).json()
    assert district["level"] is None and district["sub_areas"] == {"LK-BD-PAS": 3, "LK-BD-HAL": 2, "LK-BD-BAN": 2}
    assert c.get("/api/public/area", params={"id": "LK-CO", "at": AT}).json()["level"] is None
    assert c.get("/api/public/area", params={"id": "nowhere"}).status_code == 404


def test_region_map_is_localised_coloured_and_escaped(lk):
    svg = geo.render_region_svg(lk, {"LK-BD-PAS": 3, "LK-NE-HAN": 1}, "ta", "பயிற்சி")
    assert "பசறை" in svg and "ஆபத்து வரைபடம்" in svg and geo.level_colour(lk, 3) in svg and "பயிற்சி" in svg
    assert svg.count("<polygon") >= len(lk.region.gazetteer) - 10                 # whole region drawn
    evil = lk.model_copy(deep=True)
    evil.region.gazetteer[0].names["en"] = "<script>alert(1)</script>"
    assert "<script>" not in geo.render_region_svg(evil, {}, "en")


def test_exercises_can_be_hidden_from_the_public_map(registry, corpus):
    store = Store()
    c = TestClient(create_app(Settings(rate_limit_per_min=100, public_show_exercise=False), store=store,
                              registry=registry, mailer=FileMailer(), subscribers=SubscriptionStore()))
    publish(c, corpus["lk-clean-multilevel"]["text"])
    assert c.get("/api/public/situation", params={"at": AT}).json()["alerts"] == []
    assert c.get("/api/public/map.geojson", params={"at": AT}).json()["features"] == []
    ident = store.published[0].identifier
    assert c.get(f"/feeds/cap/{ident}.xml").status_code == 404


def test_atom_feed_links_public_cap(env, corpus):
    c, store, *_ = env
    done = publish(c, corpus["lk-clean-multilevel"]["text"])
    feed = c.get("/feeds/alerts.atom", params={"at": AT}).text
    assert f"/feeds/cap/{done['cap_identifier']}.xml" in feed and feed.startswith("<?xml")
    capx = c.get(f"/feeds/cap/{done['cap_identifier']}.xml")
    assert capx.status_code == 200 and b"<status>Exercise</status>" in capx.content


# --------------------------------------------------------------------------- subscriptions
def test_double_opt_in_then_alert_email_in_my_language_about_my_area(env, corpus, lk):
    c, store, mailer, subs = env
    subscribe(c, mailer)
    sub = subs.by_email("kumar@example.com")
    assert sub.confirmed and sub.consent["version"] and "unsubscribe" in sub.consent["text"]
    done = publish(c, corpus["lk-clean-multilevel"]["text"])
    assert done["outbox"]["email"] == {"matched": 1, "sent": 1, "failed": 0, "by_lang": {"ta-LK": 1}, "mailer": "file",
                                      "status": "done"}
    m = mailer.sent[-1]
    assert m.to == "kumar@example.com" and m.subject.startswith("[பயிற்சி] ")
    assert "- பசறை: நிலை 3 – சிவப்பு. " + lk.hazard.actions["evacuate"]["ta"] in m.body
    assert "ஹங்குரன்கெத்த:" not in m.body.split("\n\n")[2]                        # not my area: not in "your areas"
    ref = done["outbox"]["reference"]
    assert f"/alerts?ref={ref}" in m.body and "/alerts?unsubscribe=" in m.body and "117" in m.body
    assert m.headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert "kumar@example.com" not in str(store.get(done["id"]).model_dump())         # no addresses on the run


def test_unconfirmed_drill_shy_and_high_threshold_subscribers_get_nothing(env, corpus):
    c, store, mailer, subs = env
    c.post("/api/public/subscribe", json={"email": "never@confirmed.lk", "region": "lk", "areas": ["LK-BD-PAS"],
                                          "lang": "en", "consent": True})
    subscribe(c, mailer, email="nodrills@example.com", drills=False)
    subscribe(c, mailer, email="redonly@example.com", areas=("LK-NE-HAN",), min_level=3)
    before = len(mailer.sent)
    done = publish(c, corpus["lk-clean-multilevel"]["text"])
    assert done["outbox"]["email"]["matched"] == 0 and len(mailer.sent) == before


def test_min_level_filters_and_district_subscription_matches_divisions(env, corpus, registry):
    c, store, mailer, subs = env
    subscribe(c, mailer, email="district@example.com", areas=("LK-BD",), lang="en", min_level=3)
    subscribe(c, mailer, email="yellow@example.com", areas=("LK-NE-HAN",), lang="si", min_level=2)
    done = publish(c, corpus["lk-clean-multilevel"]["text"])
    to = [m.to for m in mailer.sent if m.subject.startswith("[")]
    assert to == ["district@example.com"]                       # Badulla has a Level 3 division; Hanguranketha is L1
    body = mailer.sent[-1].body
    assert "- Passara: Level 3 – Red." in body and "- Haldummulla: Level 2 – Amber." in body
    assert done["outbox"]["email"]["matched"] == 1


def test_actual_alert_reaches_subscribers_without_the_drill_opt_in(registry, corpus):
    store, mailer, subs = Store(), FileMailer(), SubscriptionStore()
    c = TestClient(create_app(Settings(rate_limit_per_min=1000, allow_actual=True), store=store, registry=registry,
                              mailer=mailer, subscribers=subs))
    subscribe(c, mailer, email="real@example.com", drills=False, lang="en")
    rid = c.post("/api/runs", json={"pack_id": "lk-landslide", "mode": "live",
                                    "source_text": live_text(corpus["lk-clean-multilevel"]["text"])}).json()["id"]
    c.post(f"/api/runs/{rid}/review", json={"reviewer": "Alice Perera", "decision": "approve"})
    done = c.post(f"/api/runs/{rid}/review", json={"reviewer": "Bob Silva", "decision": "approve"}).json()
    m = mailer.sent[-1]
    assert done["cap_status"] == "Actual" and m.to == "real@example.com" and not m.subject.startswith("[")
    assert "EXERCISE" not in m.body


def test_update_and_cancel_are_emailed_too(env, corpus):
    c, store, mailer, subs = env
    subscribe(c, mailer, email="en@example.com", areas=("LK-BD-HAL",), lang="en")
    publish(c, corpus["lk-clean-multilevel"]["text"])
    upd = publish(c, corpus["lk-update-escalation"]["text"])
    assert "UPDATE" in mailer.sent[-1].subject and "Haldummulla: Level 3 – Red" in mailer.sent[-1].body
    canc = publish(c, corpus["lk-cancel"]["text"])
    assert canc["outbox"]["email"]["sent"] == 1 and "cancelled" in mailer.sent[-1].subject
    assert "- Haldummulla" in mailer.sent[-1].body and upd["status"] == "published"


def test_no_enumeration_and_consent_required(env):
    c, store, mailer, subs = env
    r1 = c.post("/api/public/signin", json={"email": "nobody@example.com"})
    subscribe(c, mailer, email="someone@example.com")
    r2 = c.post("/api/public/signin", json={"email": "someone@example.com"})
    assert r1.status_code == r2.status_code == 202 and r1.json() == r2.json()
    no_consent = c.post("/api/public/subscribe", json={"email": "a@example.com", "region": "lk", "areas": ["LK-BD"],
                                                       "lang": "en", "consent": False})
    assert no_consent.status_code == 422
    for bad in ({"email": "not-an-email"}, {"areas": ["LK-XX"]}, {"areas": []}, {"lang": "fr"}, {"min_level": 9}):
        body = {"email": "b@example.com", "region": "lk", "areas": ["LK-BD"], "lang": "en", "consent": True, **bad}
        assert c.post("/api/public/subscribe", json=body).status_code == 422, bad


def test_magic_link_signin_manage_and_delete(env):
    c, store, mailer, subs = env
    subscribe(c, mailer, email="me@example.com", lang="si")
    c.post("/api/public/signin", json={"email": "me@example.com"})
    token = re.search(r"signin=([\w-]+)", mailer.sent[-1].body).group(1)
    assert mailer.sent[-1].subject == "ඔබේ පිවිසුම් සබැඳිය"
    sess = c.post("/api/public/session", json={"token": token}).json()["session"]
    assert c.post("/api/public/session", json={"token": token}).status_code == 404       # single use
    h = {"X-Session": sess}
    assert c.get("/api/public/me", headers=h).json()["areas"] == ["LK-BD-PAS"]
    upd = c.put("/api/public/me", headers=h, json={"areas": ["LK-KY"], "lang": "en", "min_level": 2,
                                                   "include_exercises": False}).json()
    assert upd["areas"] == ["LK-KY"] and upd["min_level"] == 2
    assert c.get("/api/public/me").status_code == 401
    assert c.delete("/api/public/me", headers=h).status_code == 200
    assert subs.by_email("me@example.com") is None and c.get("/api/public/me", headers=h).status_code == 401


def test_one_click_unsubscribe_deletes_the_record(env, corpus):
    c, store, mailer, subs = env
    subscribe(c, mailer)
    publish(c, corpus["lk-clean-multilevel"]["text"])
    link = mailer.sent[-1].headers["List-Unsubscribe"].strip("<>")
    token = link.split("token=")[1]
    assert c.post("/api/public/unsubscribe?token=" + token, data={"List-Unsubscribe": "One-Click"}).status_code == 200
    assert subs.by_email("kumar@example.com") is None
    assert c.post("/api/public/unsubscribe", json={"token": token}).status_code == 404
    assert c.post("/api/public/unsubscribe", json={"token": "sub_x.forged"}).status_code == 404


def test_tokens_are_stored_hashed_and_expire(tmp_path):
    subs = SubscriptionStore(tmp_path)
    sub, token = subs.subscribe("x@example.com", "lk", ["LK-BD"], "en", 1, False)
    raw = (tmp_path / "subscribers.json").read_text()
    assert token not in raw and "x@example.com" in raw
    for p in sub.pending.values():
        p["expires"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with pytest.raises(SubscriptionError) as e:
        subs.confirm(token)
    assert e.value.code == "expired"
    again = SubscriptionStore(tmp_path)                                         # survives a restart
    assert again.by_email("x@example.com") is not None
    if os.name != "nt":  # Windows st_mode does not encode filesystem ACL permissions.
        assert (tmp_path / "secret.key").stat().st_mode & 0o077 == 0


def test_unconfirmed_signups_are_purged(tmp_path):
    subs = SubscriptionStore(tmp_path)
    sub, _ = subs.subscribe("old@example.com", "lk", ["LK-BD"], "en", 1, False)
    sub.created_at = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    subs.purge_stale()
    assert subs.by_email("old@example.com") is None


def test_confirmation_mails_are_throttled_per_address(env):
    c, store, mailer, subs = env
    body = {"email": "victim@example.com", "region": "lk", "areas": ["LK-BD"], "lang": "en", "consent": True}
    for _ in range(6):
        assert c.post("/api/public/subscribe", json=body).status_code == 202
    assert sum(m.to == "victim@example.com" for m in mailer.sent) == 3


def test_public_forms_are_rate_limited_per_client(env):
    c, *_ = env
    codes = [c.post("/api/public/signin", json={"email": "z@example.com"}).status_code for _ in range(12)]
    assert codes.count(202) == 10 and codes[-1] == 429


def test_mail_off_disables_subscriptions(registry):
    c = TestClient(create_app(Settings(mail="off"), store=Store(), registry=registry, subscribers=SubscriptionStore()))
    r = c.post("/api/public/subscribe", json={"email": "a@example.com", "region": "lk", "areas": ["LK-BD"],
                                              "lang": "en", "consent": True})
    assert r.status_code == 503 and c.get("/api/health").json()["mail"] == "off"


def test_dev_mailbox_is_off_unless_asked(registry):
    c = TestClient(create_app(Settings(), store=Store(), registry=registry, mailer=FileMailer(),
                              subscribers=SubscriptionStore()))
    assert c.get("/api/dev/mailbox").status_code == 404


def test_email_failures_never_unpublish(registry, corpus):
    class Broken(FileMailer):
        def send(self, mail):
            if mail.subject.startswith("["):
                raise MailError("relay down")
            super().send(mail)

    store, mailer, subs = Store(), Broken(), SubscriptionStore()
    c = TestClient(create_app(Settings(rate_limit_per_min=1000), store=store, registry=registry, mailer=mailer,
                              subscribers=subs))
    subscribe(c, mailer)
    done = publish(c, corpus["lk-clean-multilevel"]["text"])
    assert done["status"] == "published" and done["outbox"]["email"]["failed"] == 1
    assert any(a["action"] == "email_dispatch" and "1 failed" in a["detail"] for a in done["audit"])


# ------------------------------------------------------------------------------ mailers
def test_smtp_mailer_uses_starttls_and_login(monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context):
            calls.append(("starttls",))

        def login(self, u, p):
            calls.append(("login", u))

        def send_message(self, msg):
            calls.append(("send", msg["To"], msg["Subject"]))

    monkeypatch.setattr("lastmile.mailer.smtplib.SMTP", FakeSMTP)
    SmtpMailer("smtp.example.org", 587, "u", "secret", True, "LastMile <a@b.lk>").send(Mail("x@y.lk", "S", "B"))
    assert calls == [("connect", "smtp.example.org", 587), ("starttls",), ("login", "u"), ("send", "x@y.lk", "S")]


def test_make_mailer_choices(monkeypatch, tmp_path):
    assert make_mailer("off") is None
    fm = make_mailer("file", str(tmp_path))
    fm.send(Mail("a@b.lk", "s", "b"))
    assert list((tmp_path / "mail").glob("*.eml"))
    monkeypatch.delenv("LASTMILE_SMTP_HOST", raising=False)
    with pytest.raises(ValueError):
        make_mailer("smtp")
    with pytest.raises(ValueError):
        make_mailer("pigeon")


def test_email_normalisation():
    assert normalise_email(" Kumar@Example.COM ") == "Kumar@example.com"
    for bad in ("", "a@b", "a b@c.lk", "<a@b.lk>", "a@b.c"):
        with pytest.raises(SubscriptionError):
            normalise_email(bad)


# ---------------------------------------------------------------------------- invariants
def test_agents_cannot_reach_email_or_subscribers():
    banned = {"mailer", "notify", "subscriptions"}
    for name in ("pipeline.py", "llm.py", "prompts.py", "templates.py", "checks.py", "rules.py", "plugins.py",
                 "mock_gemini.py", "adk_app.py"):
        tree = ast.parse((ROOT / "lastmile" / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [getattr(node, "module", "") or ""] + [a.name for a in node.names]
                assert not any(n.split(".")[-1] in banned for n in names), (name, names)


def test_notify_and_public_compose_without_network(registry, corpus):
    assert notify.dispatch(None, SubscriptionStore(), Store(), registry.get("lk-landslide"),
                           Store().get("nothing") or _published_run(registry, corpus), "http://x")["mailer"] == "off"
    assert public.regions(registry)[0]["languages"]["si"] == "සිංහල"


def _published_run(registry, corpus):
    from lastmile import publish as pub
    from lastmile.llm import FakeLLM
    from lastmile.pipeline import Pipeline
    st = Store()
    run = Pipeline(FakeLLM(), registry, st).run(corpus["lk-clean-multilevel"]["text"], "lk-landslide")
    return pub.review(st, registry, Settings(), run.id, "A. Perera", "approve")


# ------------------------------------------------------------------- email speed: batching, background
class _RelaySMTP:
    """A fake SMTP relay that records connections; can drop the connection or refuse an address."""
    log: list = []
    drop_after: int | None = None
    refuse: set = set()

    def __init__(self, host, port, timeout):
        self.sent = 0
        _RelaySMTP.log.append("connect")

    def starttls(self, context):
        _RelaySMTP.log.append("starttls")

    def login(self, u, p):
        _RelaySMTP.log.append("login")

    def send_message(self, msg):
        import smtplib

        if _RelaySMTP.drop_after is not None and self.sent >= _RelaySMTP.drop_after:
            _RelaySMTP.drop_after = None
            raise smtplib.SMTPServerDisconnected("relay closed the connection")
        if msg["To"] in _RelaySMTP.refuse:
            raise smtplib.SMTPRecipientsRefused({msg["To"]: (550, b"no such user")})
        self.sent += 1
        _RelaySMTP.log.append(("send", msg["To"]))

    def quit(self):
        _RelaySMTP.log.append("quit")

    def close(self):
        pass


@pytest.fixture
def relay(monkeypatch):
    _RelaySMTP.log, _RelaySMTP.drop_after, _RelaySMTP.refuse = [], None, set()
    monkeypatch.setattr("lastmile.mailer.smtplib.SMTP", _RelaySMTP)
    return _RelaySMTP


def test_smtp_batch_uses_one_connection_and_survives_drops_and_refusals(relay):
    mails = [Mail(f"p{i}@example.org", "S", "B") for i in range(6)]
    relay.drop_after, relay.refuse = 2, {"p4@example.org"}
    errors = SmtpMailer("relay", 587, "u", "p").send_batch(mails)
    assert errors == [None, None, None, None, "SMTPRecipientsRefused", None]
    assert relay.log.count("login") == 2          # one login, one reconnect after the drop — not one per email
    assert [x[1] for x in relay.log if isinstance(x, tuple)] == [f"p{i}@example.org" for i in (0, 1, 2, 3, 5)]


def test_smtp_alert_emails_do_not_hold_up_the_approval(registry, corpus, monkeypatch):
    """Over a real relay the approve request returns at once; the emails follow and are counted on the run."""
    import threading

    gate = threading.Event()

    class SlowRelay(SmtpMailer):
        def send_batch(self, mails):
            gate.wait(5)  # the relay is "slow" until the test has seen the approval come back
            return [None] * len(mails)

    store, subs = Store(), SubscriptionStore()
    c = TestClient(create_app(Settings(rate_limit_per_min=1000), store=store, registry=registry,
                              mailer=SlowRelay("relay"), subscribers=subs))
    for i in range(3):
        s, token = subs.subscribe(f"p{i}@example.org", "lk", ["LK-BD-PAS"], "si", 1, True)
        subs.confirm(token)
    done = publish(c, corpus["lk-clean-multilevel"]["text"])
    assert done["status"] == "published"
    assert done["outbox"]["email"]["status"] == "sending" and done["outbox"]["email"]["matched"] == 3
    gate.set()
    for _ in range(100):
        email = c.get(f"/api/runs/{done['id']}").json()["outbox"]["email"]
        if email["status"] != "sending":
            break
        time.sleep(0.05)
    assert email["status"] == "done" and email["sent"] == 3 and email["failed"] == 0
    run = c.get(f"/api/runs/{done['id']}").json()
    assert any(a["action"] == "email_dispatch" and "3 sent" in a["detail"] for a in run["audit"])


def test_smtp_login_failure_fails_the_batch_fast(relay, monkeypatch):
    import smtplib

    def bad_login(self, u, p):
        _RelaySMTP.log.append("login")
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr(_RelaySMTP, "login", bad_login)
    errors = SmtpMailer("relay", 587, "u", "wrong").send_batch([Mail(f"p{i}@example.org", "S", "B") for i in range(5)])
    assert errors == ["SMTPAuthenticationError"] * 5 and relay.log.count("login") == 1
    with pytest.raises(MailError) as e:
        SmtpMailer("relay", 587, "u", "wrong").send(Mail("x@y.lk", "S", "B"))
    assert "wrong" not in str(e.value)  # never echo credentials


def test_cached_public_map_is_never_stale(env, corpus):
    """The map drawing is cached by its inputs; a newly published alert or a cancellation must show at once."""
    c, *_ = env
    t2 = "2025-11-28T10:00:00%2B05:30"  # after the cancellation is issued, before the warning would expire

    def coloured_areas():
        return c.get(f"/api/public/map.svg?lang=si&at={t2}").text.count('fill-opacity="0.8"')  # affected divisions

    assert coloured_areas() == 0
    assert publish(c, corpus["lk-clean-multilevel"]["text"])["status"] == "published"
    assert coloured_areas() == 4 and coloured_areas() == 4   # second one served from the cache
    assert publish(c, corpus["lk-cancel"]["text"])["msg_type"] == "Cancel"
    assert coloured_areas() == 0
