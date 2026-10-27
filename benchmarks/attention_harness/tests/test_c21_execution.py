"""C21 profile, proxy and ordered-search regression tests; CPU fakes only."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace as NS

import numpy as np
import pytest

from curobo_v2 import execution_settings as es
from curobo_v2.grasp_heading import search, rotated, feedback, check_sdk_request
from curobo_v2.proxy_departure import support_departure
from test_dev_local_deployment import base, snapshot
from test_ik_path import fake_core


def test_r3_constant_and_wire_byte_identical(monkeypatch):
    path='curobo_v2/execution_settings.py'
    before=subprocess.check_output(['git','show','b553472:'+path],text=True)
    original=before.split('R3_20261025 = ',1)[1].split('\n\n',1)[0]
    assert Path(path).read_text().split('R3_20261025 = ',1)[1].split('\n\n',1)[0] == original
    prior=ModuleType('c21_prior_settings');prior.__package__='curobo_v2'
    monkeypatch.setitem(sys.modules,prior.__name__,prior)
    exec(compile(before,path,'exec'),prior.__dict__)
    pin=dict(lock_path=es.D06_LOCK,sha256='a'*64)
    monkeypatch.setattr(es,'_deployment_pin',lambda:pin)
    prior._deployment_pin=lambda:pin
    assert es.R3_20261025.canonical_json() == prior.R3_20261025.canonical_json()
    assert es.R3_20261025.sha256 == prior.R3_20261025.sha256
    assert es.from_config(prior.R3_20261025.to_dict()).canonical_json() == prior.R3_20261025.canonical_json()
    assert es.request_fields(None)=={}


def test_r4_dev_local_and_default_boundaries(base, monkeypatch):
    from openarm365_dev_v2 import dev_config as d
    monkeypatch.setattr(d,'ROOT',base.repo)
    r4=d.new_execution_settings()
    assert r4.profile=='r4-20261027'
    assert r4.grasp_heading_search==(.1,-.1,.2,-.2,.3,-.3,.5,-.5)
    assert r4.carried_proxy_support_departure=='monotone'
    r3=es.R3_20261025.to_dict();wire=r4.to_dict()
    assert {k:v for k,v in wire.items() if k in r3 and k not in ('profile','planner_deployment')} == {k:v for k,v in r3.items() if k not in ('profile','planner_deployment')}
    assert es.from_config(wire)==r4
    assert es.receipt_fields(r4)['formal_eligible'] is False
    for tier,boundary in [('legacy','dev_loop'),('legacy','formal'),('dev','formal')]:
        assert d.new_execution_settings(budget_tier=tier,budget_boundary=boundary) is None
        with pytest.raises(ValueError):
            d.validate_dev_config(dict(execution_settings=wire,budget_tier=tier,runner_boundary=boundary),budget_boundary=boundary)
    with pytest.raises(ValueError,match='dev-local'):
        es.from_config(dict(wire,planner_deployment=r3['planner_deployment']))
    for key in ('carried_proxy_support_departure','grasp_heading_search'):
        bad=deepcopy(wire);bad.pop(key)
        with pytest.raises(ValueError,match=key):es.from_config(bad)
    bad=deepcopy(wire);bad['grasp_heading_search'].reverse()
    with pytest.raises(ValueError,match='grasp_heading_search'):es.from_config(bad)
    wire['grasp_heading_search'][0]=99
    assert r4.grasp_heading_search[0]==.1


def support(name='table', x=0):
    return dict(name=name,center=[x,0,-.1],half_size=[.5,.5,.1],quat_wxyz=[1,0,0,0])


def spheres(z):return np.array([[[0,0,v,.02]] for v in z])


def test_proxy_monotone_contact_evidence():
    result=support_departure(spheres([.01,.015,.03]),[support()],{'table'})
    assert result['collision_free']
    assert result['mode']=='monotone'
    assert result['supports']['table']['start_separation_m']==pytest.approx([-.01])


@pytest.mark.parametrize('z',[[.01,.009],[.03,.019]])
def test_proxy_deeper_than_start_reported(z):
    assert not support_departure(spheres(z),[support()],{'table'})['collision_free']


@pytest.mark.parametrize('z',[[.01,.02,.019],[.01,.04,.01],[.03,.02]])
def test_proxy_uses_fixed_start_floor_like_exact_and_sweep(z):
    assert support_departure(spheres(z),[support()],{'table'})['collision_free']


def test_proxy_unmeasured_start_no_departure():
    assert not support_departure(spheres([.01]),[support()],{'table'},measured_start=False)['collision_free']


def search_core(monkeypatch, *, reject=None, margins=None):
    cls, core, q, targets, recorded=fake_core(monkeypatch, .1)
    core.execution_settings=es.R4_20261027
    core.attachment=None;core._attachment_context=None;core._world_cuboids=[]
    core.tool_frames=list(targets)
    core.check_targets=lambda t:t
    core.set_attachment=lambda c:setattr(core,'attachment',c)
    core.set_collision_world=lambda w:setattr(core,'_world_cuboids',deepcopy(w))
    batches=[];calls=[]
    class Tensor(np.ndarray):
        def detach(self):return self
        def cpu(self):return self
    tensor=lambda v:np.asarray(v).view(Tensor)
    def angle(goal):
        from scipy.spatial.transform import Rotation
        quat=next(iter(goal.values()))['quaternion_wxyz']
        return round(float(Rotation.from_quat(np.array(quat)[[1,2,3,0]]).as_rotvec()[2]),6)
    def batch(current,goals,active,locks):
        batches.append([angle(g) for g in goals])
        result=[]
        for goal in goals:
            value=np.zeros((1,1,7));value[0,0,0]=1-(margins or {}).get(angle(goal),.1)
            result.append(NS(solution=tensor(value),success=tensor([[True]])))
        return result
    core.heading_ik_batch=batch
    def motion(current, *, targets, **kw):
        a=angle(targets);stage='grasp' if core.attachment is None else 'lift' if next(iter(targets.values()))['position'][2]==.2 else 'place'
        calls.append((a,stage))
        core.last_failure=(reject or {}).get((a,stage))
        core.last_validation=dict(joint_limits=True,collision_free=core.last_failure is None)
        core.last_timing={'dt_seconds':.025,'time_from_start_seconds':[0.,.025]}
        core.last_derivatives={};core.last_solver_diagnostics=[];core.last_ik_path_metrics={}
        return None if core.last_failure else np.stack([current,current+.001])
    core.plan_ik_path=motion
    tool=next(iter(targets));lift=deepcopy(targets);lift[tool]['position'][2]=.2
    place=deepcopy(targets);place[tool]['position'][2]=.3
    context=dict(object_pose=np.eye(4).tolist(),shape_names=[],support_names=[])
    request=dict(lift_targets=lift,place_targets=place,attachment=context,world_vertical=[0,0,1])
    return core,q,targets,request,batches,calls


def run(core,q,targets,request):
    return search(core,q,targets,request,active_joints=core.joint_names[:7],locked_joints={},mode='ik_path',options={})


def test_heading_requested_first_and_no_deviation_if_feasible(monkeypatch):
    core,q,t,r,batches,calls=search_core(monkeypatch)
    assert run(core,q,t,r) is not None
    assert batches==[[0.]]
    assert calls==[(0.,'grasp'),(0.,'lift'),(0.,'place')]
    assert core.last_heading_search['heading_used_rad']==0.
    assert core.attachment is None and core._world_cuboids==[]


def test_heading_first_full_chain_and_019_gate(monkeypatch):
    core,q,t,r,batches,calls=search_core(monkeypatch,reject={(0.,'lift'):'fixture_collision',(-.1,'place'):'fixture_collision'},margins={.1:.019})
    assert run(core,q,t,r) is not None
    assert batches==[[0.],[.1,-.1,.2,-.2,.3,-.3,.5,-.5]]
    assert core.last_heading_search['heading_used_rad']==.2
    assert (.1,'grasp') not in calls
    rows=core.last_heading_search['heading_candidates']
    assert rows[1]['minimum_joint_margin_rad']==pytest.approx(.019)
    assert rows[1]['reason']=='ik_joint_margin_below_execution_limit'
    assert all(row['joint_margin_limit_rad']==.02 for row in rows)
    assert rows[2]['stages']['place']['feasible'] is False
    assert core.last_failure is None
    assert feedback(core.last_heading_search)['heading_used_rad']==.2


def test_heading_failure_retains_original_reason_and_order(monkeypatch):
    offsets=[0.,.1,-.1,.2,-.2,.3,-.3,.5,-.5]
    core,q,t,r,batches,calls=search_core(monkeypatch,reject={(a,'grasp'):'trajectory_validation_failed' for a in offsets})
    assert run(core,q,t,r) is None
    assert core.last_failure=='trajectory_validation_failed'
    assert core.last_heading_search['headings_tried_rad']==offsets
    assert 'heading_used_rad' not in core.last_heading_search
    assert core.attachment is None


@pytest.mark.parametrize('settings',[None,es.R3_20261025])
def test_old_profile_cannot_search(monkeypatch,settings):
    core,q,t,r,*_=search_core(monkeypatch);core.execution_settings=settings
    with pytest.raises(ValueError,match='r4'):run(core,q,t,r)


def test_world_vertical_rotation_and_sdk_schema():
    goal={'tool':dict(position=[1,2,3],quaternion_wxyz=[1,0,0,0])}
    assert rotated(goal,.2,[1,0,0])['tool']['quaternion_wxyz']==pytest.approx([np.cos(.1),np.sin(.1),0,0])
    assert goal['tool']['quaternion_wxyz']==[1,0,0,0]
    request=dict(object_name='obj',lift_position=[0,0,.2],place_position=[0,0,.3])
    check_sdk_request(request,{'obj':4})
    with pytest.raises(ValueError):check_sdk_request(request,{})
    with pytest.raises(ValueError):check_sdk_request(dict(request,lift_position=[0,False,0]),{'obj':4})


@pytest.mark.parametrize('fixture_clear,exact_clear,expected',[(True,True,True),(False,True,False),(True,False,False)])
def test_proxy_integration_never_masks_other_gates(monkeypatch,fixture_clear,exact_clear,expected):
    cls,core,q,targets,_=fake_core(monkeypatch,.1)
    class Tensor(np.ndarray):
        def detach(self):return self
        def cpu(self):return self
        def numpy(self):return np.asarray(self)
        def contiguous(self):return self
        def split(self,n):return [self[i:i+n] for i in range(0,len(self),n)]
    tensor=lambda v:np.asarray(v).view(Tensor)
    class Scene:pass
    scene=Scene();scene._support_names={'table'};scene._object_names={'obj'};scene._attached_indices=(0,)
    module=ModuleType('curobo_v2.service.curobo_service.contact_scene');module.CarriedSceneCollision=Scene
    monkeypatch.setitem(sys.modules,module.__name__,module)
    planner=NS(joint_names=core.joint_names,scene_collision_checker=scene,
               compute_kinematics=lambda state:NS(robot_spheres=tensor(spheres([.01,.02,.03]))))
    checker=NS(scene_model=scene,validate=lambda values:tensor([fixture_clear]*len(values)))
    core._state=lambda dense,planner:NS(position=tensor(dense))
    core.max_joint_step=1.;core.execution_settings=es.R4_20261027
    core.attachment=NS(supports={'table'},names={'obj'},sphere_count=1)
    core._world_cuboids=[support(),dict(support('obj'),center=[0,0,.01])]
    core.geometry=NS(validate=lambda *a,**kw:dict(collision_free=exact_clear))
    core._models={'test':(planner,checker,None,{})}
    assert cls.validate(core,np.zeros((3,14)),planner,checker) is expected
    assert core.last_validation['support_departure']['collision_free']
    assert core.last_validation['proxy_collision_free'] is fixture_clear


def test_proxy_other_support_penetration_reported():
    values=np.array([[[0,0,.01,.02]],[[.6,0,.019,.02]]])
    assert not support_departure(values,[support(),support('other',1.1)],{'table','other'})['collision_free']


def test_real_heading_batch_api_shape_and_order(monkeypatch):
    cls,core,q,targets,_=fake_core(monkeypatch,.1)
    class Tensor(np.ndarray):
        def clone(self):return self.copy()
    tensor=lambda v:np.asarray(v).view(Tensor)
    import curobo_v2.service.curobo_service.planner_core as module
    monkeypatch.setattr(module.torch,'as_tensor',lambda v,**kw:tensor(v),raising=False)
    captured={}
    class Goal:
        @classmethod
        def from_poses(cls,poses,*,num_goalset):
            captured.update(poses=poses,num_goalset=num_goalset)
            return poses
    types=ModuleType('curobo.types');types.Pose=NS;types.GoalToolPose=Goal
    monkeypatch.setitem(sys.modules,'curobo.types',types)
    planner=next(iter(core._models.values()))[0]
    def select(*args, **kwargs):
        assert kwargs['heading_batch'] is True
        return planner, None
    core._select = select
    def solve(goal,*,current_state,return_seeds):
        count=len(next(iter(goal.values())).position)
        captured.update(state=current_state,seeds=return_seeds)
        return NS(solution=tensor(np.zeros((count,8,7))),success=tensor(np.ones((count,8),bool)))
    planner.ik_solver.solve_pose=solve
    core._device='cpu';core.tool_frames=list(targets);core.default_quaternion=[1,0,0,0]
    core.check_targets=lambda goal:cls.check_targets(core,goal)
    offsets=list(es.R4_20261027.grasp_heading_search)
    result=cls.heading_ik_batch(core,q,[rotated(targets,a,[0,0,1]) for a in offsets],core.joint_names[:7],{})
    assert len(result)==8 and all(r.solution.shape==(1,8,7) for r in result)
    assert captured['num_goalset']==1 and captured['state'].shape==(8,14)
    quat=next(iter(captured['poses'].values())).quaternion
    assert 2*np.arctan2(quat[:,3],quat[:,0])==pytest.approx(offsets)


def test_heading_client_failure_public_list(tmp_path,monkeypatch):
    from openarm365_dev_v2.tests.test_r3_planning_rejection import client_for
    from curobo_v2.client import PlanningRejected
    offsets=[0.,.1,-.1,.2,-.2,.3,-.3,.5,-.5]
    response=dict(status='failed',reason='official_ik_non_success',headings_tried_rad=offsets)
    client=client_for(tmp_path,monkeypatch,response)
    with pytest.raises(PlanningRejected,match='headings tried') as caught:
        client.call('/plan/ik_path',dict(env_id='robocasa365'))
    assert str(caught.value)=='cuRoboV2 planning rejected: official_ik_non_success; headings tried (rad): '+str(offsets)


def test_sdk_public_feedback_keeps_heading():
    from openarm365_dev.owner import build_sdk
    events=[]
    result=dict(heading_used_rad=-.2,headings_tried_rad=[0,.1,-.1,.2,-.2],heading_candidates=[])
    sdk=build_sdk(NS(rpc=lambda *a,**kw:result),events.append)
    assert sdk.left_arm.move_delta(0,0,-.01,grasp_search={})==result
    assert events[0]['result']['heading_used_rad']==-.2


from test_reference_policy import tree, r3_deployment


def test_reference_verdict_accepts_recorded_r4_labels(tree,monkeypatch,r3_deployment):
    # Snapshot authentication is exercised above with a synthetic actual lock;
    # here reuse the reference script's frozen evidence tree to test its labels.
    from scripts.run_reference_policy import build_verdict
    import sqlite3
    value=replace(es.R4_20261027,planner_deployment=r3_deployment.planner_deployment)
    monkeypatch.setattr(es,'_require_dev_local',lambda pin:None)
    def update(v):
        if isinstance(v,list):return [update(row) for row in v]
        if not isinstance(v,dict):return v
        v={k:update(row) for k,row in v.items()}
        if 'execution_settings' in v:
            v['execution_settings']=value.to_dict()
            v['planner_source']=dict(kind='dev-local',source_root='/synthetic')
        if 'execution_profile' in v:
            v.update(execution_profile='r4-20261027',deployment_kind='dev-local',formal_eligible=False)
        return v
    for path in tree.root.rglob('*.json'):
        path.write_text(json.dumps(update(json.loads(path.read_text()))))
    for path in tree.root.rglob('*.jsonl'):
        path.write_text(''.join(json.dumps(update(json.loads(row)))+'\n' for row in path.read_text().splitlines()))
    with sqlite3.connect(tree.root/'store.sqlite') as db:
        payload=db.execute('SELECT payload FROM v2_jobs').fetchone()[0]
        db.execute('UPDATE v2_jobs SET payload=?',(json.dumps(update(json.loads(payload))),))
    verdict=build_verdict(tree.root,'exp')
    assert verdict['verdict']=='success',verdict


def test_adapter_reports_heading_and_scope_clears_it(monkeypatch):
    cls,core,q,targets,_=fake_core(monkeypatch,.1)
    from curobo_v2.service.curobo_service.planner import PlannerAdapter
    core.last_heading_search=dict(heading_used_rad=.3,headings_tried_rad=[0,.1,-.1,.2,-.2,.3],heading_candidates=[])
    core.last_validation=dict(joint_limits=True,collision_free=True)
    core.last_failure=None;core.last_solver_diagnostics=[];core.identity={}
    core.last_timing=dict(dt_seconds=.025,time_from_start_seconds=[0,.025]);core.last_derivatives={}
    core.reorder=lambda result,names:result
    adapter=PlannerAdapter.__new__(PlannerAdapter);adapter._planner=core
    adapter._world=NS(active=lambda:'world',get=lambda _:NS(revision=1,sha256='world'))
    assert adapter._result(np.zeros((2,14)),None)['heading_used_rad']==.3
    core.last_heading_search=None
    assert 'heading_used_rad' not in adapter._result(np.zeros((2,14)),None)
