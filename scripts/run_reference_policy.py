#!/usr/bin/env python3
"""One reference SDK policy on the ordinary, frozen Dev v2 execution path.

No diagnostic overrides, model evaluation, retries, or scene registration.
Exit codes: 0 success, 1 policy failure, 2 refusal/invalid evidence.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TASK = 'pick_place_counter_to_sink'
SEEDS = (102, 104, 107, 114)
CONTRACT = Path('/home/truares/桌面/AttentionBench-Workspace/experiments/scene-contracts/scene-contract-r3-104-107-114.json')
REASON = 'reference policy (simulator truth constants, diagnostic only)'


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                               allow_nan=False) + '\n', encoding='utf-8')


def preflight(out):
    forbidden = sorted(k for k in os.environ if k.startswith('ATTN_DIAG_') or k in {
        'ATTN_PLANNER_MODE', 'ATTN_IK_CANDIDATES', 'ATTN_IK_MAX_JOINT_JUMP'})
    if forbidden:
        raise ValueError('Forbidden environment variables are set: ' + ', '.join(forbidden))
    if os.path.lexists(out):
        raise ValueError('--out already exists; choose a new directory')
    from benchmarks.attention_harness.memory_validation_worker import run_activity
    active = run_activity([])
    if active:
        raise ValueError(active + ' (daily_dev_v2 --worker); wait until idle')


def contract_ref(seed, path=None):
    # Same SHA-bound uri + load_contract/seed_entry mechanism as
    # memory_cross_seed.register_contract, without a Memory-service catalog.
    from openarm365_dev_v2.scene_contract import registered_contract_ref, load_contract, seed_entry
    if seed == 102:
        ref = registered_contract_ref()
        if path is not None and Path(path).resolve() != Path(ref['uri']).resolve():
            raise ValueError('Seed 102 requires the registered scene contract')
    else:
        p = Path(path or CONTRACT).resolve(strict=True)
        ref = dict(uri=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest())
    seed_entry(load_contract(ref), TASK, seed)
    return ref


def selection(seed, source, ref):
    return dict(request_key='reference-' + hashlib.sha256((str(seed) + source).encode()).hexdigest()[:24],
                mode='interactive', budget_tier='dev', tasks=[TASK], seeds=[seed],
                policies=['autonomous'], target_depth=1, target_valid_executions=1,
                max_versions=1, help_budget=0, coverage='budget_limited',
                reproducible_reset=True, scene_contract=ref, run_notes=False, memory_family=False)


def reached_close(rpc, actions):
    """Join completed descent/close RPCs to the executor's bilateral attachment.

    Neither observations, task success nor simulator object poses confirm grasp.
    The fixed scripts finish descent with a negative world-Z arm delta.
    """
    intents = {r.get('request_id'): r for r in actions if r.get('event') == 'gripper_intent'}
    receipts = {r.get('request_id'): r for r in actions if r.get('event') == 'receipt'}
    last_descent = {}
    for row in rpc:
        req, res = row['request'], row['response']
        group, op = req.get('group', ''), req.get('operation')
        side = group.split('_')[0]
        if res.get('ok') is not True:
            return False
        if group in ('left_arm', 'right_arm'):
            args = req.get('args', [])
            dz = args[2] if len(args) >= 3 else req.get('kwargs', {}).get('dz')
            last_descent[side] = op == 'move_delta' and type(dz) in (int, float) and dz < 0
        elif group in ('left_gripper', 'right_gripper'):
            if op == 'open':
                last_descent[side] = False
            elif op == 'close' and last_descent.get(side):
                rid = req.get('request_id')
                intent, receipt = intents.get(rid, {}), receipts.get(rid, {})
                attached = receipt.get('attached_object') or {}
                if (intent.get('command') == 1 and intent.get('side') == side
                        and receipt.get('status') == 'completed'
                        and receipt.get('attach_after_grasp') is True
                        and attached.get('holder') == side and type(attached.get('target_id')) is int):
                    return True
    return False


def build_verdict(out, experiment_id):
    """Read the recorded tree; unknown/missing identity evidence fails closed."""
    from curobo_v2.execution_settings import R3_20261025
    from openarm365_dev_v2.diag_budget import task_limits
    out = Path(out)
    errors = []

    def read(path, *, lines=False, optional=False):
        try:
            text = path.read_text(encoding='utf-8')
            value = [json.loads(s) for s in text.splitlines() if s.strip()] if lines else json.loads(text)
            if (lines and any(not isinstance(v, dict) for v in value)) or (not lines and not isinstance(value, dict)):
                raise ValueError('expected JSON objects')
            return value
        except (OSError, ValueError) as exc:
            if not (optional and isinstance(exc, FileNotFoundError)):
                errors.append(f'{path.relative_to(out)}: {exc}')
            return [] if lines else {}

    expected = None
    try:
        expected = R3_20261025.to_dict()  # Authenticated deployment-06 registry binding.
    except (ValueError, OSError) as exc:
        errors.append('r3 registry: ' + str(exc))
    manifest = read(out / 'artifacts' / experiment_id / 'manifest.json') if experiment_id else {}
    if manifest.get('experiment_id') != experiment_id or not experiment_id:
        errors.append('missing/mismatched experiment id')
    cells = manifest.get('cells', [])
    if len(cells) != 1 or cells[0].get('policy_id') != 'autonomous':
        errors.append('exactly one autonomous cell required')
    for key, value in {'mode': 'interactive', 'budget_tier': 'dev', 'help_budget': 0,
                       'max_versions': 1, 'target_depth': 1, 'target_valid_executions': 1,
                       'reproducible_reset': True}.items():
        if manifest.get('selection', {}).get(key) != value:
            errors.append('selection.' + key + ' mismatch')
    configs = manifest.get('configs', {})
    if len(configs) != 1:
        errors.append('one frozen config required')
    config = next(iter(configs.values()), {})
    if expected is None or config.get('execution_settings') != expected:
        errors.append('cell execution_settings differs from registered r3')
    limits = task_limits('dev')
    for key, value in {'budget_tier': 'dev', 'runner_boundary': 'dev_loop',
                       'attempt_wall_seconds': limits['attempt_seconds'],
                       'sdk_call_limit': limits['sdk_calls'], 'horizon': limits['control_steps']}.items():
        if config.get(key) != value:
            errors.append('config.' + key + ' differs from ordinary dev budget/boundary')
    config_paths = sorted((out / 'artifacts').rglob('config.json'))
    if len(config_paths) != 2:
        errors.append('expected one cell config and one execution config')
    for path in config_paths:
        if read(path) != config:
            errors.append(f'{path.relative_to(out)}: differs from frozen cell config')
    episodes = sorted((out / 'artifacts').rglob('sandbox_receipt.json'))
    if len(episodes) != 1:
        errors.append('expected exactly one simulator execution')
    episode = episodes[0].parent if len(episodes) == 1 else None
    labels = dict(execution_profile='r3-20261025', execution_settings_source='config',
                  planner_lock_sha256=expected['planner_deployment']['sha256'] if expected else None)
    receipt_count = 0

    def check_labels(row, label):
        nonlocal receipt_count
        receipt_count += 1
        for key, value in labels.items():
            if row.get(key) != value:
                errors.append(f'{label}: {key} mismatch/missing')
        if 'diagnostic_budget_override' in row:
            errors.append(f'{label}: diagnostic budget override')

    try:
        with sqlite3.connect((out / 'store.sqlite').resolve().as_uri() + '?mode=ro', uri=True) as db:
            jobs = [dict(json.loads(raw), state=state) for state, raw in db.execute('SELECT state,payload FROM v2_jobs')]
        executions = [j for j in jobs if j.get('kind') == 'execute']
        if len(executions) != 1:
            errors.append('ledger must contain exactly one execution job')
        for job in executions:
            check_labels(job.get('receipt') or {}, 'ledger execution receipt')
            if job.get('state') != 'settled':
                errors.append('execution job is not settled')
    except (sqlite3.Error, ValueError, OSError) as exc:
        errors.append('execution ledger: ' + str(exc))

    # Audit all evidence, not just the first receipt/spawn encountered.
    for name in ('reset_receipt.json', 'sandbox_receipt.json', 'service_spawn.json'):
        paths = sorted((out / 'artifacts').rglob(name))
        if not paths:
            errors.append('missing ' + name)
        for path in paths:
            row = read(path)
            check_labels(row, str(path.relative_to(out)))
            if name == 'service_spawn.json' and row.get('lock_sha256') != labels['planner_lock_sha256']:
                errors.append(f'{path.relative_to(out)}: lock_sha256 mismatch')
    all_rpc, actions = [], []
    for name in ('rpc-receipts.jsonl', 'native-actions.jsonl'):
        paths = sorted((out / 'artifacts').rglob(name))
        if not paths and name == 'rpc-receipts.jsonl':
            errors.append('missing ' + name)
        for path in paths:
            rows = read(path, lines=True)
            for i, row in enumerate(rows):
                if name == 'rpc-receipts.jsonl':
                    if not isinstance(row.get('request'), dict) or not isinstance(row.get('response'), dict):
                        errors.append(f'{path.relative_to(out)}:{i+1}: malformed RPC'); continue
                    check_labels(row['response'], f'{path.relative_to(out)}:{i+1}')
                    all_rpc.append(row)
                else:
                    if row.get('event') == 'receipt':
                        check_labels(row, f'{path.relative_to(out)}:{i+1}')
                    actions.append(row)
    if not all_rpc:
        errors.append('no RPC execution receipts')
    completed = {r.get('request_id') for r in actions if r.get('event') == 'receipt' and r.get('status') == 'completed'}
    for row in all_rpc:
        req = row['request']
        if (req.get('group') not in ('owner', 'sensors') and row['response'].get('ok') is True
                and req.get('request_id') not in completed):
            errors.append('missing executor receipt for completed action ' + str(req.get('request_id')))
    native = read(episode / 'native_result.json') if episode else {}
    trace = read(episode / 'trace.json') if episode else {}
    safety = read(episode / 'safety.json') if episode else {}
    sandbox = read(episode / 'sandbox_receipt.json') if episode else {}
    condition = read(episode / 'scene-condition.json') if episode else {}
    if condition.get('validation', {}).get('matched') is not True:
        errors.append('scene contract was not verified')
    if condition.get('validation', {}).get('contract') != config.get('scene_contract'):
        errors.append('execution scene contract differs from config')
    if native.get('evaluated') is True and type(native.get('object_in_sink')) is not bool:
        errors.append('evaluated native result lacks object_in_sink evidence')
    if native.get('native_success') is True and (native.get('evaluated') is not True or native.get('object_in_sink') is not True):
        errors.append('native success contradicts evaluation/placement evidence')
    if sandbox.get('outcome_unknown'):
        errors.append('simulator outcome unknown')
    reference = read(out / 'reference.json')
    if reference.get('seed') != config.get('seed') or reference.get('scene_contract') != config.get('scene_contract'):
        errors.append('reference seed/contract differs from config')
    if episode:
        try:
            frozen = (out / 'reference-policy.py').read_bytes()
            if (hashlib.sha256(frozen).hexdigest() != reference.get('source_sha256')
                    or (episode / 'policy.py').read_bytes() != frozen):
                errors.append('executed source differs from reference policy')
        except OSError as exc:
            errors.append('reference source evidence: ' + str(exc))
    wall = sandbox.get('elapsed_seconds')
    if type(wall) not in (int, float) or not math.isfinite(wall) or wall < 0:
        errors.append('missing/invalid simulator elapsed_seconds'); wall = None
    failure = None
    for row in all_rpc:
        if row['response'].get('ok') is False:
            req = row['request']
            failure = dict(operation=req.get('group', '') + '.' + req.get('operation', ''),
                           message=row['response'].get('error'))
            break
    for event in trace.get('sdk_events', []):
        if event.get('status') == 'failed':
            failure = dict(operation=event.get('source', '') + '.' + event.get('operation', ''),
                           message=event.get('error', {}).get('message'))
            break
    if failure is None and trace.get('error'):
        failure = dict(operation='policy', message=trace['error'])
    monitor_reason = next(iter(safety.get('violations', [])), None)
    if episode and monitor_reason is None:
        # Monitor exceptions before their physics row is flushed still have a
        # private traceback. Do not mislabel an IK/planning rejection as abort.
        native_errors = read(episode / 'native-errors.jsonl', lines=True, optional=True)
        reasons = {r['request'].get('request_id'): r['response'].get('error') for r in all_rpc}
        for row in native_errors:
            stack = row.get('traceback', '')
            if 'in monitor\n' in stack or 'sapien_monitor.py' in stack:
                monitor_reason = reasons.get(row.get('request_id')); break
    if (out / 'run-error.json').exists():
        errors.append('run exception: ' + str(read(out / 'run-error.json').get('message')))
    result = dict(experiment_id=experiment_id, seed=config.get('seed'),
                  verdict='invalid' if errors else 'success' if native.get('native_success') is True else 'failure',
                  reasons=errors, native_success=native.get('native_success'),
                  native_evaluated=native.get('evaluated'), object_in_sink=native.get('object_in_sink'),
                  object_in_sink_source=native.get('object_in_sink_source'), first_failure=failure,
                  monitor_abort_reason=monitor_reason, reached_close=reached_close(all_rpc, actions),
                  simulator_wall_seconds=wall, execution_profile=labels['execution_profile'],
                  planner_lock_sha256=labels['planner_lock_sha256'], audited_receipts=receipt_count)
    return result


def write_verdict(out, verdict):
    write_json(out / 'verdict.json', verdict)
    lines = ['# Reference policy verdict', '', '**' + verdict['verdict'] + '**', '']
    for key, value in verdict.items():
        if key != 'verdict':
            lines.append(f'- {key}: `{json.dumps(value, ensure_ascii=False)}`')
    (out / 'verdict.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def run(seed, source, out, scene_contract=None):
    out = Path(out).absolute()
    preflight(out)
    if seed not in SEEDS:
        raise ValueError('unsupported reference seed')
    source_path = Path(source).resolve(strict=True)
    source_bytes = source_path.read_bytes()
    source_text = source_bytes.decode('utf-8')
    ref = contract_ref(seed, scene_contract)
    from curobo_v2.execution_settings import R3_20261025
    R3_20261025.to_dict()  # Refuse before allocation if deployment-06 is absent.
    from benchmarks.attention_harness.dev_ledger import DevV2Store
    from benchmarks.attention_harness import dev_experiment
    from benchmarks.attention_harness.diagnostics.motion_tracking_matrix import ScriptedClient
    out.mkdir(parents=True, exist_ok=False)
    (out / 'reference-policy.py').write_bytes(source_bytes)
    write_json(out / 'reference.json', dict(seed=seed, source=str(source_path),
               source_sha256=hashlib.sha256(source_bytes).hexdigest(), scene_contract=ref, reason=REASON))
    experiment_id = None
    try:
        store = DevV2Store(out / 'store.sqlite')
        experiment = dev_experiment.create(store, out / 'artifacts', selection(seed, source_text, ref))
        experiment_id = experiment['experiment_id']
        manifest = dev_experiment.verify_manifest(experiment)
        budget = out / 'budget.json'
        write_json(budget, dict(goal_id=manifest['goal_id'], ledger_path=str(store.path.resolve()),
                   experiment_id=experiment_id, manifest_sha256=experiment['manifest']['sha256'],
                   limits=manifest['goal_limits'], wall_limit=manifest['wall_limit']))
        dev_experiment.run(store, experiment_id, max_cells=1, max_slots=1, budget_file=budget,
                           client=ScriptedClient(json.dumps(dict(source=source_text, reason=REASON))))
    except Exception as exc:
        write_json(out / 'run-error.json', dict(message=f'{type(exc).__name__}: {exc}'))
    verdict = build_verdict(out, experiment_id)
    write_verdict(out, verdict)
    return verdict


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, choices=SEEDS, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--scene-contract', type=Path)
    args = parser.parse_args(argv)
    try:
        verdict = run(args.seed, args.source, args.out, args.scene_contract)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(verdict, ensure_ascii=False, sort_keys=True))
    return {'success': 0, 'failure': 1, 'invalid': 2}[verdict['verdict']]


if __name__ == '__main__':
    raise SystemExit(main())
