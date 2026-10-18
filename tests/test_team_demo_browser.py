"""One browser, three tabs: role selection, attachment, notification, assignment."""
import pytest
from e2e_server import AppServer
from browser_session import browser
from ui_views import show

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect


def enter(page, url, organisation, role, entry_action="Try sample case"):
    """Open the app with this organisation and role. The role is chosen once, in the top bar."""
    page.goto(url)
    page.wait_for_load_state("networkidle")
    banner = page.get_by_role("banner")
    modal = page.get_by_role("dialog")
    remembered = banner.locator("#switch-role")
    if remembered.count():
        # Another tab of this browser already chose a role; switch this tab's own.
        remembered.click()
        modal.locator(f"input[name=organisation][value={organisation}]").check()
        modal.locator(f"input[name=role][value={role}]").check()
        modal.get_by_role("button", name="Switch role", exact=True).click()
        expect(modal).not_to_be_visible()
        if "incident=" not in page.url:
            page.get_by_role("button", name=entry_action, exact=True).click()
        return
    if "incident=" not in page.url:
        show = page.get_by_role("button", name="Show navigation", exact=True)
        if show.is_visible():
            show.click()
        page.get_by_role("navigation", name="Main navigation", exact=True).get_by_role("button", name="Dashboard", exact=True).click()
        expect(page.get_by_role("heading", name="Incidents", exact=True)).to_be_visible()
        expect(modal).not_to_be_visible()
        page.get_by_role("button", name=entry_action, exact=True).click()
    expect(modal.get_by_role("heading", name="Choose organisation & role", exact=True)).to_be_visible()
    modal.locator(f"input[name=organisation][value={organisation}]").check()
    modal.locator(f"input[name=role][value={role}]").check()
    modal.get_by_role("button", name="Enter workspace", exact=True).click()


def expand_activity(page):
    card = page.locator("lm-work-activity").last
    card.get_by_text("Assistant finished", exact=True).wait_for()
    if not card.locator("wa-details").evaluate("el => el.open"):
        card.get_by_text("Assistant finished", exact=True).click()



