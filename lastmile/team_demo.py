"""Opt-in, unauthenticated role simulation. Never grants real memberships."""
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from .team_accounts import ACTIVE, ORGANISATIONS, job_role, require
from .workspace import digest, now_iso, timestamp


def enabled():
    return os.environ.get("LASTMILE_TEAM_DEMO", "").lower() in {"1", "true"}


def select(accounts, organisation, role, previous=""):
    require(enabled(), "Role simulation is disabled on this server.", 403)
    require(organisation in {o["id"] for o in ORGANISATIONS}, "Choose a known organisation.", 422)
    require(organisation in ACTIVE, "This organisation is not active in this demo.", 422)
    job = job_role(organisation, role)
    # Account-only jobs (workspace administration) have nothing to do in a simulation.
    require(job is not None and not job.get("accounts_only"), "Choose one of this organisation’s roles.", 422)
    actor = {"id": f"demo_{organisation}_{role}", "username": f"demo.{organisation}.{role}", "name": job["title"],
             "organisation": organisation, "roles": sorted(job["roles"]), "districts": ["*"], "active": True, "demo": True,
             "demo_role": role, "job_role": role, "job_title": job["title"]}
    token = secrets.token_urlsafe(32)
    previous_actor = None
    if previous:
        previous_actor = current(accounts, previous)
    def save(d):
        sessions = [s for s in d.get("demo_sessions", []) if timestamp(s["expires_at"]) > datetime.now(timezone.utc)]
        sessions.append({"hash": digest(token), "actor": actor, "expires_at": (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()})
        require(len(sessions) <= 500, "Demo session limit reached. Try later.", 429)
        d["demo_sessions"] = sessions
    accounts.edit(save)
    return token, actor, previous_actor


def current(accounts, token):
    require(enabled(), "Role simulation is disabled on this server.", 403)
    session = next((s for s in accounts.registry().get("demo_sessions", []) if hmac.compare_digest(s["hash"], digest(token)) and timestamp(s["expires_at"]) > datetime.now(timezone.utc)), None)
    require(session is not None, "Choose your demo organisation and role again.", 401)
    return session["actor"]


def record_selection(store, service, incident_id, actor, previous_actor):
    from .workspace import record_event
    def save(d):
        service.access(d, actor)
        title = lambda a: a.get("job_title") or a["demo_role"]
        prior = f"{previous_actor['organisation']} / {title(previous_actor)}" if previous_actor else "No role selected"
        entry = {"at": now_iso(), "from": prior, "organisation": actor["organisation"], "role": title(actor), "actor": actor["id"]}
        d["coordination"].setdefault("role_history", []).append(entry)
        record_event(d, actor["id"], "Demo role selected", f"{prior} → {actor['organisation']} / {title(actor)}; simulated identity, not agency authentication")
    for attempt in range(3):
        doc = service.get(incident_id, actor)
        try:
            store.mutate(incident_id, doc["version"], actor["id"], save, metadata=True)
            return
        except Exception as exc:
            if getattr(exc, "status", None) != 409 or attempt == 2:
                raise
