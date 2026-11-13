"""Offline daily Memory wiring; no Service, model or simulator is started."""
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from benchmarks.attention_harness.tests.c21_fixtures import base, r4_deployment

from benchmarks.attention_harness import daily_dev_v2 as daily, dev_experiment
from benchmarks.attention_harness.dev_ledger import DevV2Store
from benchmarks.attention_harness.dev_planner import POLICY
from benchmarks.attention_harness.dev_planner_memory import DevMemory,stage_candidate
from benchmarks.attention_harness.core.dev_store import DevConflict
from benchmarks.attention_harness.dev_memory_view import projection
from benchmarks.attention_harness.dev_loop import immutable


def _memory():
    url='http://127.0.0.1:8877'
    return dict(base_url=url,store_id='fixture-authority',namespace='daily-test',
                url_sha256=hashlib.sha256(url.encode()).hexdigest())


def _body(policy=POLICY):
    budget=dict(daily.DEFAULT_CELL)
    return dict(start=False,selection=dict(request_key='daily-memory-new-102',tasks=['open_cabinet'],
        seeds=[102],policies=[policy],mode='experiment',help_budget=1,
        eval_agent={'enabled':True,'model':'parcc/Qwen'},
        configuration=dict(goal_id='daily-memory-goal-102',experiment_budget=budget,
                           cell_budget=budget,max_iterations=1,wall_seconds=21600)))


def test_opt_in_materializes_v3_and_old_request_remains_v2(tmp_path,monkeypatch):
    from openarm365_dev_v2 import dev_config
    # The sandbox has no model copies. Only model-source discovery is replaced;
    # source locks, scene gates, budget gates and manifest creation run for real.
    monkeypatch.setattr(dev_config,'model_sources',lambda *args,**kwargs:{})
    def materialize(argv,**kwargs):
        def arg(name):return Path(argv[argv.index(name)+1])
        selection=json.loads(arg('--selection').read_bytes())
        store=DevV2Store(arg('--store')) if 'configuration' not in selection else DevV2Store(arg('--store'),configuration={
            'goal_id':selection['configuration']['goal_id'],'limits':selection['configuration']['experiment_budget'],
            'wall_seconds':selection['configuration']['wall_seconds']})
        value=dev_experiment.create(store,arg('--artifacts'),selection)
        arg('--output').write_text(json.dumps(value))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(daily.subprocess,'run',materialize)
    bridge=daily.DailyDevV2(tmp_path/'new',[],per_run_budget=True,memory=_memory())
    created=bridge.create(_body())
    manifest=json.loads(Path(created['manifest']['uri']).read_bytes())
    selection=json.loads((bridge.root/created['experiment_id']/'selection.json').read_bytes())
    assert manifest['schema']=='attentionbench.dev-experiment.v3'
    # Plan 0d second revision: Memory is a shared authority, not a planner feature.
    assert selection['memory_authority']==_memory()
    assert manifest['memory_authority']==_memory()
    assert 'planner_features' not in manifest and 'planner_features' not in selection
    assert 'configuration' not in selection
    assert 'api_key' not in json.dumps(manifest).lower()
    assert created['cells'][0]['memory_authority']==_memory()
    assert 'planner_features' not in created['cells'][0]
    reactive=_body('reactive_help');reactive['selection']['request_key']='daily-memory-reactive-102'
    reactive['selection']['configuration']['goal_id']='daily-memory-goal-reactive-102'
    other=bridge.create(reactive)
    assert other['cells'][0]['memory_authority']==_memory() and 'planner_features' not in other['cells'][0]
    directory,_,authorization=bridge.paths(created['experiment_id'])
    dispatch=bridge.dispatch_budget(bridge.store(created['experiment_id']),created,directory,authorization)
    dev_experiment.validate_budget_file(dispatch,manifest,bridge.store(created['experiment_id']),created['manifest'])

    # New daily runs also retain local notes with no Memory service configured.
    local=daily.DailyDevV2(tmp_path/'local',[],per_run_budget=True)
    local_body=_body()
    local_created=local.create(local_body)
    assert local_created['cells'][0]['run_notes'] is True
    assert 'memory_authority' not in local_created['cells'][0]
    assert local.create(local_body)['manifest']==local_created['manifest']

    old=daily.DailyDevV2(tmp_path/'old',[],per_run_budget=True)
    body=_body();body['selection']['request_key']='daily-no-memory-102'
    # Materialize an existing pre-run-notes selection; new selections now opt in.
    eid='devexp-'+__import__('hashlib').sha256(body['selection']['request_key'].encode()).hexdigest()[:24]
    old_selection=old.root/eid/'selection.json'
    old_selection.parent.mkdir(parents=True,exist_ok=True)
    old_selection.write_text(json.dumps(daily.bind_daily_advisor(body['selection'],old_selection)))
    old_created=old.create(body)
    old_manifest=json.loads(Path(old_created['manifest']['uri']).read_bytes())
    assert old_manifest['schema']=='attentionbench.dev-experiment.v2'
    assert 'planner_features' not in old_manifest
    assert 'configuration' in json.loads((old.root/old_created['experiment_id']/'selection.json').read_bytes())
    before=Path(old_created['manifest']['uri']).read_bytes()
    restarted=daily.DailyDevV2(tmp_path/'old',[],per_run_budget=True,memory=_memory())
    assert restarted.create(body)['manifest']==old_created['manifest']
    assert Path(old_created['manifest']['uri']).read_bytes()==before


