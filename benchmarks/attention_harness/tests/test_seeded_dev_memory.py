"""C0b: controlled grant/record checks; no model, simulator or service process."""
import copy
import json

import pytest

from benchmarks.attention_harness import dev_planner_memory as memory
from benchmarks.attention_harness import dev_v2_loop
from benchmarks.attention_harness.dev_memory_view import projection
from benchmarks.attention_harness.dev_loop import immutable
from benchmarks.attention_harness import dev_report
from benchmarks.attention_harness.dev_report import memory_seeded_excluded, retrieved_seeded_cells
from benchmarks.attention_harness.dev_ledger import RESOURCES
from benchmarks.attention_harness.dev_v2_memory_import import queue
from benchmarks.attention_harness.core.dev_store import DevConflict
from benchmarks.attention_harness.summarize_experiment import summarize
from openarm365_dev_v2.scene_contract import registered_contract_ref
from openarm365_dev_v2.tests.test_planner_features import setup, TestOnlyGateway, SOURCE


def test_exact_seeded_context_and_missing_fields(tmp_path, monkeypatch):
    from openarm365_dev_v2 import dev_config
    monkeypatch.setattr(dev_config, 'model_sources', lambda *args, **kwargs: {})
    _, _, _, cfg, _, _ = setup(tmp_path)
    cfg['scene_contract'] = registered_contract_ref()
    result = memory.context(cfg, 'isolated-c0b')
    assert result['seed'] == cfg['seed'] and type(result['seed']) is int
    assert result['camera_names'] == list(cfg['camera_names'])
    assert result['task_prompt'] == cfg['task_prompt']
    assert result['scene_contract_ref'] == registered_contract_ref()
    assert set(result) == set(memory.SCOPE) | {'seed', 'camera_names', 'task_prompt', 'scene_contract_ref'}
    for key, expected in [('seed', 'seed'), ('camera_names', 'camera_names'),
                          ('task_prompt', 'task_prompt'), ('scene_contract', 'scene_contract')]:
        bad = copy.deepcopy(cfg); bad.pop(key)
        with pytest.raises((ValueError, KeyError), match=expected):
            memory.context(bad, 'isolated-c0b')
    wrong=copy.deepcopy(cfg); wrong['seed']='102'
    with pytest.raises(ValueError,match='integer config seed'):
        memory.context(wrong,'isolated-c0b')


def test_seeded_grant_packet_event_view_and_result_skip(tmp_path, monkeypatch):
    from openarm365_dev_v2 import dev_config
    monkeypatch.setattr(dev_config, 'model_sources', lambda *args, **kwargs: {})
    store, _, cell, cfg, _, root = setup(tmp_path)
    gateway = TestOnlyGateway(cfg)
    original = gateway.authorize_dev_use
    def grant(**kwargs):
        value = original(**kwargs)
        value.update(seeded_for_tests=True, excluded_from_results=True,
                     source_trace_id='seeded-for-tests:controlled-memory',
                     evidence_refs=['seeded-for-tests:controlled-memory'])
        gateway.grants[value['grant_id']] = value
        return value
    gateway.authorize_dev_use = grant
    monkeypatch.setattr(memory, 'client_for', lambda _: gateway)
    settings = {'base_url': 'http://127.0.0.1:1', 'store_id': gateway.store_id(), 'namespace': 'controlled-test-only'}
    directory = root/'v1'; directory.mkdir()
    packet, _ = memory.load_memory(store, cell['key'], 1, SOURCE, cfg, settings, directory)
    assert packet['seeded_for_tests'] and packet['excluded_from_results']
    entry = packet['entries'][0]
    assert entry['seeded_for_tests'] and entry['excluded_from_results']
    assert json.loads((directory/'memory-input.json').read_bytes()) == packet
    provenance=dev_v2_loop.memory_provenance(packet)
    assert provenance['excluded_from_results'] and provenance['seeded_for_tests']
    assert provenance['entries'][0]['memory_id']==entry['memory_id']
    messages=dev_v2_loop.model_messages(cfg, SOURCE, {}, {'action':'continue','memory':packet}, {})
    assert entry['content'] in messages[-1]['content']
    assert 'seeded_for_tests' not in messages[-1]['content']
    assert 'excluded_from_results' not in messages[-1]['content']
    assert 'seeded-for-tests:' not in messages[-1]['content']
    view = projection(store, {'key': cell['key']})
    assert view['retrievals'][0]['entries'][0]['excluded_from_results'] is True
    assert view['retrievals'][0]['entries'][0]['source'] == '手工为测试种入'
    assert memory.journal_result(store, cell['key'], 1, packet, {}, {}, directory) is None
    assert not (directory/'memory-result.json').exists()
    with pytest.raises(DevConflict, match='excluded from result queue'):
        queue(store, cell['key'], 1, directory, 'result', {'grants':[entry['grant_id']]})
    with store.tx() as db:
        event = json.loads(db.execute("SELECT payload FROM v2_events WHERE kind='planner_memory_result_skipped'").fetchone()[0])
    assert event['reason'] == 'test_seeded_excluded_from_results'
    assert cell['key'] in retrieved_seeded_cells(store.path)
    assert memory_seeded_excluded({'versions':[{'memory_provenance':{'excluded_from_results':True}}]})
    assert not memory_seeded_excluded({'versions':[{}]})


def test_summary_excludes_seeded_cell_from_success_and_eval():
    base = dict(task_id='open_cabinet', seed=102, policy_id='p', repeat=0,
                state='completed', reason='done', created_at=0, generated_versions=1,
                attention_budget_level=1, request_units=0, logical_human_seconds=0,
                attention_seconds_unknown_requests=0, timeouts=0, execution_timeouts=0,
                fallbacks=0, fallback_detail={}, eval_compute={}, read_errors=[])
    good = dict(base, key='eligible', memory_seeded_excluded=False,
                versions=[dict(version=1, native_success=False, passed=False, mode='vision', settled_at=1)])
    seeded = dict(base, key='seeded', memory_seeded_excluded=True,
                  versions=[dict(version=1, native_success=True, passed=True, mode='vision', settled_at=1)])
    result = summarize({'experiment_id':'controlled', 'manifest':{}, 'cells':[good, seeded]})
    assert result['memory_seeded_excluded'] == 1
    assert result['by_policy']['p']['memory_seeded_excluded'] == 1
    assert result['by_policy']['p']['evaluated_cells'] == 1
    assert result['by_policy']['p']['native_successes'] == 0
    assert result['eval_quality']['confusion_matrix']['true_positive'] == 0


def test_dev_report_cell_excludes_seeded_version(monkeypatch):
    monkeypatch.setattr(dev_report, 'classify', lambda version, job: None)
    cell = dict(key='seeded', task_id='open_cabinet', seed=102, state='completed',
                experiment_eligible=True, mixed_policy=False, jobs=[],
                versions=[dict(version=1, valid=False, memory_provenance={'excluded_from_results':True})],
                used={k:0 for k in RESOURCES}, eval_usage={}, created_at=0)
    result = dev_report.analyze_cell(cell, dict(task_id='open_cabinet',seed=102,policy_id='p'), {})
    assert result['memory_seeded_excluded'] is True
    assert result['eligible'] is False
