"""The core flow in a real browser: one body asks another, the lead accepts, staff deliver, the asker confirms."""
import re

import pytest
from e2e_server import AppServer
from browser_session import browser
from test_team_demo_browser import enter

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect


def switch(page, organisation, role):
    page.locator("#switch-role").click()
    modal = page.get_by_role("dialog")
    modal.locator(f"input[name=organisation][value={organisation}]").check()
    modal.locator(f"input[name=role][value={role}]").check()
    modal.get_by_role("button", name="Switch role", exact=True).click()
    expect(modal).not_to_be_visible()


def test_request_between_bodies_end_to_end(browser, tmp_path):
    with AppServer({"LASTMILE_TEAM_DEMO": "true", "LASTMILE_DATA_DIR": str(tmp_path / "asks"), "LASTMILE_MAIL": "off", "LASTMILE_RATE_LIMIT_PER_MIN": "1000"}) as server:
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        enter(page, server.url + "/", "dmc", "duty_officer")
        expect(page.get_by_role("heading", name="Kotmale coordination practice", level=1, exact=True)).to_be_visible()
        switch(page, "water", "service_supervisor")
        # Three everyday tabs; the sample request is waiting for the NWSDB lead.
        expect(page.get_by_role("tab")).to_have_count(5)
        expect(page.get_by_role("tab", name="Requests (1)", exact=True)).to_be_visible()
        page.get_by_role("button", name="New request from DS", exact=False).click()
        card = page.get_by_role("article", name="Water bowsers for Kotmale Practice Hall", exact=True)
        expect(card).to_be_focused()
        expect(card.locator(".ask-route")).to_have_text(re.compile(r"^DS\W+to\W+NWSDB$"))
        expect(card).to_contain_text("2 bowsers")
        card.get_by_role("button", name="Accept", exact=True).click()
        accepted = page.get_by_role("region", name="Accepted", exact=False)
        expect(accepted.get_by_role("article")).to_have_count(1)
        # A field engineer of the receiving body delivers; they cannot accept or confirm.
        switch(page, "water", "field_engineer")
        card.get_by_role("button", name="Start work", exact=True).click()
        expect(page.get_by_role("region", name="In progress", exact=False).get_by_role("article")).to_have_count(1)
        card.get_by_role("button", name="Mark done", exact=True).click()
        expect(card.get_by_role("button", name="Confirm delivered", exact=True)).to_have_count(0)
        # The asking body's lead confirms, or sends it back with a reason.
        switch(page, "division", "relief_officer")
        expect(card.get_by_role("button", name="Confirm delivered", exact=True)).to_have_count(0)
        switch(page, "division", "divisional_secretary")
        page.get_by_role("tab", name="Overview", exact=True).click()
        page.get_by_role("button", name="Confirm a delivered request", exact=False).click()
        card.get_by_role("button", name="Not done yet", exact=True).click()
        modal = page.get_by_role("dialog")
        modal.get_by_label("Reason", exact=True).fill("Only one bowser has arrived")
        modal.get_by_role("button", name="Not done yet", exact=True).click()
        expect(modal).not_to_be_visible()
        expect(card).to_contain_text("Only one bowser has arrived")
        switch(page, "water", "field_engineer")
        card.get_by_role("button", name="Mark done", exact=True).click()
        switch(page, "division", "divisional_secretary")
        card.get_by_role("button", name="Confirm delivered", exact=True).click()
        expect(card).to_contain_text("Confirmed")
        card.locator("details.ask-history > summary").click()
        expect(card.locator(".ask-history li")).to_have_count(7)
        # A new request from the composer: the kind suggests the usual body, funding goes to NDRSC.
        page.get_by_role("button", name="Request", exact=True).click()
        modal = page.get_by_role("dialog")
        modal.get_by_label("What kind of help?", exact=True).select_option("road")
        expect(modal.get_by_label("To", exact=True)).to_have_value("rda")
        modal.get_by_label("What kind of help?", exact=True).select_option("funding")
        expect(modal.get_by_label("To", exact=True)).not_to_be_visible()
        expect(modal.get_by_label("Amount (LKR)", exact=True)).to_be_visible()
        modal.get_by_label("What kind of help?", exact=True).select_option("road")
        modal.get_by_label("What exactly is needed?", exact=True).fill("Clear the landslide on the hall access road")
        modal.get_by_role("button", name="Send request", exact=True).click()
        expect(modal).not_to_be_visible()
        new = page.get_by_role("article", name="Clear the landslide on the hall access road", exact=True)
        expect(new.locator(".ask-route")).to_have_text(re.compile(r"^DS\W+to\W+RDA$"))
        expect(new).to_contain_text("Asked")
        page.get_by_role("group", name="Show requests", exact=True).get_by_role("button", name="To us", exact=True).click()
        expect(new).to_have_count(0)
        page.screenshot(path=str(tmp_path / "requests-board.png"), full_page=True)
        # Observers read everything and act on nothing.
        switch(page, "rda", "observer")
        page.get_by_role("group", name="Show requests", exact=True).get_by_role("button", name="All", exact=True).click()
        expect(new.get_by_role("button")).to_have_count(0)
        expect(page.get_by_role("button", name="New request", exact=True)).to_have_count(0)
        expect(page.get_by_role("button", name="Request", exact=True)).to_be_disabled()
        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_role("button", name="Live data", exact=True).click()
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
        page.screenshot(path=str(tmp_path / "requests-mobile.png"), full_page=True)
        assert not errors, errors
        context.close()


