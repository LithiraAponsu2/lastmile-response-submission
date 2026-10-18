"""Suggestions in a real browser: a bulletin report suggests its warnings; the warnings suggest messages in three languages."""
import json

import pytest
from e2e_server import AppServer
from browser_session import browser
from test_team_demo_browser import enter
from ui_views import show

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect
NBRO = "EXERCISE. NBRO Level 3 (Red) landslide early warning for Kothmale and Walapane DS divisions. Level 2 (Amber): Hanguranketha."


def test_report_then_warning_suggestions_chain(browser, tmp_path):
    with AppServer({"LASTMILE_TEAM_DEMO": "true", "LASTMILE_DATA_DIR": str(tmp_path / "suggest"), "LASTMILE_MAIL": "off", "LASTMILE_RATE_LIMIT_PER_MIN": "1000"}) as server:
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        enter(page, server.url + "/", "dmc", "duty_officer")
        expect(page.get_by_role("heading", name="Kotmale coordination practice", level=1, exact=True)).to_be_visible()
        expect(page.locator("#ai-suggestions")).to_have_count(0)
        # A bulletin arrives as a report (here through an exact chat step).
        report = {"title": "NBRO bulletin", "text": NBRO, "observed_at": "2026-10-18T00:00:00Z", "recipients": []}
        composer = page.get_by_label("Ask or describe the next step", exact=True)
        composer.fill("/propose contribute " + json.dumps(report))
        composer.press("Enter")
        page.locator("div.plan-card").get_by_role("button", name="Apply change", exact=True).click(timeout=20000)
        expect(page.locator("#toast")).to_have_text("Change applied")
        show(page, "Overview")
        section = page.locator("#ai-suggestions")
        expect(section.get_by_role("heading", name="Records from “NBRO bulletin”", exact=True)).to_be_visible()
        expect(section).to_contain_text("NBRO Red landslide warning · 2 areas")
        expect(section).to_contain_text("NBRO Amber landslide warning · 1 area")
        expect(section.locator(".plan-card .pill").first).to_have_text("Suggestion")  # built by the readers, not an AI model, and labelled so
        expect(page.locator(".attention-item.proposal").filter(has_text="1 suggestion to review")).to_be_visible()
        section.get_by_role("button", name="Apply all steps", exact=True).click()
        expect(page.locator("#toast")).to_have_text("All steps applied")
        # Each recorded warning now suggests its public messages.
        cards = page.locator("#ai-suggestions div.plan-card")
        expect(cards).to_have_count(2)
        red = cards.filter(has_text="Public messages for the Red landslide warning")
        expect(red.locator(".plan-step")).to_have_count(4)
        expect(red.locator(".plan-step").nth(1)).to_contain_text("රතු")
        expect(red.locator(".plan-step").nth(2)).to_contain_text("சிவப்பு")
        expect(red.locator(".plan-step").nth(0)).to_contain_text("EVACUATE NOW: Kothmale DS, Walapane DS.")
        page.screenshot(path=str(tmp_path / "suggestions.png"), full_page=True)
        red.get_by_role("button", name="Apply all steps", exact=True).click()
        expect(page.locator("#toast")).to_have_text("All steps applied")
        expect(cards).to_have_count(1)
        show(page, "Documents")
        expect(page.locator(".message-record")).to_have_count(4)
        assert errors == []
        context.close()
