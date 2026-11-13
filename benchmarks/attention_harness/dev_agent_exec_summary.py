"""Short first read for one execution (try speed-up item 3, user approved 11-13).

Built only from the same public, screened values the broker already publishes in
executions/<id>/; it adds no new information and removes nothing: every full file
stays in the folder for targeted reads. The point is that the Dev agent's first
read is a few KB instead of hundreds of KB (state_log.json alone is often MBs).
"""
import json

SCHEMA = 'attentionbench.dev-execution-summary.v1'
TAIL_CHARS = 1500
ARG_CHARS = 160
CALL_ROWS = 60
MOVED_M = .01


def _short(value, limit=ARG_CHARS):
    text = json.dumps(value, sort_keys=True, allow_nan=False, default=str)
    return text if len(text) <= limit else text[:limit - 1] + '…'


def _robot(observation):
    robot = observation.get('robot') if isinstance(observation, dict) else None
    if not isinstance(robot, dict):
        return None
    keep = ('eef_pos', 'gripper_qpos', 'joint_pos')
    return {k: v for k, v in robot.items() if any(k.endswith(s) for s in keep)}


def _objects(observation):
    values = ((observation or {}).get('perception') or {}).get('values') if isinstance(observation, dict) else None
    return {k[:-4]: v for k, v in (values or {}).items() if k.endswith('_pos') and isinstance(v, list)}


def _moved(start, end):
    moved = {}
    for name, before in start.items():
        after = end.get(name)
        if (isinstance(after, list) and len(after) == len(before) == 3
                and all(isinstance(x, (int, float)) for x in (*before, *after))):
            distance = sum((a - b) ** 2 for a, b in zip(after, before)) ** .5
            if distance >= MOVED_M:
                moved[name] = dict(start=before, end=after, moved_m=round(distance, 4))
    return moved


def _call(index, row):
    out = dict(i=index, call=f"{row.get('source')}.{row.get('operation')}", status=row.get('status'))
    arguments = row.get('arguments')
    if arguments not in (None, {}, {'args': [], 'kwargs': {}}):
        out['args'] = _short(arguments)
    if row.get('error'):
        error = row['error']
        out['error'] = _short(error.get('message', error) if isinstance(error, dict) else error, 400)
    result = row.get('result')
    if isinstance(result, dict) and result.get('rejected'):
        out['rejected'] = _short({k: result[k] for k in ('reason',) if k in result}, 300)
    return out


def build(execution_id, *, outcome, trace, stdout, stderr, initial_observation, final_observation, files):
    """outcome/trace/observations are the screened values written beside this summary."""
    calls = [_call(i, row) for i, row in enumerate(trace if isinstance(trace, list) else []) if isinstance(row, dict)]
    failed = [row for row in (trace if isinstance(trace, list) else []) if isinstance(row, dict)
              and (row.get('status') == 'failed' or row.get('error')
                   or (isinstance(row.get('result'), dict) and row['result'].get('rejected')))]
    shown = calls if len(calls) <= CALL_ROWS else calls[:10] + [dict(omitted=len(calls) - CALL_ROWS)] + calls[-(CALL_ROWS - 10):]
    start, end = _objects(initial_observation), _objects(final_observation)
    return dict(
        schema=SCHEMA, execution_id=execution_id, outcome=outcome,
        sdk_call_count=len(calls), failed_call_count=len(failed), sdk_calls=shown,
        first_failure=None if not failed else (failed[0] if len(_short(failed[0], 3001)) <= 3000 else _short(failed[0], 3000)),
        robot=dict(start=_robot(initial_observation), end=_robot(final_observation)),
        objects_moved=_moved(start, end),
        stdout_tail=(stdout or '')[-TAIL_CHARS:], stdout_chars=len(stdout or ''),
        stderr_tail=(stderr or '')[-TAIL_CHARS:], stderr_chars=len(stderr or ''),
        full_files=files,
        note=('Short first read. Every full file listed in full_files is in this folder; read only the part you '
              'need (for example with python json slicing) instead of printing whole files.'))
