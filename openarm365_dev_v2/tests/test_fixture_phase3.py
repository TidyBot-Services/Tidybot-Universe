"""Offline product creation, telemetry and cross-profile compatibility."""
import copy
from dataclasses import asdict,replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType,SimpleNamespace as NS
import numpy as np
import pytest

pytestmark=[pytest.mark.scene_gate,pytest.mark.eval_gate]
from curobo_v2 import execution_settings as es,fixture_interaction as fi,fixture_telemetry as ft
from benchmarks.attention_harness import dev_experiment as exp,dev_tasks as dt,dev_fixture_tasks as registry
from benchmarks.attention_harness.daily_dev_v2 import DailyDevV2
from benchmarks.attention_harness.dev_ledger import DevV2Store
from benchmarks.attention_harness.dev_agent_policy_freeze import validate as agent_seal
from benchmarks.attention_harness.tests.c21_fixtures import base,r4_deployment
from benchmarks.attention_harness.tests.c22_fixtures import source_only_lock
from openarm365_dev_v2 import dev_config as dc
from openarm365_dev_v2.tests.test_fixture_interaction import settings,fixture_types,fake_mujoco_if_unavailable
from openarm365_dev_v2.tests.test_fixture_phase1f import appliance_executor

PAIRS=[(t,s) for t in fi.PILOT_TASKS for s in registry.seeds(t)]

@pytest.fixture
def identity(r4_deployment,source_only_lock,monkeypatch):
    original=exp.make_lock
    def lock(path,**kwargs):
        ref=original(path,**kwargs)
        value=json.loads(Path(path).read_bytes());value['planner_deployment_kind']='dev-local'
        Path(path).write_text(json.dumps(value,sort_keys=True))
        return dict(ref,sha256=dc.sha(path))
    monkeypatch.setattr(exp,'make_lock',lock)
    return r4_deployment

def selection(task,seed,mode):
    return dict(request_key='fixture-phase3-test',goal_key='fixture-phase3-goal',tasks=[task],seeds=[seed],policies=['autonomous'],
        mode='interactive',rules_profile=fi.PROFILE,dev_mode=mode,eval_agent={'enabled':True},
        **({} if mode=='agent' else dict(target_depth=1,target_valid_executions=1,max_versions=1)))

@pytest.mark.parametrize('task,seed',PAIRS)
@pytest.mark.parametrize('mode',['one_call','agent'])
@pytest.mark.parametrize('path',['product','daily'])
def test_every_product_config(tmp_path,monkeypatch,identity,task,seed,mode,path):
    request=selection(task,seed,mode)
    if path=='daily':
        from benchmarks.attention_harness import daily_dev_v2 as daily
        original=daily.subprocess.run
        def child(args,**kw):
            if 'benchmarks.attention_harness.dev_experiment' not in args:return original(args,**kw)
            value=lambda flag:args[args.index(flag)+1]
            store=DevV2Store(value('--store'))
            e=exp.create(store,value('--artifacts'),json.loads(Path(value('--selection')).read_bytes()))
            Path(value('--output')).write_text(json.dumps(e))
            return NS(returncode=0)
        monkeypatch.setattr(daily.subprocess,'run',child)
        bridge=DailyDevV2(tmp_path/'daily',[],per_run_budget=True)
        bridge.create(dict(selection=request,start=False))
        eid='devexp-'+hashlib.sha256(request['request_key'].encode()).hexdigest()[:24]
        e=exp.snapshot(bridge.store(eid),eid)
    else:
        store=DevV2Store(tmp_path/'db');e=exp.create(store,tmp_path/'runs',request)
    m=exp.verify_manifest(e);c=m['configs'][f'{task}:{seed}']
    from benchmarks.attention_harness import dev_fixture_success as success
    assert m['success_conditions'][f'{task}:{seed}']==success.load(task,seed)
    assert m['success_condition_dev_visible'] is True and m['eval_success_rule']==success.EVAL_RULE
    assert dc.validate_dev_config(c) is c
    assert c['execution_settings']['profile']==fi.PROFILE
    assert c['execution_settings']['fixture_telemetry_hz']==20
    assert c['seed']==c['scene_reset_seed']==seed and 'fixture_scene_contract' in c
    assert 'scene_contract' not in c
    assert c['scene_verification']==m['scene_verification']==e['scene_verification']=='fixture_contract_only'
    assert m.get('reproducible_reset') is not True and m['selection'].get('reproducible_reset') is not True
    assert len(e['cells'])==1 and e['cells'][0]['mode']=='interactive' and not e['cells'][0]['experiment_eligible']
    assert e['cells'][0]['scene_verification']=='fixture_contract_only'
    assert c['planner_source']['kind']=='dev-local' and c['budget_tier']=='dev'
    dt.verify_dev_sdk_seal(c)
    if mode=='agent':agent_seal(m)
    assert exp.validate_fairness(m)
    from openarm365_dev.policy_validation import validate_generated_policy
    validate_generated_policy(m['initial_sources'][task]['source'],config=c)
    if path=='product':assert exp.create(store,tmp_path/'runs',request)['manifest']==e['manifest']

