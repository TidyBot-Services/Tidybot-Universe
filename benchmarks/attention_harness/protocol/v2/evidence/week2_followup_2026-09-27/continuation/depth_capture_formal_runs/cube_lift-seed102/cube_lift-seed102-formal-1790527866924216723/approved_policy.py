"""Bounded repeated-action depth smoke; no task-success claim."""

from robot_sdk import gripper, sensors

for _ in range(3):
    gripper.open(settle_steps=1)
    sensors.get_observation()
    gripper.close(settle_steps=1)
    sensors.get_observation()
