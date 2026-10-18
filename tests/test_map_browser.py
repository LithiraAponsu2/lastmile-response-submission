"""Map & status in a browser: area status, publishing, the national map and the public status page."""
import pytest
from e2e_server import AppServer
from browser_session import browser
from test_requests_browser import switch

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect


def test_area_status_flows_from_workspace_to_public_page(browser, tmp_path):
    with AppServer({"LASTMILE_TEAM_DEMO": "true", "LASTMILE_DATA_DIR": str(tmp_path / "map"), "LASTMILE_MAIL": "off", "LASTMILE_RATE_LIMIT_PER_MIN": "1000"}) as server:
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(server.url + "/")
        page.get_by_role("button", name="Try sample case", exact=True).click()
        page.get_by_role("dialog").get_by_role("button", name="Enter workspace", exact=True).click()
        expect(page.get_by_role("heading", name="Kotmale coordination practice", level=1, exact=True)).to_be_visible()
        # Overview summarises the area picture and opens the full map.
        expect(page.locator(".metric.static").first).to_contain_text("3")
        page.get_by_role("button", name="Open Map & status", exact=True).click()
        expect(page.get_by_role("tab", name="Map & status", exact=True)).to_have_attribute("aria-selected", "true")
        expect(page.locator("#status-map canvas").first).to_be_visible()
        expect(page.get_by_role("heading", name="Official warnings in force (3)", exact=True)).to_be_visible()
        table = page.locator(".status-table")
        expect(table.locator("tbody tr").first).to_contain_text("Kothmale DS")  # most severe area first
        page.get_by_role("button", name="Kothmale DS", exact=True).click()
        detail = page.locator("#area-detail")
        expect(detail.get_by_role("heading", name="Kothmale DS, Nuwara Eliya", exact=True)).to_be_focused()
        water = detail.locator(".service-list li").filter(has_text="Water supply")
        expect(water).to_contain_text("Not working")
        expect(water).to_contain_text("Public")
        food = detail.locator(".service-list li").filter(has_text="Food & relief")
        expect(food).to_contain_text("Internal")
        # The layer switch recolours the same map; the legend always names what the colours mean.
        page.get_by_role("group", name="Map shows", exact=True).get_by_role("button", name="Services", exact=True).click()
        expect(page.locator("#map-legend")).to_contain_text("Worst service status")
        page.get_by_label("Service shown", exact=True).select_option("water")
        expect(page.locator("#map-legend")).to_contain_text("Water supply")
        # A field engineer reports a status; only a lead can make it public.
        switch(page, "water", "field_engineer")
        page.get_by_role("tab", name="Map & status", exact=True).click()
        page.get_by_role("button", name="+ Area status", exact=True).click()
        modal = page.get_by_role("dialog")
        expect(modal.get_by_text("Show on the public status page", exact=True)).to_have_count(0)
        modal.get_by_label("Area", exact=True).select_option("ds-nuwara-eliya-walapane")
        modal.get_by_label("Service", exact=True).select_option("water")
        modal.get_by_label("Status", exact=True).select_option("restoring")
        modal.get_by_label("What people should know (optional)", exact=True).fill("Pump station on generator; pressure low")
        modal.get_by_role("button", name="Save status", exact=True).click()
        expect(modal).not_to_be_visible()
        page.get_by_role("button", name="Walapane DS", exact=True).click()
        walapane_water = page.locator("#area-detail .service-list li").filter(has_text="Water supply")
        expect(walapane_water).to_contain_text("Being restored")
        expect(walapane_water).to_contain_text("Internal")
        expect(walapane_water.get_by_role("button", name="Show on public page", exact=True)).to_have_count(0)
        # The water supervisor publishes it; observers can read but not change the map.
        switch(page, "water", "service_supervisor")
        page.get_by_role("tab", name="Map & status", exact=True).click()
        page.get_by_role("button", name="Walapane DS", exact=True).click()
        page.locator("#area-detail .service-list li").filter(has_text="Water supply").get_by_role("button", name="Show on public page", exact=True).click()
        expect(page.locator("#area-detail .service-list li").filter(has_text="Water supply")).to_contain_text("Public")
        switch(page, "dmc", "observer")
        page.get_by_role("tab", name="Map & status", exact=True).click()
        expect(page.get_by_role("button", name="+ Area status", exact=True)).to_have_count(0)
        expect(page.get_by_role("button", name="+ Official warning", exact=True)).to_have_count(0)
        # The national status map lists areas needing attention across incidents.
        page.goto(server.url + "/#view=map")
        expect(page.get_by_role("heading", name="Sri Lanka status", exact=True)).to_be_visible()
        page.locator(".problem-list").get_by_role("button", name="Walapane DS, Nuwara Eliya", exact=True).click()
        expect(page.locator("#area-detail")).to_contain_text("Pump station on generator")
        assert not errors, errors
        # The public page: find an area by name, read it in Sinhala, then on a phone in Tamil.
        public = context.new_page()
        public.on("pageerror", lambda e: errors.append(str(e)))
        public.goto(server.url + "/public")
        expect(public.get_by_role("note")).to_contain_text("fictional")
        public.get_by_label("Search for a DS division or district", exact=True).fill("walap")
        public.get_by_role("button", name="Walapane DS", exact=False).click()
        area = public.locator("#area")
        expect(area.get_by_role("heading", name="Walapane DS, Nuwara Eliya", exact=True)).to_be_focused()
        expect(area.locator(".service.restoring").filter(has_text="Water supply")).to_contain_text("Pump station on generator")
        expect(area).not_to_contain_text("Dry rations")  # unpublished records never reach the public
        assert "area=ds-nuwara-eliya-walapane" in public.url
        public.get_by_role("group", name="Language", exact=True).get_by_role("button", name="සිංහල", exact=True).click()
        expect(public.locator("html")).to_have_attribute("lang", "si")
        expect(area).to_contain_text("ජල සැපයුම")
        assert "lang=si" in public.url
        public.set_viewport_size({"width": 390, "height": 844})
        public.goto(server.url + "/public?area=ds-nuwara-eliya-kothmale&lang=ta")
        expect(public.locator("#area")).to_contain_text("மின்சாரம்")
        assert not public.evaluate("document.documentElement.scrollWidth > innerWidth")
        public.screenshot(path=str(tmp_path / "public-mobile.png"), full_page=True)
        assert not errors, errors
        context.close()


