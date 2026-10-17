"""Exact distance rules and an isolated native MuJoCo data-copy sweep."""
import hashlib
import json
from types import SimpleNamespace

import mujoco
import numpy as np
import pytest

from openarm365_dev.base_motion import (
    BasePathRejected, aabb_distance_lower_bound, base_joint_delta,
    check_exact_base_path, required_pair_distance,
)


def test_clearance_rule_three_cases():
    assert required_pair_distance(.05) == .02
    assert required_pair_distance(.012) == pytest.approx(.010)
    assert required_pair_distance(.004) == .003
    assert required_pair_distance(.002) == .003
    assert .019 < required_pair_distance(.05)
    assert .010 >= required_pair_distance(.012)
    assert .0099 < required_pair_distance(.012)
    assert .002 < required_pair_distance(.002)


def test_aabb_and_mount_delta():
    lower = aabb_distance_lower_bound(np.zeros(3), np.array([.1,.1,.1]),
        np.array([[.3,0,0],[.3,.4,0],[.1,0,0]]),
        np.array([[.1,.1,.1]]*3))
    assert lower == pytest.approx([.1, np.hypot(.1,.2), 0])
    result = base_joint_delta(np.array([.1,0,.2]), np.array([0,0,np.pi/2]), np.zeros(3))
    assert result == pytest.approx([0,.1,.2])


class ModelAdapter:
    def __init__(self, native):
        self._model = native

    def __getattr__(self, key):
        return getattr(self._model, key)

    def body_name2id(self, name):
        return mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_BODY, name)

    def geom_id2name(self, gid):
        return mujoco.mj_id2name(self._model, mujoco.mjtObj.mjOBJ_GEOM, gid)


def test_exact_geom_distance_and_copy_preserves_live_data():
    xml = '''<mujoco><worldbody>
      <body name="robot0_openarm_body_link0">
        <joint name="base_x" type="slide" axis="1 0 0"/>
        <joint name="base_y" type="slide" axis="0 1 0"/>
        <joint name="base_yaw" type="hinge" axis="0 0 1"/>
        <geom name="mobilebase0_chassis_collision" type="sphere" size=".05"/>
      </body>
      <body name="fixture" pos=".14 0 0">
        <geom name="obstacle" type="sphere" size=".05"/>
      </body>
    </worldbody></mujoco>'''
    native = mujoco.MjModel.from_xml_string(xml)
    data = mujoco.MjData(native)
    mujoco.mj_forward(native,data)
    model = ModelAdapter(native)
    env = SimpleNamespace(sim=SimpleNamespace(model=model,data=SimpleNamespace(_data=data,qpos=data.qpos)),
                          robots=[SimpleNamespace(_ref_base_joint_pos_indexes=np.array([0,1,2]))])
    world = dict(excluded_robot_geoms=[dict(geom_id=0,name='mobilebase0_chassis_collision')],
                 inventory=[dict(geom_id=1,name='obstacle')],world_sha256='small')
    binding = dict(native_geom='mobilebase0_chassis_collision')
    before_qpos = data.qpos.copy()
    before_geom = data.geom_xpos.copy()
    clear = check_exact_base_path(env,world,binding,(0,0,0),np.zeros(3))
    assert clear['status'] == 'clear'
    assert clear['start_minimum_clearance_m'] == pytest.approx(.04)
    # Stable audit serialization from the pre-carry non-held path; elapsed time
    # is the only intentionally variable field.
    def canonical(value):
        if isinstance(value,float):return round(value,9)
        if isinstance(value,dict):return {key:canonical(item) for key,item in value.items()}
        if isinstance(value,list):return [canonical(item) for item in value]
        return value
    stable={key:value for key,value in clear.items() if key!='elapsed_seconds'}
    encoded=json.dumps(canonical(stable),sort_keys=True,separators=(',',':'))
    assert hashlib.sha256(encoded.encode()).hexdigest()=='3db80ee804f18053fdafd71bebf2012c1530fef825c9955aed45a5a0587a08f4'
    assert check_exact_base_path(env,dict(world,inventory=[],
        unexempted_inventory=world['inventory'],exempted_geom_count=1),
        binding,(0,0,0),np.zeros(3))['status']=='clear'
    with pytest.raises(BasePathRejected,match=r'mobilebase0_chassis_collision versus obstacle') as caught:
        check_exact_base_path(env,world,binding,(.03,0,0),np.zeros(3))
    audit = caught.value.collision_check
    assert audit['minimum_clearance_m'] < .02
    assert audit['distance_calculations'] > 0
    assert np.array_equal(data.qpos,before_qpos)
    assert np.array_equal(data.geom_xpos,before_geom)


