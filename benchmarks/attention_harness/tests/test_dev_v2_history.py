"""H1: public history, creation-only gate, and unchanged validation packets."""
import copy
import hashlib
import json

import pytest

from benchmarks.attention_harness import dev_v2_loop as loop
from benchmarks.attention_harness.dev_feedback_projection import input_bound, project_messages
from benchmarks.attention_harness.dev_version_history import bounded, build, encoded, summarize
from openarm365_dev_v2.tests.test_dev_feedback_projection import CONFIG, FIXTURE


@pytest.fixture
def cell(tmp_path):
    result = {'versions': [], 'jobs': []}
    feedbacks = []
    for number in range(1, 4):
        directory = tmp_path / str(number)
        directory.mkdir()
        feedback = {'execution_status': 'failed', 'public_error': '错' * 450,
                    'action_trace': [
                        {'operation': 'move_delta', 'arguments': {'dx': .25, 'dy': -.5}, 'status': 'completed'},
                        {'source': None, 'operation': 'move_delta', 'arguments': {'dx': -.125, 'dyaw': .5}, 'status': 'completed'},
                        {'source': 'left_arm', 'operation': 'move_delta', 'arguments': {'dx': 9}, 'status': 'completed'},
                        {'source': None, 'operation': 'move_delta', 'arguments': {'dx': 99}, 'status': 'failed'},
                        {'source': 'right_arm', 'operation': 'move_delta', 'arguments': {'dz': 1}, 'error': 'motion stopped'}]}
        review = {'status': 'completed', 'passed': False, 'reason': '事实' * 250,
                  'reference_suggestion': 'Not part of the Dev opinion.', 'raw_output': 'not public'}
        shown = copy.deepcopy(feedbacks[-1]) if feedbacks else {}
        if shown:
            shown['eval_review'] = {'passed': False, 'reason': review['reason']}
        help_value = {'provider': 'advisor', 'content': '建议' * 250, 'request': {'uri': '/not-for-dev'}}
        request = {'messages': [{'role': 'user', 'content': json.dumps({
            'public_feedback': shown, 'attention_control': {'help': help_value}})}]}
        version = {'version': number, 'valid': True,
                   'source': loop.immutable(directory / 'policy.py', '# 策略\n'),
                   'model_input': loop.immutable(directory / 'model-input.json', request)}
        result['versions'].append(version)
        for kind, receipt in [('execute', {'public_feedback': loop.immutable(directory / 'feedback.json', feedback)}),
                              ('eval', {'eval_review': loop.immutable(directory / 'eval.json', review)})]:
            result['jobs'].append({'version': number, 'kind': kind, 'state': 'settled', 'receipt': receipt})
        feedbacks.append(feedback)
    return result


def test_three_executed_versions_public_fields_counts_and_truncation(cell):
    result = build(cell)
    assert [e['version'] for e in result['entries']] == [1, 2, 3]
    assert result['omitted'] == 0
    for entry in result['entries']:
        assert entry['execution_status'] == 'failed'
        assert entry['public_error'] == '错' * 400
        assert entry['failed_operation'] == {'source': None, 'operation': 'move_delta', 'arguments': {'dx': 99}}
        assert entry['operation_counts']['base_moves'] == {'count': 2, 'dx': .125, 'dy': -.5, 'dyaw': .5}
        assert sum(c['count'] for c in entry['operation_counts']['by_operation']) == 5
        assert next(c['count'] for c in entry['operation_counts']['by_operation'] if c['source'] is None) == 3
        assert entry['eval'] == {'passed': False, 'reason': '事实' * 200}
        assert entry['help'] == '建议' * 200
        assert entry['source_bytes'] == len('# 策略\n'.encode())
        assert entry['source_sha256'] == hashlib.sha256('# 策略\n'.encode()).hexdigest()
    text = encoded(result).decode()
    for absent in ('uri', 'reference_suggestion', 'raw_output', 'action_trace', 'observation', '/not-for-dev'):
        assert absent not in text
    loop.screen_model_packet({'version_history': result})


