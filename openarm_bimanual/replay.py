"""Public physical controls sampled on the same clock as the four recordings."""
import math


def validate_control_recording(value, *, run_id, attempt_id):
    if (not isinstance(value, dict) or value.get('schema') != 'attentionbench.bimanual-control-recording.v1'
            or value.get('run_id') != run_id or value.get('attempt_id') != attempt_id):
        raise ValueError('Replay identity mismatch')
    rows = value.get('samples')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 5000:
        raise ValueError('Invalid control sample count')
    last_video = last_trace = -math.inf
    for row in rows:
        if not isinstance(row, dict): raise ValueError('Invalid control sample')
        video, trace = row.get('video_seconds'), row.get('trace_seconds')
        if (any(type(x) not in (int, float) or not math.isfinite(x) for x in [video, trace])
                or video < 0 or video <= last_video or trace <= last_trace):
            raise ValueError('Invalid control replay clock')
        last_video, last_trace = video, trace
        control = row.get('control')
        if not isinstance(control, dict) or any(type(control.get(k)) is not int or control[k] < 0 for k in ['step', 'epoch']):
            raise ValueError('Invalid physical sample identity')
        for side in ['left', 'right']:
            arm, grip = control.get('arms', {}).get(side), control.get('grippers', {}).get(side)
            if (not isinstance(arm, dict) or any(type(arm.get(k)) is not bool for k in ['active', 'grasped'])
                    or arm.get('action_id') is not None and not isinstance(arm['action_id'], str)
                    or not isinstance(grip, dict)
                    or any(type(grip.get(k)) not in (int, float) or not math.isfinite(grip[k]) for k in ['driver_position_m', 'follower_position_m'])):
                raise ValueError('Invalid physical hand state')
        handoff = control.get('handoff')
        if handoff is not None and (not isinstance(handoff, dict) or handoff.get('phase') not in {'receiving', 'releasing', 'completed', 'aborted'}):
            raise ValueError('Invalid handoff phase')
    if rows[0]['video_seconds'] != 0: raise ValueError('Replay must start at zero')
    return value
