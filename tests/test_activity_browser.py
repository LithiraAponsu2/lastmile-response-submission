"""Compact processing disclosure, real offline completion and SVG chevrons."""
import pytest
from e2e_server import AppServer
from browser_session import browser
from test_team_accessibility_browser import sample

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect


def test_recorded_activity_compact_reopen_and_composer(browser, tmp_path):
    with AppServer({"LASTMILE_TEAM_DEMO": "true", "LASTMILE_DATA_DIR": str(tmp_path / "activity"), "LASTMILE_MAIL": "off"}) as server:
        context, page = sample(browser, server)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.get_by_label("Ask or describe the next step", exact=True).fill("What needs attention?")
        page.get_by_role("button", name="Send message", exact=True).click()
        card = page.locator("lm-work-activity")
        expect(card.get_by_text("Assistant finished", exact=True)).to_be_visible(timeout=30000)
        assert card.locator("wa-details").evaluate("el => !el.open")
        assert card.bounding_box()["height"] <= 80
        expect(card.locator('svg[slot="expand-icon"]')).to_be_visible()
        assert card.locator('svg[slot="expand-icon"]').bounding_box()["width"] == 20
        card.get_by_text("Assistant finished", exact=True).click()
        expect(card.locator(".work-log")).to_be_visible()
        expect(card.locator(".step.completed").first).to_be_visible()
        expect(card.locator(".log-heading")).to_contain_text("Activity")
        card.screenshot(path=str(tmp_path / "activity-expanded.png"))
        # Keyboard disclosure keeps the actual saved steps available.
        card.locator("summary").focus()
        page.keyboard.press("Enter")
        expect(card.locator(".work-log")).not_to_be_visible()
        card.screenshot(path=str(tmp_path / "activity-collapsed.png"))
        before_refresh = page.locator(".chat-form").element_handle()
        page.get_by_role("button", name="Refresh", exact=True).click()
        page.wait_for_function("el => !el.isConnected", arg=before_refresh)
        before_refresh.dispose()
        expect(card.get_by_text("Assistant finished", exact=True)).to_be_visible()
        expect(card.locator(".work-log")).not_to_be_visible()
        expect(page.locator(".chat-form")).to_be_visible()
        # Refresh can replace the composer between separate visibility and bounds calls.
        # Read and validate one visible rectangle in the same browser evaluation.
        box = page.wait_for_function("""() => {
            const el = document.querySelector('.chat-form');
            if (!el) return false;
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && r.top >= 0 && r.bottom <= innerHeight + 1
                ? {y: r.y, height: r.height} : false;
        }""").json_value()
        assert box["y"] >= 0 and box["y"] + box["height"] <= 1001
        page.set_viewport_size({"width": 390, "height": 844})
        card.get_by_text("Assistant finished", exact=True).click()
        expect(card.locator(".work-log")).to_be_visible()
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
        card.screenshot(path=str(tmp_path / "activity-mobile.png"))
        # Shared dropdown and sidebar carets now use a real SVG asset.
        probe = 'el => {const s=document.createElement("select"); s.className="lm-select"; el.append(s); const icon=getComputedStyle(s,"::picker-icon").backgroundImage; s.remove(); return icon;}'
        assert "chevron-down.svg" in page.locator("body").evaluate(probe)
        assert not errors, errors
        context.close()


def test_activity_running_failure_and_interruption_visual_states(browser, tmp_path):
    with AppServer({"LASTMILE_TEAM_DEMO": "true", "LASTMILE_MAIL": "off"}) as server:
        context = browser.new_context(viewport={"width": 390, "height": 844}, reduced_motion="reduce")
        page = context.new_page()
        page.goto(server.url + "/")
        page.wait_for_load_state("networkidle")
        # Visual-only fixture, never saved as incident evidence or agent progress.
        page.evaluate("""() => {
          const fixture = document.createElement('lm-work-activity');
          fixture.id = 'activity-fixture'; fixture.style.maxWidth = '360px';
          document.querySelector('#main').prepend(fixture);
          fixture.turn = {id:'visual-fixture',status:'running',steps:[
            {label:'UI fixture: reading saved reports',status:'completed'},
            {label:'UI fixture: checking the next action',status:'running'}]};
          fixture.expanded = true;
        }""")
        card = page.locator("#activity-fixture")
        expect(card.get_by_text("Assistant is working", exact=True)).to_be_visible()
        expect(card.get_by_text("Running", exact=True)).to_be_visible()
        expect(card.locator("wa-spinner")).not_to_be_visible()
        expect(card.locator(".reduced svg")).to_be_visible()
        card.screenshot(path=str(tmp_path / "activity-running-fixture.png"))
        for status in ["failed", "interrupted"]:
            card.evaluate("(el,status) => {el.turn={...el.turn,status};}", status)
            expect(card.get_by_text("Request needs attention", exact=True)).to_be_visible()
            expect(card.get_by_text("Your saved records remain available.", exact=False)).to_be_visible()
            expect(card.get_by_text("Running", exact=True)).not_to_be_visible()
        card.screenshot(path=str(tmp_path / "activity-interrupted-fixture.png"))
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
        context.close()


def test_legacy_missing_reply_keeps_processing_with_its_own_turn(browser, tmp_path):
    with AppServer({"LASTMILE_TEAM_DEMO": "true", "LASTMILE_MAIL": "off"}) as server:
        context,page=sample(browser,server)
        # Explicit UI-only legacy fixture: a crashed turn has no assistant reply.
        # The intercepted view is never persisted as evidence or real AI activity.
        def legacy(route):
            response=route.fetch();doc=response.json()
            doc['coordination']['chat']={
                'messages':[
                    {'id':'fixture-user-1','role':'user','text':'UI fixture: interrupted old request'},
                    {'id':'fixture-user-2','role':'user','text':'UI fixture: second request'},
                    {'id':'fixture-reply-2','role':'assistant','text':'UI fixture: second response'}],
                'turns':[{'id':'fixture-turn-1','status':'interrupted','steps':[]},
                         {'id':'fixture-turn-2','status':'completed','steps':[]}], 'proposals':[]}
            route.fulfill(response=response,json=doc)
        page.route('**/api/teams/incidents/incident_*',legacy)
        page.get_by_role('button',name='Refresh',exact=True).click()
        expect(page.get_by_text('UI fixture: second response',exact=True)).to_be_visible()
        assert page.locator('[data-activity-turn="fixture-turn-1"]').evaluate("el=>el.previousElementSibling.dataset.message==='fixture-user-1'&&el.nextElementSibling.dataset.message==='fixture-user-2'")
        assert page.locator('[data-activity-turn="fixture-turn-2"]').evaluate("el=>el.nextElementSibling.dataset.message==='fixture-reply-2'")
        context.close()