def test_memory_authority_is_server_owned_and_legacy_validator_stays_strict(tmp_path):
    bridge=daily.DailyDevV2(tmp_path/'daily',[],per_run_budget=True)
    with pytest.raises(ValueError,match='not configured'):
        bridge.create({**_body(), 'selection':{**_body()['selection'],'planner_features':{'memory':_memory()}}})
    with pytest.raises(ValueError,match='cannot be mixed'):
        dev_experiment.create(DevV2Store(tmp_path/'direct.sqlite'),tmp_path/'artifacts',
            {**_body()['selection'],'planner_features':{'memory':_memory()}})


def test_five_version_daily_budget_remains_finite_under_existing_v3_gate(tmp_path):
    from benchmarks.attention_harness.dev_configuration import normalize_configuration,configured_budget
    body=_body();body['selection']['configuration']['max_iterations']=5
    body['selection']['help_budget']=5
    selected=daily.bind_daily_memory(daily.bind_daily_advisor(body['selection'],tmp_path/'no-selection.json'),_memory())
    normalized=normalize_configuration(selected)
    assert normalized['coverage']=='budget_limited'
    assert configured_budget(normalized)==daily.DEFAULT_CELL
    assert selected['goal_limits']==daily.DEFAULT_CELL


def test_ui_startup_flags_bind_daily_authority_without_service_io(tmp_path,monkeypatch):
    from benchmarks.attention_harness import ui_server
    captured=[]
    original=daily.DailyDevV2
    def bridge(*args,**kwargs):
        value=original(*args,**kwargs);captured.append(value);return value
    class OfflineServer:
        server_port=0
        def __init__(self,*args):pass
        def serve_forever(self):pass
        def server_close(self):pass
    monkeypatch.setattr(daily,'DailyDevV2',bridge)
    monkeypatch.setattr(ui_server,'ThreadingHTTPServer',OfflineServer)
    monkeypatch.setattr(ui_server.MemoryView,'service',lambda *args,**kwargs:object())
    monkeypatch.setenv('ATTENTION_MEMORY_API_KEY','test-key-only-in-process')
    monkeypatch.setattr(sys,'argv',['ui_server','--demo','--dev-v2-root',str(tmp_path/'daily'),
        '--dev-v2-per-run-budget','--memory-service-url',_memory()['base_url'],
        '--dev-v2-memory-store-id',_memory()['store_id'],'--dev-v2-memory-namespace',_memory()['namespace']])
    ui_server.main()
    assert captured[0].memory==_memory()
    assert 'test-key-only-in-process' not in json.dumps(captured[0].options())


