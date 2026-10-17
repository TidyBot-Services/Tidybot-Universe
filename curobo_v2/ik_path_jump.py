"""Diagnostic client override for the IK-path candidate jump filter."""
import math
import os
import re


DEFAULT_MAX_JOINT_JUMP = 0.35  # IKPathRequest's service default.
# A larger single step exceeds any reasonable joint travel range.
MAX_DIAGNOSTIC_JOINT_JUMP = 2.0
ENV_NAME = 'ATTN_IK_MAX_JOINT_JUMP'


def diagnostic_max_joint_jump(mode):
    raw = os.environ.get(ENV_NAME)
    if raw is None:
        return None
    if mode != 'ik_path':
        raise ValueError(f'{ENV_NAME} is only valid in ik_path mode')
    if re.fullmatch(r'(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', raw,
                    flags=re.ASCII) is None:
        raise ValueError(f'{ENV_NAME} must be a finite positive decimal at most 2.0 rad')
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f'{ENV_NAME} must be a finite positive decimal at most 2.0 rad') from exc
    if not math.isfinite(value) or not 0 < value <= MAX_DIAGNOSTIC_JOINT_JUMP:
        raise ValueError(f'{ENV_NAME} must be a finite positive decimal at most 2.0 rad')
    return value
