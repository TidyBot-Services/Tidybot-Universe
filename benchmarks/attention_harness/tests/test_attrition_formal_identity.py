import hashlib
import json
import pytest
from benchmarks.attention_harness.protocol.v2.attrition_gate import trace_condition


def formal_trace():
    lock = {'schema_version': 'attentionbench.formal-entry-lock.v1', 'suite': 'robosuite',
            'task_id': 'cube_lift', 'seed': 101, 'attention_policy': 'autonomous',
            'approved_policy_sha256': 'policy', 'approved_config_sha256': 'config'}
    lock['sha256'] = hashlib.sha256(json.dumps(lock, sort_keys=True, ensure_ascii=False,
                                             separators=(',', ':')).encode()).hexdigest()
    return {'schema_version': 'attentionbench.formal-trace.v1', 'suite': 'robosuite',
            'task_id': 'cube_lift', 'seed': 101, 'policy_sha256': 'policy',
            'config_sha256': 'config', 'entry_lock': lock}


def test_formal_hash_bound_condition():
    assert trace_condition(formal_trace()) == 'autonomous'


@pytest.mark.parametrize('mutation', [
    lambda t: t['entry_lock'].update(sha256='wrong'),
    lambda t: t.update(condition='demo_first'),
    lambda t: t.update(seed=102),
    lambda t: t.update(policy_sha256='other'),
    lambda t: t.update(entry_lock=None),
])
def test_formal_identity_rejected(mutation):
    trace = formal_trace()
    mutation(trace)
    with pytest.raises(ValueError):
        trace_condition(trace)


def test_unknown_schema_cannot_default_condition():
    trace = formal_trace()
    trace['schema_version'] = 'unknown'
    assert trace_condition(trace) is None
    assert trace_condition({'condition': 'autonomous'}) == 'autonomous'
