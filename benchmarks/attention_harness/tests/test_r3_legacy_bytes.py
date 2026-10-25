"""Compare fixed inputs with pre-C2 source at 3344c6f, not a re-encoded golden."""
import io
import json
import subprocess
from threading import RLock
from types import ModuleType, SimpleNamespace as NS
import numpy as np
import pytest
from curobo_v2 import execution_settings as es
from test_ik_path import fake_core
import test_r3_support_departure as support


def baseline(path):
    source = subprocess.check_output(['git', 'show', '3344c6f:'+path], text=True)
    module = ModuleType('legacy_baseline')
    module.__package__ = path.rsplit('/', 1)[0].replace('/', '.')
    exec(compile(source, path, 'exec'), module.__dict__)
    return module


def wire(value):
    return json.dumps(value, allow_nan=False).encode()


@pytest.mark.parametrize('path', ['/plan', '/plan/joint', '/plan/ik', '/plan/ik_path', '/plan/cooperative'])
def test_legacy_http_request_and_response_bytes(monkeypatch, path):
    import curobo_v2.client as now
    before = baseline('curobo_v2/client.py')
    bodies = []
    raw = b'{"status": "failed", "reason": "fixed fake solver"}'
    def urlopen(request, timeout):
        bodies.append(request.data)
        response = io.BytesIO(raw)
        response.headers = {'x-curobo-instance':'nonce','x-curobo-lock':'sha'}
        return response
    monkeypatch.setattr(now, 'urlopen', urlopen); before.urlopen = urlopen
    results = []
    body = {'env_id':'fixed', 'current_q':[0.]*7, 'attachment':None}
    for cls, request in [(before.V2Client,body), (now.V2Client,dict(body, **es.request_fields(es.LEGACY)))]:
        client = cls.__new__(cls)
        client._check_live = lambda: None
        client.deadline = 1e15; client.url = 'http://127.0.0.1:1'; client.token = 'fake'
        client.nonce = 'nonce'; client.lock_sha = 'sha'
        results.append(wire(client._request(path, request)))
    assert bodies[0] == bodies[1] == wire(body)
    assert results[0] == results[1]


@pytest.mark.parametrize('method', ['plan_pose', 'plan_joints', 'plan_cooperative', 'solve_ik'])
@pytest.mark.parametrize('success', [False, True])
def test_legacy_adapter_response_bytes(monkeypatch, method, success):
    fake_core(monkeypatch, .1)  # Authorize import using a fake, without a service.
    from curobo_v2.service.curobo_service.planner import PlannerAdapter
    old = baseline('curobo_v2/service/curobo_service/planner.py').PlannerAdapter
    rows = []
    for cls in (old, PlannerAdapter):
        adapter = cls.__new__(cls); adapter._lock = RLock()
        adapter._ensure_world = lambda env: None
        result = None if not success else np.zeros(7) if method == 'solve_ik' else np.zeros((2, 7))
        core = NS(named=lambda q,names=None:np.asarray(q),reorder=lambda q,names:q,
                  set_attachment=lambda a:None, joint_names=list('abcdefg'),
                  last_validation={'joint_limits':True,'collision_free':True},last_failure=None,
                  last_solver_diagnostics=[],identity={'robot':'fixed'},
                  last_timing={'dt_seconds':.1,'time_from_start_seconds':[0.,.1]},
                  last_derivatives={'velocity':np.zeros((2,7))})
        setattr(core, method, lambda *a,**kw:result)
        adapter._planner = core
        adapter._world = NS(active=lambda:'fixed',get=lambda env:NS(revision=1,sha256='world'))
        args = dict(env_id='fixed')
        if method == 'solve_ik': args['seed_q'] = [0.]*7
        else: args['current_q'] = [0.]*7
        if method == 'plan_joints': args['target_q'] = [0.]*7
        if method == 'plan_cooperative': args.update(object_pose=np.eye(4).tolist(),attachment={})
        rows.append(wire(getattr(adapter,method)(**args)))
    assert rows[0] == rows[1]


def test_legacy_ik_solve_stage_bytes(monkeypatch):
    cls, core, q, targets, _ = fake_core(monkeypatch, [.1, .2])
    old = baseline('curobo_v2/service/curobo_service/planner_core.py').CuroboPlanner
    import curobo_v2.service.curobo_service.planner_core as module
    monkeypatch.setattr(module.time, 'monotonic', lambda:100.)
    rows=[]
    for impl in (old, cls):
        result=impl.plan_ik_path(core,q,targets=targets,active_joints=core.joint_names[:7],ik_candidates=2)
        rows.append(wire(dict(trajectory=result.tolist(),validation=core.last_validation,
                             timing=core.last_timing,metrics=core.last_ik_path_metrics)))
    assert rows[0] == rows[1]


@pytest.mark.parametrize('separations', [[-.004], [-.00005, -.00001], [.003]])
def test_legacy_geometry_bytes(monkeypatch, separations):
    current = support.ExactGeometryValidator
    old = baseline('curobo_v2/service/curobo_service/geometry.py').ExactGeometryValidator
    rows=[]
    for cls in (old,current):
        monkeypatch.setattr(support,'ExactGeometryValidator',cls)
        rows.append(wire(support.validate(monkeypatch,None,separations)))
    assert rows[0] == rows[1]


