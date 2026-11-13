"""Offline v2 diagnostics. Inputs are exclusively screened workspace execution files and sdk.json.

Also copied beside devtool.py; keep imports stdlib plus the copied summary helpers.
"""
import json
import math
from pathlib import Path
import re

try:
    from .dev_agent_exec_summary import _call, _moved, _objects
except ImportError:  # standalone docs/devtool.py
    from dev_agent_exec_summary import _call, _moved, _objects


def execution_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'e?\d{1,12}', value):
        raise ValueError('expected execution id e000007 or 7')
    return 'e' + str(int(value.lstrip('e'))).zfill(6)


def execution_folder(root, eid):
    root = Path(root).resolve()
    folder = root / 'executions' / execution_id(eid)
    if folder.resolve() != folder:
        raise ValueError('execution symlinks are not allowed')
    return folder


def safe_path(path, allowed):
    path = Path(path)
    if path.resolve() != path or not any(path == base or base in path.parents for base in allowed):
        raise ValueError('outside published execution files')
    return path


def read_json(path, allowed, notes):
    try:
        return json.loads(safe_path(path, allowed).read_text())
    except (OSError, ValueError) as exc:
        notes.append(f'{path.name}: unavailable ({type(exc).__name__})')
        return None


def failure(trace, planner, notes):
    for i, row in enumerate(trace):
        if not isinstance(row, dict):
            continue
        result = row.get('result')
        if not (row.get('status') in ('failed', 'rejected') or row.get('error')
                or isinstance(result, dict) and result.get('rejected')):
            continue
        name = f"{row['source']}.{row.get('operation')}" if row.get('source') else row.get('operation')
        out = dict(index=i, call=name,
                   arguments=row.get('arguments'), previous=[_call(j, trace[j]) for j in range(max(0, i-2), i)
                                                            if isinstance(trace[j], dict)])
        for key in ('status', 'error', 'result', 'reason', 'rejection_reason'):
            if key in row:
                out[key] = row[key]  # Never shorten arguments, error or rejection evidence.
        if 'arguments' not in row:
            notes.append('first_failure.arguments: absent in published trace')
        reasons = []
        def visit(value):
            if isinstance(value, dict):
                reason = value.get('reason')
                if isinstance(reason, str):
                    reasons.append(reason)
                for item in value.values():
                    visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)
        identified = [p for p in (planner if isinstance(planner, list) else []) if isinstance(p, dict)
                      and any(k in row and p.get(k) == row[k] for k in ('request_id', 'sequence', 'trace_index', 'call_index'))]
        visit(identified or planner)
        evidence = json.dumps(row)
        matching = list(dict.fromkeys(r for r in reasons if r and (identified or re.search(r'(?<!\w)' + re.escape(r) + r'(?!\w)', evidence))))
        if matching:
            out['planner_reason'] = matching
        else:
            notes.append('first_failure.planner_reason: no matching published planner reason')
        return out, row
    notes.append('first_failure: no failed or rejected SDK call found')
    return None, None


def aligned_row(rows, call, notes, trace_index=None):
    if not rows:
        notes.append('at_failure/contacts/joint_margins: no state rows')
        return None, None
    if call:
        # Only explicit shared coordinates, never equate list index with physics step.
        containers = [call] + [call[k] for k in ('result', 'error') if isinstance(call.get(k), dict)]
        for keys in (('physics_step', 'physics_steps'), ('trace_index', 'call_index'), ('sequence',)):
            for value in containers:
                coordinate = next((value[k] for k in keys if type(value.get(k)) in (int, float)), None)
                if coordinate is None and keys[0] == 'trace_index':
                    coordinate = trace_index
                candidates = [(i, row, next((row[k] for k in keys if type(row.get(k)) in (int, float)), None))
                              for i, row in enumerate(rows) if isinstance(row, dict)]
                candidates = [c for c in candidates if c[2] is not None]
                if coordinate is not None and candidates:
                    i, row, _ = min(candidates, key=lambda c: (abs(c[2] - coordinate), -c[0]))
                    return i, dict(row=row, alignment=f'closest {keys[0]} to {coordinate}')
        if call.get('request_id') is not None:
            candidates = [(i, r) for i, r in enumerate(rows) if isinstance(r, dict) and r.get('request_id') == call['request_id']]
            if candidates:
                i, row = candidates[-1]
                return i, dict(row=row, alignment='last row with matching request_id')
    notes.append('at_failure.alignment: no reliable trace/state alignment; using last row')
    return len(rows)-1, dict(row=rows[-1], alignment='last row; no reliable trace/state alignment')


