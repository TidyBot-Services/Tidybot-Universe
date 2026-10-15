"""Keep the last base-relative pose target for neutral OpenArm commands.

Nonzero SDK deltas remain relative to the achieved pose. Neutral arm commands
(including chassis motion) retain the last desired pose, so a loaded grasp does
not integrate servo error into its target. All actuation remains torque limited
in the unchanged upstream OSC implementation.
"""
import numpy as np


def install_pose_hold(controller):
    original=controller.set_goal
    controller.set_goal_update_mode('achieved')
    original(np.zeros(controller.control_dim))
    def set_goal(action):
        controller.set_goal_update_mode('desired' if not np.any(action) else 'achieved')
        try:
            return original(action)
        finally:
            controller.set_goal_update_mode('achieved')
    controller.set_goal=set_goal