def test_legacy_sweep_request_response_bytes(monkeypatch):
    import curobo_v2.sweep as now
    old = baseline('curobo_v2/sweep.py')
    captured=[]
    def validator(*args,**kwargs):
        captured.append(wire(dict(args=args,kwargs=kwargs)))
        return NS(certify=lambda response:{'result':'PASS','input_sha256':now.input_sha256(response)})
    monkeypatch.setattr(now,'SweepValidator',validator); old.SweepValidator=validator
    response=dict(status='success',joint_names=['joint'],trajectory=[[0.],[0.]],time_from_start_seconds=[0.,1.],
                  trajectory_derivatives={'velocity':[[0.],[0.]],'acceleration':[[0.],[0.]]})
    args=dict(urdf='/fake',world=[],locks={},attachment=None,position_limits=[[-1,1]],
              maximum_speed={'joint':1.},max_acceleration=10.,max_jerk=100.)
    a=old.certify_trajectory(response,**args); b=now.certify_trajectory(response,**args)
    assert captured[0] == captured[1]
    assert wire(a) == wire(b)


def test_request_scope_and_sha_do_not_leak(monkeypatch):
    fake_core(monkeypatch,.1)
    from curobo_v2.service.curobo_service.planner import execution_request
    monkeypatch.setattr(es,'_deployment_pin',lambda:dict(lock_path=es.D06_LOCK,sha256='a'*64))
    owner=NS(_lock=RLock(),_planner=NS(geometry=NS()))
    seen=[]
    @execution_request
    def request(self, fail=False):
        seen.append(self._planner.execution_settings)
        if fail: raise ValueError('fake failure')
        return {'status':'failed'}
    assert request(owner,execution_settings=es.R3_20261025.to_dict())['execution_settings_sha256']==es.R3_20261025.sha256
    assert request(owner)=={'status':'failed'} and seen[-1] is None
    with pytest.raises(ValueError,match='fake failure'): request(owner,True,execution_settings=es.R3_20261025.to_dict())
    assert owner._planner.execution_settings is None


def test_http_models_and_routes_forward_explicit_settings(monkeypatch,tmp_path):
    import sys
    from pathlib import Path
    from pydantic import ValidationError
    config=tmp_path/'robot.yml';config.write_text('robot_cfg:\n  kinematics:\n    base_link: base\nservice: {}\n')
    prefix='curobo_v2.service.curobo_service.'
    cfg=ModuleType(prefix+'config');cfg.Config=NS(ROBOT_CFG=str(config),MAX_ENVS=1,DEVICE='cuda:0',DEFAULT_ENV='default')
    called=[]
    fake=NS(plan_joints=lambda **kw:called.append(kw) or {'status':'failed'})
    pm=ModuleType(prefix+'planner');pm.PlannerAdapter=lambda **kw:fake
    wm=ModuleType(prefix+'world');wm.WorldStore=lambda **kw:object()
    for name,module in [('config',cfg),('planner',pm),('world',wm)]:monkeypatch.setitem(sys.modules,prefix+name,module)
    monkeypatch.setattr(es,'_deployment_pin',lambda:dict(lock_path=es.D06_LOCK,sha256='a'*64))
    old=baseline('curobo_v2/service/curobo_service/server.py')
    new=ModuleType('new_server');new.__package__=prefix.rstrip('.')
    exec(compile(Path('curobo_v2/service/curobo_service/server.py').read_text(),'server.py','exec'),new.__dict__)
    bodies=[('JointPlanRequest',dict(current_q=[0.]*7,target_qpos=[.1]*7)),
            ('IKRequest',dict(target_pose=[0.,0.,.1],seed_q=[0.]*7)),
            ('PlanRequest',dict(target_pose=[0.,0.,.1],current_q=[0.]*7)),
            ('ValidateBaseRequest',dict(base_positions=[[0.,0.,0.]],target_pos=[0.,0.,0.],base_box=dict(center_xy=[0.,0.],half_extents=[1.,1.])))]
    for name,body in bodies:
        before=getattr(old,name).model_validate(body).model_dump_json()
        after=getattr(new,name).model_validate(body).model_dump_json()
        assert before.encode()==after.encode()
    body=bodies[0][1]
    route=next(r for r in new.app.routes if r.path=='/plan/joint')
    route.endpoint(new.JointPlanRequest.model_validate(body))
    assert 'execution_settings' not in called[-1]
    route.endpoint(new.JointPlanRequest.model_validate(dict(body,execution_settings=es.R3_20261025.to_dict())))
    assert called[-1]['execution_settings']==es.R3_20261025.to_dict()
    with pytest.raises(ValidationError):new.JointPlanRequest.model_validate(dict(body,execution_settings={}))


def test_legacy_attachment_spheres_and_receipt_bytes():
    from curobo_v2.attachment import SingleArmAttachment
    from test_curobo_v2_attachment import measured
    old=baseline('curobo_v2/attachment.py').SingleArmAttachment
    context,world=measured()
    before=old(context,world,[context['tool_frame']])
    after=SingleArmAttachment(context,world,[context['tool_frame']])
    assert before.spheres.tobytes()==after.spheres.tobytes()
    assert wire(before.artifact())==wire(after.artifact())
