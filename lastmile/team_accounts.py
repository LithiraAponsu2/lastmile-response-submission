"""Authenticated exercise memberships. These accounts confer no government authority.

The private registry shares the incident store's CAS persistence, but is never an
incident returned by either API. One active application writer remains required.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone

from .workspace import WorkspaceError, digest, now_iso, timestamp, uid

REGISTRY = "incident_000000000000"
ROLES = {"administrator", "coordinator", "contributor", "agency_supervisor", "finance_approver", "communications_reviewer", "auditor", "partner"}
DISTRICTS = ["Ampara", "Anuradhapura", "Badulla", "Batticaloa", "Colombo", "Galle", "Gampaha", "Hambantota", "Jaffna", "Kalutara", "Kandy", "Kegalle", "Kilinochchi", "Kurunegala", "Mannar", "Matale", "Matara", "Monaragala", "Mullaitivu", "Nuwara Eliya", "Polonnaruwa", "Puttalam", "Ratnapura", "Trincomalee", "Vavuniya"]
def job(id, title, summary, tasks, roles, idle, **extra):
    return {"id": id, "title": title, "summary": summary, "tasks": tasks, "roles": roles, "idle": idle, **extra}


# People choose a job role; ROLES above are the internal permissions each job
# carries. Workspace permission rules never read job titles.
OBSERVER = job("observer", "Observer", "Follows the shared incident without taking actions.",
               ["Read reports, requests and decisions", "Download shared evidence"], ["auditor"],
               "Observers have nothing to act on. Follow the incidents below.")
ORGANISATIONS = [
    {"id": "dmc", "name": "DMC / Emergency Operations Centre", "scope": "Coordination; not universal financial authority", "active": True, "roles": [
        job("duty_officer", "EOC Duty Officer", "Runs the incident from the Emergency Operations Centre.",
            ["Review incoming reports", "Assign requests to agencies", "Review reported outcomes and close tasks"], ["contributor", "coordinator"],
            "Nothing waiting. New reports, unassigned requests and reported outcomes will appear here."),
        job("information_officer", "Public Information Officer", "Approves what partners and the public are told.",
            ["Draft situation updates and bulletins", "Approve or retract exercise documents"], ["communications_reviewer", "contributor"],
            "No documents are waiting for your approval."),
        job("workspace_admin", "Workspace administrator", "Manages member accounts and coordinates.",
            ["Invite and deactivate members", "Open incidents and coordinate"], ["administrator", "communications_reviewer", "coordinator"],
            "Nothing waiting. New reports and outcomes to review will appear here.", accounts_only=True)]},
    {"id": "district", "name": "District Secretariat / DDMCU", "scope": "District coordination; select membership districts", "active": True, "roles": [
        job("district_coordinator", "District Disaster Coordinator", "Coordinates the district response through the DDMCU.",
            ["Review district reports", "Assign requests to agencies", "Recommend funding requests"], ["contributor", "coordinator"],
            "Nothing waiting. Reports to review and funding to recommend will appear here."),
        job("district_officer", "Disaster Management Assistant", "Gathers the field situation for the district.",
            ["Upload field reports", "Record needs as requests"], ["contributor"],
            "Nothing waiting. Upload a report or record a need when the situation changes.")]},
    {"id": "division", "name": "Divisional Secretariat", "scope": "Local needs, relief and centre reporting", "active": True, "roles": [
        job("divisional_secretary", "Divisional Secretary", "Leads the division’s shelters and relief.",
            ["Accept or decline tasks for the division", "Confirm shelter counts and readiness", "Confirm relief received"], ["agency_supervisor", "contributor"],
            "Nothing waiting. Tasks offered to your division and its reports to review will appear here."),
        job("relief_officer", "Field Relief Officer", "Reports from shelters and affected villages.",
            ["Upload shelter and needs reports", "Request relief funding"], ["contributor"],
            "Nothing waiting. Upload a report when a shelter or need changes.")]},
    {"id": "ndrsc", "name": "NDRSC relief coordination", "scope": "Relief programmes and fictional exercise finance", "active": True, "roles": [
        job("programme_officer", "Relief Programme Officer", "Provides national relief stock and programmes.",
            ["Accept relief supply tasks", "Confirm NDRSC relief stock"], ["agency_supervisor", "contributor"],
            "Nothing waiting. Relief tasks offered to NDRSC will appear here."),
        job("finance_approver", "Finance Approver", "Approves and releases relief funds.",
            ["Approve recommended funding", "Record releases and reconcile spending"], ["contributor", "finance_approver"],
            "No funding is waiting for your approval.")]},
    {"id": "water", "name": "NWSDB / water provider", "scope": "Water service and supply", "active": True, "roles": [
        job("service_supervisor", "Water Service Supervisor", "Leads water restoration for the area.",
            ["Accept water restoration tasks", "Confirm water service at shelters"], ["agency_supervisor", "contributor"],
            "Nothing waiting. Water tasks offered to NWSDB will appear here."),
        job("field_engineer", "Field Engineer", "Inspects water supply on site.",
            ["Report water status from site"], ["contributor"],
            "Nothing waiting. Upload a report after each site inspection.")]},
    {"id": "rda", "name": "Road Development Authority", "scope": "RDA roads only; other roads have separate owners", "active": True, "roles": [
        job("road_engineer", "Executive Engineer (Roads)", "Leads road clearing on RDA roads.",
            ["Accept road clearing tasks", "Confirm road access status"], ["agency_supervisor", "contributor"],
            "Nothing waiting. Road tasks offered to RDA will appear here."),
        job("technical_officer", "Technical Officer", "Reports road conditions from the field.",
            ["Report blockages and clearing progress"], ["contributor"],
            "Nothing waiting. Upload a report when road access changes.")]},
    # Invited NGOs and private partners: limited access. They join incidents they are invited to,
    # answer requests sent to them and report, but never open incidents, manage participants,
    # publish to the public page or see funding.
    {"id": "partner", "name": "Partner organisation (NGO)", "scope": "Invited NGO or private partner: limited access", "active": True, "partner": True, "roles": [
        job("partner_coordinator", "Partner coordinator", "Leads the partner's work when invited into an incident.",
            ["Accept or decline requests sent to the partner", "Keep the partner's stock and pledges up to date"], ["agency_supervisor", "contributor", "partner"],
            "Nothing waiting. Requests sent to your organisation will appear here."),
        job("partner_volunteer", "Partner field volunteer", "Delivers and reports from the field.",
            ["Upload field reports", "Mark deliveries done"], ["contributor", "partner"],
            "Nothing waiting. Upload a report after each delivery.")]},
    # Kept so saved records still name them; not selectable in this demo.
    {"id": "nbro", "name": "NBRO", "scope": "Landslide technical advice", "active": False},
    {"id": "met", "name": "Department of Meteorology", "scope": "Weather technical bulletins", "active": False},
    {"id": "irrigation", "name": "Irrigation Department", "scope": "River and reservoir information", "active": False},
    {"id": "local-roads", "name": "Local / provincial road authority", "scope": "Locally owned access roads", "active": False},
    {"id": "edl", "name": "Electricity Distribution Lanka (CEB)", "aliases": ["CEB (historical reports)"], "scope": "Exercise power provider; verify actual service area", "active": True, "roles": [
        job("area_engineer", "Area Electrical Engineer", "Leads power restoration for the area.",
            ["Accept power restoration tasks", "Confirm power service at shelters"], ["agency_supervisor", "contributor"],
            "Nothing waiting. Power tasks offered to EDL will appear here."),
        job("line_crew", "Line Crew Lead", "Repairs lines on site.",
            ["Report line damage and repairs from site"], ["contributor"],
            "Nothing waiting. Upload a report after each repair.")]},
    {"id": "leco", "name": "LECO", "scope": "Power service within provider area", "active": False},
    {"id": "health", "name": "Health response team", "scope": "Health needs and services", "active": False},
    {"id": "rescue", "name": "Rescue liaison team", "scope": "Exercise rescue requests; no real dispatch", "active": False},
    {"id": "transport", "name": "Transport liaison / SLTB", "scope": "Exercise vehicle availability", "active": False},
    {"id": "red-cross", "name": "Sri Lanka Red Cross / relief partner", "scope": "Partner contributions; no assumed mandate", "active": False},
]
for _org in ORGANISATIONS:
    _org["roles"] = _org["roles"] + [OBSERVER] if _org["active"] else []
ACTIVE = [o["id"] for o in ORGANISATIONS if o["active"]]


def job_role(organisation, role_id):
    org = next((o for o in ORGANISATIONS if o["id"] == organisation), None)
    return next((r for r in org["roles"] if r["id"] == role_id), None) if org else None


def infer_job(organisation, roles):
    """The job whose permission set matches exactly, or None for a custom set."""
    org = next((o for o in ORGANISATIONS if o["id"] == organisation), None)
    return next((r for r in (org["roles"] if org else []) if set(r["roles"]) == set(roles)), None)


def require(condition, message, status=409):
    if not condition:
        raise WorkspaceError(message, status)


def password_hash(password, salt=None):
    require(12 <= len(password) <= 128, "Use a password of 12–128 characters.", 422)
    salt = salt or secrets.token_hex(16)
    key = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32)
    return salt + ":" + key.hex()


def public_user(user):
    person = {k: user[k] for k in ("id", "username", "name", "organisation", "roles", "districts", "active")}
    job = job_role(user["organisation"], user.get("job_role", "")) or infer_job(user["organisation"], user["roles"])
    return {**person, "job_role": job["id"] if job else "", "job_title": job["title"] if job else "Custom role"}


class Accounts:
    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        self.attempts = {}
        doc = store.new_document("Private exercise membership registry")
        doc.update(id=REGISTRY, internal=True, users=[], sessions=[], invitations=[])
        store.ensure_internal(doc)

    def registry(self):
        return self.store.get(REGISTRY)

    def edit(self, fn):
        # Cross-process contention fails CAS; no last-write-wins registry updates.
        with self.lock:
            doc = self.registry()
            return self.store.mutate(REGISTRY, doc["version"], "Account service", fn)

    def throttle(self, key):
        now = datetime.now(timezone.utc).timestamp()
        with self.lock:
            if len(self.attempts) > 2000:
                self.attempts = {k: v for k, v in self.attempts.items() if v[-1] > now - 60}
            attempts = [t for t in self.attempts.get(key, []) if t > now - 60]
            require(len(attempts) < 8, "Too many account attempts. Try again in a minute.", 429)
            self.attempts[key] = attempts + [now]

    def new_user(self, username, name, password, organisation, roles, districts, job=""):
        username = username.strip().casefold()
        require(bool(re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,39}", username)), "Username: 3–40 letters, numbers, dots, hyphens or underscores.", 422)
        require(2 <= len(name.strip()) <= 80, "Enter your name (2–80 characters).", 422)
        return {"id": uid("user"), "username": username, "name": name.strip(), "password": password_hash(password),
                "organisation": organisation, "roles": roles, "districts": districts, "active": True, "job_role": job}

    def bootstrap(self, username, name, password):
        user = self.new_user(username, name, password, "dmc", ["administrator", "coordinator", "communications_reviewer"], ["*"], "workspace_admin")
        def apply(doc):
            require(not doc["users"], "Workspace setup has already been completed.")
            doc["users"].append(user)
        self.edit(apply)
        return public_user(user)

    def login(self, username, password):
        username = username.strip().casefold()
        users = self.registry()["users"]
        user = next((u for u in users if u["username"] == username and u["active"]), None)
        # Equal-cost hash for unknown names; credentials never appear in API responses.
        stored = user["password"] if user else "00" * 16 + ":" + "00" * 32
        valid = False
        if 12 <= len(password) <= 128:
            valid = hmac.compare_digest(password_hash(password, stored.split(":")[0]), stored)
        require(user is not None and valid, "Username or password is incorrect.", 401)
        token = secrets.token_urlsafe(32)
        def apply(doc):
            doc["sessions"] = [s for s in doc["sessions"] if timestamp(s["expires_at"]) > datetime.now(timezone.utc)]
            doc["sessions"].append({"hash": digest(token), "user_id": user["id"], "expires_at": (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()})
        self.edit(apply)
        return token, public_user(user)

    def current(self, token):
        require(bool(token), "Sign in to the organisation workspace.", 401)
        doc = self.registry()
        session = next((s for s in doc["sessions"] if hmac.compare_digest(s["hash"], digest(token)) and timestamp(s["expires_at"]) > datetime.now(timezone.utc)), None)
        user = next((u for u in doc["users"] if session and u["id"] == session["user_id"] and u["active"]), None)
        require(user is not None, "Your session expired. Sign in again.", 401)
        return public_user(user)

    def logout(self, token):
        self.edit(lambda d: d.update(sessions=[s for s in d["sessions"] if s["hash"] != digest(token or "")]))

    def invite(self, actor, organisation, roles, districts, job=""):
        require("administrator" in actor["roles"], "Only the workspace administrator can invite exercise members.", 403)
        require(organisation in {o["id"] for o in ORGANISATIONS}, "Unknown organisation.", 422)
        if job:
            chosen = job_role(organisation, job)
            require(chosen is not None, "Choose one of this organisation’s roles.", 422)
            roles = chosen["roles"]
        require(bool(roles) and set(roles) <= ROLES, "Select valid exercise roles.", 422)
        require(bool(districts) and set(districts) <= set(DISTRICTS + ["*"]), "Select valid districts.", 422)
        token = secrets.token_urlsafe(32)
        invitation = {"id": uid("invite"), "hash": digest(token), "organisation": organisation, "roles": sorted(set(roles)), "job_role": job,
                      "districts": sorted(set(districts)), "created_by": actor["id"], "expires_at": (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat(), "used": False}
        self.edit(lambda d: d["invitations"].append(invitation))
        return {"token": token, "expires_at": invitation["expires_at"], "notice": "Exercise membership only. Share privately; link works once."}

    def join(self, token, username, name, password):
        def apply(doc):
            invite = next((i for i in doc["invitations"] if hmac.compare_digest(i["hash"], digest(token))), None)
            require(invite is not None and not invite["used"] and timestamp(invite["expires_at"]) > datetime.now(timezone.utc), "Invitation is invalid, expired or already used.", 403)
            require(not any(u["username"] == username.strip().casefold() for u in doc["users"]), "That username is already taken.")
            user = self.new_user(username, name, password, invite["organisation"], invite["roles"], invite["districts"], invite.get("job_role", ""))
            doc["users"].append(user)
            invite["used"] = True
        self.edit(apply)
        return {"joined": True}

    def deactivate(self, actor, user_id):
        require("administrator" in actor["roles"] and user_id != actor["id"], "An administrator can deactivate another member.", 403)
        def apply(doc):
            user = next((u for u in doc["users"] if u["id"] == user_id), None)
            require(user is not None, "Member not found.", 404)
            user["active"] = False
            doc["sessions"] = [s for s in doc["sessions"] if s["user_id"] != user_id]
        self.edit(apply)
        return {"deactivated": True}