@pytest.mark.parametrize('row',registry.EXCLUSIONS)
@pytest.mark.parametrize('path',['product','daily'])
def test_excluded_seed_refused_before_creation(tmp_path,row,path):
    request=selection(row['task_id'],row['seed'],'agent')
    with pytest.raises(ValueError,match='start_state_collision'):
        if path=='daily':DailyDevV2(tmp_path/'daily',[],per_run_budget=True).create(dict(selection=request,start=False))
        else:exp.create(DevV2Store(tmp_path/'db'),tmp_path/'runs',request)
    assert not list(tmp_path.rglob('identity-lock.json')) and not list(tmp_path.rglob('selection.json'))

@pytest.mark.parametrize('task',[t for t in fi.PILOT_TASKS if t not in registry.SHARED])
def test_fixture_task_implies_profile(task):
    request=selection(task,registry.seeds(task)[0],'agent');del request['rules_profile']
    assert registry.prepare_selection(request)['rules_profile']==fi.PROFILE

@pytest.mark.parametrize('task',registry.SHARED)
def test_shared_ids_keep_default(task):
    request=dict(tasks=[task],seeds=[101])
    assert registry.prepare_selection(request) is request
    assert dt.get_task(task).seeds==dt.SEEDS


def test_registry_options_and_original_task_records(monkeypatch):
    old=ModuleType('benchmarks.attention_harness.phase3_old_tasks');old.__package__='benchmarks.attention_harness'
    monkeypatch.setitem(sys.modules,old.__name__,old)
    name='benchmarks/attention_harness/dev_tasks.py';old.__file__=str(dc.ROOT/name)
    exec(compile(subprocess.check_output(['git','show','da2b25b:'+name]),name,'exec'),old.__dict__)
    for table in ('TASKS','EXPLICIT_TASKS','FIXTURE_TASKS'):
        before=getattr(old,table);after=getattr(dt,table)
        assert list(before)==list(after)
        assert {t:asdict(r) for t,r in before.items()}=={t:asdict(r) for t,r in after.items()}
    for profile in (None,'r3-20261025','r4-20261027'):
        for collaboration in ('none','c1','handoff'):
            c=dict(budget_tier='dev',runner_boundary='dev_loop',sdk_call_limit=320,horizon=9600,attempt_wall_seconds=1920,bimanual_collaboration=collaboration)
            if profile:c['execution_settings']={'profile':profile}
            assert dt.dev_sdk(c)==old.dev_sdk(c) and dt.dev_sdk_prompt(c)==old.dev_sdk_prompt(c)
            assert dt.dev_sdk_seal(c)==old.dev_sdk_seal(c)
    bridge=object.__new__(DailyDevV2);bridge.memory=None;bridge.profiles=[];bridge.per_run_budget=True
    opts=bridge.options();rows=opts['fixture_tasks']
    assert len(rows)==14 and sum(len(t['seeds']) for t in rows)==37
    assert [sum(t['family']==f for t in rows) for f in 'ABCD']==[3,3,4,4]
    assert {t['task_id'] for t in rows if t['held_out']}==registry.HELD_OUT
    assert sum(not s['enabled'] for t in rows for s in t['seed_options'])==5
    assert [t['task_id'] for t in opts['tasks']]==list(old.TASKS)


def test_product_rate_hash_and_pilot_bytes(settings,monkeypatch):
    product=es.from_config(dict(settings.to_dict(),fixture_telemetry_hz=20))
    assert product.sha256!=settings.sha256
    assert 'fixture_telemetry_hz' not in settings.to_dict()
    assert es.from_config(product.to_dict()).sha256==product.sha256
    for invalid in (True,0,10,20.,50,None):
        with pytest.raises(ValueError):es.from_config(dict(product.to_dict(),fixture_telemetry_hz=invalid))
    for tier,boundary in [('legacy','dev_loop'),('dev','formal')]:
        with pytest.raises(ValueError):dc.new_execution_settings(budget_tier=tier,budget_boundary=boundary,rules_profile=fi.PROFILE,fixture_telemetry_hz=20)

