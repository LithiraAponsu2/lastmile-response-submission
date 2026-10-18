"""Incidents board: sidebar entry, card content per role, sorting/filtering and opening an incident."""
import pytest
from e2e_server import AppServer
from browser_session import browser

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect


def test_incident_board_cards_and_navigation(browser, tmp_path):
    with AppServer({"LASTMILE_TEAM_DEMO": "true", "LASTMILE_DATA_DIR": str(tmp_path / "board"), "LASTMILE_MAIL": "off", "LASTMILE_RATE_LIMIT_PER_MIN": "1000"}) as server:
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(server.url + "/")
        nav = page.get_by_role("navigation", name="Main navigation", exact=True)
        expect(nav.get_by_role("button", name="Dashboard", exact=True)).to_have_attribute("aria-current", "page")
        nav.get_by_role("button", name="Incidents", exact=True).click()
        expect(page).to_have_url(server.url + "/#view=incidents")
        expect(page.get_by_role("heading", name="Incidents", level=1, exact=True)).to_be_visible()
        expect(nav.get_by_role("button", name="Incidents", exact=True)).to_have_attribute("aria-current", "page")
        expect(page.get_by_text("No incidents yet.", exact=False)).to_be_visible()
        # Create the sample case, then come back to the board through the sidebar.
        page.get_by_role("button", name="Try sample case", exact=True).click()
        modal = page.get_by_role("dialog")
        modal.locator("input[name=organisation][value=dmc]").check()
        modal.locator("input[name=role][value=duty_officer]").check()
        modal.get_by_role("button", name="Enter workspace", exact=True).click()
        expect(page.get_by_role("heading", name="Kotmale coordination practice", level=1, exact=True)).to_be_visible()
        expect(nav.get_by_role("button", name="Incidents", exact=True)).to_have_attribute("aria-current", "page")
        nav.get_by_role("button", name="Incidents", exact=True).click()
        card = page.get_by_role("article", name="Kotmale coordination practice", exact=True)
        expect(card).to_be_visible()
        expect(card).to_contain_text("Nuwara Eliya")
        expect(card).to_contain_text("Active")
        expect(card).to_contain_text("Opened ")
        for label in ["Reports", "Open requests", "Shelter places", "Assignments"]:
            expect(card.locator(".card-stat", has_text=label)).to_have_count(1)
        # The practice shelter's water is down, so nothing is confirmed: the card shows the owner's last report, labelled as such.
        expect(card.locator(".card-stat", has_text="Shelter places").locator("strong")).to_have_text("60")
        expect(card.locator(".card-stat", has_text="Shelter places")).to_contain_text("free at last report · 1 shelter to check")
        expect(card.locator(".card-foot small")).not_to_contain_text("Demo role selected")
        expect(page.get_by_text("1 item needs you", exact=False)).to_be_visible()
        bodies = card.get_by_role("list", name="Participating organisations", exact=True)
        expect(bodies.get_by_role("listitem")).to_have_count(8)  # EDL/CEB joined the active bodies
        expect(bodies.locator("li.mine")).to_have_text("DMC · lead (your organisation)")
        expect(card.locator(".card-next, .card-idle")).to_have_count(1)
        expect(card.locator(".card-foot small")).not_to_be_empty()
        # Filters, search and sort only change what is shown.
        page.get_by_role("group", name="Incident status", exact=True).get_by_role("button", name="Closed", exact=True).click()
        expect(page.get_by_role("article")).to_have_count(0)
        page.get_by_role("group", name="Incident status", exact=True).get_by_role("button", name="All", exact=True).click()
        page.get_by_label("Search incidents", exact=True).fill("kotmale")
        expect(page.get_by_role("article")).to_have_count(1)
        page.get_by_label("Sort incidents", exact=True).select_option("district")
        expect(page.get_by_role("article")).to_have_count(1)
        page.screenshot(path=str(tmp_path / "incident-board.png"), full_page=True)
        # The sidebar is fixed below the header: its bottom stays on screen while the page scrolls.
        page.set_viewport_size({"width": 1280, "height": 700})
        settings = page.get_by_role("button", name="Workspace settings", exact=True)
        for y in [0, 400]:
            page.evaluate(f"scrollTo(0, {y})")
            box, header = settings.bounding_box(), page.locator(".header").bounding_box()
            assert header["y"] == 0 and box["y"] + box["height"] <= 700, (y, box)
        assert page.evaluate("scrollY") > 0
        page.evaluate("scrollTo(0, 0)")
        page.set_viewport_size({"width": 1440, "height": 1000})
        card.get_by_role("button", name="Open incident", exact=True).click()
        expect(page.get_by_role("heading", name="Kotmale coordination practice", level=1, exact=True)).to_be_visible()
        # Mobile: cards stack without horizontal scroll.
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(server.url + "/#view=incidents")
        expect(page.get_by_role("article", name="Kotmale coordination practice", exact=True)).to_be_visible()
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
        page.screenshot(path=str(tmp_path / "incident-board-mobile.png"), full_page=True)
        assert not errors, errors
        context.close()
