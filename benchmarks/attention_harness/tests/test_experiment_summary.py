import hashlib
import json
import sqlite3

import pytest
from benchmarks.attention_harness.tests.r3_fixtures import r3_deployment

from benchmarks.attention_harness.dev_configuration import require_eval_gate, normalize_configuration
from benchmarks.attention_harness import dev_experiment
from benchmarks.attention_harness.dev_experiment import create
from benchmarks.attention_harness.dev_ledger import DevV2Store
from benchmarks.attention_harness.eval_quality import summarize_eval_quality
from benchmarks.attention_harness.summarize_experiment import read_experiment, summarize, markdown


@pytest.mark.eval_gate
def test_experiment_eval_gate_prevents_creation(tmp_path, monkeypatch, r3_deployment):
    store = DevV2Store(tmp_path / 'store.sqlite')
    selection = {'request_key': 'eval-required', 'tasks': ['open_cabinet'], 'seeds': [102], 'policies': ['autonomous']}
    with pytest.raises(ValueError, match='requires eval_agent.enabled'):
        create(store, tmp_path / 'artifacts', selection)
    with pytest.raises(ValueError, match='requires eval_agent.enabled'):
        create(store, tmp_path / 'modern-artifacts', {**selection, 'target_depth': 3})
    with store.tx() as db:
        assert db.execute('SELECT count(*) FROM v2_experiments').fetchone()[0] == 0
    require_eval_gate('interactive', selection)
    require_eval_gate('diagnostic', selection)
    require_eval_gate('experiment', {**selection, 'eval_agent': {'enabled': True, 'model': 'parcc/Qwen'}})
    with pytest.raises(ValueError, match='requires eval_agent.enabled'):
        require_eval_gate('experiment', {**selection, 'eval_agent': {'enabled': False}})
    with pytest.raises(ValueError, match='requires eval_agent.enabled'):
        require_eval_gate('experiment', {**selection, 'eval_agent': {'enabled': 1}})
    with pytest.raises(ValueError, match='requires eval_agent.enabled'):
        create(store, tmp_path / 'empty-eval', {**selection, 'target_depth': 3, 'eval_agent': {}})
    with pytest.raises(ValueError, match='fixed vision Eval'):
        normalize_configuration({'eval_agent': {'enabled': True, 'model': 'text-only-model'}})
    class ReachedArtifactCreation(Exception):
        pass
    def reached(_,**kwargs):
        raise ReachedArtifactCreation
    monkeypatch.setattr(dev_experiment, 'make_lock', reached)
    monkeypatch.setattr(dev_experiment, 'require_scene_gate', lambda *args: None)
    for mode, configured in [('interactive', False), ('diagnostic', False), ('experiment', True)]:
        choice = {**selection, 'mode': mode, 'request_key': 'eval-required-' + mode}
        if configured:
            choice['eval_agent'] = {'enabled': True, 'model': 'parcc/Qwen'}
        with pytest.raises(ReachedArtifactCreation):
            create(store, tmp_path / mode, choice)


def test_eval_quality_all_outcomes_and_false_stop():
    cells = [
        {'key': 'a', 'policy_id': 'reactive_help', 'reason': 'eval_passed', 'versions': [
            {'version': 1, 'passed': True, 'mode': 'vision', 'native_success': False}]},
        {'key': 'b', 'policy_id': 'reactive_help', 'reason': 'eval_passed', 'versions': [
            {'version': 1, 'passed': False, 'mode': 'vision', 'native_success': False},
            {'version': 2, 'passed': True, 'mode': 'vision', 'native_success': True}]},
        {'key': 'c', 'policy_id': 'autonomous', 'reason': 'max_versions_reached', 'versions': [
            {'version': 1, 'passed': False, 'mode': 'vision', 'native_success': True},
            {'version': 2, 'passed': None, 'mode': 'text_only', 'native_success': None},
            {'version': 3, 'passed': None, 'mode': 'unavailable', 'native_success': False}]},
    ]
    q = summarize_eval_quality(cells)
    assert q['confusion_matrix'] == {'true_positive': 1, 'false_positive': 1, 'true_negative': 1, 'false_negative': 1}
    assert q['false_passes'] == 1 and q['false_failures'] == 1
    assert q['text_only'] == 1 and q['unavailable'] == 1 and q['unevaluated'] == 1
    assert q['false_stops'] == [{'cell': 'a', 'policy_id': 'reactive_help', 'version': 1}]
    assert q['by_policy']['reactive_help']['false_pass_rate'] == pytest.approx(1 / 2)
    assert q['by_policy']['reactive_help']['false_pass_fraction_all'] == pytest.approx(1 / 3)


