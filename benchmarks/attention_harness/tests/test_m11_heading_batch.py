"""Fixed batch cuRobo buffer regression; NumPy fake, no CUDA/model reads."""
import sys
from types import SimpleNamespace as NS,ModuleType
import numpy as np
from benchmarks.attention_harness.tests.test_ik_path import fake_core


def test_heading_model_does_not_enter_singleton_motion_warmup(monkeypatch):
    cls,core,q,targets,_=fake_core(monkeypatch,.1)
    core.settings={};core._attachment_key=None;core.attachment=None
    core._config={'kinematics':{}};core._models={};core._device='cpu';core.use_cuda_graph=True
    active=core.joint_names[:7]
    core.check_selection=lambda *args:(active,tuple(targets),{})
    core._scene=lambda:{}
    from curobo_v2.service.curobo_service import attachment_config
    monkeypatch.setattr(attachment_config,'configure_carried_body',lambda *a:{'kinematics':{}})
    class Tensor(np.ndarray):
        def detach(self):return self
        def cpu(self):return self
        def numpy(self):return np.asarray(self)
    warmups=[]
    class Planner:
        def __init__(self,cfg):
            self.cfg=cfg;self.joint_names=active
            limits=np.array([[-1.]*7,[1.]*7]).view(Tensor)
            self.kinematics=NS(get_joint_limits=lambda:NS(position=limits))
        def warmup(self,**kw):
            destination=np.zeros((1,7));destination+=np.zeros((self.cfg.batch,7))
            warmups.append(self.cfg.batch)
        def update_world(self,world):pass
    motion=ModuleType('curobo.motion_planner');motion.MotionPlanner=Planner
    motion.MotionPlannerCfg=NS(create=lambda **kw:NS(batch=kw.get('max_batch_size',1),ik_solver_config=NS(robot_config={})))
    collision=ModuleType('curobo.collision_checking')
    collision.RobotCollisionCheckerCfg=NS(load_from_config=lambda **kw:kw)
    collision.RobotCollisionChecker=lambda cfg:NS(update_world=lambda world:None)
    types=ModuleType('curobo.types');types.DeviceCfg=lambda **kw:kw
    for module in (motion,collision,types):monkeypatch.setitem(sys.modules,module.__name__,module)
    cls._select(core,q,heading_batch=True)
    cls._select(core,q,heading_batch=False)
    assert warmups==[1]


def test_singleton_then_eight_headings_preserve_fixed_solver_shape(monkeypatch):
    cls,core,q,targets,_=fake_core(monkeypatch,.1)
    import json
    from pathlib import Path
    request=json.loads((Path(__file__).parent/'fixtures/m11_s117_heading_request.json').read_text())
    q=np.asarray(request['current_q']);targets=request['targets']
    core.joint_names=request['joint_names']
    active=request['active_joints']
    core._state=lambda values,planner:values[:,[core.joint_names.index(n) for n in planner.joint_names]]
    class Tensor(np.ndarray):
        def detach(self):return self
        def cpu(self):return self
        def clone(self):return self.copy()
    def tensor(v):return np.asarray(v).view(Tensor)
    import curobo_v2.service.curobo_service.planner_core as module
    monkeypatch.setattr(module.torch,'as_tensor',lambda v,**kw:tensor(v),raising=False)
    types=ModuleType('curobo.types');types.Pose=NS
    types.GoalToolPose=NS(from_poses=lambda poses,**kw:poses)
    monkeypatch.setitem(sys.modules,'curobo.types',types)
    calls=[]
    def solve(goal,*,current_state,return_seeds):
        count=len(next(iter(goal.values())).position)
        # This is the real failing operation's invariant: destination/current
        # batch and fixed solver batch must match, including the first heading.
        destination=np.zeros((count,7));destination+=np.zeros((8,7))
        assert current_state.shape==(8,7) and return_seeds==8
        calls.append(count)
        return NS(solution=tensor(np.zeros((8,8,7))),success=tensor(np.ones((8,8),bool)))
    planner=NS(joint_names=active,ik_solver=NS(solve_pose=solve))
    core._select=lambda *a,**kw:(planner,None)
    core._device='cpu';core.tool_frames=list(targets);core.default_quaternion=[1,0,0,0]
    core.check_targets=lambda goal:cls.check_targets(core,goal)
    one=cls.heading_ik_batch(core,q,[targets],active,request['locked_joints'])
    from curobo_v2.grasp_heading import rotated
    goals=[rotated(targets,a,request['grasp_search']['world_vertical']) for a in request['heading_offsets']]
    eight=cls.heading_ik_batch(core,q,goals,active,request['locked_joints'])
    assert calls==[8,8] and len(one)==1 and len(eight)==8
    assert all(r.solution.shape==(1,8,7) for r in one+eight)
