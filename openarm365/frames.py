"""xyzw quaternions; world and current physical chassis coordinates."""
import numpy as np
from scipy.spatial.transform import Rotation

def world_delta_to_base(delta,base_quat):
    return Rotation.from_quat(base_quat).inv().apply(np.asarray(delta,dtype=float))

def base_delta_to_world(delta,base_quat):
    return Rotation.from_quat(base_quat).apply(np.asarray(delta,dtype=float))
