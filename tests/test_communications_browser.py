"""Communications in a real browser: the chat drafts an SMS from a template, it is approved and sent to areas;
a press release goes to chosen media desks; the Communications page lists messages, templates and recipients."""
import re

import pytest
from e2e_server import AppServer
from browser_session import browser
from test_team_demo_browser import enter
from ui_views import show

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect


def test_chat_sms_approve_send_press_release_and_communications_page(browser, tmp_path):
    with AppServer({"LASTMILE_TEAM_DEMO": "true", "LASTMILE_DATA_DIR": str(tmp_path / "comms"), "LASTMILE_MAIL": "off", "LASTMILE_RATE_LIMIT_PER_MIN": "1000"}) as server:
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        # The Public Information Officer drafts, approves and sends.
        enter(page, server.url + "/", "dmc", "information_officer")
        expect(page.get_by_role("heading", name="Kotmale coordination practice", level=1, exact=True)).to_be_visible()
        composer = page.get_by_label("Ask or describe the next step", exact=True)
        composer.fill("Create an SMS for Kothmale saying go to the temple hall now, then send it")
        composer.press("Enter")
        card = page.locator("div.plan-card")
        expect(card.get_by_role("heading", name="SMS draft for Kothmale DS", exact=True)).to_be_visible(timeout=20000)
        expect(card).to_contain_text("EVACUATE NOW: Kothmale DS. Go to the temple hall now.")
        expect(card).to_contain_text("Then a communications reviewer approves each message in Documents")
        card.get_by_role("button", name="Apply change", exact=True).click()
        show(page, "Documents")
        record = page.locator(".message-record").first
        expect(record).to_contain_text("Waiting for a communications reviewer")
        expect(record).to_contain_text("1 SMS")
        record.get_by_role("button", name="Approve", exact=True).click()
        record = page.locator(".message-record").first
        expect(record.locator(".pill").first).to_have_text("Approved")
        record.get_by_role("button", name="Send…", exact=True).click()
        modal = page.get_by_role("dialog")
        expect(modal.get_by_label("Kothmale DS", exact=True)).to_be_checked()
        modal.get_by_text("Other districts", exact=True).click()
        modal.get_by_label("Whole Kandy District", exact=True).check()
        expect(modal.locator(".reach")).to_contain_text("2 areas")
        modal.get_by_role("button", name="Send SMS", exact=True).click()
        expect(modal).not_to_be_visible()
        record = page.locator(".message-record").first
        expect(record.locator(".pill").first).to_have_text("Sent")
        expect(record).to_contain_text("Kandy District; Kothmale DS")
        expect(record).to_contain_text("nothing left the portal")
        page.screenshot(path=str(tmp_path / "documents-sms-sent.png"))
        # A press release from a template in the Documents tab, sent to the radio desks only.
        page.get_by_role("button", name="Prepare document", exact=True).click()
        modal = page.get_by_role("dialog")
        modal.get_by_label("Document type", exact=True).select_option("press_release")
        expect(modal.get_by_label("Template", exact=True)).to_have_value("tpl-media-release-situation")
        modal.get_by_label("Details to put in", exact=True).fill("Two safety centres are open in Kothmale.")
        expect(modal.get_by_label("Message", exact=True)).to_have_value(re.compile(r"Two safety centres are open in Kothmale\."))
        expect(modal.get_by_label("Subject", exact=True)).to_have_value("Media release: Kotmale coordination practice")
        modal.get_by_role("button", name="Prepare", exact=True).click()
        expect(modal).not_to_be_visible()
        record = page.locator(".message-record").first
        expect(record).to_contain_text("Two safety centres are open in Kothmale.")
        record.get_by_role("button", name="Approve", exact=True).click()
        page.locator(".message-record").first.get_by_role("button", name="Send…", exact=True).click()
        modal = page.get_by_role("dialog")
        modal.get_by_role("button", name="Television and radio", exact=True).click()
        page.screenshot(path=str(tmp_path / "send-press-release.png"))
        expect(modal.get_by_label("National radio news · Radio · all languages", exact=True)).to_be_checked()
        expect(modal.get_by_label("English daily newspaper · Newspaper · English", exact=True)).not_to_be_checked()
        expect(modal.get_by_label("Tamil television news desk · Television · Tamil", exact=True)).to_be_checked()
        modal.get_by_role("button", name="Send press release", exact=True).click()
        expect(modal).not_to_be_visible()
        expect(page.locator(".message-record").first).to_contain_text("National radio news")
        # Sent SMS and press releases appear on the public status page, and the SMS on the panel of an area it went to.
        expect(page.locator(".message-record").first.get_by_role("link", name="Shown on the public status page", exact=True)).to_have_attribute("href", "/public")
        public = context.new_page()
        public.goto(server.url + "/public?area=ds-nuwara-eliya-kothmale")
        messages = public.locator("#messages")
        expect(messages.locator(".message-item")).to_have_count(2)
        expect(messages.locator(".message-item").first).to_contain_text("Press release")
        expect(messages).to_contain_text("EVACUATE NOW: Kothmale DS.")
        expect(messages).to_contain_text("Sent to: Kandy District; Kothmale DS")
        expect(public.locator("#area").get_by_role("heading", name="Messages for this area", exact=True)).to_be_visible()
        expect(public.locator("#area .message-item")).to_have_count(1)
        expect(public.locator("#area")).not_to_contain_text("false")
        public.get_by_role("button", name="தமிழ்", exact=True).click()
        expect(public.get_by_role("heading", name="பொதுமக்களுக்கான செய்திகள்", exact=True)).to_be_visible()
        public.screenshot(path=str(tmp_path / "public-messages.png"), full_page=True)
        public.close()
        # The Communications page: messages grouped by incident, then templates and media desks.
        nav = page.get_by_role("navigation", name="Main navigation", exact=True)
        nav.get_by_role("button", name="Communications", exact=True).click()
        expect(page.get_by_role("heading", name="Communications", level=1, exact=True)).to_be_visible()
        group = page.locator("details.tool-group").filter(has=page.get_by_role("heading", name="Kotmale coordination practice", exact=True))
        expect(group.locator("article").filter(has_text="EVACUATE NOW")).to_contain_text("Sent")
        page.screenshot(path=str(tmp_path / "communications-messages.png"), full_page=True)
        tabs = page.get_by_role("group", name="Communications", exact=True)
        tabs.get_by_role("button", name="Templates", exact=True).click()
        expect(page.locator(".places-table tbody tr")).to_have_count(12)  # 10 message templates and 2 letter templates
        page.get_by_role("button", name="Add message template", exact=True).click()
        modal = page.get_by_role("dialog")
        modal.get_by_label("Name", exact=True).fill("Road closed")
        modal.get_by_label("Channel", exact=True).select_option("sms")
        modal.get_by_label("Language", exact=True).select_option("en")
        modal.get_by_label("Message", exact=True).fill("ROAD CLOSED in {areas}: {details} Use another route. Help: {hotline}")
        modal.get_by_role("button", name="Add message template", exact=True).click()
        expect(modal).not_to_be_visible()
        expect(page.locator(".places-table tbody tr")).to_have_count(13)
        expect(page.locator(".place-card")).to_contain_text("characters · 1 SMS")
        page.screenshot(path=str(tmp_path / "communications-templates.png"), full_page=True)
        tabs.get_by_role("button", name="Media recipients", exact=True).click()
        expect(page.locator(".places-table tbody tr")).to_have_count(15)
        # Places & stock keeps its own lists.
        nav.get_by_role("button", name="Places & stock", exact=True).click()
        expect(page.get_by_role("group", name="Lists", exact=True).get_by_role("button", name="Message templates", exact=False)).to_have_count(0)
        page.set_viewport_size({"width": 390, "height": 844})
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
        assert not errors, errors