def test_requests_page_groups_by_incident_and_highlights_what_we_supply(browser, tmp_path):
    with AppServer({"LASTMILE_TEAM_DEMO": "true", "LASTMILE_DATA_DIR": str(tmp_path / "requests-page"), "LASTMILE_MAIL": "off", "LASTMILE_RATE_LIMIT_PER_MIN": "1000"}) as server:
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        enter(page, server.url + "/", "dmc", "duty_officer")
        expect(page.get_by_role("heading", name="Kotmale coordination practice", level=1, exact=True)).to_be_visible()
        nav = page.get_by_role("navigation", name="Main navigation", exact=True)
        # DMC neither asked nor supplies the sample request: it shows under All open, not highlighted.
        nav.get_by_role("button", name="Requests", exact=True).click()
        expect(page.get_by_role("heading", name="Requests", level=1, exact=True)).to_be_visible()
        views = page.get_by_role("group", name="Show requests", exact=True)
        expect(views.get_by_role("button", name="All open (1)", exact=True)).to_have_attribute("aria-pressed", "true")
        expect(page.locator(".request-row.needs-you")).to_have_count(0)
        # NWSDB supplies it: highlighted under Needs you, grouped by its incident.
        switch(page, "water", "service_supervisor")
        expect(views.get_by_role("button", name="Needs you (1)", exact=True)).to_have_attribute("aria-pressed", "true")
        group = page.get_by_role("region", name="Kotmale coordination practice", exact=True)
        row = group.locator(".request-row.needs-you")
        expect(row).to_contain_text("Water bowsers for Kotmale Practice Hall")
        expect(row).to_contain_text("Your organisation supplies this")
        expect(row).to_contain_text("Your step: Accept or Decline")
        expect(row.locator(".ask-route")).to_have_text(re.compile(r"^DS\W+to\W+NWSDB$"))
        views.get_by_role("button", name="From us (0)", exact=True).click()
        expect(page.get_by_text("No requests match.", exact=False)).to_be_visible()
        views.get_by_role("button", name="To us (1)", exact=True).click()
        page.screenshot(path=str(tmp_path / "requests-page.png"), full_page=True)
        # Selecting it opens the incident's Requests tab on that request.
        group.locator(".request-row").click()
        expect(page.get_by_role("tab", name=re.compile(r"^Requests"))).to_have_attribute("aria-selected", "true")
        expect(page.get_by_role("article", name="Water bowsers for Kotmale Practice Hall", exact=True)).to_be_focused()
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(server.url + "/#view=requests")
        expect(page.locator(".request-row")).to_have_count(1)
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
        assert not errors, errors
        context.close()