def test_only_settled_executions_and_reviews(cell):
    cell['jobs'][0]['state'] = 'claimed'
    cell['jobs'][-1]['state'] = 'claimed'
    cell['versions'][-1].pop('model_input')
    result = build(cell)
    assert [e['version'] for e in result['entries']] == [2, 3]
    assert result['entries'][-1]['eval'] is None
    assert result['entries'][-1]['help'] is None
    assert result['omitted'] == 0


def test_no_failure_and_no_help_from_cache_or_rejected_reply(cell, tmp_path):
    empty = summarize(1, b'x', {'execution_status': 'completed', 'action_trace': []})
    assert empty['failed_operation'] is empty['eval'] is empty['help'] is None
    for v, help_value in zip(cell['versions'], [{'provider': 'cache', 'content': 'internal'},
                                               {'provider': 'advisor', 'content': 'unused', 'rejected': True}, {}]):
        v['model_input'] = loop.immutable(tmp_path / f"request-{v['version']}.json", {
            'messages': [{'content': json.dumps({'attention_control': {'help': help_value}})}]})
    assert all(e['help'] is None for e in build(cell)['entries'])


def test_history_preserves_prior_trace_projection_and_visibility(cell, tmp_path):
    request = {'messages': [{'content': json.dumps({'public_feedback': {
        'execution_status': 'failed', 'public_error': 'motion stopped', 'action_trace': []}})}]}
    cell['versions'][1]['model_input'] = loop.immutable(tmp_path / 'projected.json', request)
    first = build(cell)['entries'][0]
    assert first['operation_counts']['by_operation'] == []
    assert first['failed_operation'] is None and first['eval'] is None
    assert all(e['operation_counts']['by_operation'] == [] for e in build(cell, trace_visible=False)['entries'])


def test_count_and_utf8_byte_bounds_drop_oldest(cell):
    entry = build(cell)['entries'][0]
    small = {**entry, 'public_error': '', 'help': None, 'eval': None}
    result = bounded([{**small, 'version': n} for n in range(1, 16)])
    assert [e['version'] for e in result['entries']] == list(range(4, 16))
    assert result['omitted'] == 3
    entries = [{**entry, 'version': n} for n in range(1, 16)]
    result = bounded(entries)
    assert len(encoded(result)) <= 16000
    assert result['omitted'] > 3
    assert [e['version'] for e in result['entries']] == list(range(result['omitted'] + 1, 16))
    restored = {'entries': [entries[result['omitted'] - 1]] + result['entries'], 'omitted': result['omitted'] - 1}
    assert len(encoded(restored)) > 16000
    assert bounded([{**entry, 'failed_operation': {'arguments': {'text': 'x' * 16000}}}]) == {'entries': [], 'omitted': 1}


@pytest.mark.parametrize('field', ['public_error', 'help'])
def test_every_retained_entry_is_screened(field):
    with pytest.raises(ValueError):
        bounded([{field: 'native_success'}])


def messages(config, *, history=None, validation=False, visible=True):
    feedback = {'execution_status': 'failed', 'public_error': 'motion stopped',
                'action_trace': [{'source': None, 'operation': 'move_delta', 'arguments': {'dx': .1}, 'status': 'failed'}],
                'eval_review': {'passed': False, 'reason': 'No placement observed.', 'reference_suggestion': 'Use smaller motion.'}}
    return loop.model_messages(config, 'from robot_sdk import sensors\n', feedback, {'memory': []},
                               {'dev_calls': 2}, visible, 12000, validation_memory=validation, version_history=history)


# SHA-256 of complete messages generated by model_messages at base f329cae.
BASE_DIGESTS = {(validation, visible): digest for validation in (False, True) for visible, digest in [
    (False, '12c1765bc258810b5b4424efa8e848c2463b37601ca4994968d8e6155f103be9'),
    (True, '761217eb4da6a455871bc7ec575f6aeba08fe4bd686491f3bbfb6f8a058c73bb')]}


@pytest.mark.parametrize('validation', [False, True])
@pytest.mark.parametrize('visible', [False, True])
@pytest.mark.parametrize('gate', [{}, {'budget_tier': 'dev'}, {'budget_tier': 'dev', 'dev_version_history': False},
                                 {'budget_tier': 'legacy', 'dev_version_history': True}])
