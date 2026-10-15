"""Robosuite registration isolated from unchanged upstream source checkouts."""
from pathlib import Path
import json
import numpy as np
from robosuite.models.robots.manipulators.manipulator_model import ManipulatorModel
from robosuite.models.bases import MobileBaseModel, register_base
from robosuite.models.grippers import GRIPPER_MAPPING
from robosuite.models.grippers.gripper_model import GripperModel
from robosuite.robots import ROBOT_CLASS_MAPPING, WheeledRobot
from .model import ROOT

@register_base
class TidyverseBase365(MobileBaseModel):
    def __init__(self,idn=0): super().__init__(str(ROOT/'model/base.xml'),idn=idn)
    @property
    def top_offset(self): return np.zeros(3)
    @property
    def horizontal_radius(self): return .65

class OpenArmGripper365(GripperModel):
    def __init__(self,idn=0): super().__init__(str(ROOT/('model/gripper_right.xml' if 'left' in str(idn) else 'model/gripper.xml')),idn=idn)
    def format_action(self,action):
        # GRIP rescales [-1,1] into the original [0,.044] position actuator range.
        return np.full(2,-float(action[0]))
    @property
    def dof(self): return 1
    @property
    def init_qpos(self): return np.array([.044,.044])
    @property
    def _important_geoms(self):
        side='right' if 'left' in str(self.idn) else 'left'
        return {'left_finger':[f'openarm_{side}_left_finger_collision0'], 'right_finger':[f'openarm_{side}_right_finger_collision0'], 'left_fingerpad':[f'openarm_{side}_left_finger_collision0'], 'right_fingerpad':[f'openarm_{side}_right_finger_collision0']}

GRIPPER_MAPPING['OpenArmGripper365']=OpenArmGripper365

class OpenArm365(ManipulatorModel):
    arms=['right','left']
    initial_pose_override=None
    def __init__(self,idn=0):
        self._initial_pose=None if self.initial_pose_override is None else list(self.initial_pose_override)
        super().__init__(str(ROOT/'model/robot.xml'),idn=idn)
    @property
    def default_base(self): return 'TidyverseBase365'
    @property
    def default_gripper(self): return dict(right='OpenArmGripper365',left='OpenArmGripper365')
    @property
    def default_controller_config(self): return dict(right='osc_pose',left='osc_pose')
    @property
    def init_qpos(self):
        if self._initial_pose is not None: return np.array(self._initial_pose)
        p=json.loads((ROOT.parent/'openarm_robocasa/configs/robot_parameters_v5.json').read_text())['initial']
        return np.array(p['left']+p['right'])
    @property
    def init_base_qpos(self): return np.zeros(3)
    @property
    def base_xpos_offset(self): return {'empty':(-.6,0,0),'table':lambda n:(-.16-n/2,0,0),'bins':(-.6,0,0)}
    @property
    def top_offset(self): return np.array([0,0,1.5])
    @property
    def _horizontal_radius(self): return .7
    @property
    def arm_type(self): return 'bimanual'
    @property
    def _eef_name(self): return dict(right='openarm_left_link7',left='openarm_right_link7')
    @property
    def default_arms(self): return dict(right='OpenArm',left='OpenArm')

ROBOT_CLASS_MAPPING['OpenArm365']=WheeledRobot

def controller_config():
    arm={'type':'OSC_POSE','input_max':1,'input_min':-1,'output_max':[.05,.05,.05,.5,.5,.5], 'output_min':[-.05,-.05,-.05,-.5,-.5,-.5], 'kp':150,'damping_ratio':1,'impedance_mode':'fixed','input_type':'delta','input_ref_frame':'base','uncouple_pos_ori':True,'gripper':{'type':'GRIP'},'interpolation':None,'ramp_ratio':.2}
    # Logical left maps to physical right and holds its initial joints. Its control channel is never exposed to the policy.
    right={'type':'JOINT_POSITION','input_max':1,'input_min':-1,'output_max':.05,'output_min':-.05,'kp':300,'damping_ratio':1,'gripper':{'type':'GRIP'},'interpolation':None,'ramp_ratio':.2}
    return {'type':'BASIC','body_parts':{'right':arm,'left':right,'base':{'type':'JOINT_VELOCITY','kp':150,'output_min':[-.15,-.15,-.3],'output_max':[.15,.15,.3]}},'composite_controller_specific_configs':{}}

# Robot-specific extension of the official camera registry; upstream files stay unchanged.
from robocasa.utils import camera_utils
from scipy.spatial.transform import Rotation
camera_pos=np.array([2.,-3.,2.3]); target=np.array([.3,-1.8,1.05])
z=(camera_pos-target)/np.linalg.norm(camera_pos-target);x=np.cross([0,0,1],z);x=x/np.linalg.norm(x);y=np.cross(z,x)
camera_utils.CAM_CONFIGS['OpenArm365']={'robot0_agentview_left':{'pos':camera_pos.tolist(),'quat':Rotation.from_matrix(np.column_stack([x,y,z])).as_quat()[[3,0,1,2]].tolist(),'parent_body':None,'camera_attribs':{'fovy':'65'}}}

# Robosuite constructs robot models by registry name and id only. Cache the
# approved pose on each instance under a construction lock; no global pose leaks.
from contextlib import contextmanager
from threading import RLock
_model_construction_lock=RLock()
@contextmanager
def initial_pose(env_name):
    with _model_construction_lock:
        poses=json.loads((ROOT/'configs/robot_parameters_v1.json').read_text())[env_name]
        OpenArm365.initial_pose_override=poses['left']+poses['right']
        try: yield
        finally: OpenArm365.initial_pose_override=None
