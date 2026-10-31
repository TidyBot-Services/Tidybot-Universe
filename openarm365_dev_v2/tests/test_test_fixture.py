import copy
import json
from types import SimpleNamespace as NS

import numpy as np
import pytest

from openarm365_dev_v2 import dev_config as cfg, formal_config
from openarm365_dev_v2.test_fixture import apply_test_fixture, SETTLE_STEPS
from benchmarks.attention_harness.dev_experiment import validate_selection_fields
from benchmarks.attention_harness.daily_dev_v2 import DailyDevV2
from benchmarks.attention_harness.memory_fixture_guard import fixture_reason, refuse_import, REASON
from openarm365_dev_v2.tests.test_fixture_interaction import settings, r4_deployment
from benchmarks.attention_harness.tests.c21_fixtures import base

SPEC = {'test_fixture': True, 'label': 'handoff', 'base_delta': {'forward': -.05, 'side': -.15},
        'object_poses': {'obj': [1., 2., 3., 1., 0., 0., 0.]}}


def test_config_roundtrip_and_exclusions(monkeypatch):
    monkeypatch.setattr(cfg, 'verify_lock', lambda *a, **kw: None)
    from curobo_v2.execution_settings import R3_20261025, from_config
    settings = from_config(R3_20261025.to_dict())
    monkeypatch.setattr(cfg, 'new_execution_settings', lambda **kw: settings)
    from curobo_v2 import identity, dev_local
    monkeypatch.setattr(identity, 'load_lock', lambda *a, **kw: (None, settings.planner_deployment['sha256']))
    monkeypatch.setattr(dev_local, 'is_dev_local', lambda *_: True)
    args = ('open_cabinet', 102, {'path': 'fixture', 'sha256': 'fixture'})
    bare = cfg.make_dev_config(*args)
    assert 'test_fixture' not in bare
    frozen = json.dumps(bare).encode()
    assert cfg.validate_dev_config(bare) is bare and json.dumps(bare).encode() == frozen
    c = cfg.make_dev_config(*args, test_fixture=SPEC)
    assert cfg.validate_dev_config(c) is c and c['test_fixture'] == SPEC
    per_motion=cfg.make_dev_config(*args,test_fixture=dict(SPEC,held_reference='per_motion'))
    assert cfg.validate_dev_config(per_motion) is per_motion
    assert per_motion['test_fixture']['held_reference']=='per_motion'
    with pytest.raises(ValueError,match='formal boundary rejects test_fixture'):
        formal_config.validate_formal_config(dict(per_motion,schema_version=formal_config.SCHEMA,runner_boundary='formal'))
    with pytest.raises(ValueError,match='Dev configuration mismatch'):
        cfg.validate_dev_config(dict(bare,held_reference='per_motion'))
    with pytest.raises(ValueError, match='test_fixture'):
        cfg.make_dev_config(*args, test_fixture=SPEC, budget_boundary='formal', budget_tier='legacy')
    formal = dict(c, schema_version=formal_config.SCHEMA, runner_boundary='formal')
    with pytest.raises(ValueError, match='test_fixture'):formal_config.validate_formal_config(formal)
    reproducible = dict(c, scene_reset_seed=102)
    with pytest.raises(ValueError, match='test_fixture'):cfg.validate_dev_config(reproducible)
    with pytest.raises(ValueError, match='test_fixture'):validate_selection_fields({'test_fixture': SPEC})


def test_r5_fixture_config_and_legacy_bytes(monkeypatch, settings):
    monkeypatch.setattr(cfg, 'verify_lock', lambda *a, **kw: None)
    args=('arrange_tea',104,{'path':'fixture','sha256':'fixture'})
    plain=cfg.make_dev_config(*args,execution_settings=settings)
    encoded=json.dumps(plain).encode()
    assert 'test_fixture' not in plain and cfg.validate_dev_config(plain) is plain
    assert json.dumps(plain).encode()==encoded
    fixture=cfg.make_dev_config(*args,execution_settings=settings,test_fixture=SPEC,bimanual_collaboration='handoff')
    assert cfg.validate_dev_config(fixture) is fixture
    assert fixture['test_fixture']==SPEC and fixture['bimanual_collaboration']=='handoff'


