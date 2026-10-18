"""A plan card in a real browser: two linked steps, one edited, applied together."""
import json

import pytest
from e2e_server import AppServer
from browser_session import browser
from test_team_demo_browser import enter
from ui_views import show

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect


def test_plan_card_edit_then_apply_all_steps(browser, tmp_path):
    with AppServer({"LASTMILE_TEAM_DEMO": "true", "LASTMILE_DATA_DIR": str(tmp_path / "plans"), "LASTMILE_MAIL": "off", "LASTMILE_RATE_LIMIT_PER_MIN": "1000"}) as server:
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        enter(page, server.url + "/", "dmc", "duty_officer")
        expect(page.get_by_role("heading", name="Kotmale coordination practice", level=1, exact=True)).to_be_visible()
        steps = [("area_status", {"area": "ds-nuwara-eliya-kothmale", "sector": "roads", "level": "disrupted", "note": "Slip near the bridge"}),
                 ("ask", {"to": "rda", "kind": "road", "title": "Clear the Kothmale road", "priority": "normal", "delivery_area": "ds-nuwara-eliya-kothmale"})]
        composer = page.get_by_label("Ask or describe the next step", exact=True)
        composer.fill("/propose " + " ;; ".join(f"{a} {json.dumps(d)}" for a, d in steps))
        composer.press("Enter")
        card = page.locator("div.plan-card")
        expect(card.get_by_role("heading", name="2 linked changes", exact=True)).to_be_visible(timeout=20000)
        expect(card).to_contain_text("2 steps · applied together, or not at all")
        expect(card.locator(".plan-step")).to_have_count(2)
        expect(card.locator(".plan-step").nth(0)).to_contain_text("Roads & access in Kothmale DS: disrupted")
        expect(card.locator(".plan-step").nth(1)).to_contain_text("Request to Road Development Authority: Clear the Kothmale road (normal priority)")
        # Edit the request's priority; the plan is checked again and re-signed.
        card.get_by_role("button", name="Edit step 2", exact=True).click()
        modal = page.get_by_role("dialog")
        modal.get_by_label("Priority", exact=True).select_option("urgent")
        modal.get_by_role("button", name="Check and save", exact=True).click()
        expect(modal).not_to_be_visible()
        expect(card.locator(".plan-step").nth(1)).to_contain_text("You edited: priority")
        expect(card.locator(".plan-step").nth(1).locator("dd").filter(has_text="urgent")).to_have_count(1)
        page.screenshot(path=str(tmp_path / "plan-card.png"))
        card.get_by_role("button", name="Apply all steps", exact=True).click()
        expect(page.locator("#toast")).to_have_text("All steps applied")
        expect(page.locator("div.plan-card")).to_have_count(0)
        show(page, "Requests (2)")  # the practice case already has one open request
        expect(page.locator("main")).to_contain_text("Clear the Kothmale road")
        assert errors == []
        context.close()


FLOOD = ("Kothmale and Walapane at Red, Hanguranketa Amber, river at major flood. "
         "Send a report to NDRSC and the Divisional Secretariat asking dry rations for 1,200 families, and notify them.")


def test_flood_sentence_question_plan_letter_and_reports(browser, tmp_path):
    with AppServer({"LASTMILE_TEAM_DEMO": "true", "LASTMILE_DATA_DIR": str(tmp_path / "flood"), "LASTMILE_MAIL": "off", "LASTMILE_RATE_LIMIT_PER_MIN": "1000"}) as server:
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        enter(page, server.url + "/", "dmc", "duty_officer")
        expect(page.get_by_role("heading", name="Kotmale coordination practice", level=1, exact=True)).to_be_visible()
        composer = page.get_by_label("Ask or describe the next step", exact=True)
        composer.fill(FLOOD)
        composer.press("Enter")
        expect(page.locator("#chat-log .bubble.assistant").last).to_contain_text("own assessment", timeout=20000)
        expect(page.locator("div.plan-card")).to_have_count(0)
        composer.fill("It is our own assessment.")
        composer.press("Enter")
        card = page.locator("div.plan-card")
        expect(card.get_by_role("heading", name="3 linked changes", exact=True)).to_be_visible(timeout=20000)
        expect(card.locator(".plan-step").nth(0)).to_contain_text("Letter to NDRSC relief coordination, Divisional Secretariat")
        expect(card.locator(".plan-step").nth(0)).to_contain_text("Status by area (our own assessment, not an official warning)")
        expect(card.locator(".plan-step").nth(1)).to_contain_text("Share the letter from step 1")
        expect(card.locator(".plan-step").nth(2)).to_contain_text("Dry rations for 1,200 families")
        expect(card.locator(".plan-step").nth(2)).to_contain_text("From step 2")
        page.screenshot(path=str(tmp_path / "flood-plan.png"))
        card.get_by_role("button", name="Apply all steps", exact=True).click()
        expect(page.locator("#toast")).to_have_text("All steps applied")
        show(page, "Documents")
        record = page.locator(".letter-record").first
        expect(record).to_contain_text("Letter: Flood situation in Nuwara Eliya District: request for dry rations")
        expect(record.locator(".ack-chip")).to_have_count(2)
        record.get_by_role("button", name="Open letter", exact=True).click()
        sheet = page.get_by_role("dialog").locator(".letter-sheet")
        expect(sheet).to_contain_text("DMC / Emergency Operations Centre")
        expect(sheet).to_contain_text("Request: we request dry rations for 1,200 families.")
        expect(sheet).to_contain_text("EOC Duty Officer, DMC / Emergency Operations Centre")
        page.screenshot(path=str(tmp_path / "flood-letter.png"))
        page.get_by_role("dialog").get_by_role("button", name="Cancel", exact=True).click()
        show(page, "Reports (4)")
        expect(page.locator(".report-card").filter(has_text="Letter LM/DMC/").locator(".pill").first).to_have_text("Issued letter")
        assert errors == []
        context.close()
