"""Unchanged OpenArm MJCF with two torque-limited absolute joint controllers.

This new dev profile never enables the retired raw-action service. Native labels
are reversed relative to physical arm sides. Base goals remain zero throughout.
"""
from contextlib import contextmanager
import copy
import json
import random
from pathlib import Path
import numpy as np

PHYSICAL_TO_NATIVE = {'left': 'right', 'right': 'left'}

def initial_joints():
    from openarm365.model import ROOT
    p=json.loads((ROOT/'configs/robot_parameters_v1.json').read_text())['PickPlaceCounterToSink']
    p=copy.deepcopy(p)
    # The old OSC reset settled joint5 slightly outside its original lower limit.
    # Targets move inward; measured states and original URDF limits stay exact.
    p['left']=[-.4,-.8,0.,.8,0.,0.,0.]
    p['right']=[.4,.8,0.,.8,0.,0.,0.]
    return p

def controller_config():
    from openarm365.robot import controller_config as old_config
    c=copy.deepcopy(old_config())
    # Upstream reset settles objects with a zero action. In delta mode that
    # holds the approved initial joints; in absolute mode it drives toward zero.
    # Change only command interpretation after reset, never qpos or joint limits.
    joint={'type':'JOINT_POSITION','input_type':'delta',
        'input_max':1,'input_min':-1,'output_max':.05,'output_min':-.05,
        'kp':300,'damping_ratio':1,'impedance_mode':'fixed',
        'gripper':{'type':'GRIP'},'interpolation':None,'ramp_ratio':.2}
    c['body_parts']['right']=copy.deepcopy(joint)
    c['body_parts']['left']=copy.deepcopy(joint)
    return c

@contextmanager
def pose():
    from openarm365.robot import OpenArm365, _model_construction_lock
    with _model_construction_lock:
        old=OpenArm365.initial_pose_override
        p=initial_joints()
        OpenArm365.initial_pose_override=p['left']+p['right']
        try: yield
        finally: OpenArm365.initial_pose_override=old

def create_env(env_name, **kw):
    import robocasa, robosuite
    from openarm365 import robot  # Register the accepted physical MJCF.
    if env_name!='PickPlaceCounterToSink' or kw.get('robots')!='OpenArm365':
        raise ValueError('This dev adapter is locked to OpenArm365 Counter → Sink')
    if kw.get('seed')!=101 or kw.get('layout_and_style_ids')!=[(11,34)]:
        raise ValueError('This dev adapter requires seed101/layout11/style34')
    # Official fixtures also use global NumPy RNG for collision-debug RGBA.
    # Seed both global generators as well as the environment's own seeded RNG
    # so the full scene XML, including display/debug properties, is frozen.
    np.random.seed(101);random.seed(101)
    kw.pop('split',None)
    kw['camera_names']=['robot0_agentview_left','gripper0_right_eye_in_hand']
    with pose():
        env=robosuite.make(env_name=env_name,controller_configs=controller_config(),
            has_renderer=False,has_offscreen_renderer=True,ignore_done=True,
            use_object_obs=True,use_camera_obs=True,initialization_noise=None,**kw)
    reset=env.reset
    def activate_absolute():
        for native in ('left','right'):
            ctrl=env.robots[0].composite_controller.part_controllers[native]
            ctrl.input_type='absolute'
            ctrl.set_goal(np.array(env.sim.data.qpos[ctrl.qpos_index],copy=True))
    def reset_with_pose(*args,**kwargs):
        if env.robots[0] is not None:
            for native in ('left','right'):
                env.robots[0].composite_controller.part_controllers[native].input_type='delta'
        with pose(): result=reset(*args,**kwargs)
        activate_absolute()
        return result
    env.reset=reset_with_pose
    if env.robots[0] is not None:activate_absolute()
    return env

def joint_address(sim, name):
    matches=[n for n in sim.model.joint_names if n.endswith(name)]
    if len(matches)!=1: raise ValueError('Ambiguous native joint mapping: '+name)
    return sim.model.get_joint_qpos_addr(matches[0])

def body_pose(sim, name):
    bid=sim.model.body_name2id(name)
    t=np.eye(4);t[:3,:3]=sim.data.body_xmat[bid].reshape(3,3);t[:3,3]=sim.data.body_xpos[bid]
    return t

def mount_pose(env):
    return body_pose(env.sim,'robot0_openarm_body_link0')

def tcp_pose(env, side):
    if side not in PHYSICAL_TO_NATIVE: raise ValueError('Physical side must be left/right')
    sid=env.robots[0].eef_site_id[PHYSICAL_TO_NATIVE[side]]
    t=np.eye(4);t[:3,:3]=env.sim.data.site_xmat[sid].reshape(3,3);t[:3,3]=env.sim.data.site_xpos[sid]
    return t