@pytest.mark.parametrize('change', [
    {'base_delta': {'forward': 1.001}},
    {'base_delta': {'side': float('nan')}},
    {'object_poses': {'obj': [0, 0, 0, 2, 0, 0, 0]}},
    {'object_poses': {str(i): [0, 0, 0, 1, 0, 0, 0] for i in range(5)}},
    {'base_qpos': {'forward': .3, 'side': .1}},  # together with base_delta
    {'base_delta': None, 'base_qpos': {'forward': .3}},
    {'base_delta': None, 'base_qpos': {'forward': .3, 'side': float('inf')}},
])
def test_invalid_spec(change):
    spec={k: v for k, v in dict(SPEC, **change).items() if v is not None}
    with pytest.raises((ValueError, TypeError), match='test_fixture'):cfg.validate_test_fixture(spec)


@pytest.mark.parametrize('value', [None, 'fixed', 'settled', 'PER_MOTION', True, 1, {}])
def test_held_reference_accepts_only_per_motion(value):
    with pytest.raises(ValueError, match='test_fixture held_reference'):
        cfg.validate_test_fixture(dict(SPEC, held_reference=value))
    assert cfg.validate_test_fixture(dict(SPEC, held_reference='per_motion'))['held_reference']=='per_motion'


def test_held_reference_cannot_escape_fixture(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, 'verify_lock', lambda *a, **kw: None)
    from benchmarks.attention_harness import dev_disk
    monkeypatch.setattr(dev_disk,'require_start',lambda *_:None)
    daily=DailyDevV2.__new__(DailyDevV2);daily.root=tmp_path
    spec=dict(SPEC, held_reference='per_motion')
    with pytest.raises(ValueError,match='product run creation rejects test_fixture'):
        daily.create({'selection':{'test_fixture':spec},'start':False})
    with pytest.raises(ValueError,match='test_fixture'):
        validate_selection_fields({'test_fixture':spec})
    (tmp_path/'result.json').write_text(json.dumps({'test_fixture':spec}))
    class Store:
        def __init__(self):self.events=[]
        def tx(self):
            from contextlib import nullcontext
            return nullcontext(None)
        def event(self,db,kind,value):self.events.append((kind,value))
    store=Store()
    with pytest.raises(Exception,match=REASON):refuse_import(store,'cell',2,episode=tmp_path)
    assert store.events[0][0]=='planner_memory_offline_skipped'
    assert list(tmp_path.iterdir())==[tmp_path/'result.json']


class FakeEnv:
    def __init__(self, drift=0, object_drift=0):
        self.sim = NS(data=NS(qpos=np.zeros(24), qvel=np.zeros(23)))
        self.sim.data.qpos[6:13] = [0, 0, 0, 1, 0, 0, 0]
        self.sim.model = NS(get_joint_qpos_addr=lambda n: (6, 13), get_joint_qvel_addr=lambda n: (6, 12))
        self.sim.forward = lambda: None
        class Ctrl:
            name='JOINT_VELOCITY'
            control_dim=3
            def __init__(self, qpos_index=None):self.qpos_index=qpos_index;self.goal=None
            def set_goal(self, goal):self.goal=np.asarray(goal).copy()
        self.controllers = {'base': Ctrl(), 'left': Ctrl(slice(13,20)), 'right': Ctrl(slice(20,24))}
        self.controllers['right'].qpos_index=slice(20,24)
        self.robots = [NS(_ref_base_joint_pos_indexes=np.array([0,1,2]),
                          _ref_base_joint_vel_indexes=np.array([0,1,2]),
                          composite_controller=NS(part_controllers=self.controllers,
                              _action_split_indexes={'left':(0,7),'right':(7,11),'base':(11,14)}))]
        self.objects=[NS(name='obj',joints=['obj_joint'])]
        self.action_spec=(np.zeros(14),np.ones(14))
        self.steps=[];self.drift=drift;self.object_drift=object_drift
    def step(self, action):
        self.steps.append(action.copy())
        self.sim.data.qpos[0] += self.drift
        self.sim.data.qpos[6] += self.object_drift


