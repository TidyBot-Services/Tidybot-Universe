"""Paid-free lifecycle regression; only temporary ledgers and controlled workers."""
import copy
import json
import os
from pathlib import Path

import pytest

from benchmarks.attention_harness import dev_main_bridge as main
from benchmarks.attention_harness.dev_loop import process_identity
from benchmarks.attention_harness.dev_planner import PlannerControl, binding
from benchmarks.attention_harness.core.dev_store import DevConflict
from openarm365_dev_v2.tests.test_main_integration import make_bridge
from openarm365_dev_v2.tests.test_planner_features import add_version, SOURCE, SENSOR


def view(b,e,index=0):
    return b.get(f"/api/dev-v2/experiments/{e['experiment_id']}/cells/{e['cells'][index]['key']}/view")[0]


def own(b,e,index=0):
    c=e['cells'][index];b.store.update_cell(c['key'],state='running',owner=process_identity())
    with b.store.tx() as db:
        row=b.store.get(db,'experiments',e['experiment_id']);row.update(state='running',owner=process_identity());b.store.put(db,'experiments',e['experiment_id'],row)
    return b.store.cell(c['key'])


def reopen(b):
    return main.DevMainBridge(b.store.path,b.artifacts,b.goal_key,b.memory)


def test_prepared_zero_work_is_persistent_read_only_and_has_original_start(tmp_path,monkeypatch):
    b,body=make_bridge(tmp_path);e=b.create(body)
    def forbidden(*a,**kw):raise AssertionError('view attempted worker/clock')
    monkeypatch.setattr(main.subprocess,'Popen',forbidden)
    monkeypatch.setattr(type(b.store),'start_cell_clock',forbidden)
    monkeypatch.setattr(type(b.store),'claim',forbidden)
    before=b.store.export_ledger(b.goal_id)
    for bridge in (b,reopen(b)):
        for index in (0,1):
            row=view(bridge,e,index);l=row['lifecycle']
            assert l['phase']=='prepared' and l['can_start'] and not l['can_stop']
            assert not l['can_create_new']  # This controlled goal is fully allocated.
            assert l['experiment_id']==e['experiment_id'] and l['scope_count']==2
            assert {r['task_id'] for r in l['scope']}=={c['task_id'] for c in e['cells']}
            assert not row['jobs'] and not row['versions']
        assert {r['lifecycle']['phase'] for r in bridge.list()}=={'prepared'}
    assert b.store.export_ledger(b.goal_id)==before
    assert b.store.goal(b.goal_id)['real_started_at'] is None


def test_control_start_spawns_same_experiment_all_cells_only_on_explicit_post(tmp_path,monkeypatch):
    b,body=make_bridge(tmp_path);e=b.create(body);calls=[]
    class Worker:
        pid=os.getpid()
        def poll(self):return None
    def controlled(argv,**kw):calls.append((argv,kw));return Worker()
    monkeypatch.setattr(main.subprocess,'Popen',controlled)
    view(b,e);b.list();assert calls==[]
    result=b.post(f"/api/dev-v2/experiments/{e['experiment_id']}/run",{})
    assert result['experiment_id']==e['experiment_id'] and len(calls)==1
    argv=calls[0][0];assert argv[argv.index('--experiment')+1]==e['experiment_id']
    assert argv[argv.index('--max-cells')+1]=='2'
    # Worker handoff window is queued, even with zero jobs and a null clock.
    for bridge in (b,reopen(b)):
        l=view(bridge,e)['lifecycle'];assert l['phase']=='queued' and not l['can_start'] and l['can_stop']
    with pytest.raises(DevConflict):b.post(f"/api/dev-v2/experiments/{e['experiment_id']}/run",{})
    assert len(calls)==1
    assert len(b.experiments())==1 and b.store.goal(b.goal_id)['real_started_at'] is None
    assert not any(b.store.goal(b.goal_id)['used'].values())


@pytest.mark.parametrize('kind,phase',[('model','model'),('advisor','advisor'),('execute','sdk')])
def test_claimed_owned_jobs_have_distinct_phases_and_next_cell_is_queued(tmp_path,kind,phase):
    b,body=make_bridge(tmp_path);e=b.create(body);c=own(b,e)
    if kind=='execute':
        root=tmp_path/'sensor-cell';root.mkdir()
        cfg=json.loads(Path(e['manifest']['uri']).read_bytes())['configs'][f"{c['task_id']}:{c['seed']}"]
        from benchmarks.attention_harness.dev_v2_loop import immutable
        immutable(root/'config.json',cfg);b.store.update_cell(c['key'],artifact_dir=str(root))
        add_version(b.store,c,root,source=SENSOR)
    j=b.store.reserve(c['key'],1,kind,{'dev_calls':0});b.store.claim(j['job_id'],process_identity())
    for bridge in (b,reopen(b)):
        assert view(bridge,e)['lifecycle']['phase']==phase
        queued=view(bridge,e,1)['lifecycle']
        assert queued['phase']=='queued' and not queued['can_start']
    b.store.update_cell(c['key'],owner=None)
    assert view(b,e)['lifecycle']['phase']=='review'  # No fabricated live SDK/model claim.