def test_exact_dev_grant_and_visual_proxy_hint_share_generated_input(tmp_path,monkeypatch):
    from benchmarks.attention_harness import dev_planner_memory, dev_v2_loop
    from benchmarks.attention_harness.dev_advisor_proxy import default_config
    scope={'task_id':'open_cabinet','scene_id':'fixture-scene'}
    monkeypatch.setattr(dev_planner_memory,'context',lambda config,namespace:scope)
    class Gateway:
        def store_id(self):return _memory()['store_id']
        def list_dev_v2_memories(self, *, status=None):
            note = dict(memory_id='real-memory-1',version=3,status='trusted',
                        title='Gripper alignment',summary='Check alignment before grasping')
            return [note] if status is None or note['status'] == status else []
        def retrieve(self,context,now):
            return [SimpleNamespace(memory_id='real-memory-1',version=3,status='trusted',applicability=scope,
                                    guidance='Check the gripper alignment.')]
        def authorize_dev_use(self,**kw):
            return dict(grant_id='dev-grant-3',memory_id=kw['memory_id'],memory_version=kw['memory_version'],
                        context=kw['context'],development_id=kw['development_id'],source_policy_sha256=kw['source_policy_sha256'],
                        guidance='Check the gripper alignment.',source_trace_id='trace-1',evidence_refs=['source-1'])
    gateway=Gateway()
    packet=DevMemory(gateway,_memory(),{}).packet(development_id='cell-1:v2',source='previous source')
    assert packet['entries'][0]['version']==3 and packet['entries'][0]['grant_id']=='dev-grant-3'
    with pytest.raises(PermissionError,match='authority identity mismatch'):
        DevMemory(SimpleNamespace(store_id=lambda:'wrong-authority'),_memory(),{})
    monkeypatch.setattr(dev_planner_memory,'load_memory',lambda *args:(packet,None))
    monkeypatch.setattr(dev_v2_loop,'decide',lambda *args,**kw:dict(action='request',request_type='hint',reason='failed grasp'))
    def visual_answer(*args,**kw):
        dev_v2_loop.immutable(args[7],{'mode':'vision','feedback':'Look from the side camera before grasping.'})
        return dict(status='completed',feedback='Look from the side camera before grasping.',mode='vision',selected_moments=[],localization={})
    monkeypatch.setattr(dev_v2_loop,'advisor_answer',visual_answer)
    fake_cell=dict(policy_id=POLICY,budget={'help_count':1},used={'help_count':0},jobs=[],task_id='open_cabinet',seed=102,
                   versions=[],planner_features=dict(memory=_memory(),human_wait_seconds=0,background_seconds=0,approval_rule='none'))
    fake_store=SimpleNamespace(cell=lambda key:fake_cell)
    config=dict(advisor_proxy=default_config(True),task_id='open_cabinet',official_env='OpenCabinet',task_prompt='Open the cabinet',
                seed=102,robot='OpenArm365',execution_arm='both',controller='controller',layout_id=11,style_id=34,
                scene_id='fixture-scene',horizon=100,sdk_call_limit=100,attempt_wall_seconds=100,perception_mode='vision')
    manifest=dict(memory={'entries':[]},configs={'open_cabinet:102':config},policies={POLICY:{}},trace_visible=True,
                  selection={'mode':'experiment'})
    answer,_,_=dev_v2_loop.attention(fake_store,'cell-1',2,{},'previous source',1,[],manifest,None,tmp_path,None)
    assert answer['help']['provider']=='human_proxy'
    assert answer['memory'][0]['grant_id']=='dev-grant-3'
    monkeypatch.setattr(dev_v2_loop,'get_task',lambda key:SimpleNamespace(system_prompt=lambda config:'public task'))
    messages=dev_v2_loop.model_messages(config,'previous source',{},answer,{'dev_calls':1})
    generated_input=dev_v2_loop.immutable(tmp_path/'model-input.json',{'messages':messages})
    user=json.loads(json.loads(Path(generated_input['uri']).read_bytes())['messages'][-1]['content'])
    assert user['attention_control']['memory']==[{'title':'Gripper alignment',
        'summary':'Check alignment before grasping','content':'Check the gripper alignment.',
        'memory_id':'real-memory-1','level':'same_task'}]
    assert 'side camera' in user['attention_control']['help']['content']

    for conflict in (dict(human_wait_seconds=60,background_seconds=0,approval_rule='generated_motion_policy'),
                     dict(human_wait_seconds=0,background_seconds=5,approval_rule='none'),
                     dict(human_wait_seconds=0,background_seconds=0,approval_rule='generated_motion_policy')):
        fake_cell['planner_features']={**conflict,'memory':_memory()}
        with pytest.raises(DevConflict,match='Advisor is incompatible with planner waiting preparation and approval'):
            dev_v2_loop.attention(fake_store,'cell-1',2,{},'previous source',1,[],manifest,None,tmp_path/'reject',None)


