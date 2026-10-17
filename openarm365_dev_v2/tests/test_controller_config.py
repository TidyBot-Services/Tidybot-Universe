"""Offline checks for the Dev v2 joint-position controller gain."""

import sys
from types import ModuleType

from openarm365_dev_v2.environment import controller_config


def test_controller_config_default_and_diagnostic_gain_override(monkeypatch):
    robot = ModuleType('openarm365.robot')
    robot.controller_config = lambda: {'body_parts': {'base': {'type': 'JOINT_VELOCITY'}}}
    monkeypatch.setitem(sys.modules, 'openarm365.robot', robot)

    monkeypatch.delenv('ATTN_DIAG_JOINT_KP', raising=False)
    default = controller_config()
    assert default['body_parts']['left']['kp'] == 1500
    assert default['body_parts']['right']['kp'] == 1500

    monkeypatch.setenv('ATTN_DIAG_JOINT_KP', '300')
    overridden = controller_config()
    assert overridden['body_parts']['left']['kp'] == 300
    assert overridden['body_parts']['right']['kp'] == 300