def test_maps_in_a_hidden_phone_pane_draw_when_shown(browser, tmp_path):
    """On a phone the data pane is hidden behind Chat; its maps must not fail and must fit once shown."""
    with AppServer({"LASTMILE_TEAM_DEMO": "true", "LASTMILE_DATA_DIR": str(tmp_path / "phone"), "LASTMILE_MAIL": "off", "LASTMILE_RATE_LIMIT_PER_MIN": "1000"}) as server:
        context = browser.new_context(viewport={"width": 320, "height": 700}, reduced_motion="reduce")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(server.url + "/")
        page.get_by_role("button", name="Try sample case", exact=True).click()
        page.get_by_role("dialog").get_by_role("button", name="Enter workspace", exact=True).click()
        expect(page.get_by_role("heading", name="Kotmale coordination practice", level=1, exact=True)).to_be_visible()
        page.get_by_role("button", name="Chat", exact=True).click()
        page.get_by_role("button", name="Live data", exact=True).click()
        page.get_by_role("tab", name="Map & status", exact=True).click()
        expect(page.locator("#status-map canvas").first).to_be_visible()
        page.get_by_role("button", name="Chat", exact=True).click()
        page.get_by_role("button", name="Live data", exact=True).click()
        expect(page.locator(".more-views > .more-trigger")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        expect(page.locator("#toast")).not_to_contain_text("LatLng")
        assert not errors, errors
        context.close()
