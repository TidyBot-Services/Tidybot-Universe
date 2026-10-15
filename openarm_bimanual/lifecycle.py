"""Stop confirmation uses measured velocities and the accepted thresholds."""
import math

def _rest(values, limit, size):
    return (isinstance(values,(list,tuple)) and len(values)==size
        and all(isinstance(v,(int,float)) and not isinstance(v,bool)
                and math.isfinite(v) and abs(v)<=limit for v in values))

def stopped_components(state):
    result={}
    for side in ['left','right']:
        arm=state.get(side,{})
        gripper=state.get(side+'_gripper',{})
        result[side]=_rest(arm.get('qvel'),.05,7) and _rest(
            [gripper.get('driver_velocity_m_s'),gripper.get('follower_velocity_m_s')],.005,2)
    result['base']=_rest(state.get('base',{}).get('qvel'),.005,3)
    return result