def _ref(path, value):
    data = json.dumps(value).encode()
    path.write_bytes(data)
    return {'uri': str(path), 'sha256': hashlib.sha256(data).hexdigest()}


def test_reader_uses_v2_receipts_and_summary_does_not_write(tmp_path):
    dbpath = tmp_path / 'store.sqlite'
    manifest = _ref(tmp_path / 'manifest.json', {'schema': 'attentionbench.dev-experiment.v2',
                                               'experiment_id': 'e', 'selection': {'help_budget': 2}})
    native = _ref(tmp_path / 'native_result.json', {'run_id': 'r', 'attempt_id': 'a', 'evaluated': True, 'native_success': False})
    bundle = _ref(tmp_path / 'bundle.json', {'run_id': 'r', 'attempt_id': 'a', 'status': 'completed', 'artifacts': {'native_result': native}})
    eval_ref = _ref(tmp_path / 'eval-review.json', {'status': 'completed', 'mode': 'vision', 'passed': True})
    advisor_ref = _ref(tmp_path / 'advisor-answer.json', {'localization': {'status': 'generic_fallback'}})
    with sqlite3.connect(dbpath) as db:
        db.executescript('CREATE TABLE v2_experiments (id TEXT,payload TEXT); CREATE TABLE v2_cells (id TEXT,exp TEXT,payload TEXT); CREATE TABLE v2_versions (cell TEXT,version INTEGER,payload TEXT); CREATE TABLE v2_jobs (id TEXT,cell TEXT,version INTEGER,kind TEXT,state TEXT,payload TEXT); CREATE TABLE v2_planner_requests (id TEXT,cell TEXT,payload TEXT);')
        db.execute('INSERT INTO v2_experiments VALUES (?,?)', ('e', json.dumps({'experiment_id': 'e', 'manifest': manifest})))
        db.execute('INSERT INTO v2_cells VALUES (?,?,?)', ('c', 'e', json.dumps({'task_id': 't', 'seed': 102, 'policy_id': 'p', 'state': 'completed', 'reason': 'eval_passed', 'created_at': 1})))
        db.execute('INSERT INTO v2_versions VALUES (?,?,?)', ('c', 1, json.dumps({'version': 1})))
        for kind, receipt in [('execute', {'bundle': bundle, 'native_result': native}), ('eval', {'eval_review': eval_ref})]:
            db.execute('INSERT INTO v2_jobs VALUES (?,?,?,?,?,?)', (kind, 'c', 1, kind, 'settled', json.dumps({'kind': kind, 'version': 1, 'receipt': receipt, 'actual': {}, 'settled_at': 5})))
        db.execute('INSERT INTO v2_jobs VALUES (?,?,?,?,?,?)', ('advisor', 'c', 2, 'advisor', 'settled', json.dumps({'kind': 'advisor', 'version': 2, 'receipt': {'advisor_answer': advisor_ref}, 'actual': {'advisor_calls': 1, 'hint_human_seconds': 44}})))
        db.execute('INSERT INTO v2_planner_requests VALUES (?,?,?)', ('timeout', 'c', json.dumps({'state': 'timed_out', 'request_type': 'advisor', 'version': 3})))
    before = dbpath.read_bytes()
    result = summarize(read_experiment(dbpath))
    assert dbpath.read_bytes() == before
    assert result['eval_quality']['false_stop_count'] == 1
    assert result['by_policy']['p']['native_success_rate'] == 0
    assert result['frontier_plot_data'][0]['attention_budget_level'] == 2
    assert result['frontier_plot_data'][0]['mean_logical_human_seconds'] is None
    assert result['by_policy']['p']['request_units'] == 2
    assert result['by_policy']['p']['logical_human_seconds'] == 44
    assert result['by_policy']['p']['attention_seconds_unknown_requests'] == 1
    assert result['by_policy']['p']['timeouts'] == 1 and result['by_policy']['p']['fallbacks'] == 1
    (tmp_path / 'native_result.json').write_text('{}')
    damaged = summarize(read_experiment(dbpath))
    assert damaged['by_policy']['p']['native_success_rate'] is None
    assert 'digest mismatch' in damaged['read_errors']['c'][0]
    assert dbpath.read_bytes() == before


