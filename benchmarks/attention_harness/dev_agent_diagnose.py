"""Offline v2 diagnostics. Inputs are exclusively screened workspace execution files.

Also copied beside devtool.py; keep imports stdlib plus the copied summary helpers.
"""
import json
import math
from pathlib import Path
import re

try:
    from .dev_agent_exec_summary import _moved, _objects
except ImportError:  # standalone docs/devtool.py
    from dev_agent_exec_summary import _moved, _objects


def _storage_text(path):
    try:
        from .run_storage import read_text
    except ImportError:  # published standalone tool; live sandbox files are raw
        return path.read_text()
    return read_text(_storage_safe(path))


def _storage_safe(path):
    from .frame_cleanup import safe
    for suffix in ('', '.zst', '.derived'):
        candidate = safe(Path(str(path)+suffix))
        if candidate.exists():
            break
    return path


def _storage_files(folder):
    try:
        from .run_storage import logical_paths
    except ImportError:
        return sorted(p for p in folder.rglob('*') if p.is_file())
    return logical_paths(folder)


def _storage_size(path):
    try:
        from .run_storage import logical_size
    except ImportError:
        return path.stat().st_size
    return logical_size(_storage_safe(path))


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
        return json.loads(_storage_text(safe_path(path, allowed)))
    except (OSError, ValueError) as exc:
        notes.append(f'{path.name}: unavailable ({type(exc).__name__})')
        return None


def reason_fields(value):
    """Keep error/reason strings at any depth, without copying numeric planner data."""
    if not isinstance(value, dict):
        return {}
    out = {}
    for key, item in value.items():
        if re.search(r'reason|error|message', key, re.I):
            evidence = string_evidence(item)
            if evidence is not None or item is None:
                out[key] = evidence
        elif isinstance(item, dict):
            nested = reason_fields(item)
            if nested:
                out[key] = nested
        elif isinstance(item, list):
            nested = [fields for v in item if (fields := reason_fields(v))]
            if nested:
                out[key] = nested
    return out


