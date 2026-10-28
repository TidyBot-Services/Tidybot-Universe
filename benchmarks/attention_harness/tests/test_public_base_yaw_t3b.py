"""Offline writer contract: actual execute/boundary/runner, synthetic native I/O."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness import dev_v2_loop as loop
from benchmarks.attention_harness.tests.test_dev_v2_history import frozen_creation
from openarm365_dev_v2 import runner as native
from openarm365_dev_v2.tests.test_formal_runner import fake_native
from openarm365_dev_v2.tests.test_planner_features import setup, add_version, SENSOR


@pytest.mark.parametrize('history', [False, True])
@pytest.mark.parametrize('validation', [False, True])
@pytest.mark.parametrize('factory_path', [False, True])
@pytest.mark.parametrize('gpu_measurement', [False, True])
@pytest.mark.parametrize('terminal', ['completed', 'failed', 'timeout', 'cancelled', 'condition_invalid', 'exception', 'infrastructure_failure'])
def test_execute_seals_exact_public_yaw_decision(tmp_path, monkeypatch, frozen_creation,
                                                fake_native, history, validation, factory_path, gpu_measurement, terminal):
    from benchmarks.attention_harness import dev_experiment
    write = dev_experiment.immutable
    def freeze(path, value):
        if isinstance(value, dict) and value.get('schema') == 'attentionbench.dev-experiment.v3':
            value['dev_version_history'] = history
            for cfg in value['configs'].values():cfg['dev_version_history'] = history
        return write(path, value)
    monkeypatch.setattr(dev_experiment, 'immutable', freeze)
    store, exp, cell, config, owner, root = setup(tmp_path,
        planner_features={'approval_rule': 'none', 'human_wait_seconds': 0, 'background_seconds': 0})
    record, directory = add_version(store, cell, root, source=SENSOR)
    # Preserve runner exception handling and boundary checks, without starting a simulator.
    monkeypatch.setattr(native.OpenArm365DevRunnerV2, 'load_config', lambda self, request: config)
    monkeypatch.setattr(native, 'build_sdk', lambda owner, event: event({
        'operation': 'move_delta', 'source': 'base', 'status': 'completed',
        'arguments': {'dx': .1, 'dy': .2, 'dyaw': .3}}))
    def policy(**kw):
        if terminal == 'exception':
            raise RuntimeError('controlled policy exception after SDK event')
        return SimpleNamespace(status=terminal, error=None, call_count=1)
    monkeypatch.setattr(native, 'execute_formal_policy', policy)
    start = native.NativeOwner.start
    def start_owner(self):
        if terminal == 'infrastructure_failure':
            raise RuntimeError('controlled startup exception')
        ready = start(self)
        if terminal == 'condition_invalid':ready['scene_condition'] = {'matched': False}
        return ready
    monkeypatch.setattr(native.NativeOwner, 'start', start_owner)
    from benchmarks.attention_harness import dev_memory_validation, gpu_meter, dev_version_history
    monkeypatch.setattr(dev_memory_validation, 'policy_input', lambda *a: ({'controlled': True}, {'controlled': True}))
    meter_calls = []
    monkeypatch.setattr(gpu_meter.GpuMeter, 'start', lambda self: meter_calls.append('start') or self)
    monkeypatch.setattr(gpu_meter.GpuMeter, 'stop', lambda self: meter_calls.append('stop') or {'gpu_seconds': 1.25, 'samples': 3})
    calls = []
    enabled = dev_version_history.enabled
    monkeypatch.setattr(dev_version_history, 'enabled', lambda cfg: calls.append(cfg) or enabled(cfg))
    captured = []
    boundary = loop.run_with_formal_boundary
    def run(*a, **kw):
        result = boundary(*a, **kw)
        captured.append(copy.deepcopy(result))
        return result
    monkeypatch.setattr(loop, 'run_with_formal_boundary', run)
    args = (store, cell['key'], 1, record, config, owner, directory,
            native.OpenArm365DevRunnerV2 if factory_path else None)
    if terminal == 'infrastructure_failure':
        with pytest.raises(loop.DevConflict, match='native infrastructure'):
            loop.execute(*args, memory_validation={'controlled': True} if validation else None, gpu_measurement=gpu_measurement)
    else:
        loop.execute(*args, memory_validation={'controlled': True} if validation else None, gpu_measurement=gpu_measurement)
    metered = (validation or gpu_measurement) and not factory_path
    assert meter_calls == (['start', 'stop'] if metered else [])
    bundle = json.loads((directory / 'bundle.json').read_bytes())
    visible = history and not validation
    assert bundle.pop('public_base_yaw_visible') is visible
    assert len(calls) == 1
    # All prior bundle fields retain exactly the original serialization.
    expected = {**captured[0], 'channel': 'dev_v2', 'formal_eligible': False,
                'dev_version': 1, 'policy_id': cell['policy_id'],
                'config_lock': {'uri': str(root / 'config.json'), 'sha256': loop.sha(root / 'config.json')}}
    assert json.dumps(bundle, sort_keys=True) == json.dumps(expected, sort_keys=True)
    raw = json.loads((Path(bundle['artifact_dir']) / 'result.json').read_bytes())
    assert 'public_base_yaw_visible' not in raw  # Native/formal result remains untouched.
    job = loop.job_for(store.cell(cell['key']), 1, 'execute')
    receipt = job['review_evidence'] if terminal == 'infrastructure_failure' else job['receipt']
    assert loop.checked(receipt['bundle']) == directory / 'bundle.json'
    if terminal == 'infrastructure_failure':
        assert job['state'] == 'needs_review'
        assert not (directory / 'public-feedback.json').exists()
        return
    feedback = json.loads((directory / 'public-feedback.json').read_bytes())
    if terminal == 'condition_invalid':
        assert feedback['action_trace'] == []
    else:
        assert ('dyaw' in feedback['action_trace'][0]['arguments']) is visible
    if terminal == 'exception':
        assert bundle['status'] == 'failed'
        trace = json.loads(loop.checked(bundle['artifacts']['trace']).read_bytes())
        assert 'memory_validation' not in trace
    if metered:
        measurement = json.loads(loop.checked(job['receipt']['gpu_measurement']).read_bytes())
        assert measurement['bundle_sha256'] == job['receipt']['bundle']['sha256']
        assert measurement['cell'] == cell['key'] and measurement['version'] == 1
        assert measurement['attempt_id'] == bundle['attempt_id']
        from benchmarks.attention_harness.memory_validation_provision import Provisioner
        assert Provisioner.gpu_seconds(store, cell['key']) == 1.25
    else:
        assert 'gpu_measurement' not in job['receipt']
        assert not (directory / 'gpu-measurement.json').exists()


@pytest.mark.parametrize('flag,history,error', [
    (True, True, None), (False, True, None), ('absent', True, None),
    (False, False, None), ('absent', False, None),
    (True, False, 'requires Dev version history'), (1, True, 'must be a bool'),
    (None, True, 'must be a bool'),
])
def test_independent_validator_uses_sealed_decision(tmp_path, monkeypatch, flag, history, error):
    from benchmarks.attention_harness.tests.test_memory_independent_validator import arm
    from benchmarks.attention_harness.tests.autovalidate_fakes import Fixture
    from benchmarks.attention_harness.memory_independent_validator import validate_arm
    from attention_memory_service import dev_v2
    write = Fixture.write
    def synthetic_write(path, value):
        if path.name == 'config.json':
            value.update(budget_tier='dev', dev_version_history=history)
        if path.name == 'bundle.json' and flag != 'absent':
            value['public_base_yaw_visible'] = flag
        if path.name == 'trace.json':
            value.setdefault('sdk_events', []).append({'source': 'base', 'operation': 'move_delta',
                'status': 'completed', 'arguments': {'dyaw': .25}})
        return write(path, value)
    monkeypatch.setattr(Fixture, 'write', staticmethod(synthetic_write))
    _, _, args = arm(tmp_path)
    project = dev_v2.public_trace_projection
    seen = []
    def projection(trace, *, base_yaw_visible):
        assert base_yaw_visible is (flag is True)
        feedback = project(trace, base_yaw_visible=base_yaw_visible)
        assert ('dyaw' in feedback['action_trace'][-1]['arguments']) is (flag is True)
        seen.append(feedback)
        return feedback
    monkeypatch.setattr(dev_v2, 'public_trace_projection', projection)
    result = validate_arm(**args)
    if error:
        assert result['status'] == 'refused' and error in result['reason']
        assert not seen and not args['validator_root'].exists()
    else:
        assert result['status'] == 'passed' and len(seen) == 1
