"""OpenArm Dev contracts; fixtures are never counted as real loop evidence."""
import copy
import json
import threading
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from benchmarks.attention_harness import dev_loop as dl
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.core.dev_store import DevConflict
from benchmarks.attention_harness.dev_openarm_profile import PROFILE_ID
from benchmarks.attention_harness.parcc_client import ParccResponse
from openarm365_dev.policy_validation import validate_generated_policy

ROOT=Path(__file__).resolve().parents[3]
SOURCE='from robot_sdk import sensors, left_arm, right_arm\nobs=sensors.get_observation()\n'

def configuration():
    return json.loads((ROOT/'openarm365_dev/configs/dev_counter_sink_seed101.json').read_bytes())

def create(tmp_path,monkeypatch,attempts=2):
    cfg=configuration();store=AttentionStore(tmp_path/'attention.sqlite3')
    text=json.dumps(cfg)
    locked=dict(profile_id=PROFILE_ID,config=cfg,config_text=text,
        config_sha256=__import__('hashlib').sha256(text.encode()).hexdigest(),
        resource_sha256='b'*64,resources={k:{'path':'fixture'} for k in
            ('service_source_root','sim_python','sim_source_root','task_source_root','asset_manifest')})
    s=store.create_dev_session(locked=locked,
        budget={**dl.DEFAULT_BUDGET,'model_calls':attempts,'executions':attempts,'sdk_calls':80*attempts,'execution_seconds':480*attempts},
        artifact_dir=tmp_path/'runs',model='fixture-only',initial_source=SOURCE,request_key='openarm-fixture-session')
    monkeypatch.setattr(dl,'verify_resources',lambda _:None)
    return store,s

@pytest.mark.parametrize('poison',[
    {'reward':99},{'native_success':True},{'hidden_evaluator':'SECRET'},
    {'private_state':'SECRET'},{'derived_diagnosis':'SECRET'},
    {'error':{'message':'evaluation-derived diagnosis from reward=99'}},
    {'result':{'robot0_left_eef_pos':[1,2,3],'hidden_state':'SECRET'}},
])
def test_openarm_poison_never_reaches_model(poison):
    trace={'status':'failed','sdk_events':[{'operation':'move_delta','source':'left_arm',
        'physical_arm':'left','status':'failed',**poison}]}
    with pytest.raises(ValueError):dl.public_feedback(trace)

def test_openarm_public_actions_keep_physical_sides_and_full_preceding_source():
    trace={'status':'failed','error':'World-frame deltas limited to .05m/.5rad per coordinate',
        'sdk_events':[{'operation':'move_delta','source':'right_arm','physical_arm':'right',
            'status':'failed','arguments':{'dx':.06,'dy':0,'dz':0,'unapproved':9}},
            {'operation':'get_observation','source':'sensors','status':'completed',
                'result':{'robot0_left_eef_pos':[1,2,3],'robot0_right_eef_pos':[4,5,6],'unapproved':99}}]}
    feedback=dl.public_feedback(trace)
    s=dict(locked={'config':configuration(),'resource_sha256':'a'*64},remaining={'tokens':900})
    messages=dl.messages_for(s,SOURCE,feedback);p=json.loads(messages[1]['content'])
    assert p['previous_complete_source']==SOURCE and p['execution_arm']=='both'
    assert p['locked_task']['robot']=='OpenArm365'
    assert p['public_feedback']['action_trace'][0]['physical_arm']=='right'
    assert 'unapproved' not in json.dumps(messages)
    assert 'WORLD-frame' in messages[0]['content'] and 'base.move_normalized' not in messages[0]['content']

@pytest.mark.parametrize('code',[
    'from robot_sdk import arm\narm.move_delta(0,0,.01)',
    'from robot_sdk import base\nbase.move_normalized([0,0,0])',
    'from robot_sdk import sensors\nsensors.evaluate()',
    'from robot_sdk import sensors\nsensors._check_success()',
    'import os\nos.system("true")',
])
def test_panda_aliases_and_scoring_cannot_enter_openarm_sdk(code):
    with pytest.raises(Exception):validate_generated_policy(code)

@pytest.mark.parametrize('field,value',[('robot','PandaOmron'),('execution_arm','right'),('seed',102),('layout_id',12),('style_id',1)])
def test_robot_arm_and_scene_tampering_fails_before_dispatch(field,value):
    cfg=configuration();text=json.dumps(cfg)
    locked=dict(config=copy.deepcopy(cfg),config_text=text,config_sha256=__import__('hashlib').sha256(text.encode()).hexdigest(),resources={})
    locked['config'][field]=value
    with pytest.raises(DevConflict,match='configuration changed'):dl.verify_resources(locked)

def test_profile_list_keeps_existing_panda_and_never_silently_substitutes():
    profiles=[{'id':'existing-panda','suite':'robocasa365','task':dl.TASK,
        'robot':{'model':'PandaOmron','execution_arm':'right'}}]
    options=dl.dev_options(profiles)['profiles']
    assert next(p for p in options if p['id']=='existing-panda')['enabled'] is True
    assert next(p for p in options if p['id']==PROFILE_ID)['robot']=='OpenArm365'

