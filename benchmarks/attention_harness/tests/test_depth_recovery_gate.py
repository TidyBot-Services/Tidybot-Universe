import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from benchmarks.attention_harness.protocol.v2.attrition_gate import assess
from benchmarks.attention_harness.protocol.v2.depth_recovery_evidence import verify_recovery


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write(p, value):
    p.write_text(json.dumps(value))
    return sha(p)


def recovery(tmp_path, mutation=None):
    first = np.array([[[1.1]]], dtype=np.float32)
    second = np.array([[[0.5]]], dtype=np.float32)
    a, b, e = (tmp_path / name for name in ('first.npy', 'second.npy', 'equivalence.npz'))
    np.save(a, first); np.save(b, second)
    metric = (0.1 / (1 - second.astype(np.float64) * (1 - 0.1 / 10))).astype(np.float32)
    original = {'agentview_depth': first, 'agentview_image': np.zeros((1, 1, 3), dtype=np.uint8), 'robot0_joint_pos': np.zeros(7)}
    public = {**original, 'agentview_depth': metric, 'agentview_intrinsics': np.eye(3), 'agentview_pose_mat': np.eye(4)}
    if mutation:
        mutation(public)
    np.savez(e, **{'original__'+k:v for k,v in original.items()}, **{'public__'+k:v for k,v in public.items()},
             normalized_depth=second, near=np.array(0.1), far=np.array(10.0))
    repeat = {'status': 'valid', 'observation_only': True, 'simulation_time_unchanged': True,
              'qpos_unchanged': True, 'simulation_time_before': 0.1, 'simulation_time_after': 0.1,
              'qpos_before': [0.2], 'qpos_after': [0.2], 'intrinsics_before': np.eye(3).tolist(),
              'intrinsics_after': np.eye(3).tolist(), 'pose_before': np.eye(4).tolist(), 'pose_after': np.eye(4).tolist(),
              'capture_path': str(b), 'capture_sha256': sha(b), 'frame_sha256': hashlib.sha256(second.tobytes()).hexdigest()}
    return {'camera_name': 'agentview', 'capture_path': str(a), 'capture_sha256': sha(a),
            'frame_sha256': hashlib.sha256(first.tobytes()).hexdigest(), 'repeat_observation': repeat,
            'equivalence': {'path': str(e), 'sha256': sha(e), 'passed': True}}


def test_gate_requires_frozen_independent_review_and_recomputes_artifacts(tmp_path):
    d = recovery(tmp_path)
    ids = {'attempt_id': 'a', 'run_id': 'r'}
    native = {**ids, 'evaluated': True, 'native_success': False}
    safety = {**ids, 'source': 'independent_safety_monitor', 'unsafe_attempts': 0, 'violations': []}
    receipt = {**ids, 'service_revision': 'service-commit', 'service_source_unchanged': True,
               'service_stop': {'leader_reaped': True, 'process_group_gone': True}}
    trace = {**ids, 'suite': 'robosuite', 'task_id': 'cube_lift', 'seed': 101, 'condition': 'autonomous',
             'policy_sha256': 'policy', 'config_sha256': 'config', 'backend_steps': [{'step': 0, 'depth_recovery': d}],
             'status': 'completed', 'error': None}
    docs = {'native_result': native, 'safety': safety, 'sandbox_receipt': receipt, 'trace': trace}
    hashes = {k: write(tmp_path/(k+'.json'), value) for k,value in docs.items()}
    bundle = tmp_path/'bundle.json'; write(bundle, {})
    attempt = {'index': 0, 'issues': [], 'status': 'completed', 'policy_error': None,
               'native_evaluated': True, 'native_success': False, 'safety_source': 'independent_safety_monitor',
               'safety_unsafe_attempts': 0, 'safety_violations': [], 'artifact_sha256': hashes,
               'raw_trace_link': {**ids, 'bundle': str(bundle)}}
    slot = {'suite': 'robosuite', 'task_id': 'cube_lift', 'seed': 101, 'condition': 'autonomous', 'order': 0,
            'policy_sha256': 'policy', 'config_sha256': 'config', 'service_revision': 'service-commit', 'artifact_root': str(tmp_path)}
    plan = tmp_path/'plan.json'; ph = write(plan, {'slots': [slot], 'predeclared_before_runs': True,
                'grid': {'tasks': [['robosuite','cube_lift']], 'seeds': [101], 'conditions': ['autonomous']}})
    audit = tmp_path/'audit.json'; ah = write(audit, {**slot, 'profile_case_complete': True, 'issues': [],
                   'attempt_count': 1, 'attempts': [attempt], 'native_success': False})
    ledger = tmp_path/'ledger.json'; lh = write(ledger, {'plan_sha256': ph, 'rows': [{**slot, 'case_complete': True,
                'invalid_reasons': [], 'audit_path': str(audit), 'audit_sha256': ah, 'native_success': False}]})
    assert not assess(plan, ledger, ph, lh)['structural_attrition_gate']
    review = tmp_path/'review.json'; rh = write(review, {'schema_version': 'attentionbench.depth-recovery-review.v1',
        'reviewer_role': 'independent_auditor', 'reviewer_id': 'test-reviewer', 'plan_sha256': ph, 'ledger_sha256': lh,
        'recoveries': [{'attempt_id': 'a', 'step': 0, 'approved': True, 'trace_sha256': hashes['trace'],
        'service_revision': 'service-commit', 'first_capture_sha256': d['capture_sha256'],
        'repeat_capture_sha256': d['repeat_observation']['capture_sha256'], 'equivalence_sha256': d['equivalence']['sha256']}]})
    result = assess(plan, ledger, ph, lh, review, rh)
    assert result['structural_attrition_gate'], result['issues']
    assert result['valid_native_failures'] == 1
    assert not assess(plan, ledger, ph, lh, review, 'wrong')['structural_attrition_gate']
    (tmp_path/'second.npy').write_bytes(b'corrupt')
    assert not assess(plan, ledger, ph, lh, review, rh)['structural_attrition_gate']


@pytest.mark.parametrize('change', [
    lambda p: p.update(agentview_depth=p['agentview_depth'].astype(np.float64)),
    lambda p: p.update(agentview_depth=np.ones((2,2),dtype=np.float32)),
    lambda p: p.update(agentview_depth=p['agentview_depth']*2),
    lambda p: p.update(robot0_joint_pos=np.ones(7)),
    lambda p: p.update(agentview_intrinsics=np.eye(3)*2),
])
def test_recovery_rejects_inequivalent_public_observation(tmp_path, change):
    assert verify_recovery(recovery(tmp_path, change))


def test_recovery_rejects_physics_advance_even_with_true_flags(tmp_path):
    d = recovery(tmp_path)
    d['repeat_observation']['qpos_after'] = [0.3]
    d['repeat_observation']['simulation_time_after'] = 0.2
    assert 'qpos_changed_or_missing' in verify_recovery(d)
    assert 'simulation_time_changed_or_missing' in verify_recovery(d)
