"""Development-only wrist-orientation reach probe."""

from robot_sdk import arm

arm.move_delta(0.0, 0.0, 0.0, rotation_delta=(0.0, 0.5, 0.0))