def test_two_openarm_versions_use_only_public_failure_and_settle_cumulatively(tmp_path,monkeypatch):
    from benchmarks.attention_harness import formal_runner_boundary as boundary
    store,s=create(tmp_path,monkeypatch);inputs=[]
    class Client:
        def chat(self,**kwargs):
            inputs.append(kwargs['messages']);n=len(inputs)
            return ParccResponse('fixture-reported-model',json.dumps({'source':SOURCE+f'print({n})\n','reason':'repair public delta error'}),None,.01,1,{'total_tokens':7})
    def execute(req,**kwargs):
        d=req.artifact_root/'fixture';d.mkdir(parents=True)
        trace=dl.immutable(d/'trace.json',{'status':'failed','error':'public delta error','sdk_events':[]})
        receipt=dl.immutable(d/'sandbox_receipt.json',{'sdk_call_budget':{'dispatched':3},'elapsed_seconds':.1})
        return dict(status='failed',artifact_dir=str(d),native_evaluator={'native_success':True,'reward':999,'private_state':'SECRET'},
            artifacts={'trace':trace,'sandbox_receipt':receipt})
    monkeypatch.setattr(boundary,'run_with_formal_boundary',execute)
    dl.run_session(store,s['session_id'],client=Client(),runner_factory=lambda **kw:NS(cancel_event=threading.Event()))
    final=store.get_dev_session(s['session_id']);assert final['state']=='budget_exhausted'
    assert final['used']['tokens']==14 and final['used']['model_calls']==2 and final['used']['executions']==2
    assert final['used']['sdk_calls']==6 and not any(final['reserved'].values())
    packet=json.loads(inputs[1][1]['content'])
    assert packet['previous_complete_source']==Path(final['versions'][0]['source']['uri']).read_text()
    assert packet['public_feedback']['public_error']=='public delta error'
    assert 'SECRET' not in json.dumps(inputs) and 'reward' not in json.dumps(inputs)
    for v in final['versions']:
        assert v['formal_eligible'] is False and v['actual_model']=='fixture-reported-model'
        with pytest.raises(FileExistsError):dl.immutable(v['source']['uri'],'overwrite')
    with store._connect() as db:assert db.execute('SELECT COUNT(*) FROM runs').fetchone()[0]==0

def test_unknown_openarm_execution_retains_reservation_and_reopen_cannot_retry(tmp_path,monkeypatch):
    from benchmarks.attention_harness import formal_runner_boundary as boundary
    store,s=create(tmp_path,monkeypatch);calls=[]
    class Client:
        def chat(self,**kw):
            calls.append(kw);return ParccResponse('fixture',json.dumps({'source':SOURCE,'reason':'fixture'}),None,.01,1,{'total_tokens':5})
    monkeypatch.setattr(boundary,'run_with_formal_boundary',lambda *a,**k:dict(outcome_unknown=True,status='failed',artifacts={}))
    with pytest.raises(DevConflict):dl.run_session(store,s['session_id'],client=Client(),runner_factory=lambda **k:NS(cancel_event=threading.Event()))
    reopened=AttentionStore(store.path);final=reopened.get_dev_session(s['session_id'])
    assert final['state']=='needs_review' and final['reserved']['executions']==1 and len(calls)==1
    assert not final['owner']
    with pytest.raises(DevConflict):reopened.resume_dev_session(s['session_id'])

def test_sent_native_rpc_without_receipt_is_unknown_even_on_stop(tmp_path):
    from openarm365_dev.owner import NativeOwner,OutcomeUnknown
    import io,time
    owner=NativeOwner(python='fixture',config=tmp_path/'config',evidence=tmp_path,
        deadline=time.monotonic()+10,cancel_check=lambda:False)
    owner.process=NS(stdin=io.BytesIO())
    owner.read=lambda:(_ for _ in ()).throw(InterruptedError('operator stop'))
    with pytest.raises(OutcomeUnknown):owner.rpc('left_arm','move_delta',[0,0,.01])
    assert len(owner.unresolved)==1 and not owner.ledger


def test_openarm_lock_preserves_virtualenv_invocation_path():
    from benchmarks.attention_harness.dev_openarm_profile import lock_profile
    locked=lock_profile()
    assert locked['resources']['sim_python']['path'].endswith('/work/service_env/bin/python')
    assert Path(locked['resources']['sim_python']['path']).is_symlink()

def test_stop_cancels_current_openarm_execution_and_prevents_next_model(tmp_path,monkeypatch):
    from benchmarks.attention_harness import formal_runner_boundary as boundary
    store,s=create(tmp_path,monkeypatch);runner=NS(cancel_event=threading.Event());calls=[]
    class Client:
        def chat(self,**kw):
            calls.append(kw);return ParccResponse('fixture',json.dumps({'source':SOURCE,'reason':'fixture'}),None,.01,1,{'total_tokens':5})
    def execute(req,**kw):
        store.stop_dev_session(s['session_id'])
        assert runner.cancel_event.wait(2),'session watcher did not cancel active execution'
        d=req.artifact_root/'fixture';d.mkdir(parents=True)
        trace=dl.immutable(d/'trace.json',{'status':'cancelled','error':'operator cancelled','sdk_events':[]})
        receipt=dl.immutable(d/'sandbox_receipt.json',{'sdk_call_budget':{'dispatched':1},'elapsed_seconds':.1})
        return dict(status='cancelled',artifact_dir=str(d),artifacts={'trace':trace,'sandbox_receipt':receipt})
    monkeypatch.setattr(boundary,'run_with_formal_boundary',execute)
    dl.run_session(store,s['session_id'],client=Client(),runner_factory=lambda **kw:runner)
    final=AttentionStore(store.path).get_dev_session(s['session_id'])
    assert final['state']=='stopped' and not final['owner'] and len(calls)==1
    assert final['used']['executions']==1 and final['used']['model_calls']==1
    assert all(j['state']=='settled' for j in final['jobs'])
