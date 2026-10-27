"""C4 offline acceptance: no simulator, process launch, provider or registration."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from types import ModuleType, SimpleNamespace

import pytest
from benchmarks.attention_harness.tests.c21_fixtures import base, r4_deployment

from scripts import run_reference_policy as ref
from benchmarks.attention_harness.tests.r3_fixtures import r3_deployment


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for key in list(os.environ):
        if key.startswith('ATTN_DIAG_') or key in ('ATTN_PLANNER_MODE', 'ATTN_IK_CANDIDATES', 'ATTN_IK_MAX_JOINT_JUMP'):
            monkeypatch.delenv(key)


@pytest.mark.parametrize('key', ['ATTN_DIAG_BUDGET_SCALE', 'ATTN_DIAG_UNRECOGNIZED',
                                'ATTN_PLANNER_MODE', 'ATTN_IK_CANDIDATES', 'ATTN_IK_MAX_JOINT_JUMP'])
@pytest.mark.parametrize('value', ['', '1'])
def test_environment_refusal(tmp_path, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    out = tmp_path / 'out'
    with pytest.raises(ValueError, match=key):
        ref.run(102, tmp_path / 'missing.py', out)
    assert not out.exists()


@pytest.mark.parametrize('kind', ['directory', 'file', 'broken_symlink'])
def test_existing_output_refused(tmp_path, kind):
    out = tmp_path / 'out'
    if kind == 'directory': out.mkdir()
    elif kind == 'file': out.write_text('keep')
    else: out.symlink_to(tmp_path / 'missing')
    with pytest.raises(ValueError, match='already exists'):
        ref.run(102, tmp_path / 'missing.py', out)
    assert os.path.lexists(out)


def test_daily_worker_refused(tmp_path, monkeypatch):
    from benchmarks.attention_harness import memory_validation_worker as worker
    monkeypatch.setattr(worker, 'run_activity', lambda ledgers: 'Another run is active' if ledgers == [] else None)
    with pytest.raises(ValueError, match='daily_dev_v2 --worker'):
        ref.run(102, tmp_path / 'missing.py', tmp_path / 'out')


def jsonl(path, rows):
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))


@pytest.fixture
def tree(tmp_path, r3_deployment):
    root = tmp_path / 'out'
    episode = root / 'artifacts' / 'exp' / 'cell' / 'v1' / 'executions' / 'openarm365-dev-fake'
    episode.mkdir(parents=True)
    contract = {'uri': '/fake/contract.json', 'sha256': 'c' * 64}
    config = dict(execution_settings=r3_deployment.to_dict(), budget_tier='dev', runner_boundary='dev_loop',
                  attempt_wall_seconds=1920, sdk_call_limit=320, horizon=9600, seed=102, scene_contract=contract)
    manifest = dict(experiment_id='exp', selection=ref.selection(102, 'pass\n', contract),
                    cells=[dict(key='cell', policy_id='autonomous')], configs={'pick:102': config})
    ref.write_json(root / 'artifacts' / 'exp' / 'manifest.json', manifest)
    ref.write_json(episode / 'config.json', config)
    ref.write_json(root / 'artifacts' / 'exp' / 'cell' / 'config.json', config)
    labels = dict(execution_profile='r3-20261025', execution_settings_source='config',
                  planner_lock_sha256=r3_deployment.planner_deployment['sha256'])
    ref.write_json(episode / 'sandbox_receipt.json', dict(labels, elapsed_seconds=12.5))
    ref.write_json(episode / 'reset_receipt.json', labels)
    planner = episode / 'planner'; planner.mkdir()
    ref.write_json(planner / 'service_spawn.json', dict(labels, lock_sha256=labels['planner_lock_sha256']))
    def rpc(rid, group, op, args=()):
        return dict(request=dict(request_id=rid, group=group, operation=op, args=list(args)),
                    response=dict(labels, request_id=rid, ok=True))
    rows = [rpc('open', 'left_gripper', 'open'), rpc('down', 'left_arm', 'move_delta', [0, 0, -.02]),
            rpc('close', 'left_gripper', 'close'), rpc('lift', 'left_arm', 'move_delta', [0, 0, .02])]
    actions = [dict(labels, event='receipt', request_id=rid, status='completed') for rid in ('open', 'down', 'lift')]
    actions += [dict(event='gripper_intent', request_id='close', command=1., side='left'),
               dict(labels, event='receipt', request_id='close', status='completed', attach_after_grasp=True,
                    attached_object=dict(holder='left', target_id=42))]
    jsonl(episode / 'rpc-receipts.jsonl', rows)
    jsonl(episode / 'native-actions.jsonl', actions)
    ref.write_json(episode / 'native_result.json', dict(evaluated=True, native_success=True, object_in_sink=True))
    ref.write_json(episode / 'trace.json', dict(sdk_events=[], status='completed', error=None))
    ref.write_json(episode / 'safety.json', dict(violations=[]))
    ref.write_json(episode / 'scene-condition.json', dict(validation=dict(matched=True, contract=contract)))
    (root / 'reference-policy.py').write_text('pass\n'); (episode / 'policy.py').write_text('pass\n')
    ref.write_json(root / 'reference.json', dict(seed=102, source_sha256=hashlib.sha256(b'pass\n').hexdigest(), scene_contract=contract))
    with sqlite3.connect(root / 'store.sqlite') as db:
        db.execute('CREATE TABLE v2_jobs (state TEXT, payload TEXT)')
        db.execute('INSERT INTO v2_jobs VALUES (?, ?)', ('settled', json.dumps(dict(kind='execute', receipt=labels))))
    return SimpleNamespace(root=root, episode=episode, labels=labels, rows=rows, actions=actions)


@pytest.mark.parametrize('outcome', ['success', 'before_close', 'after_close'])
def test_evidence_verdict(tree, outcome):
    t = tree
    if outcome != 'success':
        at = 1 if outcome == 'before_close' else 3
        t.rows[at]['response'].update(ok=False, error='Measured joint limit exceeded')
        jsonl(t.episode / 'rpc-receipts.jsonl', t.rows[:at+1])
        completed = {r['request']['request_id'] for r in t.rows[:at]}
        jsonl(t.episode / 'native-actions.jsonl', [a for a in t.actions if a['request_id'] in completed])
        ref.write_json(t.episode / 'native_result.json', dict(evaluated=True, native_success=False, object_in_sink=False))
        ref.write_json(t.episode / 'safety.json', dict(violations=['Measured joint limit exceeded']))
    verdict = ref.build_verdict(t.root, 'exp')
    assert verdict['verdict'] == ('success' if outcome == 'success' else 'failure'), verdict
    assert verdict['reached_close'] is (outcome != 'before_close')
    assert verdict['native_success'] is (outcome == 'success')
    assert verdict['object_in_sink'] is (outcome == 'success')
    assert verdict['simulator_wall_seconds'] == 12.5
    assert verdict['experiment_id'] == 'exp'
    if outcome != 'success':
        assert verdict['first_failure'] == dict(operation='left_arm.move_delta', message='Measured joint limit exceeded')
        assert verdict['monitor_abort_reason'] == 'Measured joint limit exceeded'
    ref.write_verdict(t.root, verdict)
    assert json.loads((t.root / 'verdict.json').read_text()) == verdict
    assert '**' + verdict['verdict'] + '**' in (t.root / 'verdict.md').read_text()


@pytest.mark.parametrize('where', ['config', 'sandbox', 'reset', 'rpc', 'executor', 'spawn', 'spawn_lock', 'ledger', 'extra_spawn'])
@pytest.mark.parametrize('field', ['execution_profile', 'execution_settings_source', 'planner_lock_sha256'])
def test_settings_mismatch_invalid(tree, where, field):
    t = tree
    if where == 'config':
        path = t.root / 'artifacts' / 'exp' / 'cell' / 'config.json'
        value = json.loads(path.read_text()); value['execution_settings']['base_hold_drift_m'] = 99
    elif where in ('sandbox', 'reset', 'spawn', 'spawn_lock', 'extra_spawn'):
        path = t.episode / {'sandbox': 'sandbox_receipt.json', 'reset': 'reset_receipt.json'}.get(where, 'planner/service_spawn.json')
        value = json.loads(path.read_text()); value['lock_sha256' if where == 'spawn_lock' else field] = 'wrong'
        if where == 'extra_spawn':
            path = t.episode / 'other' / 'service_spawn.json'; path.parent.mkdir()
    elif where == 'rpc':
        t.rows[-1]['response'][field] = 'wrong'; jsonl(t.episode / 'rpc-receipts.jsonl', t.rows)
    elif where == 'executor':
        t.actions[-1][field] = 'wrong'; jsonl(t.episode / 'native-actions.jsonl', t.actions)
    else:
        with sqlite3.connect(t.root / 'store.sqlite') as db:
            job = json.loads(db.execute('SELECT payload FROM v2_jobs').fetchone()[0])
            job['receipt'][field] = 'wrong'; db.execute('UPDATE v2_jobs SET payload=?', (json.dumps(job),))
    if where not in ('rpc', 'executor', 'ledger'): ref.write_json(path, value)
    verdict = ref.build_verdict(t.root, 'exp')
    assert verdict['verdict'] == 'invalid' and verdict['reasons']


@pytest.mark.parametrize('change', ['no_attachment', 'failed_close', 'no_descent', 'wrong_side', 'intent_only'])
def test_close_requires_executor_confirmation_after_descent(tree, change):
    t = tree
    if change == 'no_attachment': t.actions[-1]['attached_object'] = None
    elif change == 'failed_close': t.rows[2]['response']['ok'] = False
    elif change == 'no_descent': t.rows[1]['request']['args'][2] = .02
    elif change == 'wrong_side': t.actions[-1]['attached_object']['holder'] = 'right'
    else: t.actions.pop()
    # Native success and object placement must never substitute for grasp evidence.
    assert ref.reached_close(t.rows, t.actions) is False


@pytest.mark.parametrize('missing', ['planner/service_spawn.json', 'reset_receipt.json', 'rpc-receipts.jsonl', 'sandbox_receipt.json'])
def test_missing_receipt_invalid(tree, missing):
    (tree.episode / missing).unlink()
    assert ref.build_verdict(tree.root, 'exp')['verdict'] == 'invalid'


def test_monitor_abort_from_private_traceback(tree):
    t = tree
    t.rows[-1]['response'].update(ok=False, error='Stationary base drift')
    jsonl(t.episode / 'rpc-receipts.jsonl', t.rows)
    jsonl(t.episode / 'native-errors.jsonl', [dict(request_id='lift', traceback='File "executor.py", line 270, in monitor\n guard_base_and_arms(...)')])
    assert ref.build_verdict(t.root, 'exp')['monitor_abort_reason'] == 'Stationary base drift'


@pytest.mark.parametrize('change', ['source', 'missing_executor', 'missing_sink', 'unknown', 'second_execution', 'malformed_rpc'])
def test_incomplete_or_substituted_evidence_invalid(tree, change):
    t = tree
    if change == 'source': (t.episode / 'policy.py').write_text('different\n')
    elif change == 'missing_executor': (t.episode / 'native-actions.jsonl').unlink()
    elif change == 'missing_sink': ref.write_json(t.episode / 'native_result.json', dict(evaluated=True, native_success=False))
    elif change == 'unknown': ref.write_json(t.episode / 'sandbox_receipt.json', dict(t.labels, elapsed_seconds=1, outcome_unknown=True))
    elif change == 'malformed_rpc': (t.episode / 'rpc-receipts.jsonl').write_text('{broken')
    else:
        with sqlite3.connect(t.root / 'store.sqlite') as db:
            db.execute('INSERT INTO v2_jobs SELECT * FROM v2_jobs')
    assert ref.build_verdict(t.root, 'exp')['verdict'] == 'invalid'


def test_failure_before_any_completed_action_needs_no_executor_receipt(tree):
    t = tree
    row = t.rows[1]
    row['response'].update(ok=False, error='IK rejected before descent')
    jsonl(t.episode / 'rpc-receipts.jsonl', [row])
    (t.episode / 'native-actions.jsonl').unlink()
    ref.write_json(t.episode / 'native_result.json', dict(evaluated=True, native_success=False, object_in_sink=False))
    verdict = ref.build_verdict(t.root, 'exp')
    assert verdict['verdict'] == 'failure' and not verdict['reached_close']
    assert verdict['monitor_abort_reason'] is None


@pytest.mark.parametrize('seed', ref.SEEDS)
def test_contract_and_ordinary_creation(tmp_path, monkeypatch, r4_deployment, seed):
    from benchmarks.attention_harness import dev_experiment as exp
    from benchmarks.attention_harness.dev_ledger import DevV2Store
    from openarm365_dev_v2 import dev_config
    from openarm365_dev_v2.scene_contract import SCHEMA_V2, registered_contract_ref
    p = tmp_path / 'contract.json'
    ref.write_json(p, dict(schema=SCHEMA_V2, tolerance=1e-6, tasks={ref.TASK: dict(seeds={str(seed): dict(normalized_sha256='c'*64)})}))
    monkeypatch.setattr(ref, 'CONTRACT', p)
    contract = ref.contract_ref(seed)
    assert contract == (registered_contract_ref() if seed == 102 else dict(uri=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
    monkeypatch.setattr(dev_config, 'model_sources', lambda *a, **kw: {})
    made = []
    original = exp.make_dev_config
    def capture(*args, **kwargs):
        made.append(kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(exp, 'make_dev_config', capture)
    experiment = exp.create(DevV2Store(tmp_path / 'ledger.sqlite'), tmp_path / 'artifacts', ref.selection(seed, 'pass\n', contract))
    manifest = exp.verify_manifest(experiment)
    config = manifest['configs'][f'{ref.TASK}:{seed}']
    assert config['execution_settings'] == r4_deployment.to_dict()
    assert config['runner_boundary'] == 'dev_loop' and made == [dict(budget_tier='dev', execution_settings=r4_deployment)]
    assert config['scene_contract'] == contract and config['scene_reset_seed'] == seed
    assert config['attempt_wall_seconds'] == 1920 and config['sdk_call_limit'] == 320
    assert manifest['cell_budget']['executions'] == 1
    assert not config['eval_agent']['enabled']


def test_contract_refuses_wrong_seed_and_102_override(tmp_path):
    p = tmp_path / 'contract.json'
    ref.write_json(p, dict(schema='attentionbench.public-scene-contract.v2', tolerance=1e-6, tasks={}))
    with pytest.raises(ValueError, match='no reference'): ref.contract_ref(104, p)
    with pytest.raises(ValueError, match='registered'): ref.contract_ref(102, p)


@pytest.mark.parametrize('r3', [False, True])
def test_private_native_placement_uses_task_predicate(monkeypatch, r3):
    from openarm365_dev_v2.worker import native_evaluation
    calls = []
    fake = ModuleType('robocasa.utils')
    fake.object_utils = SimpleNamespace(obj_inside_of=lambda *a, **kw: calls.append((a, kw)) or True)
    monkeypatch.setitem(sys.modules, 'robocasa', ModuleType('robocasa'))
    monkeypatch.setitem(sys.modules, 'robocasa.utils', fake)
    env = SimpleNamespace(sink=object(), _check_success=lambda: False)
    config = dict(task_id=ref.TASK, execution_settings={'profile': 'r3-20261025'} if r3 else None)
    result = native_evaluation(env, config)
    assert result['native_success'] is False
    if r3:
        assert result['object_in_sink'] is True
        assert calls == [((env, 'obj', env.sink), dict(partial_check=True))]
    else:
        assert result == dict(evaluated=True, native_success=False) and not calls


@pytest.mark.parametrize('seed', ref.SEEDS)
def test_normal_dispatch_once_with_fake_native(tmp_path, monkeypatch, r4_deployment, seed):
    """Real create/verify/run + real runner; only native and policy sandbox faked."""
    from benchmarks.attention_harness import dev_experiment as exp
    from openarm365_dev_v2 import dev_config, runner as native
    from curobo_v2.execution_settings import receipt_fields
    monkeypatch.setattr(dev_config, 'model_sources', lambda *a, **kw: {})
    contract = tmp_path / 'contract.json'
    ref.write_json(contract, dict(schema='attentionbench.public-scene-contract.v2', tolerance=1e-6,
                                tasks={ref.TASK: dict(seeds={str(seed): dict(normalized_sha256='c'*64)})}))
    source = tmp_path / 'input.py'
    code = 'from robot_sdk import sensors\nsensors.get_observation()\n'
    source.write_text(code)
    labels = receipt_fields(r4_deployment)
    calls = []
    class Owner:
        def __init__(self, **kw):
            self.ledger = []; self.sent = {}; self.unresolved = []
            self.evidence = kw['evidence']
            self.config = json.loads(Path(kw['config']).read_text())
        def start(self):
            calls.append('native_start')
            ref.write_json(self.evidence / 'reset_receipt.json', labels)
            planner = self.evidence / 'planner'; planner.mkdir()
            ref.write_json(planner / 'service_spawn.json', dict(labels, lock_sha256=labels['planner_lock_sha256']))
            jsonl(self.evidence / 'rpc-receipts.jsonl', [dict(request=dict(group='sensors', operation='get_observation', request_id='observe'), response=dict(labels, ok=True))])
            jsonl(self.evidence / 'native-actions.jsonl', [])
            return dict(initial_observation=dict(robot={}, perception=dict(values={})), capabilities={},
                        scene_state={}, scene_condition=dict(matched=True, contract=self.config['scene_contract']))
        def rpc(self, group, operation, *args, **kwargs):
            return (dict(robot={}, perception=dict(values={})) if operation == 'observe' else
                    dict(evaluated=True, native_success=False, object_in_sink=False))
        def stop(self, reason):
            return dict(leader_reaped=True, process_group_gone=True, planner_process_groups_gone=True)
    monkeypatch.setattr(native, 'NativeOwner', Owner)
    monkeypatch.setattr(native, 'probe_sandbox', lambda: {'fake': True})
    monkeypatch.setattr(native, 'build_sdk', lambda *args: None)
    monkeypatch.setattr(native, 'finalize_recording', lambda *args: {})
    def policy(**kwargs):
        calls.append('policy')
        assert kwargs['code'] == code and kwargs['max_sdk_calls'] == 320
        return SimpleNamespace(status='completed', error=None, call_count=1)
    monkeypatch.setattr(native, 'execute_formal_policy', policy)
    real_run = exp.run
    def dispatch(store, experiment_id, **kwargs):
        assert kwargs['max_cells'] == kwargs['max_slots'] == 1
        assert 'runner_factory' not in kwargs
        assert json.loads(kwargs['client'].reply) == dict(source=code, reason=ref.REASON)
        calls.append('dispatch')
        return real_run(store, experiment_id, **kwargs)
    monkeypatch.setattr(exp, 'run', dispatch)
    verdict = ref.run(seed, source, tmp_path / 'out', None if seed == 102 else contract)
    assert verdict['verdict'] == 'failure', verdict
    assert calls == ['dispatch', 'native_start', 'policy']
    assert verdict['reached_close'] is False
    assert source.read_text() == code


@pytest.fixture(autouse=True)
def offline_r4_snapshot(r4_deployment):
    """New dev defaults bind an isolated authenticated snapshot."""