def test_disabled_gate_complete_request_byte_identical(validation, visible, gate):
    result = messages({**CONFIG, **gate}, history={'entries': [], 'omitted': 0}, validation=validation, visible=visible)
    assert hashlib.sha256(encoded(result)).hexdigest() == BASE_DIGESTS[validation, visible]


def test_validation_ignores_enabled_history_and_keeps_allowlist(cell, tmp_path):
    config = {**CONFIG, 'budget_tier': 'dev', 'dev_version_history': True}
    result = messages(config, history=build(cell), validation=True)
    assert hashlib.sha256(encoded(result)).hexdigest() == BASE_DIGESTS[True, True]
    from benchmarks.attention_harness.memory_validation_generation import exposure_receipt
    # Its unchanged allowed-key check accepts the real generated request.
    origin = loop.immutable(tmp_path / 'origin.py', 'from robot_sdk import sensors\n')
    request = loop.immutable(tmp_path / 'validation-input.json', {'messages': result})
    exposure_receipt(tmp_path, 'cell', 1, request, origin)


def test_enabled_packet_and_total_bound_including_inspect_trace(cell):
    feedback = json.loads(FIXTURE.read_bytes())
    config = {**CONFIG, 'budget_tier': 'dev', 'dev_version_history': True}
    history = build(cell)
    original = loop.model_messages(config, '# source', feedback,
        {'action': 'inspect_trace', 'public_trace_summary': feedback['action_trace'], 'memory': []},
        {'dev_calls': 2}, version_history=history)
    packet = json.loads(original[-1]['content'])
    assert packet['version_history'] == history
    assert 'earlier versions in this run, oldest first' in original[0]['content']
    projected, audit = project_messages(original, 30000)
    assert input_bound(projected) <= 30000
    shown = json.loads(projected[-1]['content'])
    assert shown['version_history'] == {'entries': [], 'omitted': 3}
    assert shown['attention_control']['public_trace_summary'] == shown['public_feedback']['action_trace']
    assert audit['history_entries_dropped'] == 3
    loop.screen_model_packet(shown)


def test_history_alone_can_be_trimmed_and_fitting_request_is_untouched(cell):
    original = messages({**CONFIG, 'budget_tier': 'dev', 'dev_version_history': True}, history=build(cell))
    assert project_messages(original, input_bound(original)) == (original, None)
    projected, audit = project_messages(original, input_bound(original) - 1)
    packet = json.loads(projected[-1]['content'])
    assert [e['version'] for e in packet['version_history']['entries']] == [2, 3]
    assert packet['public_feedback'] == json.loads(original[-1]['content'])['public_feedback']
    assert audit['history_entries_dropped'] == packet['version_history']['omitted'] == 1


@pytest.fixture
def frozen_creation(monkeypatch):
    from benchmarks.attention_harness import dev_experiment as experiments
    from openarm365_dev_v2 import dev_config
    # Exercise real config validation with the frozen constructor. Creating a
    # native planner deployment/model lock is unrelated to this offline test.
    monkeypatch.setattr(experiments, 'new_execution_settings', lambda **kw: None)
    monkeypatch.setattr(experiments, 'make_dev_config', dev_config._make_dev_config)
    def lock(path, **kw):
        ref = loop.immutable(path, {'source_root': str(dev_config.ROOT), 'files': {}})
        return {'path': ref['uri'], 'sha256': ref['sha256']}
    monkeypatch.setattr(experiments, 'make_lock', lock)
    monkeypatch.setattr(experiments, 'verify_lock', lambda *a, **kw: None)
    monkeypatch.setattr(dev_config, 'verify_lock', lambda *a, **kw: None)


