"""Unified navigation, compact activity and geometry in a real browser."""
import pytest
import uuid
from e2e_server import AppServer
from browser_session import browser
from test_team_demo_browser import expand_activity

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect


def test_home_role_entry_local_components_activity_and_equal_panes(browser, tmp_path):
    with AppServer({"LASTMILE_TEAM_DEMO": "true", "LASTMILE_DATA_DIR": str(tmp_path / "unified"), "LASTMILE_MAIL": "off", "LASTMILE_RATE_LIMIT_PER_MIN": "1000"}) as server:
        context = browser.new_context(viewport={"width":1440,"height":1000}, reduced_motion="reduce")
        page = context.new_page()
        errors, external = [], []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("request", lambda r: external.append(r.url) if not r.url.startswith(server.url) else None)
        page.goto(server.url + "/")
        page.wait_for_load_state("networkidle")
        expect(page.get_by_role("heading", name="Incidents", exact=True)).to_be_visible()
        expect(page.get_by_role("dialog")).not_to_be_visible()
        nav = page.get_by_role("navigation", name="Main navigation", exact=True)
        expect(nav.get_by_role("button")).to_have_count(10)
        expect(page.get_by_role("link", name="Organisation workspace", exact=True)).to_have_count(0)
        expect(page.get_by_role("region", name="Incident statistics", exact=True)).to_contain_text("Active incidents")
        nav.get_by_role("button", name="Dashboard", exact=True).click()
        expect(page.get_by_role("heading", name="Incidents", exact=True)).to_be_visible()
        expect(page.get_by_role("dialog")).not_to_be_visible()
        page.screenshot(path=str(tmp_path / "unified-home-empty.png"), full_page=True)
        page.get_by_role("button", name="Try sample case", exact=True).click()
        modal = page.get_by_role("dialog")
        expect(modal.get_by_role("heading", name="Choose organisation & role", exact=True)).to_be_visible()
        modal.locator("input[name=organisation][value=dmc]").check()
        modal.locator("input[name=role][value=duty_officer]").check()
        modal.get_by_role("button", name="Enter workspace", exact=True).click()
        expect(page.get_by_role("heading", name="Kotmale coordination practice", level=1, exact=True)).to_be_visible()
        upload = page.get_by_role("button", name="Upload a report", exact=True)
        button_box, glyph_box = upload.bounding_box(), page.locator("#upload-report svg").bounding_box()
        assert abs(button_box["x"] + button_box["width"]/2 - glyph_box["x"] - glyph_box["width"]/2) < 1
        assert abs(button_box["y"] + button_box["height"]/2 - glyph_box["y"] - glyph_box["height"]/2) < 1
        upload.click()
        close, close_glyph = modal.get_by_role("button", name="Close dialog", exact=True).bounding_box(), modal.locator("#close-dialog svg").bounding_box()
        assert abs(close["y"] + close["height"]/2 - close_glyph["y"] - close_glyph["height"]/2) < 1
        page.keyboard.press("Escape")
        page.get_by_label("Ask or describe the next step", exact=True).fill("What needs attention?")
        page.get_by_role("button", name="Send message", exact=True).click()
        activity = page.locator("lm-work-activity")
        expect(activity.get_by_text("Assistant finished", exact=True)).to_be_visible(timeout=30000)
        assert activity.locator("wa-details").evaluate("el => !el.open")
        expect(activity.locator(".step").first).not_to_be_visible()
        expand_activity(page)
        expect(activity.locator(".step.completed").first).to_be_visible()
        page.screenshot(path=str(tmp_path / "unified-incident-activity.png"), full_page=True)
        activity.get_by_text("Assistant finished", exact=True).click()
        assert activity.locator("wa-details").evaluate("el => !el.open")
        # Layout-only stress block; not operational evidence or an invented tool.
        page.locator("#data-area").evaluate("el => {const block=document.createElement('div'); block.id='layout-stress'; block.style.height='1800px'; el.append(block);}")
        # The composer stays on screen however long the data pane is, before and after scrolling the page.
        composer_visible = "(() => {const r=document.querySelector('.chat-form').getBoundingClientRect(); return r.top>=0 && r.bottom<=innerHeight+1;})()"
        page.wait_for_function(composer_visible)
        assert page.evaluate("document.querySelector('.chat').getBoundingClientRect().height < document.querySelector('#data-area').getBoundingClientRect().height - 200")
        page.evaluate("scrollTo(0, 1200)")
        page.wait_for_function(composer_visible)
        page.evaluate("scrollTo(0, 0)")
        page.locator("#layout-stress").evaluate("el => el.remove()")
        incident_url = page.url
        assert "/teams" not in incident_url
        page.get_by_role("link", name="Back to Dashboard", exact=True).click()
        expect(page.get_by_role("region", name="Incident statistics", exact=True)).to_contain_text("1")
        # Dashboard: role-aware attention, shelter places (water is down, so the last report is shown and labelled, never "Unknown") and the incident map.
        expect(page.get_by_role("heading", name="Dashboard", level=1, exact=True)).to_be_visible()
        expect(page.get_by_role("heading", name="Needs your attention", exact=True)).to_be_visible()
        shelters = page.get_by_role("region", name="Shelter capacity", exact=True)
        expect(shelters).to_contain_text("Kotmale Practice Hall")
        expect(shelters).to_contain_text("Needs a check")
        expect(shelters).to_contain_text("Last report: 40 in use, 60 free of 100")
        expect(shelters).to_contain_text("Water unavailable")
        expect(page.get_by_role("region", name="Incident statistics", exact=True)).to_contain_text("Free places at last report")
        expect(page.locator(".dashboard")).not_to_contain_text("Unknown")
        # Recent activity has its own page now.
        expect(page.get_by_role("heading", name="Recent activity", exact=True)).to_have_count(0)
        page.get_by_role("navigation", name="Main navigation", exact=True).get_by_role("button", name="Activity", exact=True).click()
        expect(page.get_by_role("heading", name="Activity", level=1, exact=True)).to_be_visible()
        expect(page.get_by_role("heading", name="Today", exact=True)).to_be_visible()
        expect(page.locator(".activity-list li").first).to_be_visible()
        page.get_by_label("Search activity", exact=True).fill("no such thing")
        expect(page.get_by_text("Nothing matches.", exact=False)).to_be_visible()
        page.get_by_role("navigation", name="Main navigation", exact=True).get_by_role("button", name="Dashboard", exact=True).click()
        expect(page.locator("#dashboard-map .incident-pin")).to_have_count(1)
        page.screenshot(path=str(tmp_path / "unified-home-saved.png"), full_page=True)
        # The role is chosen in the top bar, outside every incident; entering never asks again.
        page.get_by_role("banner").locator("#switch-role").click()
        modal.locator("input[name=organisation][value=water]").check()
        modal.locator("input[name=role][value=service_supervisor]").check()
        modal.get_by_role("button", name="Switch role", exact=True).click()
        expect(modal).not_to_be_visible()
        page.get_by_role("button", name="Open incident", exact=True).click()
        expect(page.get_by_label("Active organisation and role")).to_contain_text("NWSDB")
        expect(modal).not_to_be_visible()
        nav.get_by_role("button", name="Dashboard", exact=True).click()
        expect(page.get_by_role("heading", name="Incidents", exact=True)).to_be_visible()
        page.get_by_role("button", name="Open incident", exact=True).click()
        expect(page.get_by_label("Active organisation and role")).to_contain_text("NWSDB")
        expect(modal).not_to_be_visible()
        page.locator("#switch-role").click()
        modal.locator("input[name=role][value=observer]").check()
        modal.get_by_role("button", name="Switch role", exact=True).click()
        expect(upload).to_be_disabled()
        page.set_viewport_size({"width":390,"height":844})
        page.get_by_role("button", name="Live data", exact=True).click()
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
        page.screenshot(path=str(tmp_path / "unified-mobile.png"), full_page=True)
        assert not errors
        assert not external, external
        context.close()


