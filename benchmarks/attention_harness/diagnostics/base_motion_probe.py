"""Six isolated OpenArm365 Dev v2 base trials on seed 101.

Dry run is offline. Real execution is explicit and uses one scripted provider
response and one new deterministic episode for each trial.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

TRIALS = ('base_y_plus', 'base_x_minus', 'base_yaw_plus', 'over_limit', 'collision', 'arm_after_base')


def policy_source(name, collision_delta=(0.20, 0., 0.)):
    commands = {
        'base_y_plus': 'base.move_delta(0, 0.10, 0)',
        'base_x_minus': 'base.move_delta(-0.10, 0, 0)',
        'base_yaw_plus': 'base.move_delta(0, 0, 0.2)',
        'over_limit': 'base.move_delta(0.25, 0, 0)',
        'collision': f'base.move_delta({collision_delta[0]!r}, {collision_delta[1]!r}, {collision_delta[2]!r})',
        'arm_after_base': 'base.move_delta(0, 0.10, 0)\nleft_arm.move_delta(0, 0, 0.01)',
    }
    if name not in commands: raise ValueError(name)
    return 'from robot_sdk import base, left_arm\n' + commands[name] + '\n'


def collision_command(world_path):
    """Pick a 20 cm colliding direction in the measured reset world."""
    if world_path is None or not Path(world_path).is_file():
        raise RuntimeError('Measured reset world snapshot unavailable for collision trial')
    import yaml
    from openarm365_dev.base_motion import check_chassis_path
    from curobo_v2.client import PlanningRejected
    from openarm365_dev.config import LOCK
    world = json.loads(Path(world_path).read_text())
    lock = json.loads(LOCK.read_text())
    config = Path(lock['project_root']) / 'config/openarm_fixed.yml'
    binding = yaml.safe_load(config.read_text())['service']['native_chassis']
    center = binding['center_in_planner_frame']
    boxes = sorted(world['cuboids'], key=lambda b: (b['center'][0]-center[0])**2+(b['center'][1]-center[1])**2)
    directions = []
    for box in boxes[:12]:
        dx, dy = box['center'][0]-center[0], box['center'][1]-center[1]
        norm = math.hypot(dx, dy)
        if norm > 1e-8: directions.append((.20*dx/norm, .20*dy/norm, 0.))
    directions += [(0., .20, 0.), (0., -.20, 0.), (.20, 0., 0.), (-.20, 0., 0.)]
    for delta in directions:
        try: check_chassis_path(world, binding, delta)
        except PlanningRejected: return delta
    if not directions: raise RuntimeError('Measured world contains no obstacle direction')
    return directions[0]


def _jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.is_file() else []


def summarize(name, trial_dir, wall_seconds, error=None):
    feedback = next(trial_dir.glob('artifacts/**/cells/*/v1/public-feedback.json'), None)
    public = json.loads(feedback.read_text()) if feedback else {}
    events = public.get('action_trace', [])
    episode = next(trial_dir.glob('artifacts/**/cells/*/v1/executions/openarm365-dev-*'), None)
    result = json.loads((episode/'result.json').read_text()) if episode and (episode/'result.json').is_file() else {}
    trace = json.loads((episode/'trace.json').read_text()) if episode and (episode/'trace.json').is_file() else {}
    journal = _jsonl(episode/'native-actions.jsonl') if episode else []
    physics = _jsonl(episode/'native-physics.jsonl') if episode else []
    base = next((row for row in reversed(journal) if row.get('event') == 'base_tracking'), None)
    intent = next((row for row in journal if row.get('event') == 'base_intent'), None)
    rejection = next((row for row in journal if row.get('event') == 'base_collision_rejected'), None)
    # Public feedback omits source on some SDK failures; operation is still present.
    move = next((row for row in events if row.get('operation') == 'move_delta' and row.get('source') in (None, 'base')), {})
    arm = next((row for row in events if row.get('source') == 'left_arm' and row.get('operation') == 'move_delta'), {})
    before = base['before_base_qpos'] if base else intent['before_base_qpos'] if intent else rejection['before_base_qpos'] if rejection else [0., 0., 0.]
    after = base['after_base_qpos'] if base else physics[-1]['base_qpos'] if physics else before
    change = [after[i]-before[i] for i in range(3)]
    event_error = move.get('error')
    if isinstance(event_error, dict): event_error = event_error.get('message')
    public_error = public.get('public_error') or event_error or result.get('error') or trace.get('error') or error
    return dict(trial=name, public_status=move.get('status') or public.get('execution_status') or result.get('status') or 'failed', public_error=public_error,
                arm_status=arm.get('status'), base_before=before, base_after=after,
                base_change=change, target=base['target_base_qpos'] if base else intent['target_base_qpos'] if intent else None,
                arrival_error=base['base_error'] if base else None,
                arm_joint_change=base['arm_joint_change'] if base else None,
                elapsed_seconds=base['elapsed_seconds'] if base else wall_seconds,
                collision_check=base['collision_check'] if base else intent['collision_check'] if intent else None,
                episode=str(episode) if episode else None)


def markdown_table(rows):
    lines = ['| 试验 | 公开结果 | 公开错误全文 | 底座变化 x/y/yaw | 到位误差 x/y/yaw | 最大手臂变化 | 耗时(s) |',
             '| --- | --- | --- | --- | --- | ---: | ---: |']
    def vector(value): return '—' if value is None else ', '.join(f'{float(x):.4f}' for x in value)
    def cell(value): return str(value).replace('|', r'\|').replace('\n', '<br>') if value else '—'
    for row in rows:
        change = row.get('arm_joint_change')
        arm = '—' if change is None else f'{max(abs(x) for x in change):.5f}'
        lines.append(f"| {row['trial']} | {row['public_status']} | {cell(row.get('public_error'))} | {vector(row.get('base_change'))} | "
                     f"{vector(row.get('arrival_error'))} | {arm} | {row['elapsed_seconds']:.2f} |")
    return '\n'.join(lines) + '\n'


def run_one(name, root, collision_delta):
    from benchmarks.attention_harness.dev_ledger import DevV2Store
    from benchmarks.attention_harness.dev_experiment import create, run, verify_manifest
    from benchmarks.attention_harness.diagnostics.motion_tracking_matrix import ScriptedClient
    started = __import__('time').monotonic()
    root.mkdir(parents=True, exist_ok=False)
    store = DevV2Store(root/'store.sqlite')
    selection = dict(request_key='base-probe-'+hashlib.sha256(name.encode()).hexdigest()[:24],
                     mode='interactive', tasks=['pick_place_counter_to_sink'], seeds=[101],
                     policies=['autonomous'], target_depth=1, target_valid_executions=1,
                     max_versions=1, help_budget=0, coverage='budget_limited', reproducible_reset=True)
    experiment = create(store, root/'artifacts', selection)
    manifest = verify_manifest(experiment)
    budget = root/'budget.json'
    budget.write_text(json.dumps(dict(goal_id=manifest['goal_id'], ledger_path=str(store.path.resolve()),
        experiment_id=experiment['experiment_id'], manifest_sha256=experiment['manifest']['sha256'],
        limits=manifest['goal_limits'], wall_limit=manifest['wall_limit'])))
    reply = json.dumps({'source': policy_source(name, collision_delta), 'reason': 'scripted base-motion diagnostic'})
    try: run(store, experiment['experiment_id'], max_cells=1, budget_file=budget, client=ScriptedClient(reply))
    except Exception as exc: return summarize(name, root, __import__('time').monotonic()-started, f'{type(exc).__name__}: {exc}')
    return summarize(name, root, __import__('time').monotonic()-started)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=Path('base-motion-results'))
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    if args.dry_run:
        for name in TRIALS: print(f'{name}: {policy_source(name).strip()}')
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    results = []
    world = None
    for name in TRIALS:
        try:
            delta = collision_command(world) if name == 'collision' else (0.20, 0., 0.)
            row = run_one(name, args.out/name, delta)
        except Exception as exc:
            row = dict(trial=name, public_status='failed', public_error=f'{type(exc).__name__}: {exc}',
                       arm_status=None, base_before=None, base_after=None, base_change=None,
                       target=None, arrival_error=None, arm_joint_change=None,
                       elapsed_seconds=0., collision_check=None, episode=None)
        results.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        if world is None and row['episode']:
            candidate = Path(row['episode'])/'planner/world-0001.json'
            if candidate.is_file(): world = candidate
        (args.out/'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2)+'\n')
        (args.out/'results.md').write_text(markdown_table(results))
    return 0


if __name__ == '__main__': raise SystemExit(main())
