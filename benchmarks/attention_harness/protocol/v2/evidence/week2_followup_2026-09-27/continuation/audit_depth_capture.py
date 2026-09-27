"""Path-relative audit of fresh-process replay and formal capture smoke."""
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def load(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


replay_freeze = load(ROOT / 'depth_exact_replay_freeze.json')
assert sha(ROOT / 'replay_exact_depth_500.py') == replay_freeze['script_sha256']
replay = load(ROOT / 'depth_exact_replay' / 'progress.json')
assert len(replay) == replay_freeze['repetitions'] == 20
assert all(row == load(ROOT / 'depth_exact_replay' /
                       f"fresh_service_{row['index']:02d}" / 'result.json') for row in replay)
assert all(row['status'] == 'completed' and row['native_success'] is False
           and row['service_stop']['leader_reaped']
           and row['service_stop']['process_group_gone'] for row in replay)
initial_hashes = {row['initial_depth_sha256'] for row in replay}
step_hashes = {row['step_depth_sha256'] for row in replay}
assert len(initial_hashes) == len(step_hashes) == 1

formal_freeze = load(ROOT / 'depth_capture_formal_freeze.json')
assert sha(ROOT / 'run_depth_capture_formal.py') == formal_freeze['script_sha256']
formal = load(ROOT / 'depth_capture_formal_progress.json')
assert [(row['task'], row['seed']) for row in formal] == [
    (case['task'], case['seed']) for case in formal_freeze['cases']
]
rows = []
for row, case in zip(formal, formal_freeze['cases']):
    assert sha(ROOT / case['config']) == case['config_sha256']
    directory = ROOT / row['artifact_dir']
    result = load(directory / 'result.json')
    trace = load(directory / 'trace.json')
    safety = load(directory / 'safety.json')
    native = load(directory / 'native_result.json')
    receipt = load(directory / 'sandbox_receipt.json')
    for kind, name in (('trace', 'trace.json'), ('safety', 'safety.json'),
                       ('sandbox_receipt', 'sandbox_receipt.json'),
                       ('native_result', 'native_result.json')):
        assert sha(directory / name) == row['artifacts'][kind]['sha256']
        assert row['artifacts'][kind]['matches_receipt']
    assert row['status'] == result['status'] == trace['status'] == 'completed'
    assert result['formal_eligible'] is False
    assert native['evaluated'] and native['native_success'] is False
    assert row['native_evaluator'] == result['native_evaluator']
    assert safety['unsafe_attempts'] == row['safety_unsafe_attempts'] == 0
    assert sum(e['operation'] in ('open', 'close') for e in trace['sdk_events']) == 6
    stop = receipt['service_stop']
    assert stop['leader_reaped'] and stop['process_group_gone']
    assert row['invalid_frame_files'] == []
    rows.append({'task': row['task'], 'seed': row['seed'],
                 'native_success': False, 'unsafe_attempts': 0,
                 'six_gripper_actions': True, 'artifact_hashes_match': True,
                 'service_reaped': True, 'invalid_frames': 0})

report = {'schema_version': 'attentionbench.depth-capture-audit.v1',
          'formal_eligible': False,
          'exact_replay_freeze_sha256': sha(ROOT / 'depth_exact_replay_freeze.json'),
          'formal_freeze_sha256': sha(ROOT / 'depth_capture_formal_freeze.json'),
          'fresh_process_replays': len(replay),
          'replay_initial_depth_unique_hashes': len(initial_hashes),
          'replay_step_depth_unique_hashes': len(step_hashes),
          'formal_rows': rows,
          'original_root_cause_confirmed': False}
(ROOT / 'depth_capture_audit.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
print(json.dumps({'replays': len(replay), 'formal_cases': len(rows),
                  'root_cause_confirmed': False}))
