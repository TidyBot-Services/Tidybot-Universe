"""Development-only repeated action probe for the public Service path."""

from robot_sdk import gripper

gripper.open(settle_steps=1)
gripper.close(settle_steps=1)
gripper.open(settle_steps=1)
