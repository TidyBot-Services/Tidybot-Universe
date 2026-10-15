"""Explicit contact-mode viscous damping; native fixed tendon owns coupling."""


def apply_contact_controller(rig, side, *, stiffness, driver_damping, follower_damping, mode):
    forces=[]
    for number,damping in [(1,driver_damping),(2,follower_damping)]:
        name=f'openarm_{side}_finger_joint{number}';joint=rig.joints[rig.idx[name]]
        effort=float(rig.jxml[name].find('limit').get('effort'));forces.append(effort)
        joint.set_drive_properties(stiffness if number==1 else 0,damping,effort,mode=mode)
    return {'driver_stiffness':stiffness,'driver_damping':driver_damping,
            'follower_position_stiffness':0,'follower_viscous_damping':follower_damping,
            'official_force_limits':forces,'coupling':'native_fixed_tendon'}
