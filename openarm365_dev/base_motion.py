"""Preflight the authenticated chassis against a measured, frozen world snapshot."""
import math
import numpy as np
from scipy.spatial.transform import Rotation
from curobo_v2.client import PlanningRejected

BASE_TRANSLATION_LIMIT_M = 0.20
BASE_YAW_LIMIT_RAD = 0.30
BASE_CLEARANCE_M = 0.02
BASE_SAMPLE_TRANSLATION_M = 0.02
BASE_SAMPLE_YAW_RAD = 0.05
# Allow at most 1 mm of numerical overlap at the chassis/support interface.
# The 2 cm clearance skin applies to obstacles rising above this interface.
BASE_SUPPORT_HEIGHT_TOLERANCE_M = 0.001


def guard_base_and_arms(base, held_base, arm, start_arm=None):
    if start_arm is None:
        if np.max(np.abs(np.asarray(base)-np.asarray(held_base))) > .001:
            raise RuntimeError('Uncommanded base drift exceeded 1mm/1mrad')
    elif np.max(np.abs(np.asarray(arm)-np.asarray(start_arm))) > .004:
        raise RuntimeError('Arm joint drift during base motion exceeded 4mrad')


def validate_delta(dx, dy, dyaw):
    try: delta = np.asarray([dx, dy, dyaw], dtype=float)
    except (TypeError, ValueError) as exc: raise PlanningRejected('Base delta must be finite numeric metres/radians') from exc
    if delta.shape != (3,) or not np.isfinite(delta).all():
        raise PlanningRejected('Base delta must be finite numeric metres/radians')
    if np.linalg.norm(delta[:2]) > BASE_TRANSLATION_LIMIT_M + 1e-12 or abs(delta[2]) > BASE_YAW_LIMIT_RAD + 1e-12:
        raise PlanningRejected('Base delta exceeds 0.20m translation or 0.3rad yaw per SDK call')
    return delta


def _obb_overlap(ca, ra, ha, cb, rb, hb, clearance):
    """Conservative separating-axis clearance check for oriented boxes."""
    axes = [*ra.T, *rb.T, *(np.cross(a, b) for a in ra.T for b in rb.T)]
    difference = cb - ca
    for axis in axes:
        length = np.linalg.norm(axis)
        if length < 1e-10: continue
        axis = axis / length
        reach = np.sum(ha * np.abs(ra.T @ axis)) + np.sum(hb * np.abs(rb.T @ axis))
        if abs(np.dot(difference, axis)) > reach + clearance + 1e-10: return False
    return True


def check_chassis_path(world, binding, delta):
    """Check interpolated body-frame motion in the *current* planner frame.

    The mount origin is the yaw pivot. The chassis centre is offset behind it.
    A separating axis must provide at least 2 cm of projected clearance.
    """
    delta = validate_delta(*delta)
    center = np.asarray(binding['center_in_planner_frame'], float)
    half = np.asarray(binding['half_size'], float)
    if center.shape != (3,) or half.shape != (3,) or np.any(half <= 0):
        raise PlanningRejected('Authenticated chassis binding is invalid')
    obstacles = []
    if len(world['cuboids']) != len(world['inventory']):
        raise PlanningRejected('World collision inventory is incomplete')
    for box in world['cuboids']:
        c = np.asarray(box['center'], float); h = np.asarray(box['half_size'], float)
        r = Rotation.from_quat(np.asarray(box['quat_wxyz'], float)[[1, 2, 3, 0]]).as_matrix()
        # A support's rotated bounding box ends below the chassis bottom.
        # This geometric test covers MuJoCo box floors as well as planes while
        # retaining cabinets, counters and objects that rise beside the base.
        top_z = c[2] + np.dot(np.abs(r[2, :]), h)
        if top_z <= center[2] - half[2] + BASE_SUPPORT_HEIGHT_TOLERANCE_M:
            continue
        obstacles.append((box['name'], c, r, h))
    samples = max(1, math.ceil(np.linalg.norm(delta[:2]) / BASE_SAMPLE_TRANSLATION_M),
                  math.ceil(abs(delta[2]) / BASE_SAMPLE_YAW_RAD))
    for index in range(samples + 1):
        t = index / samples
        yaw = float(delta[2] * t)
        r = Rotation.from_euler('z', yaw).as_matrix()
        c = r @ center + np.array([delta[0] * t, delta[1] * t, 0.])
        for name, oc, orientation, oh in obstacles:
            if np.linalg.norm(c - oc) > np.linalg.norm(half + BASE_CLEARANCE_M) + np.linalg.norm(oh):
                continue
            if _obb_overlap(c, r, half, oc, orientation, oh, BASE_CLEARANCE_M):
                raise PlanningRejected(f'Base path clearance below {BASE_CLEARANCE_M:.2f}m at sample {index}/{samples}: {name}')
    return {'status': 'clear', 'samples': samples + 1, 'minimum_clearance_m': BASE_CLEARANCE_M,
            'world_sha256': world['world_sha256'], 'obstacles_checked': len(obstacles)}
