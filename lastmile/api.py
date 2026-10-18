"""FastAPI app: JSON API, the coordination app and the public pages (web/).

Security posture of this demo build (documented in README):
  * agents have no publish path; publish.py needs a named human reviewer
  * optional X-API-Key (LASTMILE_API_KEY); per-client rate limit on POST /api/runs (cost guard)
  * strict headers / CSP; pages render all data with textContent (no innerHTML of data)
  * reviewer names are self-declared here — put IAP / Firebase Auth in front for real use
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import cap, checks, geo, notify, public, publish, rules
from .config import Settings
from .eval import load_corpus
from .llm import LLM, PRESET_FAULTS, FakeLLM, make_llm, thinking_budget
from .packs import PackError, PackRegistry
from .plugins import Plugin, from_spec
from .pipeline import Options, Pipeline, reverify_edit
from .schemas import Run
from .mailer import FileMailer, Mailer, make_mailer
from .store import Store
from .subscriptions import CONSENT_TEXT, SubscriptionError, SubscriptionStore, normalise_email, validate_prefs
from .workspace import WorkspaceError, WorkspaceStore

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class RunIn(BaseModel):
    pack_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    source_text: str = Field(min_length=10)
    mode: Literal["replay", "live"] = "replay"
    inject: Optional[str] = None  # demo only: ask the FAKE model to make one mistake


class DraftIn(BaseModel):
    reviewer: str
    fields: dict[str, str]


class ReviewIn(BaseModel):
    reviewer: str
    decision: Literal["approve", "reject"]
    note: str = ""


class SubscribeIn(BaseModel):
    email: str = Field(max_length=254)
    region: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    areas: list[str] = Field(max_length=50)
    lang: str = Field(pattern=r"^[a-z]{2,3}$")
    min_level: int = 1
    include_exercises: bool = False
    consent: bool = False


class PrefsIn(BaseModel):
    areas: list[str] = Field(max_length=50)
    lang: str = Field(pattern=r"^[a-z]{2,3}$")
    min_level: int = 1
    include_exercises: bool = False


class TokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)


class EmailIn(BaseModel):
    email: str = Field(max_length=254)


class _RateLimiter:
    def __init__(self, per_min: int) -> None:
        self.per_min = per_min
        self.hits: dict[str, deque] = defaultdict(deque)
        self.global_hits: deque = deque()
        self.lock = threading.Lock()

    def check(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            for q in (self.hits[key], self.global_hits):
                while q and now - q[0] > 60:
                    q.popleft()
            if len(self.hits[key]) >= self.per_min or len(self.global_hits) >= self.per_min * 6:
                return False
            self.hits[key].append(now)
            self.global_hits.append(now)
            return True


def create_app(settings: Optional[Settings] = None, registry: Optional[PackRegistry] = None,
               store: Optional[Store] = None,
               llm_factory: Optional[Callable[[Optional[list]], LLM]] = None,
               plugins: Optional[list[Plugin]] = None,
               mailer: Optional[Mailer] = None, subscribers: Optional[SubscriptionStore] = None,
               workspace_store: Optional[WorkspaceStore] = None) -> FastAPI:
    settings = settings or Settings.from_env()
    Options(drafting=settings.drafting)  # fail at startup, not on the first request, if LASTMILE_DRAFTING is wrong
    plugin_list: list[Plugin] = plugins if plugins is not None else from_spec(settings.plugins)
    registry = registry or PackRegistry()
    cloud_files = None
    if settings.storage_bucket:
        from .cloud_storage import CloudAlertStore, CloudFiles, CloudSubscriptionStore, CloudWorkspaceStore
        cloud_files = CloudFiles(settings.storage_bucket, settings.data_dir or "var")
        store = store or CloudAlertStore(cloud_files)
        workspace_store = workspace_store or CloudWorkspaceStore(cloud_files)
        subscribers = subscribers or CloudSubscriptionStore(cloud_files)
    store = store or Store(settings.data_dir)
    limiter = _RateLimiter(settings.rate_limit_per_min)
    public_limiter = _RateLimiter(settings.public_rate_limit_per_min)   # subscribe / sign-in / unsubscribe
    mailer = mailer if mailer is not None else make_mailer(settings.mail, settings.data_dir)
    subs = subscribers or SubscriptionStore(settings.data_dir)
    packs_by_region = {p.region.id: p for p in reversed(registry.all())}
    # alert emails over SMTP are sent here, after the approve request has returned (one job at a time; each job
    # uses a few connections). Non-daemon threads: a normal shutdown finishes the sends in progress.
    email_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lastmile-email")
    # warm the one-off costs here so the first alert of the day is as fast as the rest
    cap.warm_up()
    for p in registry.all():
        geo.load_boundaries(p.region.id)
    if settings.llm == "gemini":
        for var in ("LASTMILE_THINKING_FAST", "LASTMILE_THINKING_VERIFIER"):
            thinking_budget(var)  # a mistyped budget fails at startup, not on every alert
        try:  # import the SDK and build the shared client now (no network call): saves ~0.5 s on the first alert
            _ = make_llm(settings.llm).client  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 — a model problem must show as a blocked run, not stop the console
            pass

    def default_factory(faults: Optional[list]) -> LLM:
        if settings.llm == "fake":
            return FakeLLM(faults)
        return make_llm(settings.llm)

    make = llm_factory or default_factory

    app = FastAPI(title="LastMile Alert", version="0.1.0", docs_url="/api/docs", redoc_url=None)

    # ----------------------------------------------------------- plumbing
    @app.middleware("http")
    async def headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        path = request.url.path
        if path.endswith(".svg"):
            resp.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
        elif not path.startswith("/api/docs") and path != "/openapi.json":
            resp.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data: blob: https://tile.openstreetmap.org; style-src 'self'; script-src 'self'; "
                "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        if path.startswith("/api/") and "cache-control" not in resp.headers:
            resp.headers["Cache-Control"] = "no-store"
        return resp

    def auth(request: Request) -> None:
        if settings.api_key:
            given = request.headers.get("x-api-key", "")
            if not secrets.compare_digest(given.encode(), settings.api_key.encode()):
                raise HTTPException(401, "missing or wrong X-API-Key")

    hub = workspace_store or WorkspaceStore(Path(settings.data_dir or "var") / "response.sqlite3")

    from .teams_api import mount_teams
    mount_teams(app, hub, settings, WEB_DIR, cloud=cloud_files)

    @app.exception_handler(WorkspaceError)
    async def _workspace_err(_: Request, e: WorkspaceError):
        # A refused incident action (permission, stale version, failed check) is a plain JSON error, not a 500.
        return JSONResponse({"detail": str(e)}, status_code=e.status)

    @app.exception_handler(publish.PublishError)
    async def _pub_err(_: Request, e: publish.PublishError):
        return JSONResponse({"error": e.code, "detail": str(e)}, status_code=e.status)

    @app.exception_handler(PackError)
    async def _pack_err(_: Request, e: PackError):
        return JSONResponse({"error": "pack", "detail": str(e)}, status_code=404)

    def get_run(run_id: str) -> Run:
        run = store.get(run_id)
        if run is None:
            raise HTTPException(404, "run not found")
        return run

    def view(run: Run) -> dict[str, Any]:
        pack = registry.get(run.pack_id)
        d = run.model_dump()
        eff = publish.effective_status(run, settings)
        d["blocking_count"] = len(run.blocking())
        d["effective_cap_status"] = eff
        d["required_approvals"] = pack.channels.required_approvals("live" if eff == "Actual" else "replay")
        d["approver_count"] = len({a.reviewer.casefold() for a in run.approvals if a.decision == "approve"})
        d["languages"] = {k: v.name for k, v in pack.region.languages.items()}
        d["pack_name"] = pack.name
        d["level_labels"] = {str(s.level): s.label for s in pack.hazard.scale}
        d["sms_info"] = {}
        for lg, dr in run.drafts.items():
            enc, units, segs = checks.sms_segments(dr.sms)
            d["sms_info"][lg] = {"encoding": enc, "units": units, "segments": segs,
                                 "max_segments": pack.channels.sms_max_segments,
                                 "headline_chars": len(dr.headline), "headline_max": pack.channels.headline_max,
                                 "voice_bytes": len(dr.spoken.encode("utf-8")),
                                 "voice_max_bytes": pack.channels.voice_max_bytes,
                                 # what a phone would show, with sender, hotline, reference and drill marker
                                 "channel_preview": checks.sms_envelope(
                                     pack, lg, dr.sms, exercise=eff != "Actual",
                                     ref=publish.reference(run.cap_identifier) if run.cap_identifier
                                     else checks.REF_PLACEHOLDER)}
            enc_c, units_c, segs_c = checks.sms_segments(d["sms_info"][lg]["channel_preview"])
            d["sms_info"][lg].update(channel_segments=segs_c, channel_encoding=enc_c)
        d["places"] = []
        if run.facts:
            for p in run.facts.places:
                g = pack.place(p.canonical) if p.canonical else None
                d["places"].append({"as_written": p.as_written, "canonical": p.canonical, "level": p.level,
                                    "name": g.names if g else None, "kind": g.kind if g else None,
                                    "parent": g.parent if g else None})
        return d

    def summary(run: Run) -> dict[str, Any]:
        return {"id": run.id, "created_at": run.created_at, "pack_id": run.pack_id, "mode": run.mode,
                "status": run.status, "msg_type": run.msg_type, "blocking": len(run.blocking()),
                "max_level": run.derived.max_level if run.derived else None,
                "cap_identifier": run.cap_identifier}

    # -------------------------------------------------------------- routes
    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "llm": settings.llm, "drafting": settings.drafting, "allow_actual": settings.allow_actual,
                "data_backend": "cloud-storage" if cloud_files else "local",
                "mail": getattr(mailer, "name", "off"),
                "auth_required": bool(settings.api_key), "packs": registry.bundle_ids(),
                "plugins": [p.name for p in plugin_list]}

    @app.get("/api/packs")
    def packs() -> list[dict]:
        return [p.summary() for p in registry.all()]

    @app.get("/api/samples")
    def samples() -> list[dict]:
        out = []
        for c in load_corpus():
            if c.get("ui"):
                out.append({"id": c["id"], "title": c["title"], "pack": c["pack"], "text": c["text"],
                            "setup": c.get("setup", []), "defect": c.get("defect", "none")})
        return out

    scorecard_cache: dict[str, Any] = {}

    @app.get("/api/scorecard")
    def scorecard() -> dict:
        """The safety-net evaluation (python -m lastmile.eval) on the bundled synthetic corpus, computed once.

        Always uses the scripted fake model with fault injection: it measures whether the checks catch known
        model and source mistakes, not how good any real model's language is.
        """
        if not scorecard_cache:
            from .eval import evaluate
            full, tpl = evaluate("fake", registry=registry), evaluate("fake", registry=registry, drafting="templates")
            keys = ("cases", "pipeline_matches_expectations", "risky_cases", "baseline_unsafe_in_queue",
                    "pipeline_unsafe_in_queue", "clean_cases", "pipeline_false_blocks", "residual_cases")
            scorecard_cache.update({
                "model": "scripted fake model with fault injection (measures the safety net, not language quality)",
                "drafting_model": {k: full[k] for k in keys},
                "drafting_templates": {k: tpl[k] for k in keys},
            })
        return scorecard_cache

    @app.get("/api/inject-options")
    def inject_options() -> dict:
        return {"enabled": settings.llm == "fake", "options": sorted(PRESET_FAULTS)}

    @app.post("/api/runs", dependencies=[Depends(auth)])
    def create_run(body: RunIn, request: Request) -> dict:
        if len(body.source_text) > settings.max_source_chars:
            raise HTTPException(413, f"source text longer than {settings.max_source_chars} characters")
        client = request.client.host if request.client else "unknown"
        if not limiter.check(client):
            raise HTTPException(429, "rate limit exceeded; try again in a minute")
        faults = None
        if body.inject:
            if settings.llm != "fake":
                raise HTTPException(422, "inject is only available with the fake model")
            if body.inject not in PRESET_FAULTS:
                raise HTTPException(422, f"unknown inject option; choose one of {sorted(PRESET_FAULTS)}")
            faults = PRESET_FAULTS[body.inject]
        registry.get(body.pack_id)  # 404 on unknown pack
        pipe = Pipeline(make(faults), registry, store,
                        Options(max_source_chars=settings.max_source_chars, drafting=settings.drafting,
                                allow_actual=settings.allow_actual),
                        plugins=plugin_list)
        run = pipe.run(body.source_text, body.pack_id, body.mode)
        return view(run)

    @app.get("/api/runs", dependencies=[Depends(auth)])
    def list_runs(limit: int = 30) -> list[dict]:
        return [summary(r) for r in store.list(max(1, min(limit, 200)))]

    @app.get("/api/runs/{run_id}", dependencies=[Depends(auth)])
    def read_run(run_id: str) -> dict:
        return view(get_run(run_id))

    @app.put("/api/runs/{run_id}/drafts/{lang}", dependencies=[Depends(auth)])
    def edit(run_id: str, lang: str, body: DraftIn) -> dict:
        with store._lock:
            before = len(get_run(run_id).audit)
            run = publish.edit_draft(store, registry, run_id, lang, body.fields, body.reviewer, plugin_list)
            if len(run.audit) > before:  # edited content and advisory re-verification form one gate
                reverify_edit(run, registry.get(run.pack_id), make(None), lang)
                store.put(run)
            return view(run)

    @app.post("/api/runs/{run_id}/review", dependencies=[Depends(auth)])
    def review(run_id: str, body: ReviewIn, request: Request) -> dict:
        was_published = get_run(run_id).status == "published"
        run = publish.review(store, registry, settings, run_id, body.reviewer, body.decision, body.note,
                             plugins=plugin_list)
        if run.status == "published" and not was_published:
            # the alert is out; now tell the people who asked for email about these areas (counts only on record)
            summary, mails = notify.prepare(mailer, subs, store, registry.get(run.pack_id), run, base_url(request))
            if mailer is None or mailer.name == "file" or not mails:
                record_email(run, notify.deliver(mailer, mails, summary))
            else:  # a network relay can take seconds per batch: the reviewer must not wait for it
                run.outbox = {**run.outbox, "email": {**summary, "status": "sending"}}
                store.put(run)
                email_pool.submit(send_in_background, run, mails, summary)
        return view(run)

    def record_email(run: Run, summary: dict) -> None:
        run.outbox = {**run.outbox, "email": summary}
        store.audit(run, "system", "email_dispatch",
                    f"{summary['sent']} sent, {summary['failed']} failed, {summary['matched']} matched")

    def send_in_background(run: Run, mails: list, summary: dict) -> None:
        try:
            record_email(run, notify.deliver(mailer, mails, summary))
        except Exception as e:  # noqa: BLE001 — the alert is already published; record, never raise
            record_email(run, {**summary, "status": "error", "failed": len(mails) - summary["sent"],
                               "error": type(e).__name__})

    @app.post("/api/runs/{run_id}/retract", dependencies=[Depends(auth)])
    def retract(run_id: str, request: Request) -> dict:
        """Draft the cancellation of ONE published alert that was sent in error (Hawaii 2018 took 38 minutes).

        Uses the pack's pre-approved "sent in error" wording; it still goes through every check and needs a named
        human approval like any other run.
        """
        original = get_run(run_id)
        if original.status != "published" or original.msg_type == "Cancel" or not original.cap_identifier:
            raise HTTPException(409, "only a published alert (not a cancellation) can be retracted")
        client = request.client.host if request.client else "unknown"
        if not limiter.check(client):
            raise HTTPException(429, "rate limit exceeded; try again in a minute")
        pack = registry.get(original.pack_id)
        assert original.facts
        if original.mode == "replay":   # replays run on the bulletin's clock: retract a minute after it was issued
            issued = (checks.parse_dt(original.facts.issued_iso, pack) or datetime.now(timezone.utc))
            issued = issued + timedelta(minutes=1)
        else:
            issued = datetime.now(timezone.utc)
        text = (f"CORRECTION - ALERT SENT IN ERROR\n"
                f"Issued: {issued.astimezone(pack.tz).strftime('%Y-%m-%d %H:%M')}\n"
                f"The alert {original.cap_identifier} was sent in error. Disregard that alert.")
        pipe = Pipeline(make(None), registry, store,
                        Options(max_source_chars=settings.max_source_chars, drafting=settings.drafting,
                                allow_actual=settings.allow_actual), plugins=plugin_list)
        run = pipe.run(text, original.pack_id, original.mode, cancel_target=original.cap_identifier)
        store.audit(run, "system", "retraction_of", original.cap_identifier)
        return view(run)

    @app.get("/api/runs/{run_id}/cap-preview.xml", dependencies=[Depends(auth)])
    def cap_preview(run_id: str) -> Response:
        run = get_run(run_id)
        return Response(publish.preview_cap(run, registry, settings), media_type="application/xml")

    @app.get("/api/runs/{run_id}/cap.xml", dependencies=[Depends(auth)])
    def cap_final(run_id: str) -> Response:
        run = get_run(run_id)
        if run.status != "published" or not run.cap_xml:
            raise HTTPException(409, "alert is not published; only an approved alert has a final CAP file")
        return Response(run.cap_xml.encode("utf-8"), media_type="application/cap+xml")

    def _place_levels(run: Run) -> dict[str, int]:
        if not run.derived:
            return {}
        return {pid: lv for lv, pids in run.derived.places_by_level.items() for pid in pids}

    @app.get("/api/runs/{run_id}/map.svg", dependencies=[Depends(auth)])
    def map_svg(run_id: str) -> Response:
        run = get_run(run_id)
        return Response(geo.render_svg(registry.get(run.pack_id), _place_levels(run)), media_type="image/svg+xml")

    @app.get("/api/runs/{run_id}/geojson", dependencies=[Depends(auth)])
    def geojson(run_id: str) -> dict:
        run = get_run(run_id)
        return geo.geojson(registry.get(run.pack_id), _place_levels(run))

    @app.get("/api/runs/{run_id}/outbox", dependencies=[Depends(auth)])
    def outbox(run_id: str) -> dict:
        run = get_run(run_id)
        if run.status != "published":
            raise HTTPException(409, "nothing in the outbox until the alert is approved and published")
        return run.outbox

    @app.get("/api/active", dependencies=[Depends(auth)])
    def active(at: Optional[str] = None) -> list[dict]:
        """Alerts in force at `at` (ISO 8601, default now): not cancelled, already sent, not yet expired."""
        try:
            when = datetime.fromisoformat(at) if at else datetime.now(timezone.utc)
        except ValueError:
            raise HTTPException(422, "at must be an ISO 8601 date-time with a UTC offset")
        if when.tzinfo is None:
            raise HTTPException(422, "at must include a UTC offset, e.g. 2025-11-27T23:00:00+05:30")
        out = []
        for a in store.active_alerts():
            try:
                pack = registry.get(a.pack_id)
            except PackError:
                continue
            if rules.is_active(a, when, pack):
                out.append(a.model_dump())
        return out

    # --------------------------------------------------- public: no login
    def base_url(request: Request) -> str:
        return settings.public_url or str(request.base_url).rstrip("/")

    def public_check(request: Request) -> None:
        client = request.client.host if request.client else "unknown"
        if not public_limiter.check(client):
            raise HTTPException(429, "too many requests; try again in a minute")

    def when(at: Optional[str]) -> datetime:
        if not at:
            return datetime.now(timezone.utc)
        try:
            dt = datetime.fromisoformat(at)
        except ValueError:
            raise HTTPException(422, "at must be an ISO 8601 date-time with a UTC offset")
        if dt.tzinfo is None:
            raise HTTPException(422, "at must include a UTC offset")
        return dt

    @app.exception_handler(SubscriptionError)
    async def _sub_err(_: Request, e: SubscriptionError):
        return JSONResponse({"error": e.code, "detail": str(e)}, status_code=e.status)

    @app.get("/alerts", include_in_schema=False)
    def public_page() -> FileResponse:
        # The earlier warning map with email alerts; the public page is now /public.
        return FileResponse(WEB_DIR / "public.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/public/regions")
    def public_regions() -> list[dict]:
        return public.regions(registry)

    @app.get("/api/public/situation")
    def public_situation(region: str = "lk", at: Optional[str] = None) -> dict:
        try:
            return public.situation(registry, store, region, when(at), settings.public_show_exercise)
        except KeyError:
            raise HTTPException(404, "unknown region")

    @app.get("/api/public/area")
    def public_area(id: str, region: str = "lk", at: Optional[str] = None) -> dict:
        pack = packs_by_region.get(region)
        if pack is None:
            raise HTTPException(404, "unknown region")
        sit = public.situation(registry, store, region, when(at), settings.public_show_exercise)
        try:
            return public.area_status(pack, sit, id)
        except KeyError:
            raise HTTPException(404, "unknown area")

    @app.get("/api/public/map.svg")
    def public_map(region: str = "lk", lang: str = "en", at: Optional[str] = None) -> Response:
        pack = packs_by_region.get(region)
        if pack is None:
            raise HTTPException(404, "unknown region")
        lang = lang if lang in pack.region.languages else pack.langs[0]
        sit = public.situation(registry, store, region, when(at), settings.public_show_exercise)
        mark = pack.region.languages[lang].ui_text("exercise_tag") if sit["exercise"] else ""
        return Response(region_svg(region, tuple(sorted(sit["area_levels"].items())), lang, mark),
                        media_type="image/svg+xml")

    @app.get("/api/public/map.geojson")
    def public_map_geojson(region: str = "lk", at: Optional[str] = None) -> dict:
        pack = packs_by_region.get(region)
        if pack is None:
            raise HTTPException(404, "unknown region")
        sit = public.situation(registry, store, region, when(at), settings.public_show_exercise)
        result = geo.geojson(pack, sit["area_levels"])
        result.update(at=sit["at"], exercise=sit["exercise"])
        return result

    @lru_cache(maxsize=256)
    def region_svg(region: str, area_levels: tuple, lang: str, mark: str) -> str:
        """The drawing depends only on these inputs, which are worked out fresh on every request (so a new or
        expired alert changes the key and the map is never stale); in a disaster most people ask for the same one."""
        return geo.render_region_svg(packs_by_region[region], dict(area_levels), lang, mark)

    @app.get("/feeds/alerts.atom")
    def atom(request: Request, at: Optional[str] = None) -> Response:
        return Response(public.atom_feed(registry, store, base_url(request), when(at), settings.public_show_exercise),
                        media_type="application/atom+xml")

    @app.get("/feeds/cap/{identifier}.xml")
    def public_cap(identifier: str) -> Response:
        xml = public.find_cap(store, identifier, settings.public_show_exercise)
        if xml is None:
            raise HTTPException(404, "no such published alert")
        return Response(xml.encode("utf-8"), media_type="application/cap+xml")

    # -------------------------------------------- email subscriptions (public)
    def need_mail() -> Mailer:
        if mailer is None:
            raise HTTPException(503, "email alerts are not enabled on this deployment")
        return mailer

    @app.post("/api/public/subscribe", status_code=202)
    def subscribe(body: SubscribeIn, request: Request) -> dict:
        public_check(request)
        m = need_mail()
        if not body.consent:
            raise HTTPException(422, "please tick the consent box: we need it to store your email address")
        email = normalise_email(body.email)
        pack = validate_prefs(packs_by_region, body.region, body.areas, body.lang, body.min_level)
        sub, token = subs.subscribe(email, body.region, body.areas, body.lang, body.min_level, body.include_exercises)
        if subs.allow_mail(email):
            m.send(notify.compose_confirm(pack, sub, token, base_url(request)))
        # same answer whether or not the address was known: no way to probe who subscribed
        return {"ok": True, "detail": "If the address can receive email, a confirmation link is on its way."}

    @app.post("/api/public/confirm")
    def confirm(body: TokenIn, request: Request) -> dict:
        public_check(request)
        sub, _unsub = subs.confirm(body.token)
        return {"ok": True, "subscription": sub.public_view()}

    @app.post("/api/public/signin", status_code=202)
    def signin(body: EmailIn, request: Request) -> dict:
        public_check(request)
        m = need_mail()
        email = normalise_email(body.email)
        started = subs.start_signin(email)
        if started and subs.allow_mail(email):
            sub, token = started
            m.send(notify.compose_signin(packs_by_region[sub.region], sub, token, base_url(request)))
        return {"ok": True, "detail": "If this address has confirmed warning emails, a sign-in link is on its way."}

    @app.post("/api/public/session")
    def session(body: TokenIn, request: Request) -> dict:
        public_check(request)
        sub, token = subs.finish_signin(body.token)
        return {"session": token, "subscription": sub.public_view()}

    def me_of(request: Request):
        return subs.by_session(request.headers.get("x-session", ""))

    @app.get("/api/public/me")
    def me(request: Request) -> dict:
        return me_of(request).public_view()

    @app.put("/api/public/me")
    def me_update(body: PrefsIn, request: Request) -> dict:
        sub = me_of(request)
        validate_prefs(packs_by_region, sub.region, body.areas, body.lang, body.min_level)
        subs.update(sub, body.areas, body.lang, body.min_level, body.include_exercises)
        return sub.public_view()

    @app.delete("/api/public/me")
    def me_delete(request: Request) -> dict:
        subs.delete(me_of(request))
        return {"ok": True, "detail": "Your email address and preferences have been deleted."}

    @app.post("/api/public/unsubscribe")
    async def unsubscribe(request: Request, token: Optional[str] = None) -> dict:
        """Token in the URL (RFC 8058 one-click from a mail app, form body ignored) or in a JSON body (the page)."""
        public_check(request)
        if not token and request.headers.get("content-type", "").startswith("application/json"):
            try:
                token = str((await request.json()).get("token", ""))
            except ValueError:
                token = ""
        ok = subs.unsubscribe(token or "")
        if not ok:
            raise HTTPException(404, "this unsubscribe link is not valid (perhaps already used)")
        return {"ok": True, "detail": "Unsubscribed. Your email address has been deleted."}

    @app.get("/api/public/consent")
    def consent_text() -> dict:
        return {"text": CONSENT_TEXT}

    @app.get("/api/dev/mailbox", include_in_schema=False)
    def dev_mailbox() -> list[dict]:
        if not (settings.dev_mailbox and isinstance(mailer, FileMailer)):
            raise HTTPException(404, "not found")
        return [{"to": m.to, "subject": m.subject, "body": m.body, "headers": m.headers} for m in reversed(mailer.sent)]

    # ------------------------------------------------------------- static
    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "teams.html", headers={"Cache-Control": "no-cache"})

    @app.get("/console", include_in_schema=False)
    def warning_console() -> RedirectResponse:
        # The separate warning review console was retired; warnings are recorded in each incident.
        return RedirectResponse("/", status_code=308)

    if WEB_DIR.is_dir():
        # Compress local UI assets without changing authenticated JSON responses.
        app.mount("/static", GZipMiddleware(StaticFiles(directory=WEB_DIR), minimum_size=1024), name="static")
    return app


def app_factory() -> FastAPI:  # uvicorn lastmile.api:app_factory --factory
    settings = Settings.from_env()
    settings.data_dir = settings.data_dir or "var"
    return create_app(settings)