def test_summary_separates_unknown_and_attention_components():
    base = {'task_id': 't', 'seed': 102, 'policy_id': 'p', 'generated_versions': 1,
            'attention_budget_level': 2, 'eval_compute': {'eval_calls': 1, 'provider_tokens': 10, 'model_seconds': 3},
            'read_errors': [], 'timeouts': 0, 'fallbacks': 0, 'created_at': 1}
    cells = [
        {**base, 'key': 'success', 'state': 'completed', 'reason': 'eval_passed', 'request_units': 1,
         'logical_human_seconds': 44, 'versions': [{'version': 1, 'native_success': True, 'passed': True,
                                                    'mode': 'vision', 'settled_at': 7}]},
        {**base, 'key': 'unknown', 'state': 'failed', 'reason': 'max_versions_reached', 'request_units': 0,
         'logical_human_seconds': 0, 'timeouts': 1,
         'versions': [{'version': 1, 'native_success': None, 'passed': False,
                       'mode': 'vision', 'settled_at': 5}]},
        {**base, 'key': 'invalid', 'state': 'condition_invalid', 'reason': 'eval_vision_unavailable',
         'request_units': 0, 'logical_human_seconds': 0, 'versions': []},
        {**base, 'key': 'dominated', 'policy_id': 'q', 'state': 'failed', 'reason': 'max_versions_reached',
         'request_units': 2, 'logical_human_seconds': 50,
         'versions': [{'version': 1, 'native_success': False, 'passed': False,
                       'mode': 'vision', 'settled_at': 6}]},
    ]
    result = summarize({'experiment_id': 'test', 'cells': cells})
    row = result['by_policy']['p']
    assert row['evaluated_cells'] == 1 and row['unknown_cells'] == 1
    assert row['native_success_rate'] == 1 and row['final_version_success_rate'] == 1
    assert row['request_units'] == 1 and row['logical_human_seconds'] == 44
    assert row['first_success_versions'] == [1] and row['time_to_first_success_seconds'] == [6]
    assert row['invalid_cells']['vision_unavailable'] == 1 and row['timeouts'] == 1
    assert row['eval_compute']['eval_calls'] == 2
    assert result['nondominated_policies_by_budget']['2'] == ['p']
    rendered = markdown(result)
    assert 'Marginal utility per request' in rendered and 'False passes by policy' in rendered


def test_frontier_requires_paired_scene():
    cells = []
    for policy, seed, native in [('p', 101, True), ('q', 102, False)]:
        cells.append({'key': policy, 'task_id': 't', 'seed': seed, 'policy_id': policy,
                      'state': 'completed', 'reason': 'max_versions_reached', 'created_at': 1,
                      'attention_budget_level': 1, 'request_units': 0, 'logical_human_seconds': 0,
                      'timeouts': 0, 'fallbacks': 0, 'read_errors': [],
                      'eval_compute': {'eval_calls': 0, 'provider_tokens': 0, 'model_seconds': 0},
                      'versions': [{'version': 1, 'native_success': native, 'passed': False,
                                    'mode': 'vision', 'settled_at': 2}]})
    result = summarize({'experiment_id': 'unpaired', 'cells': cells})
    assert all(p['paired_task_scene_repeats'] == 0 and p['native_success_rate'] is None
               for p in result['frontier_plot_data'])
    assert result['nondominated_policies_by_budget']['1'] == []
