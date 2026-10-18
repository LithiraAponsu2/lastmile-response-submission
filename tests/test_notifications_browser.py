"""The bell in a real browser: a body sees what others did that concerns it, opens or reads each item, or reads them all."""
import re

import pytest
from e2e_server import AppServer
from browser_session import browser  # noqa: F401  (fixture)
from test_requests_browser import switch
from test_team_demo_browser import enter

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect


def unread(bell):
    label = bell.get_attribute("aria-label")
    return int(re.search(r"(\d+) unread", label).group(1)) if "unread" in label else 0


def test_the_bell_lists_items_reads_each_or_all_and_read_items_leave(browser, tmp_path):
    with AppServer({"LASTMILE_TEAM_DEMO": "true", "LASTMILE_DATA_DIR": str(tmp_path / "bell"), "LASTMILE_MAIL": "off", "LASTMILE_RATE_LIMIT_PER_MIN": "1000"}) as server:
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        enter(page, server.url + "/", "dmc", "duty_officer")
        expect(page.get_by_role("heading", name="Kotmale coordination practice", level=1, exact=True)).to_be_visible()
        switch(page, "water", "service_supervisor")
        bell, role = page.locator("#notes-button"), page.locator("#switch-role")
        # One icon, no label text; who you act as is one control in the top-right corner, and language lives in settings.
        expect(bell).not_to_contain_text("Notifications")
        expect(role).to_contain_text("Service Supervisor")
        assert role.bounding_box()["x"] > bell.bounding_box()["x"]
        expect(page.get_by_role("banner").locator("select")).to_have_count(0)
        # DMC opened the sample case with NWSDB in it, and the Divisional Secretariat asked NWSDB for bowsers.
        expect(bell).to_have_attribute("aria-label", re.compile(r"^Notifications, \d+ unread$"), timeout=15000)
        total = unread(bell)
        bell.click()
        pane = page.get_by_role("region", name="Notifications", exact=True)
        expect(pane).to_be_visible()
        expect(page.locator("#notes-title")).to_be_focused()
        expect(pane.get_by_role("button", name=re.compile("^New request for your organisation.*arrived"))).to_be_visible()
        asked = pane.get_by_role("button", name=re.compile("^Divisional Secretariat asked your organisation for help"))
        expect(asked).to_contain_text("Water bowsers for Kotmale Practice Hall · 2 bowsers")
        expect(asked).to_contain_text(re.compile(r"Kotmale coordination practice · (just now|\d+ min ago)"))
        # Opening the pane reads nothing by itself.
        assert unread(bell) == total
        # Read one: it leaves the list and the count drops.
        opened = "Mark as read: DMC opened an incident and added your organisation"
        pane.get_by_role("button", name=opened, exact=True).click()
        expect(pane.get_by_role("button", name=opened, exact=True)).to_have_count(0)
        expect(bell).to_have_attribute("aria-label", f"Notifications, {total - 1} unread")
        # Opening an item reads it too.
        asked.click()
        expect(pane).to_be_hidden()
        expect(page.get_by_role("article", name="Water bowsers for Kotmale Practice Hall", exact=True)).to_be_focused()
        expect(bell).to_have_attribute("aria-label", f"Notifications, {total - 2} unread")
        bell.click()
        expect(pane.get_by_role("button", name=re.compile("^Divisional Secretariat asked your organisation"))).to_have_count(0)
        # Read all: nothing is left and the count goes.
        pane.get_by_role("button", name="Mark all read", exact=True).click()
        expect(pane).to_contain_text("You’re all caught up")
        expect(bell).to_have_attribute("aria-label", "Notifications")
        expect(bell.locator(".badge")).to_have_count(0)
        page.keyboard.press("Escape")
        expect(pane).to_be_hidden()
        expect(bell).to_be_focused()
        # What was read stays read after a reload.
        page.reload()
        expect(page.get_by_role("heading", name="Kotmale coordination practice", level=1, exact=True)).to_be_visible()
        page.wait_for_timeout(1500)
        expect(page.locator("#notes-button")).to_have_attribute("aria-label", "Notifications")

        # On a phone the header and the pane fit the screen.
        phone = context.new_page()
        phone.set_viewport_size({"width": 320, "height": 700})
        phone.goto(page.url)
        phone.locator("#notes-button").click()
        expect(phone.get_by_role("region", name="Notifications", exact=True)).to_be_visible()
        assert phone.evaluate("document.documentElement.scrollWidth") <= 320
        box = phone.locator("#notes-pane").bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= 320
        assert errors == []
        context.close()
