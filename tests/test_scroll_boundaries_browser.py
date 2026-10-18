"""Clipped incident rows must not extend the document through accessible labels."""
import pytest

from browser_session import browser
from e2e_server import AppServer
from test_team_accessibility_browser import sample

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect


def assert_page_ends_at_content(page):
    bounds = page.evaluate("""() => ({
        document: document.documentElement.scrollHeight,
        content: document.querySelector('#app').getBoundingClientRect().bottom + scrollY,
        viewport: innerHeight
    })""")
    assert abs(bounds["document"] - max(bounds["content"], bounds["viewport"])) <= 2, bounds


def test_dashboard_scroll_stops_at_content_with_many_incidents(browser, tmp_path):
    with AppServer({"LASTMILE_TEAM_DEMO": "true", "LASTMILE_MAIL": "off",
                    "LASTMILE_RATE_LIMIT_PER_MIN": "1000"}) as server:
        context, page = sample(browser, server)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        headers = {"X-Demo-Session": page.evaluate('sessionStorage.getItem("lastmile-demo-session")')}
        # Real fictional records exercise labels below a bounded, scrolling list.
        for index in range(10):
            response = page.request.post(server.url + "/api/teams/incidents", headers=headers,
                data={"title": f"Scroll boundary exercise {index}", "district": "Nuwara Eliya",
                      "participants": ["dmc", "water"]})
            assert response.ok, response.text()
        page.get_by_role("link", name="Back to Dashboard", exact=True).click()
        expect(page.locator(".incident-list > article")).to_have_count(11)
        expect(page.locator(".card-bodies .sr-only")).to_have_count(11)
        footer = page.get_by_text("Figures reflect the portal records available to your organisation.", exact=True)
        expect(footer).to_be_visible()
        for width, height in [(1690, 940), (1440, 700), (390, 844), (320, 650)]:
            page.set_viewport_size({"width": width, "height": height})
            page.evaluate("scrollTo(0, 0)")
            assert_page_ends_at_content(page)
            for _ in range(3):
                page.evaluate("scrollTo(0, document.documentElement.scrollHeight)")
                page.wait_for_timeout(60)
                assert_page_ends_at_content(page)
                # Only the shell's normal bottom padding may follow the final notice.
                notice = footer.bounding_box()
                padding = 26 if width > 760 else 16
                assert abs(notice["y"] + notice["height"] + padding - height) <= 2
            if width > 760:
                header = page.get_by_role("banner").bounding_box()
                sidebar = page.get_by_role("complementary", name="Application sidebar").bounding_box()
                assert abs(header["y"]) <= 1
                assert abs(sidebar["y"] + sidebar["height"] - height) <= 2
                # The independent list still scrolls and its final incident can be reached.
                page.locator(".incident-list").evaluate("el => el.scrollTop = el.scrollHeight")
                page.locator(".incident-list > article").last.get_by_role("button", name="Open incident", exact=True).scroll_into_view_if_needed()
                assert_page_ends_at_content(page)
                toggle = page.get_by_role("button", name="Collapse sidebar", exact=True)
                toggle.click()
                assert_page_ends_at_content(page)
                page.get_by_role("button", name="Expand sidebar", exact=True).click()
            assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
            page.screenshot(path=str(tmp_path / f"dashboard-scroll-{width}.png"))
        assert not errors, errors
        context.close()
