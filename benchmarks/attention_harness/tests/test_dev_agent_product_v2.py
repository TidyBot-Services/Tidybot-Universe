"""P3 product gates, seals, documentation, projection and compatibility."""
import copy
import json
import subprocess
import types
from pathlib import Path

import pytest
from benchmarks.attention_harness import dev_experiment as exp
from benchmarks.attention_harness import dev_agent_policies as policies
from benchmarks.attention_harness.dev_agent_policy_freeze import V1,V2,source_hashes
from benchmarks.attention_harness.dev_ledger import DevV2Store
from benchmarks.attention_harness.tests.c21_fixtures import base,r4_deployment,offline_dev_identity
from benchmarks.attention_harness.tests.test_dev_agent_configuration import SELECTION
from benchmarks.attention_harness.tests.test_dev_agent_policy_v2_flow import Rig, Memory, drive_rig
from benchmarks.attention_harness.dev_agent_workspace import TOOLS,Workspace,read_beneath
from benchmarks.attention_harness.dev_agent_view import projection,artifact
from benchmarks.attention_harness.daily_dev_v2 import DailyDevV2


def create(tmp_path, **changes):
    store=DevV2Store(tmp_path/'db')
    e=exp.create(store,tmp_path/'runs',dict(SELECTION,**changes))
    return store,e,exp.verify_manifest(e)


def test_options_preserve_one_call_and_passthrough():
    from benchmarks.attention_harness.dev_help import POLICY_IDS
    options=exp.options()
    assert options['policies']==list(POLICY_IDS) and len(options['policies'])==7
    assert options['agent_policies']==policies.policy_options()
    bridge=object.__new__(DailyDevV2);bridge.memory=None;bridge.profiles=[];bridge.per_run_budget=True
    assert bridge.options()['agent_policies']==options['agent_policies']
    assert len(options['agent_policies']['policies'])==8


@pytest.mark.parametrize('policy',policies.AGENT_POLICY_IDS)
def test_creation_v2_defaults_seals_and_cells(tmp_path,offline_dev_identity,policy):
    store,e,m=create(tmp_path,policies=[policy])
    for row in [m,m['selection'],*m['configs'].values(),*m['cells'],*e['cells']]:assert row['agent_policy_version']==V2
    assert m['policies']=={policy:policies.normalize_parameters(policy)}
    assert m['agent_policy_parameters_sha256']=={policy:policies.parameters_sha256(policy)}
    assert m['agent_policy_sources_sha256']==source_hashes()
    assert m['selection']['help_budget']==1
    with pytest.raises(Exception,match='policy is frozen'):
        exp.switch(store,e['cells'][0]['key'],'agent_initiated_help')


@pytest.mark.parametrize('change',[{'k':True},{'k':0},{'k':2,'extra':1},{}])
def test_new_registry_rejects_invalid_parameters(tmp_path,offline_dev_identity,change):
    with pytest.raises(ValueError):create(tmp_path,policies=['retry_k_then_ask'],policy_parameters={'retry_k_then_ask':change})


def test_v1_creation_and_retry_are_byte_identical(tmp_path,offline_dev_identity):
    # P2 create is the oracle; normalize() absent marker must remain its v1 shape.
    source=subprocess.check_output(['git','show','efbceb3:benchmarks/attention_harness/dev_experiment.py'],text=True)
    old=types.ModuleType('benchmarks.attention_harness._p2_create');old.__package__='benchmarks.attention_harness'
    exec(compile(source,'p2-create','exec'),old.__dict__)
    store=DevV2Store(tmp_path/'db');root=tmp_path/'runs'
    e=old.create(store,root,SELECTION);before=Path(e['manifest']['uri']).read_bytes()
    # Existing request without version gets its original manifest, not upgraded.
    assert exp.create(store,root,SELECTION)['manifest']==e['manifest']
    (tmp_path/'db').unlink();store=DevV2Store(tmp_path/'db')
    now=exp.create(store,root,SELECTION,agent_policy_version=V1)
    assert Path(now['manifest']['uri']).read_bytes()==before
    assert exp.verify_manifest(now).get('agent_policy_version',V1)==V1


@pytest.mark.parametrize('where',['selection','config','cell','parameters','digest','source','policy'])
def test_fairness_rejects_drift(tmp_path,offline_dev_identity,where):
    _,_,m=create(tmp_path)
    if where=='selection':m['selection']['agent_policy_version']=V1
    elif where=='config':next(iter(m['configs'].values())).pop('agent_policy_version')
    elif where=='cell':m['cells'][0]['agent_policy_version']=V1
    elif where=='parameters':m['selection']['policy_parameters']={'autonomous':{'extra':1}}
    elif where=='digest':m['agent_policy_parameters_sha256']['autonomous']='0'*64
    elif where=='source':m['agent_policy_sources_sha256']['dev_agent_attention_v2.py']='0'*64
    else:m['policies']={'unknown':{}}
    with pytest.raises(ValueError):exp.validate_fairness(m)


