"""Offline T1 regression: no simulator, GPU, model assets or live services."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace as NS

import numpy as np
import pytest
from benchmarks.attention_harness import dev_tasks
from benchmarks.attention_harness.tests.c21_fixtures import base, r4_deployment
from benchmarks.attention_harness.tests.r3_fixtures import r3_deployment
from benchmarks.attention_harness.tests.test_reference_policy import tree
from openarm365_dev_v2 import dev_config
from openarm365_dev_v2.environment import initial_joints
from openarm365_dev_v2.scene_contract import fingerprint, seed_entry
from scripts import run_reference_policy as runner
from scripts.m11_cabinet_probe import TASK, parse_seeds, interior_regions
from scripts.m11.make_cabinet_reference import generate
from scripts.m11.build_scene_contract import build

ROOT=Path(__file__).resolve().parents[3]


def baseline(path, monkeypatch):
    name='m11_baseline_'+Path(path).stem
    module=ModuleType(name);module.__file__=str(ROOT/path)
    module.__package__=str(Path(path).parent).replace('/','.')
    monkeypatch.setitem(sys.modules,name,module)
    raw=subprocess.check_output(['git','show','e62021f:'+path],cwd=ROOT)
    exec(compile(raw,path,'exec'),module.__dict__)
    return module


def test_registration_family_prompt():
    from attention_memory_service.families import family_of
    assert list(dev_tasks.TASKS)==['pick_place_counter_to_sink','open_cabinet']
    assert TASK not in [row['task_id'] for row in dev_tasks.options()['tasks']]
    task=dev_tasks.get_task(TASK)
    assert task.official_env=='PickPlaceCounterToCabinet'
    assert task.task_prompt=='Pick the object from the counter and place it in the cabinet.'
    assert task.seeds==tuple(range(101,126))
    assert family_of(TASK)==family_of(runner.TASK)=='pick_and_place'
    assert family_of('open_cabinet') is None
    from openarm365_dev.policy_validation import validate_generated_policy
    validate_generated_policy(Path(task.initial_policy).read_text())
    assert initial_joints(TASK)==initial_joints(runner.TASK)


def test_frozen_prompt_digest(monkeypatch):
    original=Path.read_bytes
    def read(path):
        data=original(path)
        return data+b' ' if path.name==TASK+'.json' else data
    monkeypatch.setattr(Path,'read_bytes',read)
    with pytest.raises(ValueError,match='digest'):
        dev_tasks.register_dev_task(dev_tasks.get_task(TASK))


@pytest.mark.parametrize('seed',[100,126,1001,1010,True,101.0])
def test_held_out_and_invalid_seeds(seed):
    with pytest.raises(ValueError):dev_tasks.validate_selection(TASK,seed)


@pytest.mark.parametrize('tier,boundary',[('legacy','dev_loop'),('legacy','formal'),('dev','formal')])
def test_dev_only(tier,boundary):
    with pytest.raises(ValueError,match='cabinet pick/place requires dev'):
        dev_config._make_dev_config(TASK,101,{},budget_tier=tier,budget_boundary=boundary)


def test_configs_and_sink_bytes_unchanged(monkeypatch,r4_deployment):
    monkeypatch.setattr(dev_config,'verify_lock',lambda *a,**k:None)
    old=baseline('openarm365_dev_v2/dev_config.py',monkeypatch)
    old.verify_lock=lambda *a,**k:None
    before=old.make_dev_config(runner.TASK,102,{'path':'fixture','sha256':'fixture'})
    after=dev_config.make_dev_config(runner.TASK,102,{'path':'fixture','sha256':'fixture'})
    assert json.dumps(before).encode()==json.dumps(after).encode()
    cabinet=dev_config.make_dev_config(TASK,125,{'path':'fixture','sha256':'fixture'})
    assert dev_config.validate_dev_config(cabinet)==cabinet
    assert cabinet['initial_joint_targets']==after['initial_joint_targets']
    assert cabinet['layout_id']==11 and cabinet['style_id']==34
    old_tasks=baseline('benchmarks/attention_harness/dev_tasks.py',monkeypatch)
    for tid in [runner.TASK,'open_cabinet']:
        assert dev_tasks.get_task(tid).public(after)==old_tasks.get_task(tid).public(after)
        assert dev_tasks.get_task(tid).system_prompt(after).encode()==old_tasks.get_task(tid).system_prompt(after).encode()
    assert dev_tasks.dev_sdk_seal(after)==old_tasks.dev_sdk_seal(after)
    assert runner.selection(102,'source',{})==baseline('scripts/run_reference_policy.py',monkeypatch).selection(102,'source',{})


def test_sealed_assets_and_existing_manifests_untouched():
    # Git byte comparison of tracked configuration/manifest/contract records only.
    names=subprocess.check_output(['git','ls-files','openarm365/configs','openarm365_dev_v2/scene_contracts',
                                   'openarm365_dev_v2/policies/pick_place_counter_to_sink.py',
                                   'benchmarks/attention_harness/robocasa365/config.py'],text=True,cwd=ROOT).splitlines()
    assert names
    for name in names:
        assert (ROOT/name).read_bytes()==subprocess.check_output(['git','show','e62021f:'+name],cwd=ROOT)
    changed=subprocess.check_output(['git','diff','--name-only','e62021f'],text=True,cwd=ROOT).splitlines()
    fixture_rule_files={'openarm365_dev/'+name+'.py' for name in (
        'executor','planner','policy_validation','policy_worker','sandbox','world','base_motion')}
    assert not any((n.startswith('openarm365_dev/') and n not in fixture_rule_files) or 'manifest' in n.lower() for n in changed)


def test_native_evidence_matches_upstream_and_public_fixture(monkeypatch):
    from openarm365_dev_v2.worker import native_evaluation, cabinet_public_values
    calls=[]
    ou=NS(obj_inside_of=lambda *a,**kw:calls.append((a,kw)) or True)
    package=ModuleType('robocasa'); utils=ModuleType('robocasa.utils');utils.object_utils=ou
    monkeypatch.setitem(sys.modules,'robocasa',package);monkeypatch.setitem(sys.modules,'robocasa.utils',utils)
    env=NS(cab=NS(pos=[1.,2.,3.]),_check_success=lambda:False)
    value=native_evaluation(env,dict(task_id=TASK,execution_settings={'profile':'r4-20261027'}))
    assert value['native_success'] is False and value['object_in_cabinet'] is True
    assert calls[-1]==((env,'obj',env.cab),{})
    assert 'partial_check=False' in value['object_in_cabinet_source']
    assert cabinet_public_values(env)=={'cabinet_pos':[1.,2.,3.]}
    env.sink=object()
    assert native_evaluation(env,dict(task_id=runner.TASK,execution_settings={'profile':'r4-20261027'}))['object_in_sink']
    assert calls[-1][1]=={'partial_check':True}


def camera_env(**fixtures):
    model=NS(cam_pos=np.zeros((1,3)),cam_quat=np.zeros((1,4)),
             camera_name2id=lambda name:0)
    forwards=[]
    return NS(**fixtures,sim=NS(model=model,forward=lambda:forwards.append(True))),forwards


@pytest.mark.parametrize('height',[.45,1.75])
def test_cabinet_display_camera_without_sink_or_fxtr(height):
    from openarm365.environment import configure_display_camera
    from openarm365_dev_v2.extra_views import look_target
    position=np.array([.2,-.4,height])
    env,forwards=camera_env(cab=NS(pos=position))
    configure_display_camera(env)
    np.testing.assert_array_equal(env.sim.model.cam_pos[0],position+[1.7,-1.2,1.25])
    from scipy.spatial.transform import Rotation
    rotation=Rotation.from_quat(env.sim.model.cam_quat[0][[1,2,3,0]]).as_matrix()
    offset=np.array([1.7,-1.2,1.25])
    np.testing.assert_allclose(rotation[:,2],offset/np.linalg.norm(offset))
    np.testing.assert_array_equal(look_target(env),position)
    np.testing.assert_array_equal(position,[.2,-.4,height])
    assert forwards==[True]


@pytest.mark.parametrize('fixture',['sink','fxtr'])
def test_legacy_display_camera_bytes_unchanged(fixture,monkeypatch):
    from openarm365.environment import configure_display_camera
    old=baseline('openarm365/environment.py',monkeypatch)
    before,before_calls=camera_env(**{fixture:NS(pos=[.2,-.4,.8])})
    after,after_calls=camera_env(**{fixture:NS(pos=[.2,-.4,.8])})
    old.configure_display_camera(before)
    configure_display_camera(after)
    assert before.sim.model.cam_pos.tobytes()==after.sim.model.cam_pos.tobytes()
    assert before.sim.model.cam_quat.tobytes()==after.sim.model.cam_quat.tobytes()
    assert before_calls==after_calls==[True]


def test_reference_task_cli(monkeypatch,tmp_path):
    calls=[]
    monkeypatch.setattr(runner,'run',lambda *a:calls.append(a) or {'verdict':'failure'})
    assert runner.main(['--task',TASK,'--seed','125','--source','policy.py','--out',str(tmp_path/'out')])==1
    assert calls[0][-1]==TASK
    with pytest.raises(ValueError,match='explicit candidate'):runner.contract_ref(102,task=TASK)
    assert runner.selection(125,'source',{},TASK)['tasks']==[TASK]


def test_cabinet_verdict_requires_own_evidence(tree):
    for path in tree.root.rglob('config.json'):
        value=json.loads(path.read_text());value['task_id']=TASK;runner.write_json(path,value)
    manifest_path=tree.root/'artifacts/exp/manifest.json'
    value=json.loads(manifest_path.read_text());next(iter(value['configs'].values()))['task_id']=TASK
    value['selection']['tasks']=[TASK];runner.write_json(manifest_path,value)
    p=tree.root/'reference.json';value=json.loads(p.read_text());value['task_id']=TASK;runner.write_json(p,value)
    assert runner.build_verdict(tree.root,'exp')['verdict']=='invalid'
    runner.write_json(tree.episode/'native_result.json',dict(evaluated=True,native_success=True,
        object_in_cabinet=True,object_in_cabinet_source='fixture'))
    value=runner.build_verdict(tree.root,'exp')
    assert value['verdict']=='success' and value['object_in_cabinet'] is True
    assert 'object_in_sink' not in value


def sample_probe():
    sites={'shelf':[np.array([-.3,0.,1.]),np.array([.3,0.,1.]),np.array([-.3,.4,1.]),np.array([-.3,0.,1.5])]}
    regions=interior_regions(NS(get_int_sites=lambda **kw:sites))
    return dict(task_id=TASK,seed=101,scene_reset_seed=101,layout_style=[11,34],
        same_normalized_public_state=True,motion_calls=0,planner_calls=0,
        execution_settings={'profile':'r4-20261027'},resets=[dict(scene=dict(
            target=dict(public_name='obj',aabb_world_m=[[.1,-.3,.8],[.15,-.25,.85]],grasp_axis_world_xy_rad=0.),
            cabinet=dict(interior_regions=regions)))])


@pytest.mark.parametrize('arm',['left','right'])
def test_generated_template_valid_and_bounded(arm):
    from openarm365_dev.policy_validation import validate_generated_policy
    source=generate(sample_probe(),arm=arm,base_dx=.45,base_dy=.20,base_dyaw=.65)
    validate_generated_policy(source)
    assert 'grasp_search=' in source and 'diagnostic only' in source and arm+'_gripper.close()' in source
    # Run the restricted source against a numeric SDK fake; no simulator/planner.
    position=[0.,-.5,1.2];quat=[1.,0.,0.,0.];events=[]
    def arm_move(dx,dy,dz,rotation_delta=None,**kw):
        assert sum(v*v for v in [dx,dy,dz])**.5<=.05+1e-9
        for i,v in enumerate([dx,dy,dz]):position[i]+=v
        if rotation_delta is not None:
            from scipy.spatial.transform import Rotation
            quat[:]=(Rotation.from_rotvec(rotation_delta)*Rotation.from_quat(quat)).as_quat().tolist()
        events.append(('move',list(position),kw))
    def base_move(dx,dy,yaw):
        assert (dx*dx+dy*dy)**.5<=.20 and abs(yaw)<=.30
        events.append(('base',None,{}))
    sdk=ModuleType('robot_sdk')
    sdk.sensors=NS(get_observation=lambda:{'robot0_'+arm+'_eef_pos':position[:],'robot0_'+arm+'_eef_quat':quat[:]},
        find_objects=lambda:[{'name':'obj'},{'name':'cabinet'}])
    setattr(sdk,arm+'_arm',NS(move_delta=arm_move))
    setattr(sdk,arm+'_gripper',NS(open=lambda:events.append(('open',position[:],{})),close=lambda:events.append(('close',position[:],{}))))
    sdk.base=NS(move_delta=base_move)
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(sys.modules,'robot_sdk',sdk)
        scope={};exec(source,scope)
    assert scope['calls'][0]<=320
    search=[e for e in events if e[2].get('grasp_search')]
    assert len(search)==1 and search[0][2]['grasp_search']['object_name']=='obj'
    close=next(i for i,e in enumerate(events) if e[0]=='close')
    assert events[close-1][2].get('grasp_search')
    release=max(i for i,e in enumerate(events) if e[0]=='open')
    assert events[release-1][1]==pytest.approx(scope['place'])
    assert events[-1][1]==pytest.approx(scope['safe_front'])


def test_probe_region_and_seed_guards():
    regions=sample_probe()['resets'][0]['scene']['cabinet']['interior_regions']
    assert regions['shelf']['aabb_world_m']==[[-.3,0.,1.],[.3,.4,1.5]]
    assert parse_seeds('101-125')==list(range(101,126))
    for seeds in ['1001','101,101','126','125-101']:
        with pytest.raises(argparse.ArgumentTypeError):parse_seeds(seeds)
    with pytest.raises(ValueError):generate(sample_probe(),entry_depth=1.)
    with pytest.raises(ValueError):generate(sample_probe(),base_dx=float('nan'))


def write_probes(folder):
    for i in range(2):
        value=sample_probe();value['pid']=100+i
        state={'robot':{'joint':[.1,.2]},'objects':{'obj_pos':[.1,.2,.3]},'fixtures':{}}
        value['resets']=[dict(reset_index=j,state=state,kp={'left':[1500]*7,'right':[1500]*7},
            normalized_sha256=fingerprint(state,True),raw_sha256=fingerprint(state)) for j in range(2)]
        path=folder/'seed101'/f'process-{i+1:03d}'/'probe.json';path.parent.mkdir(parents=True)
        runner.write_json(path,value)
        runner.write_json(path.with_name('receipt.json'),dict(pid=value['pid'],returncode=0,
            probe_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))


def test_candidate_contract_and_tampering(tmp_path):
    write_probes(tmp_path)
    contract=build(tmp_path,[101],2)
    assert seed_entry(contract,TASK,101)['resets']==4
    assert contract['provenance']['registered'] is False
    path=tmp_path/'seed101/process-002/probe.json'
    value=json.loads(path.read_text());value['resets'][1]['state']['objects']['obj_pos'][0]=4.
    runner.write_json(path,value)
    with pytest.raises(ValueError,match='receipt'):build(tmp_path,[101],2)


def test_probe_twice_uses_canonical_config_without_motion(monkeypatch,tmp_path,r4_deployment):
    """Exercise orchestration with an inert env; a step/planner API is absent."""
    from contextlib import nullcontext
    from scripts import m11_cabinet_probe as probe
    from openarm365_dev_v2 import environment, scene_contract
    from openarm365_dev import identity
    from benchmarks.attention_harness.diagnostics import object_proxy_measure
    calls=[]
    env=NS(robots=[NS(composite_controller=NS(part_controllers={
        side:NS(kp=np.full(7,1500)) for side in ('left','right')}))],
        reset=lambda:calls.append('reset'),close=lambda:calls.append('close'))
    def create(name,**kwargs):
        assert name=='PickPlaceCounterToCabinet'
        assert kwargs['seed']==kwargs['scene_reset_seed']==125
        assert kwargs['layout_and_style_ids']==[(11,34)]
        assert kwargs['horizon']==9600
        return env
    monkeypatch.setattr(environment,'create_env',create)
    monkeypatch.setattr(dev_config,'make_lock',lambda *a,**k:{'path':'fixture','sha256':'fixture'})
    monkeypatch.setattr(dev_config,'verify_lock',lambda *a,**k:None)
    monkeypatch.setattr(identity.OfficialEnvironmentIdentity,'verify_identity',lambda self:{'offline':True})
    physical=ModuleType('openarm365.environment')
    physical.validate_physical_model=lambda e:calls.append('physical_check')
    physical.configure_display_camera=lambda e:None
    monkeypatch.setitem(sys.modules,'openarm365.environment',physical)
    monkeypatch.setattr(object_proxy_measure,'writable_mjcf_assets',lambda out:nullcontext())
    monkeypatch.setattr(scene_contract,'capture_state',lambda e:{'robot':{'q':[.1]},'objects':{},'fixtures':{}})
    monkeypatch.setattr(probe,'geometry',lambda e:{'offline':True})
    result=probe.probe_once(125,tmp_path/'probe')
    assert calls==['reset','physical_check','reset','physical_check','close']
    assert result['same_normalized_public_state'] and result['planner_calls']==result['motion_calls']==0
    assert result['scene_reset_seed']==125
    assert result['execution_settings']['goal_joint_margin_rad']==.02
    assert len(result['resets'])==2


def test_cabinet_memory_context_uses_explicit_catalog(monkeypatch,tmp_path,r4_deployment):
    from benchmarks.attention_harness import dev_planner_memory as memory
    from benchmarks.attention_harness.memory_cross_seed import target_config
    monkeypatch.setattr(dev_config,'verify_lock',lambda *a,**k:None)
    config=dev_config.make_dev_config(TASK,101,{'path':'fixture','sha256':'fixture'})
    contract=tmp_path/'candidate.json'
    runner.write_json(contract,dict(schema='attentionbench.public-scene-contract.v2',tolerance=1e-6,
        tasks={TASK:dict(seeds={str(s):{'normalized_sha256':'a'*64} for s in (101,102)})}))
    ref=dict(uri=str(contract),sha256=hashlib.sha256(contract.read_bytes()).hexdigest())
    config.update(scene_contract=ref,scene_reset_seed=101)
    monkeypatch.setattr(memory,'sdk_identity',lambda c:dict(contract=dev_tasks.dev_sdk(c),prompt=dev_tasks.dev_sdk_prompt(c),implementation={}))
    catalog=dict(role='contract',root=str(tmp_path),files={contract.name:ref['sha256']})
    ctx=memory.context(config,'m11-offline',contract_catalog=catalog)
    assert ctx['task_id']==TASK and ctx['task_variant_id'].startswith('PickPlaceCounterToCabinet:')
    assert ctx['sdk_interface_sha256']==config['dev_sdk_sha256']
    assert memory.memory_scope(config,'interactive')['level']=='reference_only'
    path=tmp_path/'config.json';runner.write_json(path,config)
    origin=dict(config=dict(uri=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest()),
                cross_seed=dict(seed=102,contract=ref,runs=1))
    moved=target_config(origin)
    assert moved['task_id']==TASK and moved['seed']==moved['scene_reset_seed']==102
    assert dev_config.validate_dev_config(moved)==moved


@pytest.mark.parametrize('fault',['pid','reset','kp','state','unstable','profile'])
def test_contract_rejects_invalid_evidence(tmp_path,fault):
    write_probes(tmp_path)
    path=tmp_path/'seed101/process-002/probe.json'
    value=json.loads(path.read_text())
    if fault=='pid':value['pid']=100
    elif fault=='reset':value['resets'][1]['reset_index']=0
    elif fault=='kp':value['resets'][0]['kp']['left'][0]=300
    elif fault=='profile':value['execution_settings']['profile']='r3-20261025'
    else:
        row=value['resets'][1]
        row['state']['robot']['joint'][0]=.4
        if fault=='unstable':
            row['normalized_sha256']=fingerprint(row['state'],True)
            row['raw_sha256']=fingerprint(row['state'])
    runner.write_json(path,value)
    runner.write_json(path.with_name('receipt.json'),dict(pid=value['pid'],returncode=0,
        probe_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    with pytest.raises(ValueError):build(tmp_path,[101],2)


def test_cabinet_extra_view_target():
    from openarm365_dev_v2.extra_views import look_target
    assert look_target(NS(cab=NS(pos=[1.,2.,1.7]))).tolist()==[1.,2.,1.7]
    assert look_target(NS(sink=NS(pos=[1.,2.,.9]))).tolist()==[1.,2.,1.]
