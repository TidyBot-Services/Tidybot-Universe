"""Read only audit of completed depth stability smoke artifacts."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
progress = json.loads((ROOT / 'formal_depth_progress.json').read_text())
freeze = json.loads((ROOT / 'depth_stability_freeze.json').read_text())
configs = {(item['task'], item['seed']): item for item in freeze['configs']}
audited = []
for case in progress:
    directory = Path(case['artifact_dir'])
    runner = json.loads((directory / 'result.json').read_text())
    native = json.loads((directory / 'native_result.json').read_text())
    safety = json.loads((directory / 'safety.json').read_text())
    trace = json.loads((directory / 'trace.json').read_text())
    sandbox = json.loads((directory / 'sandbox_receipt.json').read_text())
    hashes = {}
    for kind, filename in (('trace', 'trace.json'), ('safety', 'safety.json'),
                           ('sandbox_receipt', 'sandbox_receipt.json'),
                           ('native_result', 'native_result.json')):
        actual = hashlib.sha256((directory / filename).read_bytes()).hexdigest()
        hashes[kind] = {'sha256': actual, 'matches_runner': actual == runner['artifacts'][kind]['sha256']}
    events = trace.get('sdk_events', [])
    stop = sandbox['service_stop']
    audited.append({
        'task': case['task'], 'seed': case['seed'], 'artifact_dir': str(directory),
        'config_matches_freeze': runner['config_sha256'] == configs[(case['task'], case['seed'])]['sha256'],
        'policy_matches_freeze': runner['policy_sha256'] == freeze['policy_sha256'],
        'service_revision_matches_freeze': runner['service_revision'] == freeze['service_revision'],
        'status': runner['status'], 'error': runner['error'],
        'native_evaluated': native['evaluated'], 'native_success': native['native_success'],
        'native_matches_runner': native['native_success'] == runner['native_evaluator']['native_success'],
        'trace_status_matches_runner': trace['status'] == runner['status'],
        'sdk_actions': [e.get('operation') for e in events if e.get('event_type') == 'sdk.gripper_command'],
        'safety_unsafe_attempts': safety['unsafe_attempts'],
        'safety_violations': safety['violations'],
        'artifact_hashes': hashes,
        'service_stop': stop,
        'service_reaped': stop['leader_reaped'] and stop['process_group_gone'],
        'formal_eligible': False,
    })
(ROOT / 'formal_depth_audit.json').write_text(json.dumps(audited, indent=2, sort_keys=True) + '\n')
print(json.dumps({'cases': len(audited),
                  'all_hashes': all(all(v['matches_runner'] for v in x['artifact_hashes'].values()) for x in audited),
                  'all_native': all(x['native_evaluated'] and x['native_matches_runner'] for x in audited),
                  'all_frozen_inputs': all(x['config_matches_freeze'] and x['policy_matches_freeze']
                                           and x['service_revision_matches_freeze'] for x in audited),
                  'all_safe': all(x['safety_unsafe_attempts'] == 0 for x in audited),
                  'all_reaped': all(x['service_reaped'] for x in audited),
                  'actions': [len(x['sdk_actions']) for x in audited]}))
