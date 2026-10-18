"""Complete saved case through browser handover, guarded close and reopen."""
import pytest
from e2e_server import AppServer
from browser_session import browser
from lastmile.exercise_case import FullExercise
from ui_views import manage, show

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect


def login(page, url, username, password):
    page.goto(url + "/teams")
    page.wait_for_load_state("networkidle")
    page.get_by_label("Username", exact=True).fill(username)
    page.get_by_label("Password", exact=True).fill(password)
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.get_by_role("button", name="Open incident", exact=True).click()
    expect(page.get_by_role("heading", name="Kotmale full response exercise", exact=True)).to_be_visible()


def test_complete_case_browser_lifecycle_handover_and_mobile(browser, tmp_path):
    with AppServer({"LASTMILE_DATA_DIR": str(tmp_path / "server"), "LASTMILE_API_KEY": "full-browser-key", "LASTMILE_MAIL": "off", "LASTMILE_RATE_LIMIT_PER_MIN": "1000"}) as server:
        runner = FullExercise(server.client, "full-browser-key")
        result = runner.run()
        assert result["final_status"] == "closed"
        admin_ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
        owner_ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
        page, owner = admin_ctx.new_page(), owner_ctx.new_page()
        errors = []
        for p in (page, owner): p.on("pageerror", lambda e: errors.append(str(e)))
        login(page, server.url, "case-central", runner.password)
        expect(page.get_by_role("button", name="Upload a report", exact=True)).to_be_disabled()
        for tab, action in [("Needs & assignments", "Record request"), ("Funding records", "Request funding"), ("Documents", "Prepare document")]:
            show(page, tab)
            expect(page.get_by_role("button", name=action, exact=True)).to_have_count(0)
        expect(page.get_by_role("button", name="Download document", exact=True).first).to_be_visible()
        expect(page.get_by_role("button", name="New request", exact=True)).to_have_count(0)
        manage(page).get_by_role("button", name="Reopen incident", exact=True).click()
        dialog = page.get_by_role("dialog")
        dialog.get_by_label("Reason / receiving note", exact=True).fill("Exercise audit requires a fresh handover cycle")
        dialog.get_by_role("button", name="Reopen with reason", exact=True).click()
        expect(dialog).not_to_be_visible()
        expect(page.get_by_role("button", name="Upload a report", exact=True)).to_be_enabled()
        manage(page).get_by_role("button", name="Manage lifecycle", exact=True).click()
        dialog.get_by_label("Phase", exact=True).select_option("recovery")
        dialog.get_by_label("Reason / receiving note", exact=True).fill("Audit review complete; return to recovery")
        dialog.get_by_role("button", name="Apply lifecycle change", exact=True).click()
        expect(dialog).not_to_be_visible()
        manage(page).get_by_role("button", name="Manage lifecycle", exact=True).click()
        dialog.get_by_label("Action", exact=True).select_option("close")
        dialog.get_by_label("Reason / receiving note", exact=True).fill("Verify stale handover cannot close this incident")
        dialog.get_by_role("button", name="Apply lifecycle change", exact=True).click()
        expect(dialog.get_by_role("alert")).to_contain_text("current approved and received handover")
        dialog.get_by_role("button", name="Cancel", exact=True).click()
        show(page, "Documents")
        page.get_by_role("button", name="Prepare document", exact=True).click()
        dialog.get_by_label("Document type", exact=True).select_option("handover")
        dialog.get_by_label("Audience / receiving team", exact=True).fill("Receiving exercise supervisor")
        dialog.get_by_role("button", name="Generate snapshot", exact=True).click()
        expect(dialog).not_to_be_visible()
        page.get_by_role("button", name="Approve exact snapshot", exact=True).click()
        login(owner, server.url, "shelter", runner.password)
        show(owner, "Documents")
        owner.get_by_role("button", name="Accept handover", exact=True).click()
        expect(owner.get_by_role("button", name="Accept handover", exact=True)).to_have_count(0)
        page.bring_to_front()
        page.get_by_role("button", name="Refresh", exact=True).click()
        manage(page).get_by_role("button", name="Manage lifecycle", exact=True).click()
        dialog.get_by_label("Action", exact=True).select_option("close")
        dialog.get_by_label("Reason / receiving note", exact=True).fill("Received current handover; close the exercise")
        dialog.get_by_role("button", name="Apply lifecycle change", exact=True).click()
        expect(dialog).not_to_be_visible()
        expect(manage(page).get_by_role("button", name="Reopen incident", exact=True)).to_be_visible()
        show(page, "Needs & assignments")
        expect(page.get_by_text("Accommodate 20 people: fulfilled", exact=False)).to_be_visible()
        show(page, "Funding records")
        expect(page.get_by_text("Fictional relief support: reconciled", exact=False)).to_be_visible()
        show(page, "Overview")
        page.screenshot(path=str(tmp_path / "full-case-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_role("button", name="Live data", exact=True).click()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(tmp_path / "full-case-mobile.png"), full_page=True)
        assert not errors
        admin_ctx.close(); owner_ctx.close(); runner.close_clients()