def test_fairness_cross_run_versions_and_budget(tmp_path,offline_dev_identity):
    store,_,m=create(tmp_path)
    v1=exp.create(store,tmp_path/'runs',dict(SELECTION,request_key='explicit-v1-test'),agent_policy_version=V1)
    old=exp.verify_manifest(v1)
    with pytest.raises(ValueError,match='mixed_agent_policy_versions'):exp.validate_fairness([old,m])
    assert exp.validate_fairness([m,copy.deepcopy(m)])
    other=copy.deepcopy(m);other['selection']['help_budget']=3;other['agent_budget']['help']=3
    with pytest.raises(ValueError,match='comparison_conditions_differ'):exp.validate_fairness([m,other])
    other=copy.deepcopy(m);other['run_notes']=not m.get('run_notes',False)
    with pytest.raises(ValueError,match='comparison_conditions_differ'):exp.validate_fairness([m,other])


def test_one_call_rejects_agent_only_policy_and_version(tmp_path,offline_dev_identity):
    for extra in ({'policies':['agent_initiated_help']},{'agent_policy_version':V2}):
        with pytest.raises(ValueError):
            create(tmp_path,dev_mode='one_call',target_depth=1,target_valid_executions=1,max_versions=1,**extra)


def test_tools_v1_bytes_and_v2_hint_only(tmp_path):
    raw=subprocess.check_output(['git','show','efbceb3:benchmarks/attention_harness/dev_agent_workspace.py'],text=True)
    # Compare the original literal itself; no legacy import side effects required.
    import ast
    tree=ast.parse(raw);before=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='TOOLS' for t in n.targets))
    assert TOOLS==before
    from benchmarks.attention_harness.tests.test_dev_agent_d1 import CONFIG
    w=Workspace(tmp_path/'v1').prepare({'task_id':'fake'},CONFIG)
    assert read_beneath(w.canonical,'docs/tools.md')==before.encode()
    w=Workspace(tmp_path/'v2').prepare({'task_id':'fake'},dict(CONFIG,agent_policy_version=V2,agent_policy_id='trace_aware_hint_only'))
    text=read_beneath(w.canonical,'docs/tools.md').decode()
    for word in ('pending_ask','ask_required','forced_fallback_blocked','min_new_executions','300 probes','costs nothing','hint_only run has no Memory'):assert word in text
    assert "read the current version's notes" not in text


def test_hint_only_stages_provisional_without_memory_input(tmp_path,monkeypatch):
    memory=Memory(True);r=Rig(tmp_path,'trace_aware_hint_only',memory=memory)
    staged=[];memory_inputs=[]
    # The settled seam must produce the same staging calls as other policies.
    # Staging eligibility/effectiveness remains the existing D7 verifier's job.
    def stage(store,key,version,directory,workspace,ledger):
        path=directory/'provisional.json';path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps({'status':'staged','version':version}));staged.append(path)
    monkeypatch.setattr('benchmarks.attention_harness.dev_agent_memory.stage_provisional',stage)
    class Export:
        root=tmp_path/'exports';store=None;key='cell'
        def capture(self,*args):pass
        def settle(self,response,notes):
            memory_inputs.append(notes)
            return {'version':int(response['version_id'][1:])} if response['op']=='submit' else None
    result,_=drive_rig(r,[['submit'],['submit'],[]],memory=memory,exporter=Export())
    assert result['reason']=='agent_idle' and len(staged)==2
    assert all(json.loads(p.read_text())['status']=='staged' for p in staged)
    assert memory_inputs and all(x is None for x in memory_inputs)
    assert memory.calls==[] and not (r.ws.canonical/'memory').exists() and not (r.ws.ws/'memory').exists()


def product_rig(tmp_path,policy='agent_initiated_help'):
    r=Rig(tmp_path,policy);store=DevV2Store(tmp_path/'ledger.sqlite')
    selection=dict(SELECTION,policies=[policy],agent_policy_version=V2,help_budget=10)
    with store.tx() as db:
        store.put(db,'experiments','fixture',dict(experiment_id='fixture',selection=selection,state='completed'))
        db.execute('INSERT INTO v2_cells VALUES (?,?,?)',('cell','fixture',json.dumps(dict(key='cell',experiment_id='fixture',task_id='fake',seed=1,policy_id=policy,mode='interactive',dev_mode='agent',artifact_dir=str(tmp_path),state='completed',reason='agent_idle'))))
    return r,store