def test_same_browser_tabs_report_communication_and_acceptance(browser, tmp_path):
    with AppServer({"LASTMILE_TEAM_DEMO": "true", "LASTMILE_DATA_DIR": str(tmp_path / "demo"), "LASTMILE_MAIL": "off", "LASTMILE_RATE_LIMIT_PER_MIN": "1000"}) as server:
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        central, division, water = [context.new_page() for _ in range(3)]
        errors = []
        for page in (central, division, water):
            page.on("pageerror", lambda e: errors.append(str(e)))
        enter(central, server.url + "/teams", "dmc", "duty_officer", "+ New incident")
        modal = central.get_by_role("dialog")
        modal.get_by_label("Incident name", exact=True).fill("Kotmale multi-tab water exercise")
        modal.locator("input[name=participants][value=water]").check()  # landslide suggestions leave NWSDB out
        modal.get_by_role("button", name="Open incident", exact=True).click()
        expect(central.get_by_role("heading", name="Kotmale multi-tab water exercise", exact=True)).to_be_visible()
        incident_url = central.url
        enter(division, incident_url, "division", "relief_officer")
        enter(water, incident_url, "water", "service_supervisor")
        expect(water.get_by_role("heading", name="Kotmale multi-tab water exercise", exact=True)).to_be_visible()
        assert central.evaluate('sessionStorage.getItem("lastmile-demo-session")') != water.evaluate('sessionStorage.getItem("lastmile-demo-session")')
        division.bring_to_front()
        division.get_by_role("button", name="Upload a report", exact=True).click()
        modal = division.get_by_role("dialog")
        modal.get_by_label("Report title", exact=True).fill("Kotmale water inspection needed")
        modal.locator('input[name=file]').set_input_files({"name": "water-report.txt", "mimeType": "text/plain", "buffer": b"EXERCISE Kotmale water unavailable. Need 1 inspection by water team."})
        modal.locator('input[name=recipients][value=water]').check()
        modal.get_by_role("button", name="Upload and analyse", exact=True).click()
        expect(modal).not_to_be_visible()
        expand_activity(division)
        expect(division.locator(".step.completed").first).to_be_visible(timeout=30000)
        expect(division.locator(".bubble.assistant")).to_contain_text("Kotmale water unavailable", timeout=30000)
        central.bring_to_front()
        central.get_by_role("tab", name="Reports", exact=False).click()
        expect(central.get_by_role("heading", name="Kotmale water inspection needed", exact=True)).to_be_visible(timeout=20000)
        notification_button = central.get_by_label("Active organisation and role").get_by_role("button", name="Shared with you", exact=False)
        assert notification_button.evaluate("el => getComputedStyle(el).color !== getComputedStyle(el).backgroundColor")
        central.get_by_role("button", name="Accept evidence", exact=True).click()
        division.bring_to_front()
        division.get_by_role("button", name="Refresh", exact=True).click()
        show(division, "Needs & assignments")
        division.get_by_role("button", name="Record request", exact=True).click()
        modal = division.get_by_role("dialog")
        modal.get_by_label("What is needed?", exact=True).fill("Inspect Kotmale water")
        modal.get_by_label("Request kind", exact=True).select_option("water")
        modal.get_by_label("Quantity (blank if unknown)", exact=True).fill("1")
        modal.get_by_label("Unit", exact=True).fill("inspection")
        modal.locator('select[name=source_id]').select_option(index=1)
        modal.get_by_role("button", name="Record request", exact=True).click()
        expect(modal).not_to_be_visible()
        central.bring_to_front()
        show(central, "Needs & assignments")
        expect(central.get_by_role("button", name="Propose assignment", exact=True)).to_be_visible(timeout=20000)
        central.get_by_role("button", name="Propose assignment", exact=True).click()
        modal = central.get_by_role("dialog")
        modal.locator('select[name=recipient]').select_option("water")
        modal.get_by_role("button", name="Create reviewed offer", exact=True).click()
        expect(modal).not_to_be_visible()
        water.bring_to_front()
        show(water, "Shared with you", exact=False)
        expect(water.get_by_text("New shared report: please acknowledge", exact=True)).to_be_visible(timeout=20000)
        water.get_by_role("button", name="Open shared report", exact=True).click()
        expect(water.get_by_role("heading", name="Kotmale water inspection needed", exact=True)).to_be_visible()
        with water.expect_download() as info:
            water.get_by_role("button", name="Download original: water-report.txt", exact=True).click()
        assert info.value.suggested_filename == "water-report.txt"
        report = water.get_by_role("article", name="Kotmale water inspection needed", exact=True)
        report.get_by_role("button", name="Acknowledge", exact=True).click()
        expect(report.locator(".ack-chip.done")).to_contain_text("NWSDB")
        report.get_by_role("button", name="Ask to acknowledge", exact=True).click()
        modal = water.get_by_role("dialog")
        modal.locator('input[name=recipients][value=division]').check()
        modal.get_by_label("Message", exact=True).fill("Water team reviewing; please keep this location available.")
        modal.get_by_role("button", name="Ask to acknowledge", exact=True).click()
        expect(modal).not_to_be_visible()
        show(water, "Needs & assignments")
        expect(water.get_by_role("button", name="Accept assignment", exact=True)).to_be_visible(timeout=20000)
        water.get_by_role("button", name="Accept assignment", exact=True).click()
        expect(water.get_by_text("accepted", exact=True)).to_be_visible()
        division.bring_to_front()
        show(division, "Shared with you", exact=False)
        expect(division.get_by_text("Water team reviewing; please keep this location available.", exact=True)).to_be_visible(timeout=20000)
        central.bring_to_front()
        central.get_by_label("Ask or describe the next step", exact=True).fill("What needs attention?")
        central.get_by_role("button", name="Send message", exact=True).click()
        expect(central.locator(".bubble.assistant")).to_contain_text("Kotmale multi-tab water exercise", timeout=30000)
        central.locator("#switch-role").click()
        modal = central.get_by_role("dialog")
        modal.locator("input[name=role][value=observer]").check()
        modal.get_by_role("button", name="Switch role", exact=True).click()
        expect(modal).not_to_be_visible()
        expect(central.get_by_role("button", name="Upload a report", exact=True)).to_be_disabled()
        expect(central.get_by_role("button", name="Manage lifecycle", exact=True)).to_have_count(0)
        expect(water.get_by_label("Active organisation and role")).to_contain_text("Water Service Supervisor")
        central.get_by_role("button", name="Access history", exact=True).click()
        expect(modal.get_by_role("heading", name="Workspace access history", exact=True)).to_be_visible()
        expect(modal).to_contain_text("Observer")
        modal.get_by_role("button", name="Close", exact=True).click()
        central.locator("#switch-role").click()
        modal.locator("input[name=role][value=duty_officer]").check()
        modal.get_by_role("button", name="Switch role", exact=True).click()
        expect(modal).not_to_be_visible()
        expect(central.locator(".bubble.assistant")).to_contain_text("Kotmale multi-tab water exercise")
        central.get_by_role("button", name="Collapse chat", exact=True).click()
        expect(central.get_by_role("complementary", name="Incident chat", exact=True)).to_have_count(0)
        central.screenshot(path=str(tmp_path / "demo-desktop.png"), full_page=True)
        central.set_viewport_size({"width": 390, "height": 844})
        central.get_by_role("button", name="Chat", exact=True).click()
        central.screenshot(path=str(tmp_path / "demo-mobile.png"), full_page=True)
        assert not central.evaluate("document.documentElement.scrollWidth > innerWidth")
        assert not errors
        context.close()