def test_waiting_for_approval_is_not_model_or_sdk_and_control_still_cannot_approve(tmp_path):
    b,body=make_bridge(tmp_path);e=b.create(body);c=own(b,e)
    root=tmp_path/'cell';root.mkdir()
    cfg=json.loads(Path(e['manifest']['uri']).read_bytes())['configs'][f"{c['task_id']}:{c['seed']}"]
    from benchmarks.attention_harness.dev_v2_loop import immutable
    immutable(root/'config.json',cfg);b.store.update_cell(c['key'],artifact_dir=str(root))
    add_version(b.store,c,root)
    request=PlannerControl(b.store).request(c['key'],1,'approval',binding(c['key'],1,SOURCE,cfg),60)
    for bridge in (b,reopen(b)):
        row=view(bridge,e);assert row['lifecycle']['phase']=='approval'
        assert row['planner']['requests'][0]['request_id']==request['request_id']
    with pytest.raises(PermissionError):
        b.post(f"/api/dev-v2/experiments/{e['experiment_id']}/approvals/{request['request_id']}/decision",{},human_authorized=False)
    assert not b.store.cell(c['key'])['jobs']


def test_next_round_without_live_worker_is_queued_and_can_continue_same_plan(tmp_path):
    b,body=make_bridge(tmp_path);e=b.create(body)
    with b.store.tx() as db:
        g=b.store.get(db,'goals',b.goal_id);g['real_started_at']=__import__('time').time();b.store.put(db,'goals',b.goal_id,g)
    for bridge in (b,reopen(b)):
        row=view(bridge,e);assert row['state']=='planned'
        assert row['lifecycle']['phase']=='queued' and row['lifecycle']['can_start']
        assert row['lifecycle']['start_label']=='Continue experiment'


@pytest.mark.parametrize('state',['completed','cancelled','failed','condition_invalid','not_started'])
def test_terminal_cannot_be_started_or_stopped_as_if_running(tmp_path,state):
    b,body=make_bridge(tmp_path);e=b.create(body)
    for c in e['cells']:b.store.update_cell(c['key'],state=state)
    with b.store.tx() as db:
        row=b.store.get(db,'experiments',e['experiment_id']);row['state']=state;b.store.put(db,'experiments',e['experiment_id'],row)
    for bridge in (b,reopen(b)):
        l=view(bridge,e)['lifecycle'];assert l['phase']=='terminal' and not l['can_start'] and not l['can_stop']
    with pytest.raises(DevConflict):b.start(e['experiment_id'])


def test_exhausted_original_goal_disables_start_without_renewal(tmp_path):
    b,body=make_bridge(tmp_path);e=b.create(body)
    with b.store.tx() as db:
        g=b.store.get(db,'goals',b.goal_id);g['authorized_until']=1;b.store.put(db,'goals',b.goal_id,g)
    before=b.store.goal(b.goal_id)
    assert not view(b,e)['lifecycle']['can_start']
    with pytest.raises(DevConflict):b.start(e['experiment_id'])
    assert b.store.goal(b.goal_id)==before


def test_export_ui_fixtures_from_persisted_reopened_ledgers(tmp_path):
    """Optional artifact consumed by the actual JS renderer regression."""
    fixtures={}
    for phase in ('prepared','queued','model','advisor','sdk','approval','preparing','review','terminal'):
        directory=tmp_path/phase;directory.mkdir()
        b,body=make_bridge(directory);e=b.create(body);c=e['cells'][0]
        if phase in {'model','advisor','sdk','approval','preparing'}:c=own(b,e)
        if phase in {'model','advisor','sdk'}:
            kind={'sdk':'execute'}.get(phase,phase)
            if phase=='sdk':
                root=directory/'cell';root.mkdir()
                cfg=json.loads(Path(e['manifest']['uri']).read_bytes())['configs'][f"{c['task_id']}:{c['seed']}"]
                from benchmarks.attention_harness.dev_v2_loop import immutable
                immutable(root/'config.json',cfg);b.store.update_cell(c['key'],artifact_dir=str(root));add_version(b.store,c,root,source=SENSOR)
            j=b.store.reserve(c['key'],1,kind,{'dev_calls':0});b.store.claim(j['job_id'],process_identity())
        if phase=='approval':
            cfg=json.loads(Path(e['manifest']['uri']).read_bytes())['configs'][f"{c['task_id']}:{c['seed']}"]
            root=directory/'cell';root.mkdir()
            from benchmarks.attention_harness.dev_v2_loop import immutable
            immutable(root/'config.json',cfg);b.store.update_cell(c['key'],artifact_dir=str(root));add_version(b.store,c,root)
            PlannerControl(b.store).request(c['key'],1,'approval',binding(c['key'],1,SOURCE,cfg),60)
        if phase=='queued':
            with b.store.tx() as db:
                row=b.store.get(db,'goals',b.goal_id);row['real_started_at']=__import__('time').time();b.store.put(db,'goals',b.goal_id,row)
        if phase in {'review','terminal'}:b.store.update_cell(c['key'],state='needs_review' if phase=='review' else 'completed')
        old=view(b,e);fresh=view(reopen(b),e)
        assert fresh['lifecycle']==old['lifecycle'] and fresh['lifecycle']['phase']==phase
        fixtures[phase]={'session':b.list()[0],'before':old,'refresh':fresh}
    directory=tmp_path/'spare';directory.mkdir();b,body=make_bridge(directory)
    body['selection']['tasks']=body['selection']['tasks'][:1];e=b.create(body)
    fixtures['prepared_with_capacity']={'session':b.list()[0],'before':view(b,e),'refresh':view(reopen(b),e)}
    target=Path(os.environ.get('LIFECYCLE_FIXTURE_OUT',str(tmp_path/'fixtures.json')))
    target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(fixtures,indent=2)+'\n')


