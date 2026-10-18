"""Keyboard, focus, recovery, progressive forms and narrow-screen behaviour."""
import re
import uuid

import pytest
from e2e_server import AppServer
from browser_session import browser
from test_team_demo_browser import enter

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect


@pytest.fixture(scope="module")
def ux_server(tmp_path_factory):
    with AppServer({"LASTMILE_TEAM_DEMO": "true", "LASTMILE_DATA_DIR": str(tmp_path_factory.mktemp("accessible-team")), "LASTMILE_MAIL": "off", "LASTMILE_RATE_LIMIT_PER_MIN": "1000"}) as server:
        yield server


def sample(browser, server, viewport=None):
    context = browser.new_context(viewport=viewport or {"width": 1440, "height": 1000}, reduced_motion="reduce")
    page = context.new_page()
    enter(page, server.url + "/teams", "dmc", "duty_officer")
    expect(page.get_by_role("heading", name="Kotmale coordination practice", level=1, exact=True)).to_be_visible()
    return context, page


def test_keyboard_tabs_skip_link_search_and_dialog_focus(browser, ux_server):
    context, page = sample(browser, ux_server)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    tabs = page.get_by_role("tablist", name="Incident data sections")
    expect(tabs).to_be_visible()
    # Prebuilt tabs are light-DOM children slotted into the shadow tablist.
    expect(page.locator('.incident-tabs > wa-tab[tabindex="0"]')).to_have_count(1)
    overview = page.get_by_role("tab", name="Overview", exact=True)
    overview.focus(); page.keyboard.press("ArrowRight")
    area_map = page.get_by_role("tab", name="Map & status", exact=True)
    requests = page.get_by_role("tab", name=re.compile(r"^Requests"))
    reports = page.get_by_role("tab", name="Reports", exact=False)
    expect(area_map).to_be_focused()
    page.keyboard.press("ArrowRight")
    expect(requests).to_be_focused()
    expect(overview).to_have_attribute("aria-selected", "true")
    page.keyboard.press("End")
    expect(page.get_by_role("tab", name=re.compile(r"^Public requests"))).to_be_focused()
    page.keyboard.press("ArrowLeft")
    expect(reports).to_be_focused()
    page.keyboard.press("Enter")
    expect(reports).to_have_attribute("aria-selected", "true")
    expect(reports).to_be_focused()
    expect(page.locator("#panel-sources")).to_have_attribute("aria-labelledby", "tab-sources")
    page.keyboard.press("Home")
    expect(overview).to_be_focused()
    search = page.get_by_label("Search reports", exact=True)
    search.fill("Fictional centre readiness")
    expect(page.locator("#panel-sources article:visible")).to_have_count(1)
    expect(search).to_be_focused()
    page.get_by_label("Review state", exact=True).select_option("rejected")
    expect(page.get_by_text("No matching reports.", exact=False)).to_be_visible()
    page.get_by_role("button", name="Clear filters", exact=True).click()
    expect(search).to_be_focused()
    expect(page.locator("#panel-sources article:visible")).to_have_count(3)  # the two sample reports plus the fictional bulletin
    page.get_by_role("button", name="Upload a report", exact=True).click()
    modal = page.get_by_role("dialog")
    expect(modal.locator("#modal-title")).to_be_focused()
    expect(modal.get_by_label("Observation time", exact=True)).not_to_be_visible()
    modal.get_by_label("Report title", exact=True).fill("Missing original exercise")
    modal.get_by_role("button", name="Upload and analyse", exact=True).click()
    expect(modal.get_by_role("alert")).to_contain_text("at least 10 characters")
    expect(modal.get_by_role("alert")).to_be_focused()
    modal.locator('input[name=file]').set_input_files({"name": "exercise-water.txt", "mimeType": "text/plain", "buffer": b"EXERCISE water update for keyboard review only"})
    expect(modal.get_by_label("What was reported?", exact=True)).to_be_disabled()
    expect(modal.locator("#attachment-status")).to_contain_text("exercise-water.txt selected")
    page.keyboard.press("Escape")
    expect(modal).not_to_be_visible()
    expect(page.get_by_role("button", name="Upload a report", exact=True)).to_be_focused()
    incident = page.url.split("incident=")[1].split("&")[0]
    page.get_by_role("link", name="Skip to workspace", exact=True).focus()
    page.keyboard.press("Enter")
    expect(page.locator("#main")).to_be_focused()
    assert "incident=" + incident in page.url
    page.reload(); page.wait_for_load_state("networkidle")
    expect(page.get_by_role("tab", name="Reports", exact=False)).to_have_attribute("aria-selected", "true")
    assert not errors
    context.close()


