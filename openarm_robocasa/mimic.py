"""Native SAPIEN fixed tendons: loader coefficients/stiffness with explicit damping."""
import math


def create_native_mimics(robot, mimics, *, damping):
    if isinstance(damping,bool) or not isinstance(damping,(int,float)) or not math.isfinite(damping) or damping<0:
        raise ValueError('native tendon damping must be finite and nonnegative')
    for mimic in mimics:
        joint=robot.find_joint_by_name(mimic.joint);driver=robot.find_joint_by_name(mimic.mimic)
        if joint.parent_link!=driver.parent_link:
            raise ValueError('OpenArm supports only audited sibling-finger native mimics')
        robot.create_fixed_tendon([joint.parent_link,joint.child_link,driver.child_link],
            [0,-mimic.multiplier,1],[0,-1/mimic.multiplier,1],rest_length=mimic.offset,
            stiffness=1e5,damping=damping)
