"""Read-only incident progression, projected from saved operational records.

Graph edges express recorded relationships, never an LLM's inferred causality.
Access/session/chat events remain in the audit store, outside this projection.
"""
from .team_accounts import ORGANISATIONS
import re

NAMES = {o['id']: o['name'] for o in ORGANISATIONS}


def _title(text):
    return re.sub(r'^Fictional\s+', '', re.sub(r'\s*·\s*fictional\b', '', text, flags=re.I), flags=re.I)


def _actor(text):
    for org, name in NAMES.items():
        prefix='demo_'+org+'_'
        if text.startswith(prefix): return name+' · '+text[len(prefix):].replace('_',' ')
    return text


def build_story(doc, request_id=None):
    c = doc['coordination']
    sources = {s['id']: s for s in doc['sources']}
    requests = c.get('requests', [])
    if request_id:
        requests = [r for r in requests if r['id'] == request_id]
    nodes, edges, milestones = [], [], []
    seen = set()

    def node(key, title, kind, state, organisation='', details=None, record_id=None, tab=None):
        if key not in seen:
            seen.add(key)
            nodes.append(dict(id=key, title=_title(title), kind=kind, status=state, organisation=organisation,
                              organisation_name=NAMES.get(organisation, organisation), details=details or [], record_id=record_id, tab=tab))
        return key

    def edge(start, end, label, kind='sequence'):
        if start and end and start != end and not any(e['from'] == start and e['to'] == end and e['kind'] == kind for e in edges):
            edges.append({'from': start, 'to': end, 'label': label, 'kind': kind})

    def milestone(key, title, group, at=None, organisation='', detail='', record_id=None, tab=None, time_label='Recorded'):
        milestones.append(dict(id=key, title=_title(title), group=group, at=at, organisation=organisation,
                               organisation_name=NAMES.get(organisation, organisation), detail=detail, record_id=record_id, tab=tab, time_label=time_label))

    opened = node('incident:start', 'Incident opened', 'incident', 'recorded', details=[doc['title'], c['district']])
    opening = next((e for e in doc.get('events', []) if e['action'] == 'Coordination exercise opened'), {})
    milestone('opened', 'Incident opened', 'decisions', opening.get('at', doc.get('created_at')), detail=c['district'])
    # Group report traffic by contributing body, retaining exact source links in details.
    by_body = {}
    relevant_sources = set()
    if request_id:
        relevant_sources.update(r['source_id'] for r in requests)
        for o in c.get('offers', []):
            if o['request_id'] == request_id:
                relevant_sources.add(o.get('outcome_source_id'))
                a = next((a for a in c['assets'] if a['id'] == o.get('asset_id')), None)
                if a: relevant_sources.update(f['source_id'] for f in a['facts'].values())
        relevant_sources.update(r['source_id'] for r in c.get('receipts', []) if r['request_id'] == request_id)
    for s in sources.values():
        if request_id and s['id'] not in relevant_sources:
            continue
        by_body.setdefault(s['organisation'], []).append(s)
    for org, reports in by_body.items():
        key = node('body:'+org, NAMES.get(org, org), 'reports', 'accepted' if all(s['status']=='accepted' for s in reports) else 'reported', org,
                   [f"{len(reports)} reports · {sum(s['status'] == 'accepted' for s in reports)} accepted"] + [s['title'] for s in reports], reports[-1]['id'], 'sources')
        edge(opened, key, 'Contributes')
        milestone('reports:'+org, f"{len(reports)} reports from {NAMES.get(org, org)}", 'reports', min(s['received_at'] for s in reports), org,
                  f"{sum(s['status'] == 'accepted' for s in reports)} accepted; originals and review provenance are retained.", reports[-1]['id'], 'sources', 'First received')
    end = node('incident:current', 'Incident closed' if doc['status'] == 'closed' else 'Current situation', 'incident', doc['status'],
               details=[f"Phase: {c.get('phase', 'response')}", f"{len(c.get('closure_blockers', []))} recorded work blockers", 'Work completion does not establish an all-clear.'])
    offer_keys = {}
    for r in requests:
        s = sources.get(r['source_id'], {})
        key = node(r['id'], r['title'], 'request', r['status'], r['organisation'],
                   [f"Requested: {r['quantity'] if r['quantity'] is not None else 'Unknown'} {r['unit']}", f"Remaining: {r.get('remaining', 'Unknown')} {r['unit']}", 'Evidence: '+s.get('title', 'Not available')], r['id'], 'requests')
        edge('body:'+s.get('organisation', ''), key, 'Reports need')
        milestone(r['id'], r['title'], 'requests', s.get('received_at'), r['organisation'],
                  f"{r['status'].replace('_', ' ')} · {r['quantity'] if r['quantity'] is not None else 'Unknown'} {r['unit']}", r['id'], 'requests', 'Supporting evidence received')
        outcome = node(r['id']+':outcome', 'Need fulfilled' if r['status'] == 'fulfilled' else 'Need '+r['status'].replace('_', ' '), 'outcome', r['status'], r['organisation'],
                       [f"Fulfilled: {r.get('fulfilled', 0)} {r['unit']}", f"Remaining: {r.get('remaining', 'Unknown')} {r['unit']}"], r['id'], 'requests')
        branches = []
        for o in (o for o in c.get('offers', []) if o['request_id'] == r['id']):
            title = NAMES.get(o['recipient'], o['recipient'])
            detail = [f"{o['quantity']} {r['unit']}", o.get('note', ''), f"Offered: {o.get('created_at', 'Time not recorded')}", f"Accepted/responded: {o.get('responded_at', 'Not recorded')}", f"Reviewed complete: {o.get('completed_at', 'Not recorded')}"]
            ok = node(o['id'], title, 'assignment', o['status'], o['recipient'], detail, o['id'], 'requests')
            offer_keys[o['id']] = ok
            edge(key, ok, 'Assignment offered')
            milestone(o['id']+':offered', 'Assignment offered · '+r['title'], 'requests', o.get('created_at'), o['recipient'], o.get('note',''), o['id'], 'requests')
            if o.get('responded_at'):
                milestone(o['id']+':accepted', 'Receiving team responded · '+r['title'], 'requests', o['responded_at'], o['recipient'], o.get('response_note',o['status']), o['id'],'requests')
            last = ok
            if request_id:
                # Detail view expands saved stages; absence of a timestamp is never invented.
                for suffix, caption, at, state in [('response','Receiving team response',o.get('responded_at'),'accepted' if o['status'] in {'accepted','mobilised','reported_complete','completed'} else o['status']),
                                                   ('progress','Team progress',o.get('progress_at'),o['status']),
                                                   ('review','Coordinator outcome review',o.get('completed_at'),'completed' if o.get('completed_at') else 'pending')]:
                    if at:
                        step = node(o['id']+':'+suffix,caption,'outcome',state,o['recipient'],[at, o.get('response_note','')] if suffix=='response' else [at, sources.get(o.get('outcome_source_id'),{}).get('title','')],o['id'],'requests')
                        edge(last,step,caption); last=step
            if o.get('outcome_source_id'):
                evidence = sources.get(o['outcome_source_id'], {})
                detail.append('Outcome evidence: '+evidence.get('title','Not available'))
            edge(last,outcome,'Reviewed outcome' if o['status']=='completed' else 'Awaiting outcome', 'sequence' if o['status']=='completed' else 'pending')
            branches.append(ok)
            if o.get('completed_at'):
                milestone(o['id']+':reviewed','Outcome reviewed · '+r['title'],'outcomes',o['completed_at'],o['recipient'],sources.get(o.get('outcome_source_id'),{}).get('title',''),o['id'],'requests')
        for receipt in (x for x in c.get('receipts',[]) if x['request_id']==r['id']):
            rk=node(receipt['id'],f"{receipt['quantity']} {receipt['unit']} received",'receipt','completed',r['organisation'],[sources.get(receipt['source_id'],{}).get('title',''),receipt.get('quote','')],r['id'],'requests')
            edge(key,rk,'Partial receipt'); edge(rk,outcome,'Confirmed receipt');branches.append(rk)
            milestone(receipt['id'],f"Received {receipt['quantity']} {receipt['unit']}",'outcomes',receipt.get('at'),r['organisation'],receipt.get('quote',''),r['id'],'requests')
        if not branches: edge(key,outcome,'No receiving outcome yet','pending')
        edge(outcome,end,'Outcome recorded' if r['status']=='fulfilled' else 'Still open','sequence' if r['status']=='fulfilled' else 'pending')
    for o in c.get('offers',[]):
        for dependency in o.get('depends_on',[]):
            if o['id'] in offer_keys:
                prior = next((p for p in c.get('offers',[]) if p['id']==dependency),None)
                if prior:
                    if dependency not in offer_keys:
                        offer_keys[dependency]=node(dependency,'Prerequisite · '+NAMES.get(prior['recipient'],prior['recipient']),'assignment',prior['status'],prior['recipient'],[prior.get('note',''),prior.get('completed_at','Outcome not yet reviewed')],prior['id'],'requests')
                    edge(offer_keys[dependency],offer_keys[o['id']],'Required outcome','dependency')
    if not request_id:
        for f in c.get('finance',[]):
            key=node(f['id'],f['purpose'],'finance',f['status'],f['organisation'],[f"{k.title()}: LKR {f.get(k) if f.get(k) is not None else 'Unknown'}" for k in ('requested','approved','committed','released','spent')],f['id'],'finance')
            edge('body:'+sources.get(f['source_id'],{}).get('organisation',''),key,'Requests funding')
            if f.get('approved_at'): milestone(f['id']+':approved','Funding approved · '+f['purpose'],'finance',f['approved_at'],detail='LKR '+f['approved'],record_id=f['id'],tab='finance')
            for i,e in enumerate(f.get('evidence',[])):
                milestone(f['id']+':'+str(i),f"Funding {e['state']} · LKR {e['amount']}",'finance',e.get('at'),detail=sources.get(e['source_id'],{}).get('title',''),record_id=f['id'],tab='finance')
            last=key
            if f.get('approved_at'):
                scheme=next((s for s in c.get('schemes',[]) if s['id']==f['scheme_id']),{})
                approval=node(f['id']+':approval','Budget approval','decision','approved',scheme.get('owner',''),['LKR '+f['approved'],f['approved_at'],'A separate scheme delegate approved this funding.'],f['id'],'finance')
                edge(key,approval,'Separate approval');last=approval
            for i,e in enumerate(f.get('evidence',[])):
                evidence=sources.get(e['source_id'],{})
                ek=node(f['id']+':evidence:'+str(i),f"Funding {e['state']}",'finance_evidence','recorded',evidence.get('organisation',''),['LKR '+e['amount'],evidence.get('title',''),e.get('at','')],f['id'],'finance')
                edge(last,ek,'Supporting voucher');last=ek
            if f.get('reconciliation'):
                rk=node(f['id']+':reconciled','Funding reconciled','outcome',f['status'],f['organisation'],['Balance LKR '+f['reconciliation']['balance']],f['id'],'finance')
                edge(last,rk,'Reconciliation');last=rk
            edge(last,end,'Reconciled' if f['status']=='reconciled' else 'Funding outstanding','sequence' if f['status']=='reconciled' else 'pending')
        for d in c.get('documents',[]):
            key=node(d['id'],d['kind'].replace('_',' ').title(),'document',d['status'],d['issuer'],[d['audience'],'Delivery: '+('not sent' if d['delivery']=='not_sent_exercise_artifact' else d['delivery'].replace('_',' ')),f"Snapshot version: {d['incident_version']}",f"Approved by: {_actor(d.get('approved_by','Not approved'))}",f"Received by: {_actor(d.get('accepted_by','Not received'))}"],d['id'],'documents')
            edge(opened,key,'Snapshot prepared')
            edge(key,end,'Handover received' if d['status']=='handover_accepted' else 'Document recorded','sequence')
            if d.get('approved_at'): milestone(d['id']+':approved',d['kind'].replace('_',' ').title()+' approved','communications',d['approved_at'],d['issuer'],'Approval does not establish delivery.',d['id'],'documents')
            if d.get('accepted_at'): milestone(d['id']+':received','Handover received','decisions',d['accepted_at'],detail=d['audience'],record_id=d['id'],tab='documents')
        for x in c.get('claims',[]):
            if x.get('resolved_at'): milestone(x['id'],'Conflicting reports resolved','decisions',x['resolved_at'],x['organisation'],x.get('resolution',''),x['source_id'],'sources')
        for i,e in enumerate(c.get('lifecycle',[])):
            if e['action'] not in {'phase','close','reopen'}: continue
            milestone('lifecycle:'+str(i),{'phase':'Phase changed','close':'Incident closed','reopen':'Incident reopened'}.get(e['action'],e['action'].replace('_',' ').title()),'decisions',e.get('at'),detail=e.get('reason',''))
    for n in nodes:
        if n['kind']=='reports' and not any(e['from']==n['id'] for e in edges):
            edge(n['id'],end,'Shared evidence')
    edges=[e for e in edges if e['from'] in seen and e['to'] in seen]
    milestones.sort(key=lambda m:m['at'] or '9999')
    return {'incident_id':doc['id'],'version':doc['version'],'status':doc['status'],'phase':c.get('phase','response'),
            'nodes':nodes,'edges':edges,'milestones':milestones,'requests':[{'id':r['id'],'title':r['title']} for r in c['requests']],
            'conditions':[{k:x[k] for k in ('topic','location','field','value','unit','status','observed_at','source_id') if k in x} for x in c.get('claims',[]) if x['status']!='superseded'],
            'work_blockers':c.get('closure_blockers',[]),
            'resource_readiness':[{'name':a['name'],'owner':a['owner'],'balance':a['balance']} for a in c.get('assets',[])],
            'counts':{'reports':len(sources),'requests':len(c['requests']),'fulfilled':sum(r['status']=='fulfilled' for r in c['requests']),'work_blockers':len(c.get('closure_blockers',[]))},
            'notice':'Connections reflect saved record links. Pending links are dashed. Work completion does not establish an all-clear.'}
