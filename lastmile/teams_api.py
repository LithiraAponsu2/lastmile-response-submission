"""Cookie-authenticated organisation exercise API; separate from shared demo keys."""
from __future__ import annotations

import asyncio
import base64
import json
import re
import hmac
import os
from pathlib import Path
from urllib.parse import quote
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from .coordinator import run_coordinator
from .reference import ReferenceStore
from . import bulletins
from .team_accounts import ACTIVE, Accounts, DISTRICTS, ORGANISATIONS, ROLES, public_user, require
from .team_agent import TeamTools, HistoryTools
from .team_story import build_story
from .team_documents import attachment
from .team_participation import is_active, summary as participation_summary, vocabulary as participation_vocabulary
from .team_areas import merge as merge_pictures, picture as area_picture, practice_fixture, vocabulary as area_vocabulary
from .team_workspace import TeamWorkspace, command_hash, find, has
from .workspace import WorkspaceError, digest, now_iso, record_event, timestamp, uid
from . import team_dashboard, team_demo, team_notifications
from . import team_drafting as drafting
from . import team_plans as plans
from .team_suggestions import Suggestions, Writer
from . import stock_ledger
from . import public_requests as public

COOKIE = "lastmile_team_session"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(Strict):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=1, max_length=128)


class Registration(Credentials):
    name: str = Field(min_length=2, max_length=80)
    invitation: str = Field(default="", max_length=100)


class Invitation(Strict):
    organisation: str
    job_role: str = Field(default="", max_length=40)
    roles: list[str] = Field(default_factory=list, max_length=7)
    districts: list[str] = Field(min_length=1, max_length=25)


class NewIncident(Strict):
    title: str = Field(min_length=3, max_length=120)
    district: str
    participants: list[str] = Field(min_length=1, max_length=20)
    hazard: str | None = None


class JoinRequest(Strict):
    note: str | None = Field(default=None, max_length=500)


class Command(Strict):
    version: int = Field(ge=1)
    request_id: str = Field(min_length=8, max_length=100)
    action: str = Field(max_length=50)
    data: dict


class Message(Strict):
    text: str = Field(min_length=1, max_length=20000)
    request_id: str = Field(min_length=8, max_length=100)
    mode: Literal['conversation', 'history'] = 'conversation'
    language: Literal['en', 'si', 'ta'] = 'en'


# The coordinator answers in the workspace language; records and evidence stay as written.
REPLY_LANGUAGE = {
    "si": "\n\nReply in Sinhala (සිංහල). Keep organisation names, place names, numbers, times, record IDs and quoted evidence exactly as written. End with one line in Sinhala saying this is a machine translation that a Sinhala speaker should check before it is shared.",
    "ta": "\n\nReply in Tamil (தமிழ்). Keep organisation names, place names, numbers, times, record IDs and quoted evidence exactly as written. End with one line in Tamil saying this is a machine translation that a Tamil speaker should check before it is shared.",
}


class Approval(Strict):
    hash: str = Field(min_length=64, max_length=64)
    request_id: str = Field(min_length=8, max_length=100)


class PublicPhoto(Strict):
    base64: str = Field(max_length=3500000)

class PublicRequest(Strict):
    text: str = Field(min_length=5, max_length=2000)
    latitude: float | None = None
    longitude: float | None = None
    area: str | None = Field(default=None, max_length=80)
    phone: str = Field(default="", max_length=20)
    consent: bool = False
    photos: list[PublicPhoto] = Field(default_factory=list, max_length=3)

class PublicAnswer(Strict):
    text: str = Field(min_length=2, max_length=500)

class PublicFile(Strict):
    incident_id: str = Field(min_length=10, max_length=40)

class PublicOpen(Strict):
    title: str = Field(min_length=3, max_length=120)
    hazard: str | None = None
    participants: list[str] = Field(default_factory=list, max_length=20)

class PublicStatus(Strict):
    status: Literal["closed", "hold", "verify", "filed"]
    reason: str = Field(default="", max_length=300)

class PublicSimilar(Strict):
    same: bool


class PlanEdit(Strict):
    hash: str = Field(min_length=64, max_length=64)
    changes: list[dict] = Field(min_length=1, max_length=plans.MAX_STEPS)


class PlanDismiss(Strict):
    reason: str = Field(default="", max_length=300)


class ReferenceSave(Strict):
    version: int = Field(ge=1)
    record: dict
    reason: str = Field(default="", max_length=300)


class BulletinUpload(Strict):
    version: int = Field(ge=1)
    file: dict


class ReferenceRetire(Strict):
    version: int = Field(ge=1)
    reason: str = Field(default="", max_length=300)
    restore: bool = False


class ReferenceImport(Strict):
    version: int = Field(ge=1)
    csv: str = Field(min_length=1, max_length=2500000)
    reason: str = Field(default="", max_length=300)


class DemoSelection(Strict):
    organisation: str
    role: str
    incident_id: str | None = None


