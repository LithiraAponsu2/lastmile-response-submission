"""Places & stock in a real browser: lists, standard capacity, editing with history, the map and the incident link."""
import re

import pytest
from e2e_server import AppServer
from browser_session import browser
from test_team_demo_browser import enter

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect


def test_places_lists_edit_history_map_and_incident_picker(browser, tmp_path):
    with AppServer({"LASTMILE_TEAM_DEMO": "true", "LASTMILE_DATA_DIR": str(tmp_path / "places"), "LASTMILE_MAIL": "off", "LASTMILE_RATE_LIMIT_PER_MIN": "1000"}) as server:
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        enter(page, server.url + "/", "division", "divisional_secretary")
        expect(page.get_by_role("heading", name="Kotmale coordination practice", level=1, exact=True)).to_be_visible()
        incident = re.search(r"incident=([a-z0-9_]+)", page.url).group(1)
        page.get_by_role("navigation", name="Main navigation", exact=True).get_by_role("button", name="Places & stock", exact=True).click()
        expect(page.get_by_role("heading", name="Places & stock", level=1, exact=True)).to_be_visible()
        lists = page.get_by_role("group", name="Lists", exact=True)
        expect(lists.get_by_role("button", name="Safety centres (53)", exact=True)).to_have_attribute("aria-pressed", "true")
        expect(page.locator("#places-map.leaflet-container")).to_be_visible()
        # The planning figure takes the tightest of design, floor area and toilets.
        table = page.locator("table.places-table")
        page.get_by_role("button", name="Kotmale Practice Hall", exact=True).click()
        card = page.locator(".place-card")
        expect(card).to_contain_text("Places by standard: 80 people · limited by toilets")
        card.get_by_role("button", name="Edit", exact=True).click()
        modal = page.get_by_role("dialog")
        modal.get_by_label("Working toilets · optional", exact=True).fill("6")
        modal.get_by_label("What changed and why · optional", exact=True).fill("Two toilets repaired")
        modal.get_by_role("button", name="Save changes", exact=True).click()
        expect(modal).not_to_be_visible()
        expect(page.locator(".place-card")).to_contain_text("Places by standard: 85 people · limited by floor area")
        page.get_by_text("Change history (1)", exact=True).click()
        expect(page.locator(".history-list li").first).to_contain_text("Changed: Kotmale Practice Hall")
        expect(page.locator(".history-list li").first).to_contain_text("Working toilets")
        expect(page.locator(".history-list li").first).to_contain_text("“Two toilets repaired”")
        # Another organisation's centre is read-only for a divisional secretary.
        page.get_by_role("button", name="Ratnapura Evacuation Centre", exact=True).click()
        expect(page.locator(".place-card").get_by_role("button", name="Edit", exact=True)).to_have_count(0)
        # Real reference layers: hospitals are searchable; only coordinators change them.
        lists.get_by_role("button", name=re.compile(r"^Health facilities \(\d+\)$")).click()
        expect(page.get_by_role("heading", name="Health facilities", level=2, exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="Add health facility", exact=True)).to_have_count(0)
        page.get_by_role("searchbox", name="Search Health facilities", exact=True).fill("teaching")
        expect(page.locator(".places-filters [role=status]")).to_contain_text(re.compile(r"^\d+ of \d+ health facilities$"))
        expect(table.locator("tbody tr").first).to_contain_text("Teaching")
        lists.get_by_role("button", name=re.compile(r"^River gauges")).click()
        expect(table).to_contain_text("Nagalagam Street")
        expect(table).to_contain_text("No location")
        page.screenshot(path=str(tmp_path / "places.png"), full_page=True)
        # Phone width: the table scrolls inside its box, never the page.
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(server.url + "/#view=resources")
        expect(page.get_by_role("heading", name="Places & stock", level=1, exact=True)).to_be_visible()
        expect(table).to_be_visible()
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
        # Inside the incident, a registered centre fills the resource form.
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.goto(server.url + "/#" + f"incident={incident}&tab=resources")
        page.get_by_role("button", name="Confirm resource", exact=True).click()
        picker = page.get_by_role("dialog").get_by_label("Registered place or stock (optional)", exact=True)
        expect(picker.locator("option", has_text="Kotmale Practice Hall")).to_have_count(1)
        picker.select_option("centre-kotmale-practice-hall")
        expect(page.get_by_role("dialog").get_by_label("Resource name", exact=True)).to_have_value("Kotmale Practice Hall")
        expect(page.get_by_role("dialog").locator("input[name=resource_key]")).to_have_value("centre-kotmale-practice-hall")
        assert not errors, errors
        context.close()