def test_prepared_with_free_allocation_can_create_new_without_being_running(tmp_path):
    b,body=make_bridge(tmp_path);body['selection']['tasks']=body['selection']['tasks'][:1];e=b.create(body)
    l=view(b,e)['lifecycle'];assert l['phase']=='prepared' and l['can_create_new'] and l['can_start'] and not l['can_stop']


def test_next_layer_planned_with_settled_history_is_never_projected_as_running(tmp_path):
    b,body=make_bridge(tmp_path);e=b.create(body);c=own(b,e)
    j=b.store.reserve(c['key'],1,'model',{'dev_calls':0});b.store.claim(j['job_id'],process_identity())
    b.store.settle(j['job_id'],{'dev_calls':0},{'controlled_history_only':True})
    b.store.update_cell(c['key'],state='planned',owner=None)
    with b.store.tx() as db:
        row=b.store.get(db,'experiments',e['experiment_id']);row.update(state='planned',owner=None);b.store.put(db,'experiments',e['experiment_id'],row)
    for bridge in (b,reopen(b)):
        l=view(bridge,e)['lifecycle'];assert l['phase']=='queued' and l['can_start'] and not l['can_stop']
    assert b.store.cell(c['key'])['jobs'][0]['state']=='settled'


def test_http_handler_control_start_permission_and_refresh_without_transport(tmp_path,monkeypatch):
    """Real Handler dispatch, fake socket factory and worker; no listening UI."""
    import io
    import re
    from types import SimpleNamespace
    from benchmarks.attention_harness import ui_server
    from benchmarks.attention_harness.core.store import AttentionStore
    b,body=make_bridge(tmp_path);e=b.create(body);calls=[]
    monkeypatch.setattr(ui_server,'ThreadingHTTPServer',lambda address,handler:SimpleNamespace(handler=handler))
    server=ui_server.create_server(store=AttentionStore(tmp_path/'ui.sqlite'),artifact_root=tmp_path,port=0,dev_v2_bridge=b)
    def call(path,payload=None,token=None):
        h=server.handler.__new__(server.handler);h.path=path;h.headers={};h.wfile=io.BytesIO()
        h.send_response=lambda status:setattr(h,'result_status',status);h.send_header=lambda *args:None;h.end_headers=lambda:None
        if token:h.headers['X-Attention-Token']=token
        if payload is not None:
            data=json.dumps(payload).encode();h.rfile=io.BytesIO(data);h.headers.update({'Content-Type':'application/json','Content-Length':str(len(data))});h.do_POST()
        else:h.do_GET()
        return h.result_status,h.wfile.getvalue()
    status,page=call('/ui/');assert status==200
    token=re.search(rb'data-control-token="([^"]+)"',page)[1].decode()
    base=f"/api/dev-v2/experiments/{e['experiment_id']}"
    for _ in range(2):
        row=json.loads(call(f"{base}/cells/{e['cells'][0]['key']}/view")[1]);assert row['lifecycle']['phase']=='prepared'
    assert call(base+'/run',{})[0]==403 and not calls
    class Worker:
        pid=os.getpid()
        def poll(self):return None
    def spawn(argv,**kw):calls.append(argv);return Worker()
    monkeypatch.setattr(main.subprocess,'Popen',spawn)
    assert call(base+'/run',{},token)[0]==200 and len(calls)==1
    assert calls[0][calls[0].index('--experiment')+1]==e['experiment_id']
    assert call(base+'/run',{},token)[0]==409 and len(calls)==1
    assert len(b.experiments())==1 and b.store.goal(b.goal_id)['real_started_at'] is None