def mount_teams(app, hub, settings, web_dir, cloud=None):
    accounts, service = Accounts(hub), TeamWorkspace(hub)
    app.state.team_accounts, app.state.team_workspace = accounts, service
    # Standing places and stock live beside the incident store: same data directory, same private bucket.
    local = getattr(hub, "path", ":memory:")
    reference = ReferenceStore(cloud.directory if cloud else settings.data_dir or (Path(local).parent if local != ":memory:" else "var"), cloud=cloud)
    app.state.reference = reference
    service.reference = reference  # message templates and media recipients for drafting and sending

    def suggestion_writer():
        """Offline, suggestions are built by code; with Gemini, translations and notes come from the stronger model."""
        if settings.llm != "gemini":
            return Writer()
        from .llm import make_llm
        llm = make_llm("gemini")
        return Writer(translate=drafting.gemini_translate(llm), note=drafting.gemini_note(llm), model=llm.model_verifier)
    # Offline suggestions are quick and run inside the request; Gemini ones run in the background.
    suggestions = Suggestions(service, hub, background=settings.llm == "gemini", writer=suggestion_writer,
                              enabled=os.environ.get("LASTMILE_SUGGESTIONS", "on").casefold() != "off")
    app.state.suggestions = suggestions
    service.ledger = stock_ledger.Ledger(hub, reference)
    service.ledger.create()
    service.on_stock_taken = suggestions.replan_for_stock

    def public_reader():
        """With Gemini, the fast model reads public text and photos; offline, keyword rules do (labelled)."""
        if settings.llm != "gemini":
            return None
        from .llm import make_llm
        return lambda: public.gemini_reader(make_llm("gemini"))
    requests_inbox = public.PublicRequests(hub, cloud.directory if cloud else settings.data_dir or "var", cloud=cloud, demo=team_demo.enabled,
                                           reader=public_reader(), background=settings.llm == "gemini",
                                           ai_daily_limit=int(os.environ.get("LASTMILE_PUBLIC_AI_PER_DAY", "300")))
    requests_inbox.after_file = suggestions.after_change
    app.state.public_requests = requests_inbox

    @app.middleware("http")
    async def bounded_team_body(request: Request, call_next):
        if (request.url.path.startswith("/api/teams") or request.url.path.startswith("/api/public/requests")) and request.method == "POST":
            size, chunks = 0, []
            ceiling = 11000000 if request.url.path == "/api/public/requests" else 3000000  # three photos, base64
            async for chunk in request.stream():
                size += len(chunk)
                if size > ceiling:
                    return JSONResponse({"detail": f"Request exceeds the {ceiling // 1000000} MB upload limit."}, status_code=413)
                chunks.append(chunk)
            request._body = b"".join(chunks)
        return await call_next(request)

    def same_origin(request: Request):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            require(not origin or origin == str(request.base_url).rstrip("/"), "Cross-origin account actions are blocked.", 403)
            require(request.headers.get("content-type", "").split(";")[0] == "application/json", "Use JSON for account actions.", 415)

    def user(request: Request):
        if request.headers.get("x-demo-session"):
            return team_demo.current(accounts, request.headers["x-demo-session"])
        return accounts.current(request.cookies.get(COOKIE, ""))

    router = APIRouter(prefix="/api/teams", dependencies=[Depends(same_origin)])

    @app.get("/teams", include_in_schema=False)
    def page():
        # Browsers preserve an existing fragment when Location has no fragment.
        return RedirectResponse("/", status_code=307, headers={"Cache-Control": "no-cache"})

    def incident_card(doc, actor=None):
        c = doc["coordination"]
        if actor and actor["organisation"] not in c["participants"]:
            # Outsiders see that the incident is going and who is involved, never its records.
            return {"id": doc["id"], "title": doc["title"], "updated_at": doc["updated_at"], "created_at": doc.get("created_at"), "participants": c["participants"],
                    "district": c["district"], "status": doc["status"], "phase": c.get("phase", "response"), **participation_summary(doc, actor)}
        return {**(participation_summary(doc, actor) if actor else {}), "id": doc["id"], "title": doc["title"], "version": doc["version"], "updated_at": doc["updated_at"], "created_at": doc.get("created_at"), "participants": c["participants"],
                "district": c["district"], "status": doc["status"], "phase": c.get("phase", "response"),
                "reports": len(doc["sources"]), "pending_reports": sum(s["status"] == "reported" for s in doc["sources"]),
                "requests": len(c["requests"]), "open_requests": sum(r["status"] not in {"fulfilled", "withdrawn", "transferred_unresolved"} for r in c["requests"]),
                "queue": sum(o["recipient"] == actor["organisation"] and o["status"] == "offered" for o in c["offers"]) if actor else 0}

    @router.get("/demo/catalogue")
    def demo_catalogue():
        require(team_demo.enabled(), "Role simulation is disabled on this server.", 403)
        # Only explicitly simulated incidents may be discovered before identity
        # selection. Never expose sources, account-based records or legacy data.
        return sorted([incident_card(d) for d in hub.team_documents() if d["coordination"].get("demo") is True], key=lambda d: d["updated_at"], reverse=True)

    @router.get("/setup")
    def setup():
        return {"setup_required": not bool(accounts.registry()["users"]), "exercise": True, "demo": team_demo.enabled(),
                "organisations": ORGANISATIONS if team_demo.enabled() else [], "roles": sorted(ROLES) if team_demo.enabled() else []}

    @router.post("/demo/select")
    def demo_select(body: DemoSelection, request: Request):
        token, actor, previous = team_demo.select(accounts, body.organisation, body.role, request.headers.get("x-demo-session", ""))
        if body.incident_id:
            # Switching to a body outside the open incident is allowed; there is just nothing to record there.
            try:
                team_demo.record_selection(hub, service, body.incident_id, actor, previous)
            except WorkspaceError as error:
                if error.status != 403:
                    raise
        return {"token": token, "user": actor}

    @router.post("/incidents/{incident_id}/demo-enter")
    def demo_enter(incident_id: str, actor=Depends(user)):
        require(actor.get("demo"), "This control is for role simulation only.", 403)
        team_demo.record_selection(hub, service, incident_id, actor, None)
        return service.view(service.get(incident_id, actor), actor)

    @router.post("/bootstrap")
    def bootstrap(body: Registration, request: Request):
        accounts.throttle("setup:" + (request.client.host if request.client else "unknown"))
        secret = os.environ.get("LASTMILE_TEAM_SETUP_KEY") or settings.api_key
        if secret:
            require(hmac.compare_digest(request.headers.get("x-setup-key", "").encode(), secret.encode()), "Workspace setup requires the server setup key.", 403)
        else:
            require(request.client and request.client.host in {"127.0.0.1", "::1", "testclient"} and request.url.hostname in {"127.0.0.1", "localhost", "::1", "testserver"}, "Configure LASTMILE_TEAM_SETUP_KEY before remote workspace setup.", 403)
        require(not body.invitation, "Use Join for invitations.", 422)
        return accounts.bootstrap(body.username, body.name, body.password)

    @router.post("/login")
    def login(body: Credentials, request: Request, response: Response):
        accounts.throttle("login:" + (request.client.host if request.client else "unknown"))
        token, person = accounts.login(body.username, body.password)
        response.set_cookie(COOKIE, token, max_age=43200, httponly=True, secure=request.url.scheme == "https", samesite="strict", path="/api/teams")
        return person

    @router.post("/join")
    def join(body: Registration, request: Request):
        accounts.throttle("join:" + (request.client.host if request.client else "unknown"))
        return accounts.join(body.invitation, body.username, body.name, body.password)

    @router.post("/logout")
    def logout(request: Request, response: Response, actor=Depends(user)):
        accounts.logout(request.cookies.get(COOKIE, ""))
        response.delete_cookie(COOKIE, path="/api/teams")
        return {"signed_out": True}

    @router.get("/me")
    def me(actor=Depends(user)):
        return {"user": actor, "organisations": ORGANISATIONS, "districts": DISTRICTS, "roles": sorted(ROLES), "model": settings.llm,
                "runtime": "Google ADK / Vertex AI" if settings.llm == "gemini" else "Google ADK / deterministic exercise driver"}

    @router.get("/members")
    def members(actor=Depends(user)):
        require(not actor.get("demo"), "Simulated administrators cannot inspect real accounts.", 403)
        require(has(actor, "administrator"), "Only administrators manage membership.", 403)
        return [public_user(u) for u in accounts.registry()["users"]]

    @router.post("/invitations")
    def invite(body: Invitation, actor=Depends(user)):
        require(not actor.get("demo"), "Use the demo role selector; simulated administrators cannot grant real memberships.", 403)
        return accounts.invite(actor, body.organisation, body.roles, body.districts, body.job_role)

    @router.post("/members/{user_id}/deactivate")
    def deactivate(user_id: str, actor=Depends(user)):
        require(not actor.get("demo"), "Simulated administrators cannot change real memberships.", 403)
        return accounts.deactivate(actor, user_id)

    @router.get("/incidents")
    def incidents(actor=Depends(user)):
        result = []
        for doc in hub.team_documents():
            # Every incident in scope is listed; only participants get the full card and can enter.
            if service.in_scope(doc, actor):
                result.append(incident_card(doc, actor))
        return sorted(result, key=lambda d: d["updated_at"], reverse=True)

    @router.get("/3w")
    def who_does_what_where(actor=Depends(user)):
        """Accepted and active work per organisation, sector and area: requests being delivered and services being restored."""
        sector = {"water": "water", "supplies": "food", "shelter": "shelter", "transport": "transport", "team": "rescue", "road": "roads", "power": "power", "other": "other"}
        rows = []
        for doc in hub.team_documents():
            c = doc["coordination"]
            if not service.in_scope(doc, actor) or actor["organisation"] not in c["participants"]:
                continue
            home = "district-" + c["district"].lower().replace(" ", "-")
            meta = {"incident_id": doc["id"], "incident_title": doc["title"], "incident_status": doc["status"]}
            for a in c.get("asks", []):
                if a["kind"] == "funding" or a["status"] not in {"accepted", "in_progress", "done", "confirmed"}:
                    continue
                rows.append({"organisation": a["to"], "sector": sector.get(a["kind"], "other"), "area": a.get("delivery_area") or home, "what": a["title"],
                             "amount": f"{a['quantity']} {a['unit']}" if a.get("quantity") else "", "status": a["status"], "kind": "request", "for": a["from"],
                             "at": a.get("updated_at") or a["created_at"], "record_id": a["id"], **meta})
            for r in c.get("area_status", []):
                if r["level"] in {"restoring", "disrupted"} and r["organisation"] in {"water", "rda", "edl", "local-roads"}:
                    rows.append({"organisation": r["organisation"], "sector": r["sector"], "area": r["area"], "what": r.get("note") or "", "amount": "",
                                 "status": r["level"], "kind": "service", "for": None, "at": r["at"], "record_id": r["id"], **meta})
        return sorted(rows, key=lambda r: r["at"], reverse=True)

    @router.get("/requests")
    def requests_overview(actor=Depends(user)):
        """Requests between organisations across every incident this body takes part in, grouped by incident."""
        groups = []
        for doc in hub.team_documents():
            if not service.in_scope(doc, actor) or actor["organisation"] not in doc["coordination"]["participants"]:
                continue
            asks = service.view(doc, actor)["coordination"].get("asks", [])
            if asks:
                groups.append({"incident_id": doc["id"], "title": doc["title"], "district": doc["coordination"]["district"], "status": doc["status"], "updated_at": doc["updated_at"], "asks": asks})
        return sorted(groups, key=lambda g: g["updated_at"], reverse=True)

    @router.post("/incidents/{incident_id}/join")
    def join_incident(incident_id: str, body: JoinRequest, actor=Depends(user)):
        return incident_card(service.request_join(incident_id, actor, body.note), actor)

    def member_views(actor):
        views, docs = [], [d for d in hub.team_documents() if service.in_scope(d, actor)]
        for doc in docs:
            try:
                service.access(doc, actor)
            except WorkspaceError:
                continue
            views.append(service.view(doc, actor))
        return views, docs

    @router.get("/activity")
    def activity(actor=Depends(user)):
        """Everything recorded in the incidents this person takes part in, newest first."""
        views, _ = member_views(actor)
        board = team_dashboard.build(views, actor, activity_limit=500)
        return {"activity": board["activity"], "incidents": [{"id": v["id"], "title": v["title"], "district": v["coordination"]["district"], "status": v["status"]} for v in views]}

    @router.get("/notifications")
    def notifications(actor=Depends(user)):
        """The bell: what is waiting for this person now, and what other bodies did that concerns their organisation."""
        views, docs = member_views(actor)
        member_ids = {v["id"] for v in views}
        waiting = team_dashboard.build(views, actor, activity_limit=0)["attention"]
        return {"generated_at": now_iso(), "organisation": actor["organisation"], "waiting": waiting,
                "items": team_notifications.feed(views, [d for d in docs if d["id"] not in member_ids], actor)}

    @router.get("/dashboard")
    def dashboard(actor=Depends(user)):
        views, docs = member_views(actor)
        board = team_dashboard.build(views, actor)
        member_ids = {v["id"] for v in views}
        for card in board["incidents"]:
            card.update(participation_summary(next(d for d in docs if d["id"] == card["id"]), actor))
        board["others"] = sorted([incident_card(d, actor) for d in docs if d["id"] not in member_ids], key=lambda d: d["updated_at"], reverse=True)
        board["incident_types"] = participation_vocabulary()
        # One national picture across the incidents this person can see.
        board["picture"] = merge_pictures([v["coordination"]["picture"] for v in views if v["status"] != "closed"])
        board["vocabulary"] = area_vocabulary()
        return board

    @app.get("/public", include_in_schema=False)
    def public_page():
        return FileResponse(web_dir / "status.html", headers={"Cache-Control": "no-cache"})

    @app.get("/status", include_in_schema=False)
    def status_page(request: Request):
        # The page's earlier address: links already shared (SMS, press, bookmarks) keep working.
        query = request.url.query
        return RedirectResponse("/public" + ("?" + query if query else ""), status_code=308)

    # ---------------------------------------------------------------- public requests for help
    def public_origin(request: Request):
        origin = request.headers.get("origin")
        require(not origin or origin == str(request.base_url).rstrip("/"), "Send requests from this site.", 403)
        require(request.headers.get("content-type", "").split(";")[0] == "application/json", "Use JSON.", 415)

    @app.post("/api/public/requests", dependencies=[Depends(public_origin)], status_code=201)
    def public_submit(body: PublicRequest, request: Request):
        try:
            photos = [{"bytes": base64.b64decode(p.base64, validate=True)} for p in body.photos]
        except ValueError:
            raise WorkspaceError("A photo could not be read.", 422) from None
        record = requests_inbox.submit(body.text, lat=body.latitude, lon=body.longitude, area=body.area, phone=body.phone, consent=body.consent,
                                       photos=photos, client=request.client.host if request.client else "?")
        return requests_inbox.track(record["code"])

    @app.get("/api/public/requests/{code}")
    def public_track(code: str):
        require(bool(re.fullmatch(r"LM-[A-Z0-9]{6}", code)), "Request not found.", 404)
        return requests_inbox.track(code)

    @app.post("/api/public/requests/{code}/answer", dependencies=[Depends(public_origin)])
    def public_answer(code: str, body: PublicAnswer):
        require(bool(re.fullmatch(r"LM-[A-Z0-9]{6}", code)), "Request not found.", 404)
        requests_inbox.answer(code, body.text)
        return requests_inbox.track(code)

    @app.get("/api/public/ai-quality")
    def ai_quality():
        """The latest recorded benchmark of the public-request reader: scores only, no messages."""
        out = {}
        for key, name in (("development", "results.json"), ("held_out", "results-holdout.json")):
            path = Path(__file__).resolve().parent.parent / "data" / "benchmark" / name
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                out[key] = {"run_at": data["run_at"], "cases": data["cases"], "scores": {r: {k: v for k, v in s.items() if k not in {"failures"}} for r, s in data["scores"].items()},
                            "rescored": data.get("rescored")}
        return out

    @app.get("/api/public/status")
    def public_status():
        """Published area status across open incidents. No login, no personal data, read-only."""
        pictures, incidents, messages = [], [], []
        names = {o["id"]: o["name"] for o in ORGANISATIONS}
        for doc in hub.team_documents():
            if doc["status"] == "closed":
                continue
            c = doc["coordination"]
            # Messages already sent to the public: SMS to areas and press releases. Emails to newsrooms stay internal.
            for d in c.get("documents", []):
                if d.get("status") == "sent" and d.get("kind") in {"sms", "press_release"} and d.get("text"):
                    messages.append({"id": d["id"], "kind": d["kind"], "subject": d.get("subject"), "text": d["text"], "language": d.get("language", "en"),
                                     "issuer": names.get(d["issuer"], d["issuer"]), "sent_at": d["sent_at"], "areas": (d.get("sent_to") or {}).get("areas", []), "incident": doc["title"]})
            balances = {a["id"]: service.balance(c, a) for a in c.get("assets", []) if a.get("kind") == "shelter" and a.get("public")}
            p = area_picture(doc, public_only=True, balances=balances)
            if not (p["areas"] or p["centres"] or p["layers"] or any(m["incident"] == doc["title"] for m in messages)):
                continue
            pictures.append(p)
            incidents.append({"id": doc["id"], "title": doc["title"], "district": c["district"], "updated_at": doc["updated_at"], "phase": c.get("phase")})
        out = merge_pictures(pictures)
        out.update(incidents=incidents, vocabulary=area_vocabulary(), exercise=True, messages=sorted(messages, key=lambda m: m["sent_at"], reverse=True)[:20],
                   notice="Prototype exercise: every warning, status and figure here is fictional. Follow instructions from the DMC, NBRO, the Department of Meteorology and local officials.")
        return JSONResponse(out, headers={"Cache-Control": "no-cache"})

    @app.get("/api/public/places")
    def public_places():
        """Listed safety centres, river levels and public hotlines. No login; no personal data."""
        from .bulletins import gauge_state
        from .reference import derive
        centres = [{"id": r["id"], "name": r["name"], "type": r["type"], "area": r["area"], "latitude": r["latitude"], "longitude": r["longitude"],
                    "places_by_standard": derive("centres", r, {})["capacity"], "step_free": r.get("step_free"), "accessible_toilet": r.get("accessible_toilet"),
                    "women_children_space": r.get("women_children_space"), "fictional": bool(r.get("fictional"))}
                   for r in reference.dataset("centres")["records"] if not r.get("retired") and r.get("latitude") is not None]
        gauges = []
        for g in reference.dataset("gauges")["records"]:
            state = gauge_state(g)
            if g.get("retired") or g.get("latitude") is None or state["state"] == "no_reading":
                continue
            gauges.append({"name": g["name"], "river": g["river"], "area": g.get("area"), "latitude": g["latitude"], "longitude": g["longitude"], "unit": g["unit"],
                           "state": state["state"], "trend": state.get("trend"), "level": state["level"], "at": state["at"], "stale": state["stale"]})
        hotlines = [{"label": r["label"], "number": r["number"], "hours": r.get("hours")} for r in reference.dataset("contacts")["records"]
                    if r.get("public") and r.get("number") and r["channel"] == "hotline" and not r.get("retired")]
        return JSONResponse({"centres": centres, "gauges": gauges, "hotlines": hotlines,
                             "notice": "Listed centres are places planned for use, not confirmation that a centre is open."}, headers={"Cache-Control": "no-cache"})

    @router.post("/incidents")
    def create(body: NewIncident, actor=Depends(user)):
        return service.view(service.create(actor, body.title, body.district, body.participants, body.hazard), actor)

    @router.post("/practice")
    def practice(actor=Depends(user)):
        doc = service.create(actor, "Kotmale coordination practice", "Nuwara Eliya", list(ACTIVE), "landslide")
        def seed(d):
            at = now_iso()
            report = "EXERCISE FIXTURE — not an agency submission.\nCurrent capacity: 100 people\nOccupied: 40 people\nExternal pending: 0 people\nStatus: accepting\nAccess: passable\nWater: unavailable\nPower: working"
            shelter_source, need_source, bulletin_source = uid("source"), uid("source"), uid("source")
            bulletin = "EXERCISE FIXTURE — fictional bulletin, not issued by NBRO or the Department of Meteorology.\nLandslide: Amber (be alert) for Kothmale and Walapane DS divisions; Yellow (remain vigilant) for Hanguranketa DS division. Valid 24 hours.\nHeavy rain: Amber for Nuwara Eliya District; showers above 100 mm likely."
            for sid, title, text, organisation in [(shelter_source, "Fictional centre readiness", report, "division"), (need_source, "Fictional accommodation need", "EXERCISE FIXTURE: 20 people need accommodation in Kotmale; water restoration confirmation is pending.", "district"), (bulletin_source, "Fictional landslide and rain bulletin", bulletin, "dmc")]:
                d["sources"].append({"id": sid, "title": title, "text": text, "hash": digest(text), "organisation": organisation, "author": "exercise_fixture", "observed_at": at, "published_at": at, "received_at": at, "status": "accepted", "reviewed_by": "exercise_fixture", "fixture": True, "attachment": None, "issues": []})
            values = {"capacity": (100, "Current capacity: 100 people"), "occupied": (40, "Occupied: 40 people"), "external_pending": (0, "External pending: 0 people"), "status": ("accepting", "Status: accepting"), "access": ("passable", "Access: passable"), "water": ("unavailable", "Water: unavailable"), "power": ("working", "Power: working")}
            d["coordination"]["assets"].append({"id": uid("asset"), "name": "Kotmale Practice Hall", "kind": "shelter", "owner": "division", "resource_key": "centre-kotmale-practice-hall", "latitude": 7.02, "longitude": 80.59, "facts": {k: {"value": value, "quote": quote, "source_id": shelter_source, "observed_at": at, "confirmed_by": "exercise_fixture"} for k, (value, quote) in values.items()}})
            d["coordination"]["requests"].append({"id": uid("request"), "title": "Accommodate 20 people", "kind": "shelter", "quantity": 20, "unit": "people", "source_id": need_source, "organisation": "district", "created_by": "exercise_fixture", "status": "open"})
            # The core collaboration story: one body asks another, directly.
            note = "Water is unavailable at the hall and 40 people are sheltering there. Please send bowsers."
            d["coordination"]["asks"].append({"id": uid("ask"), "from": "division", "to": "water", "kind": "water", "title": "Water bowsers for Kotmale Practice Hall",
                "quantity": 2, "unit": "bowsers", "amount": None, "note": note, "due_at": None, "source_id": shelter_source, "created_by": "exercise_fixture", "created_at": at, "status": "asked",
                "history": [{"at": at, "actor": "exercise_fixture", "organisation": "division", "step": "asked", "note": note}]})
            practice_fixture(d, at, bulletin_source, need_source)
            record_event(d, actor["id"], "Labelled practice fixture loaded", "All contributions, review and resource facts in this seed are fictional. Water prevents a commitment; no actual agency reported them.")
        return service.view(hub.mutate(doc["id"], doc["version"], actor["id"], seed), actor)

    @router.get("/incidents/{incident_id}")
    def get(incident_id: str, actor=Depends(user)):
        service.get(incident_id, actor)  # scope check before anything else runs
        suggestions.on_read(incident_id)
        return service.view(service.get(incident_id, actor), actor)

    @router.get('/incidents/{incident_id}/story')
    def incident_story(incident_id: str, request_id: str | None = None, actor=Depends(user)):
        doc = service.view(service.get(incident_id, actor), actor)
        if request_id:
            find(doc['coordination']['requests'], request_id)
        return build_story(doc, request_id)

    @router.post("/incidents/{incident_id}/commands")
    def command(incident_id: str, body: Command, actor=Depends(user)):
        try:
            service.execute(incident_id, actor, body.version, body.request_id, body.action, body.data)
        except (KeyError, TypeError, AttributeError):
            raise WorkspaceError("The action is missing a required field or has the wrong field type.", 422) from None
        suggestions.after_change(incident_id)
        return service.view(service.get(incident_id, actor), actor)

    @router.get("/incidents/{incident_id}/sources/{source_id}/attachment")
    def download(incident_id: str, source_id: str, actor=Depends(user)):
        doc = service.get(incident_id, actor)
        item = service.source(doc, source_id, accepted=False).get("attachment")
        require(item is not None, "No attachment for this source.", 404)
        return Response(base64.b64decode(item["base64"]), media_type="application/octet-stream", headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(item["name"], safe=""), "Cache-Control": "no-store"})

    @router.get("/incidents/{incident_id}/documents/{document_id}")
    def document(incident_id: str, document_id: str, actor=Depends(user)):
        doc = service.get(incident_id, actor)
        item = find(doc["coordination"]["documents"], document_id)
        content = f"Status: {item['status']}\nIssuer: {item['issuer']}\nSHA256: {item['hash']}\nDelivery: {item['delivery']}\n\n{item['body']}"
        return Response(content, media_type="text/plain; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{item["kind"]}-exercise.txt"', "Cache-Control": "no-store"})

    drafters = {}

    def letter_drafter():
        """Letters are written by the stronger model when Gemini is configured; offline, the points are kept as written."""
        if settings.llm != "gemini":
            return drafting.offline
        if "gemini" not in drafters:
            from .llm import make_llm
            drafters["gemini"] = drafting.gemini(make_llm("gemini"))
        return drafters["gemini"]

    def chat(doc, actor):
        return doc["coordination"]["chats"].setdefault(actor["id"], {"messages": [], "turns": [], "proposals": []})

    def metadata(incident_id, actor, fn):
        for attempt in range(3):
            doc = service.get(incident_id, actor)
            try:
                return hub.mutate(incident_id, doc["version"], actor["id"], lambda d: fn(chat(d, actor)), metadata=True)
            except WorkspaceError as exc:
                if exc.status != 409 or attempt == 2:
                    raise

    @router.post("/incidents/{incident_id}/chat")
    def message(incident_id: str, body: Message, actor=Depends(user)):
        doc = service.get(incident_id, actor)
        previous = chat(doc, actor)
        old = next((t for t in previous["turns"] if t["request_id"] == body.request_id), None)
        input_hash = digest(body.text if body.mode == 'conversation' else body.mode + ':' + body.text)
        if old:
            require(old["input_hash"] == input_hash, "This chat request identifier was used for a different message.")
            return service.view(doc, actor)
        active = [t for t in previous["turns"] if t["status"] == "running" and (datetime.now(timezone.utc) - timestamp(t["at"])).total_seconds() < 120]
        require(not active, "Your previous turn is still running. Wait for its result.")
        turn_id = uid("turn")
        def start(c):
            # Recheck under lock: two concurrent requests cannot open two turns.
            require(not any(t["status"] == "running" and (datetime.now(timezone.utc) - timestamp(t["at"])).total_seconds() < 120 for t in c["turns"]), "Your previous turn is still running.")
            for t in c["turns"]:
                if t["status"] == "running":
                    t["status"] = "interrupted"
            c["messages"].append({"id": uid("message"), "turn_id": turn_id, "role": "user", "text": body.text, "at": now_iso()})
            c["turns"].append({"id": turn_id, "at": now_iso(), "request_id": body.request_id, "input_hash": input_hash, "purpose": body.mode, "snapshot_version": doc['version'], "status": "running", "steps": []})
        metadata(incident_id, actor, start)
        def progress(**step):
            tools.trace.append(step)
            def update(c):
                turn = find(c["turns"], turn_id)
                prior = next((s for s in turn["steps"] if s["step_id"] == step["step_id"]), None)
                if prior:
                    prior.update(step)
                else:
                    turn["steps"].append(step)
            metadata(incident_id, actor, update)
        tools = (HistoryTools if body.mode == 'history' else TeamTools)(service, actor, incident_id, progress)
        tools.submitted_text = body.text
        if body.language != "en":
            tools.agent_instruction = tools.agent_instruction + REPLY_LANGUAGE[body.language]
        try:
            tools.drafter = letter_drafter()  # inside the turn: a misconfigured model fails the turn visibly
            outcome = asyncio.run(run_coordinator(tools, body.text, settings.llm))
        except Exception as error:  # the class name only: provider messages can carry request details
            outcome = {"text": "The coordinator could not finish this turn. Your report and saved records are intact. Use the data controls or retry a focused question.", "failed": True, "runtime": "unavailable", "usage": {}, "error": type(error).__name__}
        def finish(c):
            c["messages"].append({"id": uid("message"), "turn_id": turn_id, "role": "assistant", "text": outcome["text"], "at": now_iso()})
            c["proposals"].extend(tools.proposals)
            # Which models did the work: the planner, plus any drafting call a tool made.
            models = [{"role": "planner", "model": outcome["model"]}] if outcome.get("model") else []
            find(c["turns"], turn_id).update(status="failed" if outcome["failed"] else "completed", finish_reason=outcome.get("finish_reason"), usage=outcome["usage"], runtime=outcome["runtime"], tools=tools.tool_results, models=models + tools.models, error=outcome.get("error"))
        doc = metadata(incident_id, actor, finish)
        if tools.steps and not outcome["failed"]:
            # The turn's steps become one plan card, checked again as this person against the incident now.
            def keep(d):
                plan = tools.make_plan(d)
                d["coordination"].setdefault("plans", []).append(plan)
                find(d["coordination"]["chats"][actor["id"]]["turns"], turn_id)["plan_id"] = plan["id"]
            doc = plan_meta(incident_id, actor, keep)
        return service.view(doc, actor)

    def plan_meta(incident_id, actor, fn):
        """Change plans without changing the incident version; retry if another write lands first."""
        for attempt in range(3):
            doc = service.get(incident_id, actor)
            try:
                return hub.mutate(incident_id, doc["version"], actor["id"], fn, metadata=True)
            except WorkspaceError as exc:
                if exc.status != 409 or attempt == 2:
                    raise

    def own_plan(d, plan_id, actor):
        plan = find(d["coordination"].get("plans", []), plan_id)
        require(plans.may_act(plan, actor), "This plan was prepared for someone else.", 403)
        return plan

    def template_text():
        return " ".join(r.get("body", "") + " " + (r.get("subject") or "") for r in reference.dataset("templates")["records"])

    @router.post("/incidents/{incident_id}/plans/{plan_id}/approve")
    def approve_plan(incident_id: str, plan_id: str, body: Approval, actor=Depends(user)):
        doc = service.get(incident_id, actor)
        plan = own_plan(doc, plan_id, actor)
        receipt = {"hash": plan["hash"], "actor": actor["id"], "plan_id": plan_id}
        prior = doc["coordination"]["replays"].get(body.request_id)
        if prior:
            require(prior == receipt and plan["status"] == "applied", "This request identifier belongs to another action.")
            return service.view(doc, actor)
        if plan["status"] == "applied":
            require(plan.get("request_id") == body.request_id, "This plan was already applied.")
            return service.view(doc, actor)
        require(plan["hash"] == body.hash and plan["hash"] == plans.plan_hash(plan["steps"]), "The plan changed. Review it again before applying.")
        require(not plans.blocking(plan), "Fix the flagged numbers first: edit the step or dismiss the plan.")
        require(plans.is_current(plan, doc), "The incident changed or this plan expired. Refresh it to check it again.")
        reserves = any(s["action"] in stock_ledger.FIELDS for s in plan["steps"])
        try:
            holds = stock_ledger.prepare(service, doc, actor, lambda d: plans.run_steps(service, d, actor, plan["steps"])) if reserves else []
        except stock_ledger.Shortage as shortage:
            # Someone else took the stock first: never apply an old picture; prepare a new suggestion from what is left.
            if plan["origin"] == "event":
                suggestions.replace(incident_id, plan_id)
                raise WorkspaceError(f"{shortage} A new suggestion using the stock that is left is ready.", 409) from None
            raise
        changes = {}
        def apply(d):
            exact = own_plan(d, plan_id, actor)
            require(exact["hash"] == body.hash and plans.is_current(exact, d) and not plans.blocking(exact), "This plan was already applied, changed or expired.")
            created = plans.run_steps(service, d, actor, exact["steps"], record=True, plan=exact)
            stock_ledger.attach(d, holds)
            changes.update(stock_ledger.follow_requests(service, d))
            exact.update(status="applied", request_id=body.request_id, approved_by=actor["id"], approved_at=now_iso(), created=created)
            d["coordination"]["replays"][body.request_id] = receipt
        token = stock_ledger.TAKEN.set(holds)
        try:
            hub.mutate(incident_id, doc["version"], actor["id"], apply)
        except Exception:
            if holds:
                stock_ledger.release(service, holds)
            raise
        finally:
            stock_ledger.TAKEN.reset(token)
        if any(changes.values()):
            stock_ledger.settle(service, changes)
        if holds:
            suggestions.replan_for_stock({h["stock_id"] for h in holds}, incident_id)
        suggestions.after_change(incident_id)
        return service.view(service.get(incident_id, actor), actor)

    @router.post("/incidents/{incident_id}/plans/{plan_id}/edit")
    def edit_plan(incident_id: str, plan_id: str, body: PlanEdit, actor=Depends(user)):
        def change(d):
            exact = own_plan(d, plan_id, actor)
            require(exact["status"] == "pending", "Only a pending plan can be edited.")
            require(exact["hash"] == body.hash, "The plan changed. Reload it before editing.")
            plans.edit(service, d, actor, exact, body.changes, template_text())
        return service.view(plan_meta(incident_id, actor, change), actor)

    @router.post("/incidents/{incident_id}/plans/{plan_id}/refresh")
    def refresh_plan(incident_id: str, plan_id: str, actor=Depends(user)):
        def check(d):
            exact = own_plan(d, plan_id, actor)
            require(exact["status"] == "pending", "Only a pending plan can be checked again.")
            plans.refresh(service, d, actor, exact, template_text())
        return service.view(plan_meta(incident_id, actor, check), actor)

    @router.post("/incidents/{incident_id}/plans/{plan_id}/dismiss")
    def dismiss_plan(incident_id: str, plan_id: str, body: PlanDismiss, actor=Depends(user)):
        def drop(d):
            exact = own_plan(d, plan_id, actor)
            require(exact["status"] == "pending", "Only a pending plan can be dismissed.")
            exact.update(status="dismissed", dismissed_by=actor["id"], dismissed_at=now_iso(), dismiss_reason=body.reason.strip())
        return service.view(plan_meta(incident_id, actor, drop), actor)

    @router.post("/incidents/{incident_id}/proposals/{proposal_id}/approve")
    def approve(incident_id: str, proposal_id: str, body: Approval, actor=Depends(user)):
        doc = service.get(incident_id, actor)
        p = find(chat(doc, actor)["proposals"], proposal_id)
        require(p["hash"] == body.hash and p["hash"] == command_hash(p["action"], p["data"]), "The proposal hash changed.")
        receipt = {"hash": p["hash"], "actor": actor["id"], "proposal_id": proposal_id}
        prior = doc["coordination"]["replays"].get(body.request_id)
        if prior:
            require(prior == receipt and p["status"] == "approved", "This request identifier belongs to another action.")
            return service.view(doc, actor)
        if p["status"] == "approved":
            require(p.get("request_id") == body.request_id, "This proposal was already applied.")
            return service.view(doc, actor)
        require(p["status"] == "pending" and p["version"] == doc["version"] and timestamp(p["expires_at"]) > datetime.now(timezone.utc), "This proposal is stale or expired. Ask for a fresh proposal.")
        # Current membership and code-owned domain validation run again on approval.
        def apply(d):
            service.access(d, actor)
            exact = find(chat(d, actor)["proposals"], proposal_id)
            require(exact["status"] == "pending" and exact["hash"] == body.hash and exact["version"] == d["version"] and timestamp(exact["expires_at"]) > datetime.now(timezone.utc), "Proposal already applied, stale or expired.")
            try:
                service.apply(d, actor, exact["action"], exact["data"])
            except (KeyError, TypeError, AttributeError):
                raise WorkspaceError("Invalid proposal fields; request a fresh proposal.", 422) from None
            exact.update(status="approved", request_id=body.request_id, approved_by=actor["id"], approved_at=now_iso())
            d["coordination"]["replays"][body.request_id] = receipt
            record_event(d, actor["id"], "Exact chat proposal applied", exact["action"])
        return service.view(hub.mutate(incident_id, doc["version"], actor["id"], apply), actor)

    # ------------------------------------------------------------ public requests: hold queue, closed by AI, photos
    def acting(actor):
        return bool(set(actor["roles"]) & {"contributor", "agency_supervisor", "coordinator", "finance_approver", "communications_reviewer"})

    def public_scope(record, actor):
        """Coordinators handle requests in their districts; anyone in the incident sees one filed there."""
        require(bool(record["demo"]) == bool(actor.get("demo")), "Request not found.", 404)
        district = public.district_of(record["area"]) if record["area"] else None
        if record["incident_id"]:
            try:
                service.get(record["incident_id"], actor)
                return
            except WorkspaceError:
                pass
        require("coordinator" in actor["roles"] and ("*" in actor.get("districts", ["*"]) or actor.get("demo") or district in actor["districts"]),
                "Coordinators in this district handle requests that are not in an incident.", 403)

    @router.get("/public-requests")
    def public_queue(actor=Depends(user)):
        """Every public request this person may see, by state: waiting for a decision (no incident), being checked,
        in an incident (with its request and what the person sees) and closed. Coordinators see those with no incident."""
        from .team_participation import lead_org
        out, docs = {"hold": [], "verify": [], "filed": [], "closed": []}, {}
        for record in requests_inbox.inbox()["requests"]:
            if record["status"] not in out:
                continue
            try:
                public_scope(record, actor)
            except WorkspaceError:
                continue
            item = {**requests_inbox.staff_copy(record), "incident_id": record["incident_id"], "incident_title": None, "incident_status": None,
                    "stage": public.STAGES.get(record["status"], "Received"), "ask": None}
            if record["incident_id"]:
                if record["incident_id"] not in docs:
                    try:
                        docs[record["incident_id"]] = hub.get(record["incident_id"])
                    except WorkspaceError:
                        docs[record["incident_id"]] = None
                doc = docs[record["incident_id"]]
                if doc:
                    c = doc["coordination"]
                    item.update(incident_title=doc["title"], incident_status=doc["status"])
                    stage, _, ask = requests_inbox.progress(record, c, item["stage"]) if record["status"] in {"filed", "verify"} else (item["stage"], None, None)
                    if ask:
                        item.update(stage=stage, ask={k: ask[k] for k in ("id", "to", "status", "title")})
                    # Same rule as the incident view: the phone number is for the lead body and the body asked to help.
                    if actor["organisation"] not in {lead_org(c)} | {a["to"] for a in c.get("asks", []) if a.get("public_request_id") == record["id"]}:
                        item["phone"] = None
            out[record["status"]].append(item)
        out["closed"] = out["closed"][-100:]
        return out

    @router.get("/public-requests/{request_id}/photos/{number}")
    def public_photo(request_id: str, number: int, actor=Depends(user)):
        record = requests_inbox.find(request_id)
        public_scope(record, actor)
        data, mime = requests_inbox.photo(record, number)
        return Response(data, media_type=mime, headers={"Cache-Control": "private, max-age=600"})

    def may_change(incident_id, actor):
        """Changing a request in an incident needs an acting role in an organisation still active there."""
        require(acting(actor), "Observers cannot change requests.", 403)
        if incident_id:
            doc = service.get(incident_id, actor)
            require(doc["status"] != "closed", "This incident is closed.", 409)
            require(is_active(doc["coordination"], actor["organisation"]), "Your organisation has stood down from this incident.", 403)

    @router.post("/public-requests/{request_id}/file")
    def public_file(request_id: str, body: PublicFile, actor=Depends(user)):
        record = requests_inbox.find(request_id)
        public_scope(record, actor)
        may_change(body.incident_id, actor)
        requests_inbox.file(request_id, body.incident_id, actor)
        return service.view(service.get(body.incident_id, actor), actor)

    @router.post("/public-requests/{request_id}/similar")
    def public_similar(request_id: str, body: PublicSimilar, actor=Depends(user)):
        record = requests_inbox.find(request_id)
        public_scope(record, actor)
        may_change(record["incident_id"], actor)
        requests_inbox.same_problem(request_id, body.same, actor)
        return service.view(service.get(record["incident_id"], actor), actor)

    @router.post("/public-requests/{request_id}/open")
    def public_open(request_id: str, body: PublicOpen, actor=Depends(user)):
        record = requests_inbox.find(request_id)
        public_scope(record, actor)
        may_change(None, actor)
        district = public.district_of(record["area"])
        require(district is not None, "This request has no location to open an incident for.", 422)
        doc = service.create(actor, body.title, district, body.participants, body.hazard)
        requests_inbox.file(request_id, doc["id"], actor)
        return service.view(service.get(doc["id"], actor), actor)

    @router.post("/public-requests/{request_id}/status")
    def public_set_status(request_id: str, body: PublicStatus, actor=Depends(user)):
        record = requests_inbox.find(request_id)
        public_scope(record, actor)
        may_change(record["incident_id"], actor)
        record = requests_inbox.set_status(request_id, body.status, actor, body.reason.strip())
        if record["incident_id"]:
            suggestions.after_change(record["incident_id"])
        return requests_inbox.staff_copy(requests_inbox.find(request_id))

    # ------------------------------------------------------------ places & stock (standing reference data)
    @router.get("/reference")
    def reference_summary(actor=Depends(user)):
        return reference.summary(actor)

    @router.get("/reference/{name}")
    def reference_view(name: str, area: str | None = None, q: str | None = None, actor=Depends(user)):
        return reference.view(name, actor, area=area, query=(q or "")[:80] or None)

    @router.get("/reference/{name}/export")
    def reference_export(name: str, template: bool = False, actor=Depends(user)):
        reference.dataset(name)
        return Response(reference.export_csv(name, template), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="lastmile-{name}{"-template" if template else ""}.csv"', "Cache-Control": "no-store"})

    @router.post("/reference/{name}")
    def reference_save(name: str, body: ReferenceSave, actor=Depends(user)):
        return reference.save(name, actor, body.version, body.record, body.reason)

    @router.post("/reference/gauges/bulletin")
    def gauge_bulletin(body: BulletinUpload, actor=Depends(user)):
        meta, text = attachment(body.file)
        return reference.import_gauge_bulletin(actor, body.version, text, {"name": meta["name"], "sha256": meta["sha256"]})

    @router.get("/incidents/{incident_id}/layers/{layer_id}")
    def layer_geometry(incident_id: str, layer_id: str, actor=Depends(user)):
        doc = hub.get(incident_id)
        service.access(doc, actor)
        layer = find(doc["coordination"].get("map_layers", []), layer_id)
        require(not layer["removed_at"], "This layer was removed.", 404)
        return JSONResponse(layer["geojson"], headers={"Cache-Control": "private, max-age=300"})

    @app.get("/api/public/layers/{layer_id}")
    def public_layer(layer_id: str):
        """A published map layer's geometry. Only layers a lead chose to publish, in open incidents."""
        for doc in hub.team_documents():
            if doc["status"] == "closed":
                continue
            for layer in doc["coordination"].get("map_layers", []):
                if layer["id"] == layer_id and layer.get("public") and not layer["removed_at"]:
                    return JSONResponse(layer["geojson"], headers={"Cache-Control": "public, max-age=300"})
        return JSONResponse({"detail": "Layer not found."}, status_code=404)

    @router.get("/incidents/{incident_id}/sources/{source_id}/read")
    def read_source(incident_id: str, source_id: str, actor=Depends(user)):
        """What an official bulletin in this report says, as rows to check before adding them."""
        doc = hub.get(incident_id)
        service.access(doc, actor)
        source = find(doc["sources"], source_id)
        gauges = [g for g in reference.dataset("gauges")["records"] if not g.get("retired")]
        return {"source_id": source_id, "title": source["title"], "observed_at": source.get("observed_at"), **bulletins.read_report(source.get("text") or "", gauges)}

    @router.post("/reference/{name}/import")
    def reference_import(name: str, body: ReferenceImport, actor=Depends(user)):
        return reference.import_csv(name, actor, body.version, body.csv, body.reason)

    @router.post("/reference/{name}/{record_id}/retire")
    def reference_retire(name: str, record_id: str, body: ReferenceRetire, actor=Depends(user)):
        return reference.retire(name, actor, body.version, record_id, body.reason, body.restore)

    app.include_router(router)