@pytest.mark.parametrize('product',[False,True])
def test_decimation_keeps_unsampled_peak_and_checks_every_step(settings,product):
    if product:settings=es.from_config(dict(settings.to_dict(),fixture_telemetry_hz=20))
    rows=[];e=NS(execution_settings=settings,env=NS(sim=NS(model=NS(opt=NS(timestep=.002)))),goals=np.zeros(1),planner=NS(names=['joint']),active_request='a',journal='journal',physics_steps=0)
    e.log=lambda path,row:rows.append(row);ft.begin(e)
    for step in range(1,501):
        e.physics_steps=step
        c=dict(force_norm_n=99. if step==17 else 1.,contact_kind='neighbourhood',physical_side='left',
            geom_id=4,name='panel',fixture='mw',pattern='start_button',part_geom_id=5,part_name='start_button',
            part_kind='press',contact_radius_m=.035,contact_point_distance_m=.02,part_distance_m=.001)
        sample=ft.sample(e,{1:c},np.array([.1 if step==17 else .01]))
        assert bool(sample)==(not product or (step-1)%25==0)
        assert ft.guards(e,np.zeros(3),np.zeros(3),{'left':[.006,.006]})
    stats=ft.receipt(e)['fixture_pilot']
    assert stats['contact_steps']==stats['guard_record_only_steps']==500
    assert stats['maximum_force_n']==99 and stats['maximum_joint_tracking_error_rad']==.1
    assert sum(r['event']=='fixture_contact_sample' for r in rows)==(20 if product else 500)
    assert sum(r['event']=='fixture_guard_sample' for r in rows)==(20 if product else 500)
    if product:
        peak=stats['maximum_force_contact']
        assert peak['physics_step']==17 and peak['part_name']=='start_button'
        assert peak['part_kind']=='press' and peak['contact_radius_m']==.035 and peak['contact_point_distance_m']==.02


def test_product_monitor_still_rejects_unsampled_contact(monkeypatch,fixture_types,settings):
    settings=es.from_config(dict(settings.to_dict(),fixture_telemetry_hz=20))
    e,_=appliance_executor(monkeypatch,fixture_types,settings,offset=.04)
    e.env.sim.model.opt=NS(timestep=.002);e.physics_steps=1
    with pytest.raises(RuntimeError,match='Unexpected physical'):e.monitor()


def test_r5_workspace_docs(tmp_path,settings):
    from benchmarks.attention_harness.dev_agent_workspace import Workspace,read_beneath
    c=dict(budget_tier='dev',runner_boundary='dev_loop',sdk_call_limit=320,horizon=9600,attempt_wall_seconds=1920,
        execution_settings=settings.to_dict())
    c.update(dt.dev_sdk_seal(c))
    w=Workspace(tmp_path/'ws').prepare({'task_id':'turn_on_microwave'},c)
    text=read_beneath(w.canonical,'docs/sdk.md').decode()
    for phrase in ('returned interaction parts','1 cm','3.5 cm','2 cm clearance','move_to(0.006)','fingertips together'):
        assert phrase in text

@pytest.mark.parametrize('profile',[None,'r3-20261025','r4-20261027',fi.PROFILE])
@pytest.mark.parametrize('layered',[False,True])
def test_memory_profile_applicability(tmp_path,identity,profile,layered):
    from benchmarks.attention_harness import dev_planner_memory as memory
    from openarm365_dev_v2.scene_contract import registered_contract_ref
    e=exp.create(DevV2Store(tmp_path/'db'),tmp_path/'runs',selection('open_cabinet',102,'agent'))
    config=exp.verify_manifest(e)['configs']['open_cabinet:102']
    contexts={};configs={}
    for p in (None,'r3-20261025','r4-20261027',fi.PROFILE):
        c=copy.deepcopy(config)
        if p!=fi.PROFILE:
            c.pop('fixture_scene_contract');c['scene_contract']=registered_contract_ref()
            if p is None:c.pop('execution_settings');c.pop('planner_source')
            else:c['execution_settings']['profile']=p
            c.update(dt.dev_sdk_seal(c))
        contexts[p]=memory.context(c,'test');configs[p]=c
        assert contexts[p].get('execution_profile')==p
    class Gateway:
        def store_id(self):return 'store'
        def retrieve(self,*args,**kwargs):
            return [NS(memory_id=str(p),version=1,guidance='Observe before moving.',status='trusted',applicability=ctx)
                for p,ctx in contexts.items()]
        def retrieve_entries(self,*args,**kwargs):
            return [dict(memory_id=str(p),version=1,guidance='Observe before moving.',status='trusted',applicability=ctx,
                match_level='same_scene',trusted_here=True) for p,ctx in contexts.items()]
    m=memory.DevMemory(Gateway(),dict(namespace='test',store_id='store'),configs[profile],layered=layered,mode='interactive')
    assert [n.memory_id for n in m.eligible()]==[str(profile)]