def vector(value, size=3):
    return isinstance(value, list) and len(value) == size and all(type(x) in (int, float) and math.isfinite(x) for x in value)


def mapping(value):
    return value if isinstance(value, dict) else {}


def target(call, eef, side):
    if not call or call.get('source') != side + '_arm':
        return None
    arguments = mapping(call.get('arguments'))
    args = arguments.get('args', [])
    args = args if isinstance(args, list) else []
    kwargs = mapping(arguments.get('kwargs', arguments))
    position = kwargs.get('position', kwargs.get('target_position'))
    if isinstance(kwargs.get('target'), dict):
        position = kwargs['target'].get('position', position)
    if vector(position):
        return position
    if call.get('operation') == 'move_delta' and vector(eef):
        delta = kwargs.get('delta')
        if delta is None:
            if not args and not any(k in kwargs for k in ('dx', 'dy', 'dz')):
                return None
            delta = [kwargs.get(k, args[i] if len(args) > i else 0.) for i, k in enumerate(('dx', 'dy', 'dz'))]
        if vector(delta):
            # Delta is relative to the call's start, not necessarily the failure row.
            return dict(delta=delta)
    return None


def robot_at(row, call, trace, index, initial, notes):
    robot = mapping(row.get('robot', row))
    out = {}
    for side in ('left', 'right'):
        arm = {}
        prefix = 'robot0_' + side + '_'
        for source, dest in (('eef_pos', 'eef_pos'), ('eef_quat', 'eef_quat'), ('gripper_qpos', 'gripper_opening')):
            if prefix + source in robot:
                arm[dest] = robot[prefix + source]
        tcp = mapping(row.get('tcp')).get(side)
        if isinstance(tcp, dict):
            for source, dest in (('position', 'eef_pos'), ('quaternion_xyzw', 'eef_quat')):
                if source in tcp:
                    arm[dest] = tcp[source]
        if 'eef_pos' not in arm and isinstance(tcp, list) and len(tcp) == 4 and all(isinstance(v, list) and len(v) == 4 for v in tcp):
            arm['eef_pos'] = [tcp[i][3] for i in range(3)]
        if side in mapping(row.get('gripper_qpos')):
            arm['gripper_opening'] = row['gripper_qpos'][side]
        for key in ('eef_pos', 'gripper_opening'):
            if key not in arm:
                notes.append(f'at_failure.{side}.{key}: absent in published row')
        if vector(arm.get('eef_pos')):
            goal = target(call, arm['eef_pos'], side)
            if isinstance(goal, dict):
                # Use a published pre-call observation only when no intervening motion
                # can have changed this EEF. Otherwise the absolute target is unknown.
                before = None
                for earlier in reversed(trace[:index]):
                    if not isinstance(earlier, dict):
                        continue
                    if earlier.get('source') in (side+'_arm', 'bimanual', 'base'):
                        break
                    result = earlier.get('result')
                    if earlier.get('source') == 'sensors' and isinstance(result, dict):
                        obs = result.get('observation', result)
                        pos = mapping(obs.get('robot', obs)).get(prefix+'eef_pos')
                        if vector(pos):
                            before = pos
                            break
                else:
                    initial_robot = mapping(initial.get('robot', initial)) if isinstance(initial, dict) else {}
                    pos = initial_robot.get(prefix+'eef_pos')
                    if vector(pos):
                        before = pos
                if before is None:
                    notes.append(f'at_failure.{side}.distance_to_target_m: delta start EEF unavailable')
                    goal = None
                else:
                    goal = [a+b for a, b in zip(before, goal['delta'])]
            if goal is not None:
                arm['target_position'] = goal
                arm['distance_to_target_m'] = math.dist(arm['eef_pos'], goal)
        if arm:
            out[side] = arm
    return out


def contacts_in(value):
    if not isinstance(value, dict):
        return {}
    return {k: v if 'contact' in k.lower() else contacts_in(v) for k, v in value.items()
            if 'contact' in k.lower() or isinstance(v, dict) and contacts_in(v)}


