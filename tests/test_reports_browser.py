"""Reports in a browser: any body uploads, grouping by organisation or time, filters, and acknowledgement."""
from datetime import datetime, timedelta, timezone

import pytest
from e2e_server import AppServer
from browser_session import browser
from test_team_demo_browser import enter
from test_requests_browser import switch

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect


def test_reports_grouped_filtered_and_acknowledged(browser, tmp_path):
    with AppServer({"LASTMILE_TEAM_DEMO": "true", "LASTMILE_DATA_DIR": str(tmp_path / "reports"), "LASTMILE_MAIL": "off", "LASTMILE_RATE_LIMIT_PER_MIN": "1000"}) as server:
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        enter(page, server.url + "/", "dmc", "duty_officer")
        expect(page.get_by_role("heading", name="Kotmale coordination practice", level=1, exact=True)).to_be_visible()
        # A road body reports an older observation and asks NWSDB to acknowledge it.
        switch(page, "rda", "technical_officer")
        page.get_by_role("button", name="Add report", exact=True).click()
        modal = page.get_by_role("dialog")
        modal.get_by_label("What was reported?", exact=True).fill("EXERCISE landslide blocks the hall access road; water bowsers cannot pass.")
        modal.get_by_label("Report title", exact=True).fill("Access road blocked")
        asks = modal.get_by_role("group", name="Ask to acknowledge", exact=True)
        expect(asks.locator("input[value=dmc]")).to_be_checked()
        expect(asks.locator("input[value=rda]")).to_have_count(0)
        asks.locator("input[value=water]").check()
        modal.get_by_label("Message to them (optional)", exact=True).fill("Bowser route is blocked; please plan another way")
        modal.get_by_text("Source timing · change for an older report", exact=True).click()
        modal.get_by_label("Observation time", exact=True).fill((datetime.now(timezone.utc) - timedelta(days=2)).isoformat())
        modal.get_by_role("button", name="Upload and analyse", exact=True).click()
        expect(modal).not_to_be_visible()
        page.get_by_role("tab", name="Reports", exact=False).click()
        groups = page.locator(".report-group")
        expect(groups).to_have_count(4)  # Road Development Authority, Divisional Secretariat, District Secretariat, DMC (fictional bulletin)
        card = page.get_by_role("article", name="Access road blocked", exact=True)
        expect(card.locator(".ack-chip")).to_have_count(2)
        expect(card.locator(".ack-chip.waiting")).to_have_count(2)
        page.get_by_label("Organisation", exact=True).select_option("rda")
        expect(page.locator(".report-card")).to_have_count(1)
        page.get_by_label("Organisation", exact=True).select_option("all")
        page.get_by_label("Time", exact=True).select_option("24")
        expect(page.locator(".report-card")).to_have_count(3)
        expect(card).to_have_count(0)
        page.get_by_label("Time", exact=True).select_option("all")
        page.get_by_role("group", name="Group reports by", exact=True).get_by_role("button", name="By time", exact=True).click()
        expect(page.locator(".report-group-title")).to_have_text(["Last hour3", "Older1"])
        page.screenshot(path=str(tmp_path / "reports-by-time.png"), full_page=True)
        # Filter controls share one height and stay on one line in a wide pane.
        heights = page.locator(".filter-bar > input, .filter-bar > select").evaluate_all("els => els.map(el => Math.round(el.getBoundingClientRect().height))")
        assert set(heights) == {40}, heights
        page.set_viewport_size({"width": 2000, "height": 1000})
        page.get_by_role("button", name="Collapse chat", exact=True).click()
        tops = page.locator(".filter-bar > *:not([hidden])").evaluate_all("els => new Set(els.map(el => { const r = el.getBoundingClientRect(); return Math.round((r.top + r.bottom) / 2); })).size")
        assert tops == 1, tops
        page.screenshot(path=str(tmp_path / "reports-wide.png"))
        page.get_by_role("button", name="Show chat", exact=True).click()
        page.set_viewport_size({"width": 1440, "height": 1000})
        # NWSDB is told to acknowledge, from Overview and in the report itself.
        switch(page, "water", "service_supervisor")
        page.get_by_role("tab", name="Overview", exact=True).click()
        page.get_by_role("button", name="Acknowledge a report from RDA", exact=False).click()
        expect(card).to_be_focused()
        expect(card).to_contain_text("RDA asks NWSDB to acknowledge")
        expect(card).to_contain_text("Bowser route is blocked")
        card.get_by_role("button", name="Acknowledge", exact=True).click()
        expect(card.locator(".ack-chip.done")).to_contain_text("NWSDB ✓")
        expect(card.get_by_role("button", name="Acknowledge", exact=True)).to_have_count(0)
        # An observer reads acknowledgements but cannot give one.
        switch(page, "dmc", "observer")
        page.get_by_role("tab", name="Reports", exact=False).click()
        expect(card.locator(".ack-chip.waiting")).to_contain_text("DMC")
        expect(card.get_by_role("button", name="Acknowledge", exact=True)).to_have_count(0)
        switch(page, "dmc", "information_officer")
        expect(page.locator(".ack-banner")).to_contain_text("1 report waiting for DMC")
        page.locator(".ack-banner").get_by_role("button", name="Show only these", exact=True).click()
        expect(page.locator(".report-card")).to_have_count(1)
        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_role("button", name="Live data", exact=True).click()
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
        page.screenshot(path=str(tmp_path / "reports-mobile.png"), full_page=True)
        assert not errors, errors
        context.close()
