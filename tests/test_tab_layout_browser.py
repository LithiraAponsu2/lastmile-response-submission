"""Tab chrome must not absorb free vertical space from short panels or tall chat."""
import pytest
from e2e_server import AppServer
from browser_session import browser
from test_team_accessibility_browser import sample
from ui_views import show

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect


def test_tabs_stay_compact_across_panels_and_viewports(browser, tmp_path):
    with AppServer({"LASTMILE_TEAM_DEMO": "true", "LASTMILE_DATA_DIR": str(tmp_path / "tabs"), "LASTMILE_MAIL": "off"}) as server:
        context, page = sample(browser, server)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        tabs = page.get_by_role("tablist", name="Incident data sections", exact=True)
        # Five everyday tabs; the rest open from More as a sixth tab.
        expect(page.get_by_role("tab")).to_have_count(5)
        for label in ["Shelters & resources", "Funding records", "Shared with you", "Reports", "Requests", "Public requests", "Map & status", "Needs & assignments", "Documents", "Activity", "Overview"]:
            tab = show(page, label, exact=label in {"Funding records", "Needs & assignments", "Documents", "Activity", "Overview", "Map & status"})
            expect(tab).to_have_attribute("aria-selected", "true")
            if label == "Shelters & resources":
                page.screenshot(path=str(tmp_path / "resources-tab.png"), full_page=True)
            assert tabs.bounding_box()["height"] <= 72, (label, tabs.bounding_box())
            assert tab.bounding_box()["height"] <= 48, (label, tab.bounding_box())
            expect(page.get_by_role("tabpanel")).to_have_count(1)
        # Layout-only stress: reproduce a tall parent with a short visible panel.
        show(page, "Shared with you", exact=False)
        page.locator("#data-area").evaluate("el => el.style.minHeight='1500px'")
        assert tabs.bounding_box()["height"] <= 72
        page.locator("#data-area").evaluate("el => el.style.minHeight=''")
        page.get_by_role("button", name="Collapse chat", exact=True).click()
        assert tabs.bounding_box()["height"] <= 72
        page.get_by_role("button", name="Show chat", exact=True).click()
        for width in [390, 320]:
            page.set_viewport_size({"width": width, "height": 844})
            page.get_by_role("button", name="Live data", exact=True).click()
            show(page, "Activity")
            assert tabs.bounding_box()["height"] <= 72
            page.screenshot(path=str(tmp_path / f"mobile-timeline-{width}.png"), full_page=True)
            overflow = page.evaluate("[...document.querySelectorAll('body *')].filter(el => el.getBoundingClientRect().right > innerWidth + 1).map(el => ({tag:el.localName,id:el.id,width:el.getBoundingClientRect().width,right:el.getBoundingClientRect().right}))")
            assert not page.evaluate("document.documentElement.scrollWidth > innerWidth"), overflow
            # Four short tabs may fit; when they overflow the scroll control stays usable.
            scroll_control = page.locator('.incident-tabs [part~="scroll-button-end"]')
            if scroll_control.is_visible():
                expect(scroll_control.locator("svg")).to_have_count(1)
                assert scroll_control.bounding_box()["width"] >= 44
            show(page, "Overview")
            assert tabs.bounding_box()["height"] <= 72
        page.screenshot(path=str(tmp_path / "mobile-tabs.png"), full_page=True)
        assert not errors, errors
        context.close()


def test_more_menu_escapes_short_panels_and_handles_keyboard(browser, tmp_path):
    with AppServer({"LASTMILE_TEAM_DEMO": "true", "LASTMILE_DATA_DIR": str(tmp_path / "menu"), "LASTMILE_MAIL": "off"}) as server:
        context, page = sample(browser, server)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        show(page, "Shared with you", exact=False)
        for width, height in [(1440, 1000), (390, 650), (320, 520)]:
            page.set_viewport_size({"width": width, "height": height})
            if width < 761:
                page.get_by_role("button", name="Live data", exact=True).click()
            trigger = page.locator(".more-trigger")
            trigger.scroll_into_view_if_needed()
            trigger.click()
            menu = page.get_by_role("menu", name="More incident views", exact=True)
            last = page.locator(".more-views").get_by_role("menuitem", name="Needs & assignments", exact=True)
            expect(last).to_be_visible()
            bounds = menu.bounding_box()
            assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= width
            assert bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= height
            # Hit testing catches clipping that visibility/bounding boxes miss.
            last.scroll_into_view_if_needed()
            assert last.evaluate("el => { const r = el.getBoundingClientRect(); const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2); return hit === el || el.contains(hit); }")
            page.screenshot(path=str(tmp_path / f"more-menu-{width}.png"))
            page.keyboard.press("Escape")
            expect(menu).not_to_be_visible()
            expect(trigger).to_be_focused()
            page.keyboard.press("ArrowDown")
            expect(menu).to_be_visible()
            page.keyboard.press("End")
            expect(last).to_be_focused()
            page.keyboard.press("Enter")
            expect(page.get_by_role("tab", name="Needs & assignments", exact=True)).to_have_attribute("aria-selected", "true")
            expect(page.get_by_role("tab", name="Needs & assignments", exact=True)).to_be_focused()
            page.locator(".more-trigger").click()
            page.get_by_role("heading", level=1).click()
            expect(page.get_by_role("menu", name="More incident views", exact=True)).not_to_be_visible()
            assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
        for name in ["New request", "Add report", "Refresh"]:
            button = page.get_by_role("button", name=name, exact=True)
            expect(button.locator("svg[aria-hidden=true]")).to_have_count(1)
        page.get_by_role("button", name="Refresh", exact=True).click()
        expect(page.get_by_role("heading", name="Kotmale coordination practice", level=1, exact=True)).to_be_visible()
        assert not errors, errors
        context.close()