def joint_margins(row, sdk, notes):
    limits = sdk.get('joint_limits') if isinstance(sdk, dict) else None
    if isinstance(limits, list) and isinstance(sdk.get('joint_names'), list) and len(limits) == len(sdk['joint_names']):
        limits = dict(zip(sdk['joint_names'], limits))
    if not isinstance(limits, dict) or not limits:
        notes.append('joint_margins: docs/sdk.json contains no named joint limits')
        return None
    positions = row.get('joint_pos', {})
    robot = mapping(row.get('robot', row))
    positions = dict(positions) if isinstance(positions, dict) else {}
    names = row.get('joint_names', sdk.get('joint_names'))
    if isinstance(names, list) and isinstance(row.get('qpos'), list):
        positions.update(zip(names, row['qpos']))
    for side in ('left', 'right'):
        joints = robot.get('robot0_'+side+'_joint_pos')
        if isinstance(joints, list):
            positions.update((f'openarm_{side}_joint{i+1}', q) for i, q in enumerate(joints))
    margins = []
    for name, bounds in limits.items():
        if isinstance(bounds, dict):
            bounds = [bounds.get('lower'), bounds.get('upper')]
        q = positions.get(name)
        if vector(bounds, 2) and type(q) in (int, float) and math.isfinite(q) and bounds[0] <= bounds[1]:
            margins.append(dict(joint=name, margin_rad=min(q-bounds[0], bounds[1]-q)))
    if not margins:
        notes.append('joint_margins: no published joint positions matching named limits')
        return None
    return sorted(margins, key=lambda v: v['margin_rad'])[:3]


def build(root, eid):
    """Read only this execution folder and docs/sdk.json, without RPC or private inputs."""
    folder = execution_folder(root, eid)
    sdk_path = Path(root).resolve() / 'docs/sdk.json'
    allowed = (folder, sdk_path)
    notes = []
    def read(name):
        return read_json(folder/name, allowed, notes)
    result, trace, rows, planner = read('result.json'), read('trace.json'), read('state_log.json'), read('diagnostics/planner.json')
    trace = trace if isinstance(trace, list) else []
    rows = rows if isinstance(rows, list) else []
    out = {}
    if result is not None:
        out['outcome'] = result
    first, call = failure(trace, planner, notes)
    if first is not None:
        out['first_failure'] = first
    start, end = read('initial_observation.json'), read('final_observation.json')
    i, aligned = aligned_row(rows, call, notes, first['index'] if first else None)
    if aligned and isinstance(aligned['row'], dict):
        row = aligned['row']
        out['at_failure'] = dict(row_index=i, alignment=aligned['alignment'],
                                 arms=robot_at(row, call, trace, first['index'] if first else 0, start, notes))
        contacts = [dict(row_index=j, fields=fields) for j in range(max(0, i-20), min(len(rows), i+21))
                    if (fields := contacts_in(rows[j]))]
        if contacts:
            out['contacts'] = contacts
        else:
            notes.append('contacts: no contact fields in published window')
    if isinstance(start, dict) and isinstance(end, dict):
        before, after = _objects(start), _objects(end)
        if any(vector(pos) and vector(after.get(name)) for name, pos in before.items()):
            out['objects_moved'] = _moved(before, after)
        else:
            notes.append('objects_moved: no matching published start/end object positions')
    sdk = read_json(sdk_path, allowed, notes)
    if aligned and isinstance(aligned['row'], dict):
        margins = joint_margins(aligned['row'], sdk, notes)
        if margins is not None:
            out['joint_margins'] = margins
    pointers = []
    for name in ('stdout.txt', 'stderr.txt'):
        try:
            text = safe_path(folder/name, allowed).read_text()
            pointers.append(name + ' tail: ' + text[-300:])
        except (OSError, ValueError) as exc:
            notes.append(f'{name}: unavailable ({type(exc).__name__})')
    for name in ('help.json', 'eval.json'):
        path = folder/name
        if path.is_file() and not path.is_symlink():
            pointers.append(name + ': executions/' + folder.name + '/' + name)
        else:
            notes.append(name + ': not present in execution folder')
    files = {}
    # Listing/stat only; never open frames or other non-input artifacts.
    for path in sorted(folder.rglob('*')):
        if path.name != 'diagnose.json' and not path.is_symlink() and path.is_file():
            try:
                safe_path(path, allowed)
                relative = path.relative_to(folder)
                if relative.parts[0] == 'frames' and len(relative.parts) > 1:
                    # Hundreds of frame images: one summary line instead of one entry each.
                    entry = files.setdefault('frames/', dict(files=0, bytes=0))
                    entry['files'] += 1; entry['bytes'] += path.stat().st_size
                else:
                    files[str(relative)] = path.stat().st_size
            except ValueError:
                continue
    out['pointers'] = dict(lines=pointers, full_files=files)
    if notes:
        out['notes'] = notes
    return out


def diagnose(root, eid):
    folder = execution_folder(root, eid)
    path = safe_path(folder/'diagnose.json', (folder,))
    # Broker prepublishes this for read-only Dev mounts. Standalone synthetic or
    # older writable workspaces can build it locally, without contacting anyone.
    if path.is_file():
        return json.loads(path.read_text())
    value = build(root, eid)
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2)+'\n')
    return value