def test_dev_memory_view_projects_frozen_queue_grant_validation_and_result(tmp_path):
    store=DevV2Store(tmp_path/'ledger.sqlite')
    cell={'key':'cell-102'}
    assert projection(store,cell)['empty'] is True
    source=immutable(tmp_path/'source-queue.json',dict(cell=cell['key'],version=2,kind='source',
        status='pending_offline_import',authority_candidate=False))
    result=immutable(tmp_path/'result-queue.json',dict(cell=cell['key'],version=2,kind='result',
        status='pending_offline_import',authority_candidate=False))
    packet=immutable(tmp_path/'memory-input.json',dict(entries=[dict(memory_id='memory-1',version=3,grant_id='grant-3',
        authority='fixture-authority',context=dict(scene_id='scene-102',task_prompt='Open the cabinet')),
        dict(memory_id='memory-2',version=1,grant_id='grant-4',authority='fixture-authority',
             context=dict(scene_id='scene-102')),
        dict(memory_id='memory-test-external',version=1,grant_id='grant-test',authority='fixture-authority',
             source_trace_id='seeded-for-tests:pilot-102',context=dict(scene_id='scene-102'))]))
    validation=immutable(tmp_path/'memory-validation-input.json',dict(memory_ids=['memory-1'],exposure='candidate_validation'))
    model=immutable(tmp_path/'model-input.json',dict(messages=[]))
    policy=immutable(tmp_path/'policy.json',dict(source='source'))
    receipt=immutable(tmp_path/'receipt.json',dict(response={'source':'test_seed','seeded_for_tests':True,'memory_id':'memory-1','version':3}))
    candidate=immutable(tmp_path/'candidate-receipt.json',dict(response={'memory_id':'memory-2','version':1}))
    with store.tx() as db:
        for kind,value in [
            ('planner_memory_offline_queued',dict(cell=cell['key'],version=2,kind='source',queue=source)),
            ('planner_memory_offline_queued',dict(cell=cell['key'],version=2,kind='result',queue=result)),
            ('planner_memory_retrieved',dict(cell=cell['key'],version=2,authority='fixture-authority',namespace='daily-test',packet=packet)),
            ('planner_memory_generation',dict(cell=cell['key'],version=2,memory_packet=packet,model_input=model,result_source=policy)),
            ('memory_validation_dispatch',dict(cell=cell['key'],version=2,validation_input=validation,validation_grant_id='validation-3')),
            ('planner_memory_authority_receipt',dict(cell=cell['key'],version=2,operation='result',receipt=receipt)),
            ('planner_memory_authority_receipt',dict(cell=cell['key'],version=2,operation='candidate',receipt=candidate))]:
            store.event(db,kind,value)
        before=db.execute('SELECT COUNT(*) FROM v2_events').fetchone()[0]
    view=projection(store,cell)
    with store.tx() as db:after=db.execute('SELECT COUNT(*) FROM v2_events').fetchone()[0]
    assert before==after
    assert view['queues'][0]['status']=='pending_offline_import'
    assert view['queues'][1]['status']=='offline_receipt_recorded'
    assert view['retrievals'][0]['entries'][0]['trusted'] is True
    assert view['retrievals'][0]['entries'][0]['source']=='Seeded for tests'
    assert view['retrievals'][0]['entries'][1]['source']=='Dev v2 candidate'
    assert view['retrievals'][0]['entries'][2]['seeded_for_tests'] is True
    assert view['retrievals'][0]['entries'][0]['applicability']['scene_id']=='scene-102'
    assert view['validations'][0]['grant_id']=='validation-3'
    assert view['generations'][0]['memory_packet_sha256']==packet['sha256']
    assert view['receipts'][0]['seeded_for_tests'] is True
    assert view['receipts'][0]['source']=='Seeded for tests'
    (tmp_path/'memory-input.json').unlink()
    (tmp_path/'memory-input.json').write_text('{}')
    with pytest.raises(DevConflict,match='digest mismatch'):projection(store,cell)


def test_visual_advisor_creates_only_an_offline_proposal(tmp_path):
    store=DevV2Store(tmp_path/'ledger.sqlite')
    answer=immutable(tmp_path/'advisor-hint.json',dict(status='completed',mode='vision',request_type='hint',
                                                      feedback='Use the side camera to align the gripper.'))
    help_ref=immutable(tmp_path/'help.json',dict(provider='human_proxy',mode='vision',content='Use the side camera to align the gripper.',
                                                 advisor_proxy=answer,rejected=False))
    source=immutable(tmp_path/'policy.py','from robot_sdk import sensors\n')
    feedback=immutable(tmp_path/'feedback.json',dict(public_error='grasp missed'))
    queue=stage_candidate(store,'cell-102',2,help_ref,source,feedback,tmp_path)
    assert queue is not None
    proposal=json.loads(Path(queue['uri']).read_bytes())
    assert proposal['status']=='pending_offline_import' and proposal['authority_candidate'] is False
    assert projection(store,{'key':'cell-102'})['queues'][0]['source']=='Offline import proposal'
    invalid=immutable(tmp_path/'invalid-advisor.json',dict(status='invalid_vision_unavailable',mode='vision',
                                                           request_type='hint',feedback=''))
    invalid_help=immutable(tmp_path/'invalid-help.json',dict(provider='human_proxy',mode='vision',content='invented',
                                                              advisor_proxy=invalid,rejected=False))
    assert stage_candidate(store,'cell-102',3,invalid_help,source,feedback,tmp_path/'rejected') is None


@pytest.fixture(autouse=True)
def offline_r4_snapshot(r4_deployment):
    """New dev defaults bind an isolated authenticated snapshot."""