def string_evidence(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return {k: evidence for k, v in value.items() if (evidence := string_evidence(v)) is not None}
    if isinstance(value, list):
        return [evidence for v in value if (evidence := string_evidence(v)) is not None]
    return None


def call_name(row):
    return f"{row['source']}.{row.get('operation')}" if row.get('source') else row.get('operation')


def failure(trace, planner, notes, trace_path='trace.json'):
    for i, row in enumerate(trace):
        if not isinstance(row, dict):
            continue
        result = row.get('result')
        if not (row.get('status') in ('failed', 'rejected') or row.get('error')
                or isinstance(result, dict) and result.get('rejected')):
            continue
        out = dict(index=i, call=call_name(row),
                   arguments=row.get('arguments'), previous=[dict(call=call_name(trace[j]), status=trace[j].get('status')) for j in range(max(0, i-2), i)
                                                            if isinstance(trace[j], dict)])
        for key in ('status', 'error', 'reason', 'rejection_reason'):
            if key in row:
                out[key] = row[key]  # Never shorten arguments, error or rejection evidence.
        if 'result' in row:
            result = reason_fields(row['result'])
            if isinstance(row['result'], str):
                result['message'] = row['result']
            if isinstance(row['result'], dict) and 'rejected' in row['result']:
                result['rejected'] = row['result']['rejected']
            result['full'] = f'{trace_path}[{i}]'
            out['result'] = result
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


MOVING = {'left_arm', 'right_arm', 'left_gripper', 'right_gripper', 'bimanual', 'base'}


def dispatched(call):
    return (isinstance(call, dict) and call.get('source') in MOVING
            and call.get('status') in ('completed', 'failed')
            and not mapping(call.get('result')).get('rejected'))


def aligned_row(rows, call, notes, trace_index=None, trace=()):
    if not rows:
        notes.append('at_failure/contacts: no state rows')
        return None, None
    def fallback(why):
        return len(rows)-1, dict(row=rows[-1], alignment=f'last row; no reliable trace/state alignment: {why}')
    if any(isinstance(row, dict) and row.get('request_id') is not None for row in rows):
        # The public trace has no shared coordinates. Physics request groups follow
        # dispatched moving calls in trace order; rejected calls consume no group.
        groups = {}
        previous = None
        for i, row in enumerate(rows):
            request = row.get('request_id') if isinstance(row, dict) else None
            if not isinstance(request, (str, int)):
                return fallback('state row missing request_id')
            if request != previous and request in groups:
                return fallback('non-contiguous request_id group')
            groups[request] = i
            previous = request
        calls = [j for j, row in enumerate(trace) if dispatched(row)]
        through = sum(j <= trace_index for j in calls) if trace_index is not None else len(calls)
        if through > len(groups):
            return fallback(f'{through} dispatched moving calls through failure exceed {len(groups)} request groups')
        if len(calls) != len(groups):
            return fallback(f'{len(calls)} dispatched moving calls != {len(groups)} request groups')
        if not through:
            return 0, dict(row=rows[0], alignment=f'first row (no previous dispatched group; {len(groups)} request groups)')
        i = list(groups.values())[through-1]
        why = ('failed during motion' if dispatched(call) else
               'previous dispatched group; rejected before dispatch or sensors call') if call else 'execution end; no failure'
        return i, dict(row=rows[i], alignment=f'request group {through} of {len(groups)} ({why})')
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
    return fallback('no shared coordinates or request groups')


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
        for source, dest in (('eef_pos', 'eef_pos'), ('gripper_qpos', 'gripper_opening')):
            if prefix + source in robot:
                arm[dest] = robot[prefix + source]
        tcp = mapping(row.get('tcp')).get(side)
        if isinstance(tcp, dict) and 'position' in tcp:
            arm['eef_pos'] = tcp['position']
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
                arm['distance_to_target_m'] = round(math.dist(arm['eef_pos'], goal), 4)
        for key in ('eef_pos', 'gripper_opening', 'target_position'):
            if key in arm:
                arm[key] = rounded(arm[key])
        if arm:
            out[side] = arm
    return out


# Constant run settings whose names contain "contact" (radius/window/mode labels) are not contact evidence.
SETTING_KEY = re.compile(r'^fixture_(contact|press)_.*(_m|_rad|_mode|mode)$')


def contacts_in(value):
    if not isinstance(value, dict):
        return {}
    return {k: v if 'contact' in k.lower() else contacts_in(v) for k, v in value.items()
            if not SETTING_KEY.match(k) and ('contact' in k.lower() or isinstance(v, dict) and contacts_in(v))}


def rounded(value):
    if isinstance(value, list):
        return [rounded(v) for v in value]
    return round(value, 4) if type(value) in (int, float) else value


def contact_set(value):
    if isinstance(value, dict):
        return {k: contact_set(v) for k, v in value.items()}
    if isinstance(value, list):
        return sorted(set(json.dumps(v, sort_keys=True) for v in value))
    return value


def contact_window(rows, index):
    entries = []
    for j in range(max(0, index-20), min(len(rows), index+21)):
        row = mapping(rows[j])
        bilateral = row.get('bilateral_contact_objects')
        before = mapping(rows[j-1]).get('bilateral_contact_objects') if j else bilateral
        if row.get('unexpected_contacts') or contact_set(bilateral) != contact_set(before):
            entries.append((j, dict(physics_step=row.get('physics_step'), **contacts_in(row))))
    # Keep the five qualifying samples closest to failure, in physics order.
    selected = sorted(entries, key=lambda entry: (abs(entry[0]-index), entry[0]))[:5]
    return [entry for _, entry in sorted(selected)]


def fit_output(out):
    """Budget optional context on disk as well as in the CLI. Failure evidence is sacred."""
    def fits():
        # Also bound the ordinary json.dumps representation used by coverage checks.
        return len(json.dumps(out, ensure_ascii=False, allow_nan=False)) <= 3999
    if fits():
        return out
    out['pointers']['lines'] = ['stdout.txt', 'stderr.txt', 'help.json', 'eval.json']
    while out.get('contacts') and not fits():
        out['contacts'].pop()
    if not out.get('contacts'):
        out.pop('contacts', None)
    for key in ('objects_moved', 'notes'):
        if fits():
            break
        out.pop(key, None)
    # Unusually verbose reason evidence can itself exceed the budget. It, the
    # alignment and full file inventory are retained even in that case.
    return out


def build(root, eid):
    """Read only this execution folder, without RPC or private inputs."""
    folder = execution_folder(root, eid)
    allowed = (folder,)
    notes = []
    def read(name):
        return read_json(folder/name, allowed, notes)
    result, trace, rows, planner = read('result.json'), read('trace.json'), read('state_log.json'), read('diagnostics/planner.json')
    trace = trace if isinstance(trace, list) else []
    rows = rows if isinstance(rows, list) else []
    out = {}
    if result is not None:
        out['outcome'] = reason_fields(result)
        if isinstance(result, dict) and 'status' in result:
            out['outcome']['status'] = result['status']
    first, call = failure(trace, planner, notes, f'executions/{folder.name}/trace.json')
    if first is not None:
        out['first_failure'] = first
    start, end = read('initial_observation.json'), read('final_observation.json')
    i, aligned = aligned_row(rows, call, notes, first['index'] if first else None, trace)
    if aligned and isinstance(aligned['row'], dict):
        row = aligned['row']
        out['at_failure'] = dict(row_index=i, alignment=aligned['alignment'],
                                 arms=robot_at(row, call, trace, first['index'] if first else 0, start, notes))
        contacts = contact_window(rows, i)
        if contacts:
            out['contacts'] = contacts
        else:
            notes.append('contacts: no unexpected contacts or bilateral set changes in window')
    if isinstance(start, dict) and isinstance(end, dict):
        before, after = _objects(start), _objects(end)
        if any(vector(pos) and vector(after.get(name)) for name, pos in before.items()):
            out['objects_moved'] = _moved(before, after)
        else:
            notes.append('objects_moved: no matching published start/end object positions')
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
    for path in _storage_files(folder):
        if path.name != 'diagnose.json' and not path.is_symlink():
            try:
                safe_path(path, allowed)
                relative = path.relative_to(folder)
                if relative.parts[0] == 'frames' and len(relative.parts) > 1:
                    # Hundreds of frame images: one summary line instead of one entry each.
                    entry = files.setdefault('frames/', dict(files=0, bytes=0))
                    entry['files'] += 1; entry['bytes'] += _storage_size(path)
                else:
                    files[str(relative)] = _storage_size(path)
            except ValueError:
                continue
    out['pointers'] = dict(lines=pointers, full_files=files)
    if notes:
        out['notes'] = notes
    return fit_output(out)


def encode_diagnosis(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))+'\n').encode()


def diagnose(root, eid):
    folder = execution_folder(root, eid)
    path = safe_path(folder/'diagnose.json', (folder,))
    # Broker prepublishes this for read-only Dev mounts. Standalone synthetic or
    # older writable workspaces can build it locally, without contacting anyone.
    if path.is_file():
        return json.loads(path.read_text())
    value = build(root, eid)
    path.write_bytes(encode_diagnosis(value))
    return value
