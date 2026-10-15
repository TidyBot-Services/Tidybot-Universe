"""Publish measured hand state without changing recorded history or physics."""
import json
from .display import atomic
from .lifecycle import stopped_components


def arm_control_sample(state):
    return {'step':state['total_steps'], 'epoch':state['epoch'], 'task_phase':state.get('task_phase'),
        'arms':state['arms'], 'handoff':state.get('handoff'),
        'grasp_schema':(state.get('grasp_control') or {}).get('schema'),
        'ownership':state.get('ownership'),'grasp_control':state.get('grasp_control'),
        'grippers':{s:state[s+'_gripper'] for s in ['left','right']}}


def publish_stopped_control(run_dir, run_id, attempt_id, receipt):
    from pathlib import Path
    barrier=(receipt or {}).get('stop_barrier') or {}
    state=barrier.get('stopped_state') or {}
    if (not run_dir or not run_id or not attempt_id or barrier.get('physical_rest_confirmed') is not True
            or state.get('active_action') is not None or not all(stopped_components(state).values())
            or any(type(state.get(k)) is not int or state[k]<0 for k in ['epoch','total_steps'])):
        return False
    index=attempt_id.rsplit(':',1)[-1]
    if not index.isdigit():return False
    file=Path(run_dir)/f'public_control-{index}.json'
    if file.exists():
        old=json.loads(file.read_bytes())
        if old.get('run_id')!=run_id or old.get('attempt_id')!=attempt_id:return False
    atomic(file,json.dumps({'run_id':run_id,'attempt_id':attempt_id,'source':'measured_stop_receipt',
        'physical_rest_confirmed':True,'control':arm_control_sample(state)}).encode())
    return True
