"""End to end over HTTP: the real server process, configured by environment variables, as it runs in the container.

Walks the demo script (docs/JUDGE_GUIDE.md) and the operational edges: persistence across a restart,
two-reviewer live alerts, API key, rate limit, input cap, and the Gemini configuration against the local mock.
"""

from __future__ import annotations

import pytest
from lxml import etree

from clock import live_text
from e2e_server import AppServer
from lastmile import cap
from lastmile.eval import load_corpus

AFTER_FIRST = {"at": "2025-11-27T20:00:00+05:30"}     # the synthetic bulletins replay late November 2025
NEXT_MORNING = {"at": "2025-11-28T10:00:00+05:30"}
CORPUS = {c["id"]: c for c in load_corpus()}
NS = {"c": cap.CAP_NS}


def text(case_id: str) -> str:
    return CORPUS[case_id]["text"]


def draft(c, case_id: str, **kw) -> dict:
    src = live_text(text(case_id)) if kw.get("mode") == "live" else text(case_id)
    r = c.post("/api/runs", json={"pack_id": CORPUS[case_id]["pack"], "source_text": src, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def approve(c, run_id: str, who: str = "A. Perera", expect: int = 200) -> dict:
    r = c.post(f"/api/runs/{run_id}/review", json={"reviewer": who, "decision": "approve"})
    assert r.status_code == expect, r.text
    return r.json()


def cap_of(c, run_id: str) -> etree._Element:
    r = c.get(f"/api/runs/{run_id}/cap.xml")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/cap+xml")
    assert cap.validate(r.content) == []                                    # official CAP 1.2 XSD
    return etree.fromstring(r.content)


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("lastmile-data")


@pytest.fixture(scope="module")
def server(data_dir):
    with AppServer({"LASTMILE_DATA_DIR": str(data_dir), "LASTMILE_RATE_LIMIT_PER_MIN": "1000"}) as s:
        yield s


@pytest.fixture(scope="module")
def c(server):
    with server.client() as cl:
        yield cl


# --------------------------------------------------------------------------- the app is served
def test_app_and_security_headers(c):
    r = c.get("/")
    assert r.status_code == 200 and "LastMile" in r.text
    assert c.get("/console", follow_redirects=False).status_code == 308
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "unsafe-inline" not in csp.split("style-src")[0]
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
    js = c.get("/static/teams.js").text
    assert "innerHTML" not in js                                            # invariant 7
    health = c.get("/api/health").json()
    assert (health["ok"], health["llm"], health["allow_actual"], health["drafting"]) == (True, "fake", False, "model")
    assert c.get("/api/runs").headers["cache-control"] == "no-store"
    assert c.get("/api/docs").status_code == 200


# ------------------------------------------------------------------ the demo script, in order
def test_demo_script_end_to_end(c):
    # happy path: three languages, levels on the map, only the honesty warning
    run = draft(c, "lk-clean-multilevel")
    rid = run["id"]
    assert run["status"] == "ready" and run["msg_type"] == "Alert" and set(run["drafts"]) == {"en", "si", "ta"}
    assert {i["code"] for i in run["issues"]} == {"pack_unverified"}
    assert run["effective_cap_status"] == "Exercise" and run["required_approvals"] == 1
    assert "150" in run["drafts"]["si"]["description"] and "3 වන මට්ටම – රතු" in run["drafts"]["si"]["headline"]
    assert run["sms_info"]["ta"]["encoding"] == "UCS-2"
    svg = c.get(f"/api/runs/{rid}/map.svg")
    assert svg.headers["content-type"].startswith("image/svg+xml") and "<svg" in svg.text
    assert len(c.get(f"/api/runs/{rid}/geojson").json()["features"]) == 4

    # nothing final exists before a human approves
    assert c.get(f"/api/runs/{rid}/cap.xml").status_code == 409
    assert c.get(f"/api/runs/{rid}/outbox").status_code == 409
    prev = c.get(f"/api/runs/{rid}/cap-preview.xml")
    assert b"<status>Draft</status>" in prev.content and cap.validate(prev.content) == []
    approve(c, rid, who="", expect=422)                                     # a named reviewer is required

    # the human decides -> CAP (Exercise) + outbox
    done = approve(c, rid)
    assert done["status"] == "published" and done["cap_status"] == "Exercise"
    root = cap_of(c, rid)
    assert root.findtext("c:status", namespaces=NS) == "Exercise"
    assert root.findtext("c:msgType", namespaces=NS) == "Alert"
    assert {i.findtext("c:language", namespaces=NS) for i in root.findall("c:info", NS)} == {"en-LK", "si-LK", "ta-LK"}
    box = c.get(f"/api/runs/{rid}/outbox").json()
    assert set(box["sms"]) == {"en", "si", "ta"} and box["voice"]["si"]["script"]
    assert [a["identifier"] for a in c.get("/api/active", params=AFTER_FIRST).json()] == [done["cap_identifier"]]
    assert c.get("/api/active").json() == []                                # expired by today's clock
    assert approve(c, rid, expect=409)["error"] == "already_published"

    # escalation -> Update that references the first alert
    upd = draft(c, "lk-update-escalation")
    assert upd["status"] == "ready" and upd["msg_type"] == "Update" and len(upd["references"]) == 1
    approve(c, upd["id"], who="B. Silva")
    root = cap_of(c, upd["id"])
    assert done["cap_identifier"] in root.findtext("c:references", namespaces=NS)
    assert [a["identifier"] for a in c.get("/api/active", params=NEXT_MORNING).json()] == [
        c.get(f"/api/runs/{upd['id']}").json()["cap_identifier"]]

    # cancellation closes it
    canc = draft(c, "lk-cancel")
    assert canc["status"] == "ready" and canc["msg_type"] == "Cancel"
    approve(c, canc["id"])
    assert cap_of(c, canc["id"]).findtext("c:msgType", namespaces=NS) == "Cancel"
    assert c.get("/api/active", params=NEXT_MORNING).json() == []


@pytest.mark.parametrize("case_id,code", [
    ("lk-mislabeled-level", "advice_level_mismatch"),
    ("lk-unknown-place", "unresolved_place"),
    ("lk-conflicting-figures", "source_ambiguity"),
    ("lk-injection", "prompt_injection_suspected"),
])
def test_traps_are_blocked_with_no_path_to_publish(c, case_id, code):
    run = draft(c, case_id)
    assert run["status"] == "blocked" and code in {i["code"] for i in run["issues"]}
    assert run["drafts"] == {}                                              # the model was never asked to write
    err = approve(c, run["id"], expect=409)
    assert err["error"] == "blocked"
    assert c.get(f"/api/runs/{run['id']}/cap.xml").status_code == 409
    if code == "prompt_injection_suspected":
        assert not any(t["actor"] == "ai" for t in c.get(f"/api/runs/{run['id']}").json()["trace"])


def test_fake_model_error_is_caught_and_repaired(c):
    run = draft(c, "lk-clean-multilevel", inject="drop_number")
    assert run["status"] == "ready" and run["attempts"]["si"] == 2
    assert any(t["step"].startswith("re-localize") for t in run["trace"])
    assert c.post("/api/runs", json={"pack_id": "lk-landslide", "source_text": text("lk-clean-multilevel"),
                                     "inject": "nope"}).status_code == 422


def test_reviewer_edit_rechecks_and_clears_approvals(c):
    run = draft(c, "lk-clean-multilevel", mode="replay")
    rid = run["id"]
    bad = run["drafts"]["en"]["description"].replace("150", "")
    r = c.put(f"/api/runs/{rid}/drafts/en", json={"reviewer": "A. Perera", "fields": {"description": bad}})
    assert r.status_code == 200 and r.json()["status"] == "blocked"
    assert "missing_number" in {i["code"] for i in r.json()["issues"]}
    approve(c, rid, expect=409)
    good = run["drafts"]["en"]["description"]
    r = c.put(f"/api/runs/{rid}/drafts/en", json={"reviewer": "A. Perera", "fields": {"description": good}})
    assert r.json()["status"] == "ready"
    assert c.put(f"/api/runs/{rid}/drafts/en", json={"reviewer": "A. Perera", "fields": {"facts": "x"}}).status_code == 422
    assert "edit_draft" in [a["action"] for a in r.json()["audit"]]


def test_untrusted_text_round_trips_as_data(c):
    hostile = text("lk-clean-multilevel") + "\n<img src=x onerror=alert(1)> <script>alert(2)</script>"
    run = c.post("/api/runs", json={"pack_id": "lk-landslide", "source_text": hostile}).json()
    assert run["source_text"] == hostile                                    # stored verbatim, never interpreted
    assert c.get("/api/runs/run_doesnotexist").status_code == 404
    assert c.post("/api/runs", json={"pack_id": "../etc", "source_text": hostile}).status_code == 422
    assert c.post("/api/runs", json={"pack_id": "nope", "source_text": hostile}).status_code == 404


def test_second_country_pack(c):
    run = draft(c, "np-flood-clean")
    assert run["status"] == "ready" and set(run["drafts"]) == {"en", "ne"}
    approve(c, run["id"])
    assert cap_of(c, run["id"]).findtext("c:status", namespaces=NS) == "Exercise"


# ------------------------------------------------------------- persistence survives a restart
def test_state_survives_a_restart(server, data_dir):
    with server.client() as cl:
        run = draft(cl, "lk-clean-multilevel")
        done = approve(cl, run["id"])
    server.stop()
    with AppServer({"LASTMILE_DATA_DIR": str(data_dir), "LASTMILE_RATE_LIMIT_PER_MIN": "1000"}) as again, \
            again.client() as cl:
        back = cl.get(f"/api/runs/{run['id']}").json()
        assert back["status"] == "published" and back["cap_identifier"] == done["cap_identifier"]
        assert done["cap_identifier"] in {a["identifier"] for a in cl.get("/api/active", params=AFTER_FIRST).json()}
        assert (data_dir / "outbox" / run["id"] / f"{done['cap_identifier']}.xml").is_file()
        cap_of(cl, run["id"])
        upd = draft(cl, "lk-update-escalation")                             # supersede logic sees the old alert
        assert upd["msg_type"] == "Update" and any(done["cap_identifier"] in r for r in upd["references"])
    server.start()                                                          # leave the module fixture running


# ------------------------------------------------------------------- other deployments
def test_live_alert_needs_two_distinct_reviewers():
    with AppServer({"LASTMILE_ALLOW_ACTUAL": "1"}) as s, s.client() as cl:
        assert cl.get("/api/health").json()["allow_actual"] is True
        run = draft(cl, "lk-clean-multilevel", mode="live")
        assert run["effective_cap_status"] == "Actual" and run["required_approvals"] == 2
        one = approve(cl, run["id"], who="Alice Perera")
        assert one["status"] == "ready" and one["approver_count"] == 1
        assert approve(cl, run["id"], who="alice perera", expect=409)["error"] == "duplicate_approver"
        two = approve(cl, run["id"], who="Bob Silva")
        assert two["status"] == "published"
        assert cap_of(cl, run["id"]).findtext("c:status", namespaces=NS) == "Actual"
        replay = draft(cl, "lk-clean-multilevel", mode="replay")
        assert replay["effective_cap_status"] == "Exercise"                 # replay is always an exercise


def test_live_mode_without_the_deployment_flag_stays_an_exercise():
    with AppServer() as s, s.client() as cl:
        run = draft(cl, "lk-clean-multilevel", mode="live")
        assert run["effective_cap_status"] == "Exercise" and run["required_approvals"] == 1


def test_api_key_rate_limit_and_input_cap():
    env = {"LASTMILE_API_KEY": "s3cret-key", "LASTMILE_RATE_LIMIT_PER_MIN": "2", "LASTMILE_MAX_SOURCE_CHARS": "2000"}
    with AppServer(env) as s, s.client() as cl:
        assert cl.get("/api/health").json()["auth_required"] is True        # health stays open
        body = {"pack_id": "lk-landslide", "source_text": text("lk-clean-multilevel")}
        assert cl.post("/api/runs", json=body).status_code == 401
        assert cl.get("/api/runs").status_code == 401
        assert cl.post("/api/runs", json=body, headers={"X-API-Key": "wrong"}).status_code == 401
        key = {"X-API-Key": "s3cret-key"}
        big = {"pack_id": "lk-landslide", "source_text": "x" * 2001}
        assert cl.post("/api/runs", json=big, headers=key).status_code == 413
        assert cl.post("/api/runs", json=body, headers=key).status_code == 200
        assert cl.post("/api/runs", json=body, headers=key).status_code == 200
        assert cl.post("/api/runs", json=body, headers=key).status_code == 429


def test_bad_configuration_fails_at_startup():
    with pytest.raises(RuntimeError):
        AppServer({"LASTMILE_DRAFTING": "freestyle"}).start(timeout=15)


# ------------------------------------------ the production Gemini configuration, against the local mock
@pytest.fixture
def mock_gemini():
    pytest.importorskip("google.genai")
    from lastmile.mock_gemini import MockGemini
    with MockGemini() as m:
        yield m


def _gemini_env(mock, **extra) -> dict:
    return {"LASTMILE_LLM": "gemini", "GOOGLE_API_KEY": "offline-mock", "GOOGLE_GEMINI_BASE_URL": mock.url,
            "LASTMILE_RATE_LIMIT_PER_MIN": "1000", **extra}


def test_gemini_configuration_end_to_end(mock_gemini):
    with AppServer(_gemini_env(mock_gemini)) as s, s.client() as cl:
        assert cl.get("/api/health").json()["llm"] == "gemini"
        assert cl.get("/api/inject-options").json()["enabled"] is False     # fault injection is fake-model only
        assert cl.post("/api/runs", json={"pack_id": "lk-landslide", "source_text": text("lk-clean-multilevel"),
                                          "inject": "drop_number"}).status_code == 422
        run = draft(cl, "lk-clean-multilevel")
        assert run["status"] == "ready" and run["attempts"] == {"en": 1, "si": 1, "ta": 1}
        done = approve(cl, run["id"])
        assert done["status"] == "published"
        cap_of(cl, run["id"])
        assert draft(cl, "lk-injection")["status"] == "blocked"
    roles = [r["role"] for r in mock_gemini.requests]
    assert roles.count("intake") == 1 and roles.count("localize") == 3 and roles.count("verify") == 3


def test_gemini_outage_degrades_visibly_end_to_end():
    pytest.importorskip("google.genai")
    from lastmile.mock_gemini import MockGemini
    with MockGemini(fail={"localize": 503}) as mock, AppServer(_gemini_env(mock)) as s, s.client() as cl:
        run = draft(cl, "lk-clean-multilevel")
        assert run["status"] == "ready" and "llm_fallback_template" in {i["code"] for i in run["issues"]}
        done = approve(cl, run["id"])
        assert "llm_fallback_template" in {i["code"] for i in done["issues"]}  # still on the record after approval
    with MockGemini(fail={"intake": 503}) as mock, AppServer(_gemini_env(mock)) as s, s.client() as cl:
        run = draft(cl, "lk-clean-multilevel")
        assert run["status"] == "blocked" and "llm_error" in {i["code"] for i in run["issues"]}


def test_gemini_template_first_end_to_end(mock_gemini):
    with AppServer(_gemini_env(mock_gemini, LASTMILE_DRAFTING="templates")) as s, s.client() as cl:
        assert cl.get("/api/health").json()["drafting"] == "templates"
        run = draft(cl, "lk-clean-multilevel")
        assert run["status"] == "ready" and "template_draft" in {i["code"] for i in run["issues"]}
    assert {r["role"] for r in mock_gemini.requests} == {"intake", "verify"}


# ------------------------------------------------- scripts/smoke_http.py (used by CI's docker job and after deploy)
def test_smoke_script_against_the_server():
    import subprocess
    import sys

    from e2e_server import ROOT
    with AppServer({"LASTMILE_API_KEY": "k3y", "LASTMILE_RATE_LIMIT_PER_MIN": "1000"}) as s:
        script = [sys.executable, str(ROOT / "scripts" / "smoke_http.py"), s.url]
        ok = subprocess.run(script + ["--api-key", "k3y"], capture_output=True, text=True, timeout=120)
        assert ok.returncode == 0 and "PASS" in ok.stdout, ok.stdout + ok.stderr
        assert "status=Exercise" in ok.stdout
        ro = subprocess.run(script + ["--api-key", "k3y", "--no-approve"], capture_output=True, text=True, timeout=120)
        assert ro.returncode == 0 and "published" not in ro.stdout
        bad = subprocess.run(script, capture_output=True, text=True, timeout=120)  # no key -> clear failure
        assert bad.returncode == 1 and "FAIL" in bad.stdout and "401" in bad.stdout


# ------------------------------------------------- public map + email warnings, real server, files on disk
def test_email_warning_lands_as_eml_and_subscription_survives_restart(tmp_path):
    import re as _re
    env = {"LASTMILE_DATA_DIR": str(tmp_path), "LASTMILE_RATE_LIMIT_PER_MIN": "1000", "LASTMILE_DEV_MAILBOX": "1",
           "LASTMILE_PUBLIC_URL": "https://alerts.example.lk"}
    with AppServer(env) as s, s.client() as cl:
        assert cl.get("/api/health").json()["mail"] == "file"
        r = cl.post("/api/public/subscribe", json={"email": "sunil@example.com", "region": "lk", "areas": ["LK-BD"],
                                                   "lang": "en", "include_exercises": True, "consent": True})
        assert r.status_code == 202
        body = cl.get("/api/dev/mailbox").json()[0]["body"]
        assert "https://alerts.example.lk/alerts?confirm=" in body                  # links use the public URL
        token = _re.search(r"confirm=([\w-]+)", body).group(1)
        assert cl.post("/api/public/confirm", json={"token": token}).status_code == 200
    with AppServer(env) as s, s.client() as cl:                                       # restart: still subscribed
        done = approve(cl, draft(cl, "lk-clean-multilevel")["id"])
        assert done["outbox"]["email"]["sent"] == 1
        emls = list((tmp_path / "mail").glob("*.eml"))
        alert = [f for f in emls if b"Landslide warning" in f.read_bytes()]
        assert len(alert) == 1 and b"List-Unsubscribe:" in alert[0].read_bytes()
        sit = cl.get("/api/public/situation", params=AFTER_FIRST).json()
        assert sit["area_levels"]["LK-BD-PAS"] == 3
        assert cl.get("/alerts").status_code == 200 and "script-src 'self'" in cl.get("/alerts").headers["content-security-policy"]