@pytest.mark.parametrize('other_free', [False, True])
def test_held_object_is_swept_against_fixed_and_free_obstacles(other_free):
    other_joint = '<freejoint/>' if other_free else ''
    xml = f'''<mujoco><worldbody>
      <body name="robot0_openarm_body_link0">
        <joint type="slide" axis="1 0 0"/><joint type="slide" axis="0 1 0"/>
        <joint type="hinge" axis="0 0 1"/>
        <geom name="mobilebase0_chassis_collision" type="sphere" size=".02"/>
        <geom name="finger" type="sphere" pos=".08 -.035 0" size=".02"/>
      </body>
      <body name="held" pos=".08 0 0"><freejoint/>
        <geom name="banana" type="sphere" size=".025"/>
      </body>
      <body name="other" pos=".22 .035 0">{other_joint}
        <geom name="obstacle" type="sphere" size=".015"/>
      </body>
    </worldbody></mujoco>'''
    native=mujoco.MjModel.from_xml_string(xml)
    data=mujoco.MjData(native);mujoco.mj_forward(native,data)
    model=ModelAdapter(native)
    env=SimpleNamespace(sim=SimpleNamespace(model=model,data=SimpleNamespace(_data=data,qpos=data.qpos)),
        robots=[SimpleNamespace(_ref_base_joint_pos_indexes=np.array([0,1,2]))],
        obj_body_id={'obj':model.body_name2id('held'), **({'other':model.body_name2id('other')} if other_free else {})})
    world=dict(excluded_robot_geoms=[dict(geom_id=i,name=model.geom_id2name(i)) for i in (0,1)],
        inventory=[dict(geom_id=i,name=model.geom_id2name(i)) for i in (2,3)],world_sha256='small')
    binding=dict(native_geom='mobilebase0_chassis_collision')
    before=data.qpos.copy()
    with pytest.raises(BasePathRejected,match='finger versus banana'):
        check_exact_base_path(env,world,binding,(0,0,0),np.zeros(3))
    clear=check_exact_base_path(env,world,binding,(0,0,0),np.zeros(3),held_body_id=model.body_name2id('held'))
    assert clear['status']=='clear' and clear['world_obstacle_geoms']==1
    with pytest.raises(BasePathRejected,match='banana versus obstacle') as caught:
        check_exact_base_path(env,world,binding,(.14,0,0),np.zeros(3),held_body_id=model.body_name2id('held'))
    assert caught.value.collision_check['first_violation_pair']['sample_index']>0
    assert np.array_equal(data.qpos,before)


@pytest.mark.parametrize('gap', [.012,.002])
def test_close_origin_and_absolute_floor(gap):
    # Use the same three-joint model; a near pair may approach by 2 mm,
    # while a pair already below 3 mm must be rejected at sample zero.
    xml = f'''<mujoco><worldbody><body name="robot0_openarm_body_link0">
      <joint type="slide" axis="1 0 0"/><joint type="slide" axis="0 1 0"/>
      <joint type="hinge" axis="0 0 1"/>
      <geom name="mobilebase0_chassis_collision" type="sphere" size=".05"/>
    </body><body pos="{.1+gap} 0 0"><geom name="obstacle" type="sphere" size=".05"/>
    </body></worldbody></mujoco>'''
    native = mujoco.MjModel.from_xml_string(xml)
    data = mujoco.MjData(native)
    mujoco.mj_forward(native,data)
    model = ModelAdapter(native)
    env = SimpleNamespace(sim=SimpleNamespace(model=model,data=SimpleNamespace(_data=data,qpos=data.qpos)),
                          robots=[SimpleNamespace(_ref_base_joint_pos_indexes=np.array([0,1,2]))])
    world = dict(excluded_robot_geoms=[dict(geom_id=0,name='mobilebase0_chassis_collision')],
                 inventory=[dict(geom_id=1,name='obstacle')],world_sha256='small')
    binding = dict(native_geom='mobilebase0_chassis_collision')
    if gap < .003:
        with pytest.raises(BasePathRejected,match='sample 0/1'):
            check_exact_base_path(env,world,binding,(0,0,0),np.zeros(3))
    else:
        assert check_exact_base_path(env,world,binding,(0,0,0),np.zeros(3))['status']=='clear'
        assert check_exact_base_path(env,world,binding,(.001,0,0),np.zeros(3))['status']=='clear'
        with pytest.raises(BasePathRejected):
            check_exact_base_path(env,world,binding,(.003,0,0),np.zeros(3))