def test_execution_help_projection_keeps_practice_multiple_and_failed_cost(tmp_path):
    r,store=product_rig(tmp_path)
    try:
        for i in range(2):
            r.call('run');r.call('ask_help',{'question':'Which motion should change?'})
        r.advisor.error=True
        r.call('run');r.call('ask_help',{'question':'Which approach should change?'})
        value=projection(store,store.cell('cell'),now=1005)
        rows=value['dev_v2_compat']['help_intervals']
        assert len(rows)==3 and [h['help_id'] for h in rows]==['help:e000001','help:e000002','help:e000003']
        assert all(h['after_version']==0 for h in rows)
        failed=rows[-1]['help_content']
        assert failed['charged'] and not failed['delivered'] and failed['hint_human_seconds']>0
        assert failed['actual_help_count']==1 and failed['actual_cost_known']
        assert artifact(store.cell('cell'),'executions/e000001/help.json')[0]['delivered']
        assert 'provider unavailable' in json.dumps(value['agent']['errors'])
    finally:r.close()


def test_projection_pending_required_fallback_and_controller_result_error(tmp_path):
    r,store=product_rig(tmp_path,'reactive_help')
    try:
        r.submit();r.call('submit',rid='refused')
        r.submit();r.helper.finish(reason='wall_stop',deadline=0)
        store.update_cell('cell',error='DriveError: failed turn',result={'error':'Stored result error'})
        value=projection(store,store.cell('cell'),now=1005)
        agent=value['agent']
        assert agent['pending_ask'] and agent['ask_required']
        assert agent['forced_fallback']['forced_fallback_blocked']=='wall_stop'
        assert 'DriveError' in json.dumps(agent['errors']) and 'Stored result error' in json.dumps(agent['errors'])
        assert value['dev_v2_compat']['help_intervals'][0]['help_content']['forced_fallback']
        assert artifact(store.cell('cell'),'events/forced-fallback.json')[0]['requested'] is False
    finally:r.close()


def test_report_rejects_agent_v2(tmp_path,offline_dev_identity):
    from benchmarks.attention_harness.dev_report import report
    store,e,_=create(tmp_path)
    with pytest.raises(ValueError,match='agent report'):report(store,e['experiment_id'])


def test_preview_fixture_generator_and_read_routes_only(tmp_path,monkeypatch):
    from benchmarks.attention_harness.tests.agentpolicy_preview_fixture import generate
    def forbidden(*args,**kwargs):raise AssertionError('network/model/worker forbidden')
    monkeypatch.setattr('socket.socket',forbidden)
    monkeypatch.setattr(DailyDevV2,'start',forbidden)
    pages=generate(tmp_path)
    assert len(pages)==3
    assert generate(tmp_path)==pages
    bridge=DailyDevV2(tmp_path/'daily',[],None,per_run_budget=True)
    assert len(bridge.list())==3
    for page in pages:
        value,_=bridge.get(page['view'])
        assert value['dev_mode']=='agent'
        rows=value['dev_v2_compat']['help_intervals']
        if page['label'].endswith('v2'):
            assert len(rows)==2 and rows[0]['after_version']==0
        if page['label']=='trace-v2':
            assert value['agent']['ask_required'] and value['agent']['pending_ask']
            assert value['agent']['forced_fallback']['forced_fallback_blocked']=='wall_stop'
        if page['label']=='legacy-v1':assert value['dev_v2_compat']['agent_policy_version']==V1


def test_trace_help_display_uses_trigger_boundary_for_earlier_practice(tmp_path):
    from benchmarks.attention_harness.dev_agent_ui_projection import execution_help_intervals
    ws=Workspace(tmp_path/'agent')
    ws.publish('executions/e000001/help.json',dict(help_id='help:e000001',execution_id='e000001',
        origin_version_id='v0001',status='delivered',delivered=True,agent_policy_version=V2))
    agent={'timeline':[{'kind':'no_eval_execution','execution_id':'e000001'},
                       {'kind':'formal_version','version_id':'v0001','execution_id':'e000002'}]}
    rows=execution_help_intervals({'artifact_dir':str(tmp_path),'dev_mode':'agent','policy_id':'full_trace_aware_attention_planner'},agent,[])
    assert rows[0]['execution_id']=='e000001' and rows[0]['after_version']==1
    rows=execution_help_intervals({'artifact_dir':str(tmp_path),'dev_mode':'agent','policy_id':'full_trace_aware_attention_planner'},agent,
        [{'id':'help:e000001','state':'unknown','resources':{'help':1}}])
    assert rows[0]['help_content']['hint_human_seconds']==0
    assert not rows[0]['help_content']['actual_cost_known']
