"""Saved branch/merge workflow, accessible inspection and process-before-reply."""
import pytest
from e2e_server import AppServer
from browser_session import browser
from test_full_case_browser import login
from ui_views import show
from lastmile.exercise_case import FullExercise

pw=pytest.importorskip('playwright.sync_api')
expect=pw.expect


def test_saved_flow_focus_hover_summary_and_mobile(browser,tmp_path):
    with AppServer({'LASTMILE_DATA_DIR':str(tmp_path/'case'),'LASTMILE_API_KEY':'story-browser','LASTMILE_MAIL':'off'}) as server:
        runner=FullExercise(server.client,'story-browser')
        runner.run()
        context=browser.new_context(viewport={'width':1440,'height':1000},reduced_motion='reduce')
        page=context.new_page();errors=[];requests=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('request',lambda r:requests.append(r.url))
        login(page,server.url,'case-central',runner.password)
        assert not any('lastmile-story.js' in url for url in requests)
        show(page, 'Activity')
        flow=page.locator('lm-incident-flow')
        expect(flow.get_by_role('button',name='Fit workflow')).to_be_visible()
        assert any('lastmile-story.js' in url for url in requests)
        assert flow.locator('.edge').count()>10
        expect(page.get_by_text('Demo role changes',exact=False)).to_have_count(0)
        expect(page.get_by_role('list',name='Operational milestones')).to_be_visible()
        page.screenshot(path=str(tmp_path/'story-overview.png'))
        request=runner.get()['coordination']['requests'][0]
        page.get_by_label('Workflow focus',exact=True).select_option(request['id'])
        point=flow.get_by_role('button',name='Receiving team response accepted',exact=True)
        expect(point).to_be_visible()
        point.hover()
        expect(flow.locator('wa-tooltip[open]')).to_have_count(1)
        expect(flow.get_by_role('region',name='Workflow point details')).to_be_visible()
        expect(flow.locator('.inspector')).to_contain_text('Receiving team response')
        page.keyboard.press('Escape')
        point.focus()
        expect(point).to_be_focused()
        page.keyboard.press('Enter')
        expect(flow.locator('.inspector')).to_contain_text('accepted')
        page.screenshot(path=str(tmp_path/'story-request.png'))
        page.get_by_label('Workflow focus',exact=True).select_option('')
        page.get_by_role('button',name='Summarise progression',exact=True).click()
        page.get_by_label('AI progression summary').locator('summary').click(timeout=30000)
        expect(page.get_by_label('AI progression summary')).to_contain_text('4 requests',timeout=30000)
        expect(page.get_by_label('AI progression summary')).to_contain_text('snapshot version')
        # Every saved turn's processing disclosure precedes its corresponding reply.
        cards=page.locator('#chat-log > lm-work-activity')
        assert cards.count()==5
        assert cards.evaluate_all("els=>els.every(el=>el.nextElementSibling?.matches('.bubble.assistant'))")
        page.screenshot(path=str(tmp_path/'story-summary.png'))
        page.set_viewport_size({'width':390,'height':844})
        page.get_by_role('button',name='Live data',exact=True).click()
        expect(page.get_by_label('Workflow focus',exact=True)).to_be_visible()
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
        page.screenshot(path=str(tmp_path/'story-mobile.png'))
        # Graph record activation returns to the actual entity, not a duplicate.
        page.set_viewport_size({'width':1440,'height':1000})
        node=flow.locator('button.node.request').first
        node.click()
        flow.get_by_role('button',name='Open record',exact=True).click()
        # Older evidence-based requests open their own view.
        expect(page.get_by_role('tab',name='Needs & assignments',exact=True)).to_have_attribute('aria-selected','true')
        assert runner.get()['status']=='closed'
        assert not errors,errors
        context.close();runner.close_clients()