def test_mobile_panes_reflow_and_reduced_motion(browser, ux_server, tmp_path):
    context, page = sample(browser, ux_server, {"width": 390, "height": 844})
    expect(page.get_by_role("complementary", name="Incident chat", exact=True)).to_be_visible()
    expect(page.get_by_label("Incident data", exact=True)).not_to_be_visible()
    page.screenshot(path=str(tmp_path / "ux-mobile-chat.png"), full_page=True)
    page.get_by_role("button", name="Live data", exact=True).click()
    expect(page.get_by_label("Incident data", exact=True)).to_be_visible()
    expect(page.get_by_role("complementary", name="Incident chat", exact=True)).not_to_be_visible()
    expect(page.get_by_role("heading", name="Needs you", exact=True)).to_be_visible()
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
    page.screenshot(path=str(tmp_path / "ux-mobile-data.png"), full_page=True)
    page.set_viewport_size({"width": 320, "height": 700})
    assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
    page.get_by_role("button", name="Chat", exact=True).click()
    page.get_by_label("Ask or describe the next step", exact=True).fill("Unsaved operator draft")
    page.get_by_role("button", name="Live data", exact=True).click()
    page.get_by_role("button", name="Chat", exact=True).click()
    expect(page.get_by_label("Ask or describe the next step", exact=True)).to_have_value("Unsaved operator draft")
    assert page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches")
    page.get_by_label("Ask or describe the next step", exact=True).fill("")
    context.close()


def test_background_updates_preserve_chat_focus_and_errors_recover(browser, ux_server, tmp_path):
    context, page = sample(browser, ux_server)
    chat = page.get_by_label("Ask or describe the next step", exact=True)
    chat.fill("Keep this draft while another body contributes")
    chat.evaluate("el => el.setSelectionRange(5, 9)")
    token = page.evaluate('sessionStorage.getItem("lastmile-demo-session")')
    incident = page.url.split("incident=")[1].split("&")[0]
    base = "/api/teams/incidents/" + incident
    with ux_server.client() as client:
        client.headers["x-demo-session"] = token
        version = client.get(base).json()["version"]
        client.post(base + "/commands", json={"version": version, "request_id": str(uuid.uuid4()), "action": "contribute", "data": {"title": "Incoming focus test", "text": "EXERCISE incoming report while an operator has unsent text", "observed_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()}}).raise_for_status()
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(page.get_by_role("tab", name="Reports (4)", exact=True)).to_be_visible(timeout=10000)
    expect(chat).to_be_focused()
    expect(chat).to_have_value("Keep this draft while another body contributes")
    assert chat.evaluate("el => [el.selectionStart, el.selectionEnd]") == [5, 9]
    chat.fill("")
    pattern = "**" + base
    page.route(pattern, lambda route: route.fulfill(status=503, content_type="application/json", body='{"detail":"Connection interrupted. Retry refresh."}'))
    page.get_by_role("button", name="Refresh", exact=True).click()
    expect(page.locator("#sync-status")).to_contain_text("Updates paused")
    expect(page.locator("#toast")).to_contain_text("Retry refresh")
    page.unroute(pattern)
    page.get_by_role("button", name="Refresh", exact=True).click()
    expect(page.locator("#sync-status")).not_to_have_class("sync-status offline")
    expect(page.locator("#sync-status")).to_contain_text("Updated")
    chat.fill("Draft scoped to this incident")
    page.get_by_role("link", name="Back to Dashboard", exact=True).click()
    expect(page.get_by_role("heading", name="Incidents", exact=True)).to_be_visible()
    page.evaluate("incident => location.hash = 'incident=' + incident", incident)
    expect(page.get_by_role("heading", name="Kotmale coordination practice", level=1, exact=True)).to_be_visible()
    expect(chat).to_have_value("Draft scoped to this incident")
    chat.fill("")
    page.screenshot(path=str(tmp_path / "ux-desktop.png"), full_page=True)
    context.close()
