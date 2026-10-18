"""Real Chrome: distinct accounts, shared incident, chat/data layout and mobile."""
import os
from pathlib import Path

import pytest
from e2e_server import AppServer
from browser_session import browser
from ui_views import show
from test_team_demo_browser import expand_activity

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect
PASSWORD = "browser-exercise-pass-123"
REPORT = "EXERCISE readiness update\nCurrent capacity: 100 people\nOccupied: 40 people\nExternal pending: 0 people\nStatus: accepting\nAccess: passable\nWater: working\nPower: working"


@pytest.fixture(scope="module")
def team_server(tmp_path_factory):
    with AppServer({"LASTMILE_DATA_DIR": str(tmp_path_factory.mktemp("team-browser")), "LASTMILE_MAIL": "off", "LASTMILE_API_KEY": "browser-setup-key", "LASTMILE_RATE_LIMIT_PER_MIN": "1000"}) as server:
        yield server


def open_page(context, url):
    page = context.new_page()
    page.goto(url + "/teams")
    page.wait_for_load_state("networkidle")
    return page


def test_three_accounts_contribute_confirm_allocate_and_chat(browser, team_server, tmp_path):
    admin_context, owner_context, reporter_context = [browser.new_context(viewport={"width": 1440, "height": 1000}) for _ in range(3)]
    admin = open_page(admin_context, team_server.url)
    errors = []
    admin.on("pageerror", lambda e: errors.append(str(e)))
    admin.get_by_label("Your name", exact=True).fill("Exercise coordinator")
    admin.get_by_label("Username", exact=True).fill("browseradmin")
    admin.get_by_label("Password", exact=True).fill(PASSWORD)
    admin.get_by_label("Server setup key", exact=True).fill("browser-setup-key")
    admin.get_by_role("button", name="Create workspace", exact=True).click()
    expect(admin.get_by_role("heading", name="Incidents", exact=True)).to_be_visible()
    admin.get_by_role("button", name="+ New incident", exact=True).click()
    modal = admin.get_by_role("dialog")
    modal.get_by_label("Incident name", exact=True).fill("Kotmale shared exercise")
    assert modal.locator("select[name=country] option:disabled").count() == 2
    modal.get_by_role("button", name="Open incident", exact=True).click()
    expect(admin.get_by_role("heading", name="Kotmale shared exercise", exact=True)).to_be_visible()
    expect(admin.locator(".leaflet-overlay-pane canvas")).to_have_count(1)  # status map draws areas on a canvas
    incident_url = admin.url
    def invite_page(context, username, supervisor):
        admin.get_by_role("button", name="Members", exact=True).click()
        dialog = admin.get_by_role("dialog")
        dialog.get_by_label("Organisation", exact=True).select_option("division")
        if not supervisor:
            dialog.get_by_label("Role", exact=True).select_option("relief_officer")
        dialog.get_by_role("button", name="Create invitation", exact=True).click()
        link = dialog.get_by_label("Invitation link", exact=True)
        expect(link).to_be_visible()
        url = link.input_value()
        dialog.get_by_role("button", name="Done", exact=True).click()
        page = context.new_page(); page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url); page.wait_for_load_state("networkidle")
        expect(page.get_by_role("heading", name="Join your organisation", exact=True)).to_be_visible()
        page.get_by_label("Your name", exact=True).fill(username)
        page.get_by_label("Username", exact=True).fill(username)
        page.get_by_label("Password", exact=True).fill(PASSWORD)
        page.get_by_role("button", name="Create my account", exact=True).click()
        expect(page.get_by_role("heading", name="Incidents", exact=True)).to_be_visible()
        page.get_by_role("button", name="Open incident", exact=True).click()
        expect(page.get_by_role("heading", name="Kotmale shared exercise", exact=True)).to_be_visible()
        return page
    owner = invite_page(owner_context, "browserowner", True)
    reporter = invite_page(reporter_context, "browserreporter", False)
    reporter.get_by_role("button", name="Upload a report", exact=True).first.click()
    dialog = reporter.get_by_role("dialog")
    dialog.get_by_label("Report title", exact=True).fill("Centre readiness from division")
    dialog.get_by_label("What was reported?", exact=True).fill(REPORT)
    dialog.get_by_role("button", name="Upload and analyse", exact=True).click()
    expect(dialog).not_to_be_visible()
    admin.bring_to_front()
    admin.get_by_role("tab", name="Reports", exact=False).click()
    expect(admin.get_by_role("heading", name="Centre readiness from division", exact=True)).to_be_visible(timeout=20000)
    admin.get_by_role("button", name="Accept evidence", exact=True).click()
    owner.get_by_role("button", name="Refresh", exact=True).click()
    show(owner, "Resources", exact=False)
    owner.get_by_role("button", name="Confirm resource", exact=True).click()
    dialog = owner.get_by_role("dialog")
    dialog.get_by_label("Resource name", exact=True).fill("Kotmale fictional hall")
    dialog.locator("select[name=source_id]").select_option(index=1)
    dialog.get_by_label("Latitude (optional)", exact=True).fill("7.02")
    dialog.get_by_label("Longitude (optional)", exact=True).fill("80.59")
    for key, value, quote in [("capacity", "100", "Current capacity: 100 people"), ("occupied", "40", "Occupied: 40 people"), ("external_pending", "0", "External pending: 0 people"), ("status", "accepting", "Status: accepting"), ("access", "passable", "Access: passable"), ("water", "working", "Water: working"), ("power", "working", "Power: working")]:
        dialog.locator(f"input[name={key}]").fill(value)
        dialog.locator(f"input[name={key}_quote]").fill(quote)
    dialog.get_by_role("button", name="Confirm facts", exact=True).click()
    expect(dialog).not_to_be_visible()
    expect(owner.get_by_text("60 available in planning balance", exact=False)).to_be_visible()
    admin.get_by_role("button", name="Refresh", exact=True).click()
    show(admin, "Resources", exact=False)
    expect(admin.get_by_role("heading", name="Kotmale fictional hall", exact=True)).to_be_visible()
    # API creates the evidenced request; assignment and acceptance use the actual UI.
    result = reporter.request.post(team_server.url + "/api/teams/incidents/" + incident_url.split("incident=")[1].split("&")[0] + "/commands", data={"version": reporter.request.get(team_server.url + "/api/teams/incidents/" + incident_url.split("incident=")[1].split("&")[0]).json()["version"], "request_id": "browser-report-need", "action": "contribute", "data": {"title": "Accommodation need", "text": "EXERCISE 20 people need accommodation.", "observed_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()}})
    assert result.ok, result.text()
    admin.get_by_role("button", name="Refresh", exact=True).click()
    admin.get_by_role("tab", name="Reports", exact=False).click()
    admin.get_by_role("button", name="Accept evidence", exact=True).click()
    show(admin, "Needs & assignments")
    admin.get_by_role("button", name="Record request", exact=True).click()
    dialog = admin.get_by_role("dialog")
    dialog.get_by_label("What is needed?", exact=True).fill("Accommodate 20 people")
    dialog.get_by_label("Quantity (blank if unknown)", exact=True).fill("20")
    dialog.locator("select[name=source_id]").select_option(label="Accommodation need · Divisional Secretariat")
    dialog.get_by_role("button", name="Record request", exact=True).click()
    expect(dialog).not_to_be_visible()
    admin.get_by_role("button", name="Propose assignment", exact=True).click()
    dialog = admin.get_by_role("dialog")
    dialog.locator("select[name=asset_id]").select_option(index=1)
    dialog.get_by_role("button", name="Create reviewed offer", exact=True).click()
    expect(dialog).not_to_be_visible()
    owner.get_by_role("button", name="Refresh", exact=True).click()
    show(owner, "Needs & assignments")
    owner.get_by_role("button", name="Accept assignment", exact=True).click()
    expect(owner.get_by_text("accepted", exact=True)).to_be_visible()
    admin.get_by_label("Ask or describe the next step", exact=True).fill("What needs attention?")
    admin.get_by_role("button", name="Send message", exact=True).click()
    expand_activity(admin)
    expect(admin.locator(".step.completed").first).to_be_visible(timeout=30000)
    expect(admin.locator(".bubble.assistant")).to_contain_text("Kotmale shared exercise", timeout=30000)
    admin.get_by_role("button", name="Collapse chat", exact=True).click()
    expect(admin.get_by_role("complementary", name="Incident chat", exact=True)).to_have_count(0)
    admin.get_by_role("button", name="Show chat", exact=True).click()
    expect(admin.locator(".bubble.assistant")).to_contain_text("Kotmale shared exercise")
    admin.reload(); admin.wait_for_load_state("networkidle")
    expect(admin.locator(".bubble.assistant")).to_contain_text("Kotmale shared exercise")
    admin.screenshot(path=str(tmp_path / "team-desktop.png"), full_page=True)
    assert not errors
    for context in (admin_context, owner_context, reporter_context): context.close()


def test_mobile_login_map_collapse_and_safe_text(browser, team_server, tmp_path):
    from datetime import datetime, timezone
    with team_server.client() as client:
        if client.get("/api/teams/setup").json()["setup_required"]:
            client.post("/api/teams/bootstrap", json={"username": "browseradmin", "name": "Exercise coordinator", "password": PASSWORD}, headers={"X-Setup-Key": "browser-setup-key"}).raise_for_status()
        client.post("/api/teams/login", json={"username": "browseradmin", "password": PASSWORD}).raise_for_status()
        doc = client.post("/api/teams/incidents", json={"title": "Mobile standalone exercise", "district": "Nuwara Eliya", "participants": ["division"]}).json()
        raw = 'EXERCISE untrusted text <img src=x onerror="window.teamXss=true">'
        client.post(f"/api/teams/incidents/{doc['id']}/commands", json={"version": doc["version"], "request_id": "mobile-safe-source", "action": "contribute", "data": {"title": "Literal source", "text": raw, "observed_at": datetime.now(timezone.utc).isoformat()}}).raise_for_status()
    context = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = open_page(context, team_server.url)
    page.get_by_label("Username", exact=True).fill("browseradmin")
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.get_by_role("article").filter(has=page.get_by_role("heading", name="Mobile standalone exercise", exact=True)).get_by_role("button", name="Open incident", exact=True).click()
    expect(page.get_by_role("heading", name="Mobile standalone exercise", exact=True)).to_be_visible()
    page.get_by_role("button", name="Live data", exact=True).click()
    expect(page.get_by_role("complementary", name="Incident chat", exact=True)).not_to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    expect(page.locator(".leaflet-overlay-pane canvas")).to_have_count(1)
    page.screenshot(path=str(tmp_path / "team-mobile.png"), full_page=True)
    page.get_by_role("tab", name="Reports", exact=False).click()
    page.get_by_text("Read original evidence", exact=True).click()
    expect(page.get_by_text(raw, exact=True)).to_be_visible()
    assert page.evaluate("window.teamXss !== true")
    assert page.locator(".source-text img").count() == 0
    context.close()


def test_first_time_sample_button_opens_a_labelled_blocked_case(browser, team_server):
    with team_server.client() as client:
        if client.get("/api/teams/setup").json()["setup_required"]:
            client.post("/api/teams/bootstrap", json={"username": "browseradmin", "name": "Exercise coordinator", "password": PASSWORD}, headers={"X-Setup-Key": "browser-setup-key"}).raise_for_status()
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    page = open_page(context, team_server.url)
    page.get_by_label("Username", exact=True).fill("browseradmin")
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.get_by_role("button", name="Try sample case", exact=True).click()
    expect(page.get_by_role("heading", name="Kotmale coordination practice", exact=True)).to_be_visible()
    # The blocked shelter says why, in a few words, instead of the long internal reason.
    expect(page.get_by_text("Needs a check", exact=True).first).to_be_visible()
    expect(page.get_by_text("Water unavailable", exact=False).first).to_be_visible()
    page.get_by_role("tab", name="Reports", exact=False).click()
    expect(page.get_by_role("heading", name="Centre readiness", exact=True)).to_be_visible()
    page.get_by_role("article").filter(has=page.get_by_role("heading", name="Centre readiness", exact=True)).get_by_text("Read original evidence", exact=True).click()
    expect(page.get_by_text("EXERCISE FIXTURE — not an agency submission.", exact=False)).to_be_visible()
    context.close()