def test_apply_sets_and_measures():
    env=FakeEnv()
    receipt=apply_test_fixture(env,SPEC)
    assert len(env.steps)==SETTLE_STEPS
    np.testing.assert_allclose(env.sim.data.qpos[:2],[-.05,-.15])
    np.testing.assert_allclose(env.sim.data.qpos[6:13],SPEC['object_poses']['obj'])
    np.testing.assert_allclose(env.controllers['base'].goal,[0,0,0])
    assert receipt['requested']==SPEC and receipt['object_poses']['obj']['after']==SPEC['object_poses']['obj']
    assert receipt['base_qpos']['before']==[0,0,0]


def test_drift_and_unknown_object_visible():
    with pytest.raises(RuntimeError, match=r'test_fixture.*drift_m=.*0.005'):
        apply_test_fixture(FakeEnv(drift=.001),SPEC)
    with pytest.raises(ValueError, match='test_fixture unknown object names: missing'):
        apply_test_fixture(FakeEnv(),dict(SPEC,object_poses={'missing':[0,0,0,1,0,0,0]}))
    with pytest.raises(RuntimeError, match=r'test_fixture.*obj position_error_m=0.02 angle_error_deg=0'):
        apply_test_fixture(FakeEnv(object_drift=.004),SPEC)


def test_memory_receipt_excluded(tmp_path):
    (tmp_path/'result.json').write_text(json.dumps({'test_fixture':True}))
    assert fixture_reason(episode=tmp_path)==REASON
    assert fixture_reason(config={'test_fixture':SPEC})==REASON
    class Store:
        def __init__(self):self.events=[]
        def tx(self):
            from contextlib import nullcontext
            return nullcontext(None)
        def event(self, db, kind, value):self.events.append((kind,value))
    store=Store()
    with pytest.raises(Exception, match=REASON):refuse_import(store,'cell',2,episode=tmp_path)
    assert store.events==[('planner_memory_offline_skipped',{'cell':'cell','version':2,'reason':REASON})]


def test_product_api_rejects_before_creation(monkeypatch,tmp_path):
    from benchmarks.attention_harness import dev_disk
    monkeypatch.setattr(dev_disk,'require_start',lambda *_:None)
    daily=DailyDevV2.__new__(DailyDevV2);daily.root=tmp_path
    with pytest.raises(ValueError,match='product run creation rejects test_fixture'):
        daily.create({'selection':{'test_fixture':SPEC},'start':False})
    assert list(tmp_path.iterdir())==[]


def test_absolute_base_qpos_is_independent_of_the_reset_base():
    spec={k: v for k, v in SPEC.items() if k!='base_delta'}
    spec['base_qpos']={'forward': .4, 'side': -.3}
    assert cfg.validate_test_fixture(spec) is spec
    env=FakeEnv();env.sim.data.qpos[:2]=[.1,.2]
    receipt=apply_test_fixture(env,spec)
    np.testing.assert_allclose(env.sim.data.qpos[:2],[.4,-.3])
    assert receipt['base_qpos']['before'][:2]==[.1,.2] and receipt['base_qpos']['requested'][:2]==[.4,-.3]
    far=FakeEnv();far.sim.data.qpos[:2]=[1.5,0]
    with pytest.raises(RuntimeError, match='test_fixture base_qpos is more than 1.0 m'):apply_test_fixture(far,spec)