def test_new_creation_gate_locked_and_existing_manifest_not_backfilled(tmp_path, frozen_creation):
    from benchmarks.attention_harness import dev_experiment as experiments
    from benchmarks.attention_harness.dev_ledger import DevV2Store
    store = DevV2Store(tmp_path / 'ledger.sqlite')
    selection = {'request_key': 'history-creation', 'budget_tier': 'dev', 'mode': 'interactive',
                 'tasks': ['open_cabinet'], 'seeds': [101], 'policies': ['autonomous']}
    experiment = experiments.create(store, tmp_path / 'artifacts', selection)
    manifest = experiments.verify_manifest(experiment)
    assert manifest['dev_version_history'] is True
    assert all(c['dev_version_history'] is True for c in manifest['configs'].values())
    manifest.pop('dev_version_history')
    for config in manifest['configs'].values():config.pop('dev_version_history')
    # Simulate a pre-H1 immutable manifest, then exercise the existing-run path.
    original = loop.immutable(tmp_path / 'legacy-manifest.json', manifest)
    with store.tx() as db:
        row = store.get(db, 'experiments', experiment['experiment_id'])
        row['manifest'] = original
        store.put(db, 'experiments', experiment['experiment_id'], row)
    again = experiments.create(store, tmp_path / 'artifacts', selection)
    assert again['manifest'] == original
    assert 'dev_version_history' not in experiments.verify_manifest(again)


def test_loop_sends_history_for_each_generation(tmp_path, frozen_creation, monkeypatch):
    from openarm365_dev_v2.tests.test_iteration import setup, selection
    from openarm365_dev_v2.tests.fakes import FailedEvalClient, EvalRunner
    from benchmarks.attention_harness.dev_experiment import run
    store, experiment, budget = setup(tmp_path, selection(eval_agent={'enabled': True}))
    feedback_projection = loop.public_feedback
    flags = []
    def project(trace, **kwargs):
        flags.append(kwargs.get('base_yaw_visible'))
        return feedback_projection(trace, **kwargs)
    monkeypatch.setattr(loop, 'public_feedback', project)
    client = FailedEvalClient()
    result = run(store, experiment['experiment_id'], max_cells=1, budget_file=budget,
                 client=client, runner_factory=EvalRunner)
    versions = result['cells'][0]['versions']
    assert len(versions) == 3
    assert flags == [True, True, True]
    for version in versions:
        request = json.loads(loop.checked(version['model_input']).read_bytes())
        history = json.loads(request['messages'][-1]['content'])['version_history']
        assert [e['version'] for e in history['entries']] == list(range(1, version['version']))
        assert history['omitted'] == 0
        assert all(e['eval']['passed'] is False for e in history['entries'])


@pytest.mark.parametrize('value', [True, False, 1, 'true'])
def test_config_gate_is_strict_boolean_and_dev_only(value, frozen_creation):
    from openarm365_dev_v2 import dev_config
    config = dev_config._make_dev_config('open_cabinet', 101, {'path': 'unused', 'sha256': '0' * 64})
    config['dev_version_history'] = value
    if type(value) is bool:
        assert dev_config.validate_dev_config(config) == config
    else:
        with pytest.raises(ValueError, match='version history'):
            dev_config.validate_dev_config(config)
    legacy = dev_config._make_dev_config('open_cabinet', 101, config['dev_lock'], budget_tier='legacy')
    with pytest.raises(ValueError, match='version history'):
        dev_config.validate_dev_config({**legacy, 'dev_version_history': value})


def test_base_yaw_is_public_only_for_new_history_runs():
    from benchmarks.attention_harness.dev_loop import public_feedback
    trace = {'status': 'completed', 'sdk_events': [
        {'source': 'base', 'operation': 'move_delta', 'status': 'completed',
         'arguments': {'dx': .1, 'dy': .2, 'dyaw': .3}},
        {'source': 'base', 'operation': 'move_delta', 'status': 'failed',
         'arguments': {'dx': 9, 'dy': 9, 'dyaw': 9}}]}
    old = public_feedback(trace)
    assert old == public_feedback(trace, base_yaw_visible=False)
    assert all('dyaw' not in row['arguments'] for row in old['action_trace'])
    new = public_feedback(trace, base_yaw_visible=True)
    assert new['action_trace'][0].get('source') is None
    assert new['action_trace'][0]['arguments']['dyaw'] == .3
    entry = summarize(1, b'x', new)
    assert entry['operation_counts']['base_moves'] == {'count': 1, 'dx': .1, 'dy': .2, 'dyaw': .3}
    assert entry['failed_operation']['arguments']['dyaw'] == 9
