"""Email subscribers: who wants which areas, in which language, with what consent. Publish-side only.

Privacy by design (Sri Lanka PDPA No. 9 of 2022, core provisions from 1 Jan 2027): only the email address and the
alert preferences are stored; consent is recorded with time and wording version; unsubscribing deletes the record;
every token is stored as a SHA-256 hash, so a leaked file cannot be used to sign in or unsubscribe anyone.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from .packs import Pack
from .schemas import utcnow_iso

CONSENT_VERSION = "2026-10-13"
CONSENT_TEXT = ("I agree that LastMile Alert stores my email address and these alert preferences to email me "
                "warnings for the areas I chose. I can unsubscribe at any time, which deletes this record.")
CONFIRM_TTL = timedelta(hours=24)
SIGNIN_TTL = timedelta(minutes=30)
SESSION_TTL = timedelta(days=7)
MAILS_PER_ADDRESS_PER_HOUR = 3  # confirmation / sign-in mails: the form must not become a way to flood an inbox
_EMAIL = re.compile(r"^[^@\s<>\"',;]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")


class SubscriptionError(ValueError):
    def __init__(self, message: str, code: str = "bad_request", status: int = 422):
        super().__init__(message)
        self.code, self.status = code, status


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def normalise_email(email: str) -> str:
    e = (email or "").strip()
    if not _EMAIL.match(e):
        raise SubscriptionError("enter a valid email address", "bad_email")
    local, domain = e.rsplit("@", 1)
    return f"{local}@{domain.lower()}"


class Subscriber(BaseModel):
    id: str
    email: str
    region: str
    areas: list[str]
    lang: str
    min_level: int = 1
    include_exercises: bool = False
    confirmed: bool = False
    created_at: str = Field(default_factory=utcnow_iso)
    consent: dict = Field(default_factory=dict)          # {"at", "version", "text"}
    pending: dict[str, dict] = Field(default_factory=dict)  # token hash -> {"purpose", "expires"}
    sessions: dict[str, str] = Field(default_factory=dict)  # session hash -> expires

    def public_view(self) -> dict:
        return {"email": self.email, "region": self.region, "areas": self.areas, "lang": self.lang,
                "min_level": self.min_level, "include_exercises": self.include_exercises,
                "confirmed": self.confirmed, "consent": self.consent}


def validate_prefs(pack_by_region: dict[str, Pack], region: str, areas: list[str], lang: str, min_level: int) -> Pack:
    pack = pack_by_region.get(region)
    if pack is None:
        raise SubscriptionError(f"unknown region '{region}'", "bad_region")
    areas = [a for a in dict.fromkeys(areas or [])]
    if not areas or len(areas) > 50:
        raise SubscriptionError("choose between 1 and 50 areas", "bad_areas")
    unknown = [a for a in areas if not pack.has_place(a)]
    if unknown:
        raise SubscriptionError(f"unknown areas {unknown[:5]}", "bad_areas")
    if lang not in pack.region.languages:
        raise SubscriptionError(f"language must be one of {list(pack.region.languages)}", "bad_lang")
    if min_level not in pack.levels:
        raise SubscriptionError(f"minimum level must be one of {pack.levels}", "bad_level")
    return pack


class SubscriptionStore:
    """In memory, mirrored to <data_dir>/subscribers.json when a data dir is configured."""

    def __init__(self, data_dir: Optional[os.PathLike | str] = None, secret: Optional[str] = None) -> None:
        self._lock = threading.RLock()
        self.path = Path(data_dir) / "subscribers.json" if data_dir else None
        self._secret = self._load_secret(data_dir, secret)
        self.subs: dict[str, Subscriber] = {}
        self._mail_log: dict[str, deque] = defaultdict(deque)
        if self.path and self.path.is_file():
            try:
                for raw in json.loads(self.path.read_text(encoding="utf-8")):
                    s = Subscriber.model_validate(raw)
                    self.subs[s.id] = s
            except (OSError, ValueError):
                self.subs = {}

    @staticmethod
    def _load_secret(data_dir, secret: Optional[str]) -> bytes:
        """Key for unsubscribe links: LASTMILE_SECRET_KEY, else one generated once and kept in the data dir."""
        secret = secret or os.environ.get("LASTMILE_SECRET_KEY")
        if secret:
            return secret.encode("utf-8")
        if data_dir:
            f = Path(data_dir) / "secret.key"
            if f.is_file():
                return f.read_bytes()
            Path(data_dir).mkdir(parents=True, exist_ok=True)
            key = secrets.token_bytes(32)
            f.write_bytes(key)
            os.chmod(f, 0o600)
            return key
        return secrets.token_bytes(32)  # memory-only store: links last as long as the process

    # ------------------------------------------------------------ storage
    def _save(self) -> None:
        if self.path:
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps([s.model_dump() for s in self.subs.values()], ensure_ascii=False, indent=1),
                           encoding="utf-8")
            os.replace(tmp, self.path)

    def by_email(self, email: str) -> Optional[Subscriber]:
        return next((s for s in self.subs.values() if s.email.casefold() == email.casefold()), None)

    def purge_stale(self) -> None:
        """Unconfirmed sign-ups older than the confirmation window are deleted (data minimisation)."""
        with self._lock:
            cutoff = _now() - CONFIRM_TTL
            stale = [k for k, s in self.subs.items()
                     if not s.confirmed and datetime.fromisoformat(s.created_at) < cutoff]
            for k in stale:
                del self.subs[k]
            if stale:
                self._save()

    # ----------------------------------------------------------- throttling
    def allow_mail(self, email: str) -> bool:
        with self._lock:
            q, now = self._mail_log[email.casefold()], time.monotonic()
            while q and now - q[0] > 3600:
                q.popleft()
            if len(q) >= MAILS_PER_ADDRESS_PER_HOUR:
                return False
            q.append(now)
            return True

    # -------------------------------------------------------------- tokens
    @staticmethod
    def _issue(sub: Subscriber, purpose: str, ttl: timedelta) -> str:
        token = secrets.token_urlsafe(32)
        sub.pending[_hash(token)] = {"purpose": purpose, "expires": (_now() + ttl).isoformat()}
        return token

    def _redeem(self, token: str, purpose: str) -> Subscriber:
        h = _hash(token or "")
        with self._lock:
            for s in self.subs.values():
                p = s.pending.get(h)
                if p and p["purpose"] == purpose:
                    del s.pending[h]  # single use
                    if datetime.fromisoformat(p["expires"]) < _now():
                        self._save()
                        raise SubscriptionError("this link has expired; request a new one", "expired", 410)
                    return s
        raise SubscriptionError("this link is not valid (already used, or never issued)", "bad_token", 404)

    # ----------------------------------------------------------- lifecycle
    def subscribe(self, email: str, region: str, areas: list[str], lang: str, min_level: int,
                  include_exercises: bool) -> tuple[Subscriber, str]:
        """Create or refresh a pending subscription. Returns (subscriber, confirmation token)."""
        with self._lock:
            self.purge_stale()
            sub = self.by_email(email)
            if sub is None:
                sub = Subscriber(id="sub_" + secrets.token_hex(8), email=email, region=region, areas=[], lang=lang)
                self.subs[sub.id] = sub
            if not sub.confirmed:  # preferences of a confirmed subscriber only change after signing in
                sub.region, sub.areas, sub.lang = region, list(dict.fromkeys(areas)), lang
                sub.min_level, sub.include_exercises = min_level, include_exercises
            token = self._issue(sub, "confirm", CONFIRM_TTL)
            self._save()
            return sub, token

    def confirm(self, token: str) -> tuple[Subscriber, str]:
        """Double opt-in: activates the subscription and records consent. Returns (subscriber, unsubscribe token)."""
        with self._lock:
            sub = self._redeem(token, "confirm")
            sub.confirmed = True
            sub.consent = {"at": utcnow_iso(), "version": CONSENT_VERSION, "text": CONSENT_TEXT}
            self._save()
            return sub, self.unsubscribe_token(sub)

    def start_signin(self, email: str) -> Optional[tuple[Subscriber, str]]:
        with self._lock:
            sub = self.by_email(email)
            if sub is None or not sub.confirmed:
                return None
            token = self._issue(sub, "signin", SIGNIN_TTL)
            self._save()
            return sub, token

    def finish_signin(self, token: str) -> tuple[Subscriber, str]:
        with self._lock:
            sub = self._redeem(token, "signin")
            session = secrets.token_urlsafe(32)
            now = _now()
            sub.sessions = {h: e for h, e in sub.sessions.items() if datetime.fromisoformat(e) > now}
            sub.sessions[_hash(session)] = (now + SESSION_TTL).isoformat()
            self._save()
            return sub, session

    def by_session(self, session: str) -> Subscriber:
        h, now = _hash(session or ""), _now()
        with self._lock:
            for s in self.subs.values():
                exp = s.sessions.get(h)
                if exp and datetime.fromisoformat(exp) > now:
                    return s
        raise SubscriptionError("sign in again", "signed_out", 401)

    def update(self, sub: Subscriber, areas: list[str], lang: str, min_level: int, include_exercises: bool) -> None:
        with self._lock:
            sub.areas, sub.lang = list(dict.fromkeys(areas)), lang
            sub.min_level, sub.include_exercises = min_level, include_exercises
            self._save()

    def delete(self, sub: Subscriber) -> None:
        with self._lock:
            self.subs.pop(sub.id, None)
            self._save()

    def unsubscribe_token(self, sub: Subscriber) -> str:
        """Stateless one-click unsubscribe token carried by every email: subscriber id + HMAC of it."""
        mac = hmac.new(self._secret, sub.id.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
        return f"{sub.id}.{mac}"

    def unsubscribe(self, token: str) -> bool:
        sid, _, mac = (token or "").partition(".")
        with self._lock:
            sub = self.subs.get(sid)
            if sub is None or not hmac.compare_digest(self.unsubscribe_token(sub), token or ""):
                return False
            self.delete(sub)
            return True

    # ------------------------------------------------------------- matching
    def matching(self, pack: Pack, place_levels: dict[str, int], status: str) -> list[tuple[Subscriber, dict[str, int]]]:
        """Confirmed subscribers in this region whose areas overlap the alert's at or above their minimum level,
        with the alert areas that concern each (a subscribed district matches its divisions and the reverse).
        Exercises only for those who asked for them."""
        out = []
        with self._lock:
            for s in self.subs.values():
                if not s.confirmed or s.region != pack.region.id:
                    continue
                if status != "Actual" and not s.include_exercises:
                    continue
                mine = {pid: lv for pid, lv in place_levels.items()
                        if any(pid == a or a in pack.ancestors(pid) or pid in pack.ancestors(a) for a in s.areas)}
                # below the subscriber's threshold: no email (for a cancellation, the levels of what it closes,
                # so people hear "cancelled" exactly when they would have heard the warning)
                if mine and max(mine.values()) >= s.min_level:
                    out.append((s, mine))
        return out
