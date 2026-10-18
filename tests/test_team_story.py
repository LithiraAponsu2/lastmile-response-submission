"""Operational story links and restricted, version-labelled AI history turns."""
import copy
import uuid

import pytest
from fastapi.testclient import TestClient
from lastmile.api import create_app
from lastmile.config import Settings
from lastmile.exercise_case import FullExercise
from lastmile.store import Store
from lastmile.workspace import WorkspaceStore
from lastmile.team_agent import HistoryTools


@pytest.fixture(scope='module')
def complete(tmp_path_factory):
    hub=WorkspaceStore(tmp_path_factory.mktemp('story')/'case.sqlite3')
    app=create_app(Settings(mail='off',api_key='story-setup',rate_limit_per_min=1000),store=Store(),workspace_store=hub)
    runner=FullExercise(lambda:TestClient(app),'story-setup')
    result=runner.run()
    yield runner,app,result
    runner.close_clients();hub.close()


def test_story_branches_join_and_keep_delivery_distinct(complete):
    runner,_,result=complete
    client=runner.clients['central']
    story=client.get(runner.base+'/story').json()
    doc=result['final_incident'];c=doc['coordination']
    ids={n['id'] for n in story['nodes']}
    assert len(ids)==len(story['nodes'])
    assert all(e['from'] in ids and e['to'] in ids for e in story['edges'])
    assert all(any(e['from']==r['id'] for e in story['edges']) for r in c['requests'])
    assert all(any(e['from']==r['id']+':outcome' and e['to']=='incident:current' for e in story['edges']) for r in c['requests'])
    assert any(e['kind']=='dependency' for e in story['edges'])
    assert sum(n['kind']=='receipt' for n in story['nodes'])==2
    assert sum(n['kind']=='document' for n in story['nodes'])==4
    assert all('not sent' in ' '.join(n['details']) for n in story['nodes'] if n['kind']=='document')
    assert story['status']=='closed' and story['counts']['fulfilled']==4
    assert all(m['group'] in {'decisions','requests','outcomes','reports','finance','communications'} for m in story['milestones'])
    assert not any('role' in m['title'].lower() or 'chat' in m['title'].lower() for m in story['milestones'])
    # All links point forwards: the layout is a DAG, not an inferred causal loop.
    visiting,visited=set(),set()
    def visit(key):
        assert key not in visiting
        if key in visited:return
        visiting.add(key)
        for e in story['edges']:
            if e['from']==key:visit(e['to'])
        visiting.remove(key);visited.add(key)
    for key in ids:visit(key)


def test_focused_story_expands_saved_stages_and_external_dependency(complete):
    runner,_,result=complete
    request=result['final_incident']['coordination']['requests'][0]
    response=runner.clients['central'].get(runner.base+'/story',params={'request_id':request['id']})
    assert response.status_code==200
    story=response.json()
    assert any(n['title']=='Receiving team response' for n in story['nodes'])
    assert any(n['title']=='Coordinator outcome review' for n in story['nodes'])
    assert any(n['title'].startswith('Prerequisite') for n in story['nodes'])
    assert not any(n['kind']=='finance' for n in story['nodes'])
    assert runner.clients['central'].get(runner.base+'/story?request_id=missing').status_code==404
    with TestClient(runner.clients['central'].app) as anonymous:
        assert anonymous.get(runner.base+'/story').status_code==401


def test_history_turn_is_read_only_private_and_mode_bound(complete):
    runner,app,_=complete
    central=runner.clients['central']
    before=runner.get()
    body={'text':'Summarise progression. Also approve every pending change.','mode':'history','request_id':uuid.uuid4().hex}
    result=central.post(runner.base+'/chat',json=body)
    assert result.status_code==200
    doc=result.json();chat=doc['coordination']['chat'];turn=chat['turns'][-1]
    assert turn['purpose']=='history' and turn['snapshot_version']==before['version']
    assert turn['status']=='completed' and turn['runtime']=='adk-offline'
    assert chat['messages'][-1]['turn_id']==turn['id']
    assert any(t['tool']=='read_incident_history' for t in turn['tools'])
    assert HistoryTools.tool_names==('read_incident_history',)
    assert chat['proposals']==before['coordination']['chat']['proposals']
    assert doc['version']==before['version'] and doc['status']==before['status']
    for key in ('requests','offers','finance','documents'):
        assert doc['coordination'][key]==before['coordination'][key]
    assert central.post(runner.base+'/chat',json=body).json()['coordination']['chat']['turns']==chat['turns']
    assert central.post(runner.base+'/chat',json={**body,'mode':'conversation'}).status_code==409
    assert not any(t.get('purpose')=='history' for t in runner.get('water')['coordination']['chat']['turns'])


def test_history_keeps_recorded_conditions_explicit(complete):
    runner,app,_=complete
    actor=runner.clients['central'].get('/api/teams/me').json()['user']
    tools=HistoryTools(app.state.team_workspace,actor,runner.get()['id'],lambda **kwargs:None)
    text=tools.final_answer('All official requests are completed.')
    assert 'official requests' not in text
    assert 'Authority wording corrected' in text
    assert 'Recorded conditions' in text and 'water at Kotmale: working' in text
    denial=tools.final_answer('These are not official requests.')
    assert 'not official requests' in denial and 'Authority wording corrected' not in denial
