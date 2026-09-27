"""Record rejected repair candidates without mutating Memory authority."""
import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, '/home/truares/桌面/Tidybot-Universe-attention-native')
from attention_memory_service import MemoryService
from benchmarks.attention_harness.core.store import AttentionStore

ROOT = Path(__file__).resolve().parent
UNIVERSE = Path('/home/truares/桌面/Tidybot-Universe-attention-native')
GRAPH = Path('/home/truares/桌面/attentionbench-week2-graph-live-20260927/robocasa')
ID = 'candidate:attempt:attention-robocasa-counter_to_sink-seed101-59f3e6e1b326:0'
service = MemoryService(AttentionStore(GRAPH / 'attention_memory.sqlite3'),
                        artifact_root=GRAPH / 'attempts')
record = service.get_memory(ID)
assert record.status.value == 'candidate'
assert service.get_plan(ID) is None and service.list_pairs(ID) == []
service.verify_package(ID)
policies = []
policy_dir = ROOT / 'review_policies'
policy_dir.mkdir(exist_ok=True)
for name in ('robocasa_mobile_sink_policy.py', 'robocasa_mobile_sink_segmented_candidate.py'):
    source = UNIVERSE / 'benchmarks/attention_harness/protocol/v2/policies' / name
    copy = policy_dir / name
    shutil.copy2(source, copy)
    policies.append({'path': str(copy.relative_to(ROOT)),
                     'sha256': hashlib.sha256(copy.read_bytes()).hexdigest()})
probes = []
for name in ('robocasa_pilot', 'robocasa_segmented_pilot'):
    directory = next((ROOT / name).iterdir())
    result = json.loads((directory / 'result.json').read_text())
    safety = json.loads((directory / 'safety.json').read_text())
    trace = json.loads((directory / 'trace.json').read_text())
    sandbox = json.loads((directory / 'sandbox_receipt.json').read_text())
    native = json.loads((directory / 'native_result.json').read_text())
    probes.append({
        'name': name, 'artifact_dir': str(directory.relative_to(ROOT)),
        'policy_sha256': result['policy_sha256'],
        'config_sha256': result['config_sha256'],
        'status': result['status'], 'error': result['error'],
        'native_evaluated': native['evaluated'],
        'native_success': native['native_success'],
        'safety_unsafe_attempts': safety['unsafe_attempts'],
        'safety_violations': safety['violations'],
        'sdk_operations': [(e['operation'], e['status']) for e in trace['sdk_events']],
        'service_stop': sandbox['service_stop'],
        'artifacts': {
            kind: {'sha256': hashlib.sha256((directory / filename).read_bytes()).hexdigest(),
                   'matches_runner': hashlib.sha256((directory / filename).read_bytes()).hexdigest()
                                    == result['artifacts'][kind]['sha256']}
            for kind, filename in (('trace', 'trace.json'), ('safety', 'safety.json'),
                                   ('sandbox_receipt', 'sandbox_receipt.json'),
                                   ('native_result', 'native_result.json'))
        },
    })
review = {
    'schema_version': 'attentionbench.repair-review.v1',
    'formal_eligible': False,
    'memory_id': ID,
    'source_status': record.status.value,
    'source_package_verified': True,
    'source_attempt': 'original_sources/robocasa_source_attempt',
    'advisor_candidate': 'original_sources/candidate_memory',
    'review_scope': {'suite': 'robocasa', 'task_id': 'counter_to_sink',
                     'perception_mode': 'sim_gt', 'seed': 101},
    'reviewed_policy_files': policies,
    'probes': probes,
    'approval_decision': 'rejected_for_validation',
    'reason': 'Both public-SDK policies fail on their first base.move_delta; Agent Server BaseError says timeout waiting for base to reach target pose. Independent Safety stops with action_outcome_unknown, native_success=false. Historical success used an older runtime and cannot attest the current C/A/T combination.',
    'validation_plan_recorded': False,
    'paired_cases_run': 0,
    'promotion_requested': False,
    'post_promotion_use_test': 'not_applicable; no trusted version exists',
    'candidate_status_after_review': service.get_memory(ID).status.value,
}
(ROOT / 'repair_review.json').write_text(json.dumps(review, indent=2, sort_keys=True) + '\n')
print(json.dumps({'candidate': review['candidate_status_after_review'],
                  'probes': len(probes), 'approval': review['approval_decision']}))