def test_restored_tools_collapsible_sidebar_and_record_navigation(browser, tmp_path):
    with AppServer({"LASTMILE_TEAM_DEMO": "true", "LASTMILE_DATA_DIR": str(tmp_path / "sidebar"), "LASTMILE_MAIL": "off", "LASTMILE_RATE_LIMIT_PER_MIN": "1000"}) as server:
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(server.url + "/")
        nav = page.get_by_role("navigation", name="Main navigation", exact=True)
        for label in ["Dashboard", "Incidents", "Requests", "Needs & assignments", "Who does what where", "Reports", "Communications", "Activity", "Places & stock", "Status map"]:
            expect(nav.get_by_role("button", name=label, exact=True)).to_be_visible()
        # One flat list: no More tools group, walkthrough, handover page or console link.
        expect(page.locator(".sidebar-more")).to_have_count(0)
        public = nav.get_by_role("link", name="Public status page (opens in a new tab)", exact=True)
        expect(public).to_have_attribute("href", "/public")
        expect(public).to_have_attribute("target", "_blank")
        for gone in ["Shift handover", "Guided walkthrough", "Warning review console"]:
            expect(page.get_by_role("complementary", name="Application sidebar").get_by_text(gone, exact=True)).to_have_count(0)
        nav.get_by_role("button", name="Reports", exact=True).click()
        expect(page.get_by_role("heading", name="Reports", exact=True)).to_be_visible()
        expect(page.get_by_label("Search shared records", exact=True)).to_have_count(0)
        page.get_by_role("button", name="Choose organisation & role", exact=True).click()
        page.get_by_role("dialog").get_by_role("button", name="Enter workspace", exact=True).click()
        expect(page.get_by_label("Search shared records", exact=True)).to_be_visible()
        expect(page.get_by_text("No records yet.", exact=False)).to_be_visible()
        page.get_by_role("button", name="Collapse sidebar", exact=True).click()
        expect(page.get_by_role("button", name="Expand sidebar", exact=True)).to_have_attribute("aria-expanded", "false")
        assert page.locator(".app-sidebar").bounding_box()["width"] == 76
        page.reload()
        expect(page.get_by_role("button", name="Expand sidebar", exact=True)).to_be_visible()
        page.get_by_role("button", name="Expand sidebar", exact=True).click()
        expect(page.get_by_role("button", name="Collapse sidebar", exact=True)).to_be_visible()
        nav.get_by_role("button", name="Dashboard", exact=True).click()
        page.get_by_role("button", name="Try sample case", exact=True).click()
        # The role chosen in the top bar carries into the sample; nothing asks again.
        expect(page.get_by_role("heading", name="Kotmale coordination practice", level=1, exact=True)).to_be_visible()
        first_id = page.url.split("incident=")[1].split("&")[0]
        headers = {"X-Demo-Session": page.evaluate('sessionStorage.getItem("lastmile-demo-session")')}
        doc = page.request.get(server.url + "/api/teams/incidents/" + first_id, headers=headers).json()
        for kind in ["sms", "handover"]:
            response = page.request.post(server.url + "/api/teams/incidents/" + first_id + "/commands", headers=headers, data={"version": doc["version"], "request_id": str(uuid.uuid4()), "action": "document", "data": {"kind": kind, "audience": "Exercise only", "language": "en"}})
            assert response.ok, response.status
            doc = response.json()
        second = page.request.post(server.url + "/api/teams/incidents", headers=headers, data={"title": "Second fictional case", "district": "Nuwara Eliya", "participants": ["dmc", "water"]}).json()
        response = page.request.post(server.url + "/api/teams/incidents/" + second["id"] + "/commands", headers=headers, data={"version": second["version"], "request_id": str(uuid.uuid4()), "action": "contribute", "data": {"title": "Second agency note", "text": "EXERCISE second case water inspection needed.", "observed_at": "2026-10-16T00:00:00Z"}})
        assert response.ok, response.status
        page.get_by_label("Ask or describe the next step", exact=True).fill("Keep this unsent draft")
        nav.get_by_role("button", name="Reports", exact=True).click()
        search = page.get_by_label("Search shared records", exact=True)
        expect(page.locator(".tool-records article")).to_have_count(4)  # three sample reports, including the fictional bulletin, plus one
        groups = page.locator(".tool-records details.tool-group")  # one collapsible group per incident
        expect(groups).to_have_count(2)
        second_group = groups.filter(has=page.get_by_role("heading", name="Second fictional case", exact=True))
        expect(second_group).to_contain_text("1 report")
        second_group.locator("summary").click()
        expect(second_group.locator("article")).not_to_be_visible()
        second_group.locator("summary").click()
        expect(second_group.locator("article")).to_be_visible()
        search.fill("Second agency note")
        expect(page.locator(".tool-records article")).to_have_count(1)
        page.get_by_role("button", name="Open in incident", exact=True).click()
        expect(page.get_by_role("dialog")).not_to_be_visible()
        expect(page.get_by_role("heading", name="Second fictional case", level=1, exact=True)).to_be_visible()
        expect(page.get_by_role("tab", name="Reports", exact=False)).to_have_attribute("aria-selected", "true")
        expect(page.get_by_role("article").filter(has=page.get_by_role("heading", name="Second agency note", exact=True))).to_be_focused()
        nav.get_by_role("button", name="Places & stock", exact=True).click()
        page.get_by_role("group", name="Lists", exact=True).get_by_role("button", name="In incidents", exact=True).click()
        expect(page.locator(".tool-records article")).to_have_count(1)
        page.get_by_role("button", name="Open in incident", exact=True).click()
        expect(page.get_by_label("Ask or describe the next step", exact=True)).to_have_value("Keep this unsent draft")
        expect(page.get_by_role("tab", name="Resources", exact=False)).to_have_attribute("aria-selected", "true")
        nav.get_by_role("button", name="Status map", exact=True).click()
        expect(page.get_by_role("heading", name="Sri Lanka status", exact=True)).to_be_visible()
        expect(page.locator("#status-map canvas").first).to_be_visible()
        # Needs & assignments: what is assigned (a place, a thing, a team), open first, then done.
        nav.get_by_role("button", name="Needs & assignments", exact=True).click()
        expect(page.locator(".tool-records article").first).to_contain_text("Accommodate 20 people")
        # Communications lists messages only; the handover stays inside its incident.
        nav.get_by_role("button", name="Communications", exact=True).click()
        expect(page.locator(".tool-records article")).to_have_count(1)
        expect(page.locator(".tool-records article h3")).to_have_text("SMS")
        page.get_by_role("button", name="Workspace settings", exact=True).click()
        page.get_by_label("Sidebar on desktop", exact=True).select_option("collapsed")
        page.get_by_role("button", name="Save preferences", exact=True).click()
        expect(page.get_by_role("button", name="Expand sidebar", exact=True)).to_be_visible()
        page.screenshot(path=str(tmp_path / "sidebar-rail.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        show = page.get_by_role("button", name="Show navigation", exact=True)
        expect(show).to_have_attribute("aria-expanded", "false")
        expect(nav).not_to_be_visible()
        show.click()
        expect(nav).to_be_visible()
        nav.get_by_role("button", name="Status map", exact=True).focus()
        page.keyboard.press("Escape")
        expect(show).to_be_focused()
        show.click()
        nav.get_by_role("button", name="Reports", exact=True).click()
        expect(nav).not_to_be_visible()
        expect(search).to_be_visible()
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
        page.screenshot(path=str(tmp_path / "sidebar-mobile.png"), full_page=True)
        assert not errors, errors
        # A new tab starts with the role last chosen in this browser, so entering never asks again.
        listing = context.new_page()
        listing.goto(server.url + "/#view=guide")
        expect(listing.get_by_role("banner").locator("#switch-role")).to_be_visible()
        listing.get_by_role("navigation", name="Main navigation", exact=True).get_by_role("button", name="Dashboard", exact=True).click()
        expect(listing.get_by_role("heading", name="Incidents", exact=True)).to_be_visible()
        expect(listing.get_by_role("region", name="Saved incidents").get_by_role("article")).to_have_count(2)
        listing.get_by_role("button", name="Open incident", exact=True).first.click()
        expect(listing.get_by_role("dialog")).not_to_be_visible()
        expect(listing.get_by_label("Active organisation and role")).to_be_visible()
        # UI preferences do not grant access or bypass role controls.
        assert page.request.get(server.url + "/api/teams/incidents/" + first_id).status == 401
        context.close()
