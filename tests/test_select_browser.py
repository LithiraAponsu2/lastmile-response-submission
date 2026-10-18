"""Role card picker and reusable dropdown styling with actual native picker, keyboard and form values."""
import pytest
from e2e_server import AppServer
from browser_session import browser

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect


def test_role_cards_shared_dropdown_keyboard_and_forms(browser, tmp_path):
    with AppServer({"LASTMILE_TEAM_DEMO": "true", "LASTMILE_DATA_DIR": str(tmp_path / "selects"), "LASTMILE_MAIL": "off"}) as server:
        context = browser.new_context(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(server.url + "/")
        page.get_by_role("button", name="+ New incident", exact=True).click()
        modal = page.get_by_role("dialog")
        orgs = modal.get_by_role("group", name="Organisation", exact=True)
        roles = modal.get_by_role("group", name="Your role", exact=True)
        expect(orgs.get_by_role("radio")).to_have_count(8)  # EDL/CEB joined the active bodies
        expect(orgs.get_by_role("radio", name="DMC / Emergency Operations Centre", exact=True)).to_be_checked()
        expect(roles.get_by_role("radio", name="EOC Duty Officer", exact=True)).to_be_checked()
        # Inactive bodies are named, never selectable.
        inactive = modal.get_by_role("region", name="Other agencies · not active in this demo", exact=True)
        expect(inactive.get_by_role("listitem")).to_have_count(9)  # EDL/CEB moved from inactive to active
        expect(inactive).to_contain_text("Transport liaison / SLTB")
        expect(modal.locator("input[name=organisation][value=transport]")).to_have_count(0)
        page.screenshot(path=str(tmp_path / "role-cards.png"), full_page=True)
        orgs.get_by_role("radio", name="NWSDB / water provider", exact=True).check()
        expect(roles.get_by_role("radio")).to_have_count(3)
        expect(roles.get_by_role("radio", name="Water Service Supervisor", exact=True)).to_be_checked()
        expect(roles).to_contain_text("Accept water restoration tasks")
        roles.get_by_role("radio", name="Observer", exact=True).check()
        # Native radio keyboard navigation; an Observer stays an Observer across bodies.
        orgs.get_by_role("radio", name="NWSDB / water provider", exact=True).focus()
        page.keyboard.press("ArrowRight")
        expect(orgs.get_by_role("radio", name="Road Development Authority", exact=True)).to_be_checked()
        expect(roles.get_by_role("radio", name="Observer", exact=True)).to_be_checked()
        orgs.get_by_role("radio", name="DMC / Emergency Operations Centre", exact=True).check()
        roles.get_by_role("radio", name="EOC Duty Officer", exact=True).check()
        values = modal.locator("form").evaluate('el => Object.fromEntries(new FormData(el))')
        assert values == {"organisation": "dmc", "role": "duty_officer"}
        modal.get_by_role("button", name="Enter workspace", exact=True).click()
        name = modal.get_by_label("Incident name", exact=True)
        name.fill("Dropdown regression exercise")
        country = modal.get_by_label("Country", exact=True)
        expect(country.locator('option[value=in]')).to_be_disabled()
        expect(country.locator('option[value=bd]')).to_be_disabled()
        expect(modal.locator("select:not(.lm-select)")).to_have_count(0)
        expect(modal.locator("input[name=participants]")).to_have_count(8)
        district = modal.get_by_label("District", exact=True)
        assert page.evaluate('CSS.supports("appearance", "base-select")')
        expect(district).to_have_class("lm-select")
        expect(district).to_have_css("border-top-color", "rgba(0, 0, 0, 0)")
        district.click()
        page.wait_for_function('document.querySelector("select[name=district]").matches(":open")')
        style = district.evaluate('el => {const s=getComputedStyle(el,"::picker(select)");return {radius:s.borderRadius,height:s.maxHeight,shadow:s.boxShadow};}')
        assert style["radius"] == "16px" and style["shadow"] != "none"
        expect(district.locator('option[value="Nuwara Eliya"]')).to_have_css("background-color", "rgb(232, 240, 254)")
        page.screenshot(path=str(tmp_path / "district-picker.png"), full_page=True)
        page.get_by_role("option", name="Kandy", exact=True).click()
        expect(district).to_have_value("Kandy")
        # Native keyboard navigation changes the original select value/events.
        district.focus()
        page.keyboard.press("Space")
        page.wait_for_function('document.querySelector("select[name=district]").matches(":open")')
        page.keyboard.press("Home")
        page.keyboard.press("Enter")
        expect(district).to_have_value("Ampara")
        # First Escape dismisses the picker, leaving its parent modal open.
        district.click()
        page.wait_for_function('document.querySelector("select[name=district]").matches(":open")')
        page.keyboard.press("Escape")
        expect(modal).to_be_visible()
        assert not district.evaluate('el => el.matches(":open")')
        district.select_option("Colombo")
        modal.get_by_role("button", name="Open incident", exact=True).click()
        expect(page.get_by_role("heading", name="Dropdown regression exercise", level=1, exact=True)).to_be_visible()
        expect(page.locator(".incident-meta")).to_contain_text("Colombo")
        expect(page.get_by_label("Active organisation and role")).to_contain_text("EOC Duty Officer")
        page.set_viewport_size({"width": 390, "height": 844})
        page.locator("#switch-role").click()
        expect(modal.get_by_role("radio", name="EOC Duty Officer", exact=True)).to_be_checked()
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
        assert modal.evaluate("el => el.scrollWidth <= el.clientWidth")
        page.screenshot(path=str(tmp_path / "role-cards-mobile.png"), full_page=True)
        page.keyboard.press("Escape")
        expect(modal).not_to_be_visible()
        assert not errors, errors
        context.close()