@pytest.mark.parametrize('mode',['one_call','agent'])
def test_daily_child_creation_errors_are_visible(tmp_path,monkeypatch,mode):
    from benchmarks.attention_harness import daily_dev_v2 as daily
    def failed(args,**kwargs):
        kwargs['stdout'].write(b'ValueError: visible fixture creation reason\n')
        return NS(returncode=1)
    monkeypatch.setattr(daily.subprocess,'run',failed)
    bridge=DailyDevV2(tmp_path/'daily',[],per_run_budget=True)
    with pytest.raises(Exception,match='visible fixture creation reason'):
        bridge.create(dict(selection=selection('open_drawer',101,mode),start=False))

@pytest.mark.parametrize('source,shared_memory',[('parcc',False),('codex',False),('parcc',True)])
def test_ui_one_call_budget_envelope(tmp_path,monkeypatch,identity,source,shared_memory):
    from benchmarks.attention_harness import daily_dev_v2 as daily
    request=selection('open_cabinet',102,'one_call')
    for k in ('target_depth','target_valid_executions','max_versions','goal_key'):request.pop(k)
    request.update(model_source=source,budget_tier='dev',eval_agent={'enabled':True,'model':'gpt-6-astra' if source=='codex' else 'parcc/Qwen'},configuration=dict(goal_id='fixture-ui-envelope',
        experiment_budget=dict(daily.DEFAULT_CELL),cell_budget=dict(daily.DEFAULT_CELL),max_iterations=1,wall_seconds=21600))
    original=daily.subprocess.run
    def child(args,**kw):
        if 'benchmarks.attention_harness.dev_experiment' not in args:return original(args,**kw)
        value=lambda flag:args[args.index(flag)+1]
        selected=json.loads(Path(value('--selection')).read_bytes());config=selected.get('configuration')
        store=DevV2Store(value('--store'),**({} if config is None else dict(configuration=dict(goal_id=config['goal_id'],limits=config['experiment_budget'],wall_seconds=config['wall_seconds']))))
        e=exp.create(store,value('--artifacts'),selected);Path(value('--output')).write_text(json.dumps(e))
        return NS(returncode=0)
    monkeypatch.setattr(daily.subprocess,'run',child)
    memory=dict(base_url='http://127.0.0.1:1',store_id='test-store',namespace='test') if shared_memory else None
    bridge=DailyDevV2(tmp_path/'daily',[],per_run_budget=True,memory=memory)
    bridge.create(dict(selection=request,start=False))
    eid='devexp-'+hashlib.sha256(request['request_key'].encode()).hexdigest()[:24]
    m=exp.verify_manifest(exp.snapshot(bridge.store(eid),eid));c=m['configs']['open_cabinet:102']
    dc.validate_dev_config(c)
    assert c['execution_settings']['fixture_telemetry_hz']==20 and 'scene_contract' not in c
    if shared_memory:assert m['memory_authority']==memory


def test_settings_golden_against_starting_head(monkeypatch,settings):
    old=ModuleType('curobo_v2.phase3_old_settings');old.__package__='curobo_v2'
    monkeypatch.setitem(sys.modules,old.__name__,old)
    name='curobo_v2/execution_settings.py'
    exec(compile(subprocess.check_output(['git','show','da2b25b:'+name]),name,'exec'),old.__dict__)
    for name in ('R3_20261025','R4_20261027','R5_FIXTURE_20261031'):
        a=replace(getattr(es,name),planner_deployment=settings.planner_deployment)
        b=replace(getattr(old,name),planner_deployment=settings.planner_deployment)
        assert a.canonical_json()==b.canonical_json() and a.sha256==b.sha256

@pytest.mark.parametrize('row',registry.EXCLUSIONS)
def test_product_config_cannot_bypass_exclusion(monkeypatch,settings,row):
    product=es.from_config(dict(settings.to_dict(),fixture_telemetry_hz=20))
    monkeypatch.setattr(dc,'verify_lock',lambda *a,**k:None)
    with pytest.raises(ValueError,match='start_state_collision'):
        dc.make_dev_config(row['task_id'],row['seed'],dict(path='fake',sha256='fake'),execution_settings=product)


def test_product_physics_contact_payload_is_decimated(monkeypatch,fixture_types,settings):
    product=es.from_config(dict(settings.to_dict(),fixture_telemetry_hz=20))
    e,rows=appliance_executor(monkeypatch,fixture_types,product,offset=.02)
    e.env.sim.model.opt=NS(timestep=.002)
    ft.begin(e)
    for step in range(1,27):
        e.monitor()
        physics=[r for p,r in rows if p==e.physics][-1]
        assert ('fixture_contacts' in physics)==((step-1)%25==0)
        assert ('mimic_error_m' in physics)==((step-1)%25==0)
    assert e._fixture_motion['contact_steps']==26
    assert sum(p==e.physics for p,r in rows)==26
