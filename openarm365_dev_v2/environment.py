"""Unchanged OpenArm MJCF with two torque-limited absolute joint controllers.

This new dev profile never enables the retired raw-action service. Native labels
are reversed relative to physical arm sides. Base goals remain zero throughout.
"""
from contextlib import contextmanager
import copy
import json
import math
import os
import random
from pathlib import Path
import numpy as np

PHYSICAL_TO_NATIVE = {'left': 'right', 'right': 'left'}

def initial_joints(task_id):
    from benchmarks.attention_harness.dev_tasks import get_task
    get_task(task_id)
    from openarm365.model import ROOT
    p=copy.deepcopy(json.loads((ROOT/'configs/robot_parameters_v1.json').read_text())[get_task(task_id).official_env])
    p=copy.deepcopy(p)
    # The old OSC reset settled joint5 slightly outside its original lower limit.
    # Targets move inward; measured states and original URDF limits stay exact.
    p['left']=[-.4,-.8,0.,.8,0.,0.,0.]
    p['right']=[.4,.8,0.,.8,0.,0.,0.]
    return p

def diagnostic_joint_kp():
    """Joint-position gain; the default 300 is unchanged. ATTN_DIAG_JOINT_KP is a diagnostic-only override."""
    raw=os.environ.get('ATTN_DIAG_JOINT_KP','300')
    try:value=float(raw)
    except ValueError:raise ValueError('ATTN_DIAG_JOINT_KP must be a positive finite number') from None
    if not math.isfinite(value) or value<=0:raise ValueError('ATTN_DIAG_JOINT_KP must be a positive finite number')
    return int(value) if value==int(value) else value

def controller_config():
    from openarm365.robot import controller_config as old_config
    c=copy.deepcopy(old_config())
    # Upstream reset settles objects with a zero action. In delta mode that
    # holds the approved initial joints; in absolute mode it drives toward zero.
    # Change only command interpretation after reset, never qpos or joint limits.
    joint={'type':'JOINT_POSITION','input_type':'delta',
        'input_max':1,'input_min':-1,'output_max':.05,'output_min':-.05,
        'kp':diagnostic_joint_kp(),'damping_ratio':1,'impedance_mode':'fixed',
        'gripper':{'type':'GRIP'},'interpolation':None,'ramp_ratio':.2}
    c['body_parts']['right']=copy.deepcopy(joint)
    c['body_parts']['left']=copy.deepcopy(joint)
    return c

@contextmanager
def pose(task_id):
    from openarm365.robot import OpenArm365, _model_construction_lock
    with _model_construction_lock:
        old=OpenArm365.initial_pose_override
        p=initial_joints(task_id)
        OpenArm365.initial_pose_override=p['left']+p['right']
        try: yield
        finally: OpenArm365.initial_pose_override=old

def _rounded(value):
    if isinstance(value,dict):return {str(k):_rounded(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [_rounded(v) for v in value]
    if hasattr(value,'tolist'):return _rounded(value.tolist())
    if isinstance(value,float):return round(value,6)
    return value if isinstance(value,(int,str,bool)) or value is None else str(value)

def canonical_regions(regions):
    """Reset regions in an order fixed by their geometry, named geom_0, geom_1, ... in that order.

    Upstream robocasa/models/fixtures/counter.py builds the candidate top geometries with
    ``list(set(valid_geoms))``. The elements hash by memory address, so which of the left and
    right regions is called geom_0 changes from reset to reset no matter how the random
    generators are seeded, and the later ``rng.choice`` then lands on either side. Ordering by
    the regions' own offsets and sizes removes that dependence on addresses.
    """
    if not isinstance(regions,dict):return regions
    ordered=sorted(regions.values(),key=lambda region:json.dumps(_rounded(region),sort_keys=True))
    return {f'geom_{i}':region for i,region in enumerate(ordered)}

@contextmanager
def deterministic_counter_regions():
    """Apply canonical_regions to Counter.get_reset_regions for the length of the context."""
    try:
        from robocasa.models.fixtures.counter import Counter
    except ModuleNotFoundError:
        # No simulator installed (unit tests). The real worker imports robocasa before it builds
        # the environment, so the patch is always applied where scenes are generated.
        yield
        return
    original=Counter.get_reset_regions
    def get_reset_regions(self,*args,**kwargs):
        return canonical_regions(original(self,*args,**kwargs))
    Counter.get_reset_regions=get_reset_regions
    try:yield
    finally:Counter.get_reset_regions=original

@contextmanager
def rng_epoch(seed,env=None):
    """New experiment reset contract: deterministic entropy fallback, reset RNG and region order."""
    factory=np.random.default_rng
    np.random.seed(seed);random.seed(seed)
    if env is not None:env.rng=factory(seed)
    calls=[]
    def seeded_default(seed_arg=None):
        import traceback
        calls.append({'entropy_replaced':seed_arg is None,'callers':[f.filename+':'+str(f.lineno) for f in traceback.extract_stack(limit=5)[:-1]]})
        return factory(seed if seed_arg is None else seed_arg)
    np.random.default_rng=seeded_default
    try:
        with deterministic_counter_regions():yield calls
    finally:np.random.default_rng=factory

def create_env(env_name, **kw):
    import robocasa, robosuite
    from openarm365 import robot  # Register the accepted physical MJCF.
    from benchmarks.attention_harness.dev_tasks import TASKS,validate_selection
    task=next((t for t in TASKS.values() if t.official_env==env_name),None)
    if task is None or kw.get('robots')!='OpenArm365':raise ValueError('unregistered robot/task')
    validate_selection(task.task_id,kw.get('seed'))
    if kw.get('layout_and_style_ids')!=[(11,34)]:raise ValueError('unregistered scene')
    epoch=kw.pop('scene_reset_seed',None)
    np.random.seed(kw['seed']);random.seed(kw['seed'])
    kw.pop('split',None)
    kw['camera_names']=['robot0_agentview_left','gripper0_right_eye_in_hand']
    from contextlib import nullcontext
    with pose(task.task_id), (rng_epoch(epoch) if epoch is not None else nullcontext()) as construction_rng_calls:
        env=robosuite.make(env_name=env_name,controller_configs=controller_config(),
            has_renderer=False,has_offscreen_renderer=True,ignore_done=True,
            use_object_obs=True,use_camera_obs=True,initialization_noise=None,**kw)
    env.scene_rng_events=list(construction_rng_calls or [])
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
        with pose(task.task_id), (rng_epoch(epoch,env) if epoch is not None else nullcontext()) as reset_rng_calls: result=reset(*args,**kwargs)
        env.scene_rng_events.extend(reset_rng_calls or [])
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
