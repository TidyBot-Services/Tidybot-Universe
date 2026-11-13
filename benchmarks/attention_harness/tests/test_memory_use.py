"""Offline citation audit: false positives, version cuts, export and visible errors."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.attention_harness import memory_use as audit
from benchmarks.attention_harness.dev_agent_view import projection
from benchmarks.attention_harness.dev_agent_export import Exporter
from benchmarks.attention_harness.dev_agent_loop import drive
from benchmarks.attention_harness.dev_agent_adapters import FakeProber
from benchmarks.attention_harness.dev_agent_workspace import atomic_write, encode
from benchmarks.attention_harness.tests.test_dev_agent_d2 import fake_session
from benchmarks.attention_harness.tests.test_dev_agent_d7 import fixture, Executor

FIXTURES = Path(__file__).parent/'fixtures'
SYNTHETIC = json.loads((FIXTURES/'memory-use-synthetic.json').read_text())
REAL = FIXTURES/'memory-use-real-3df60ef5'


def test_synthetic_explicit_id_quote_none_and_purity():
    data = copy.deepcopy(SYNTHETIC)
    result = audit.analyse(data['notes'], data['sources'])
    assert data == SYNTHETIC
    assert [n['referenced'] for n in result['notes']] == ['explicit', 'explicit', 'none']
    assert result['notes'][0]['evidence'][0]['event_index'] == 4
    assert result['notes'][1]['evidence'][0]['line'] == 7
    assert result['notes'][2]['evidence'] == []
    assert all(n['delivered'] for n in result['notes'])
    assert audit.validate_report(result) == result


@pytest.mark.parametrize('text,expected', [
    ('Use VERIFY THE GRASP now.', 'explicit'),
    ('Use mem-grasp.', 'explicit'),
    ('Use mem-graspExtra.', 'none'),
    ('Use mem-grasp-extra.', 'none'),
    ('after closing lift two centimetres and verify', 'none'),  # seven words
    ('AFTER closing, lift two centimetres and verify the', 'explicit'),  # eight
    ('After closing slowly lift two centimetres and verify the', 'none'),
])
def test_exact_identifiers_titles_and_eight_consecutive_words(text, expected):
    result = audit.analyse(SYNTHETIC['notes'][:1], [dict(file='message', event_index=0, text=text)])
    assert result['notes'][0]['referenced'] == expected


def test_long_evidence_excerpt_includes_match_and_is_bounded():
    text = 'intro '*100 + 'Use mem-grasp now. ' + 'tail '*100
    result = audit.analyse(SYNTHETIC['notes'][:1], [dict(file='message', event_index=0, text=text)])
    excerpt = result['notes'][0]['evidence'][0]['excerpt']
    assert 'mem-grasp' in excerpt and len(excerpt) <= 200


def test_behaviour_hook_only_for_structured_fields_never_explicit():
    notes = copy.deepcopy(SYNTHETIC['notes'])
    notes[0]['applies_when'] = 'holding an object'  # prose is not machine-readable
    notes[1]['parameters'] = dict(retreat_m=.02)
    calls = []
    def matcher(fields, sources):
        calls.append(fields)
        return [dict(file='trace.json', event_index=9, excerpt='Matched retreat_m')]
    result = audit.analyse(notes, [], behavioural_matcher=matcher)
    assert calls == [dict(parameters=dict(retreat_m=.02))]
    assert [n['referenced'] for n in result['notes']] == ['none']*3
    assert 'behavioural_hint' not in result['notes'][0]
    assert result['notes'][1]['behavioural_hint'][0]['event_index'] == 9
    assert 'behavioural_hint' not in audit.analyse(notes, [])['notes'][1]


def write(root, name, value):
    root.mkdir(parents=True, exist_ok=True)
    atomic_write(root, name, value if isinstance(value, bytes) else encode(value))


def event(kind, text, event_type='item.completed'):
    return dict(type=event_type, item=dict(type=kind, text=text))


def synthetic_run(root):
    private = root/'agent/private'
    note = SYNTHETIC['notes'][0]
    old = event('agent_message', 'mem-grasp before delivery')
    echo = dict(type='item.completed', item=dict(type='command_execution', aggregated_output='mem-grasp'))
    events = [old, echo, event('agent_message', 'Seven words only: after closing lift two centimetres and verify'),
              event('reasoning', 'Following mem-grasp in this version'),
              event('agent_message', 'mem-unused is in the NEXT version')]
    lines = [json.dumps(e).encode()+b'\n' for e in events]
    name = 'turns/t000001/events.jsonl'
    write(private, name, b''.join(lines))
    write(private, 'memory/v1/memory-delivered.json', dict(notes=SYNTHETIC['notes']))
    write(private, 'memory/v1/memory-use-start.json', {name: len(lines[0])})
    write(private, 'memory/v1/memory-use-end.json', {name: sum(map(len, lines[:4]))})
    write(private, 'turns/t000001/input.json', dict(prompt='mem-unused'))
    write(root, 'v1/policy.py', b'print("mem-unused")\nx = "# mem-unused"\n# Clear cabinet first\n')
    return private, name


def test_version_scoping_ignores_tool_echo_prompt_strings_and_later_messages(tmp_path):
    synthetic_run(tmp_path)
    result = audit.analyse_version(tmp_path, 1)
    assert 'errors' not in result
    assert [n['referenced'] for n in result['notes']] == ['explicit', 'explicit', 'none']
    assert result['notes'][0]['evidence'] == [dict(file='agent/private/turns/t000001/events.jsonl',
        event_index=3, excerpt='Following mem-grasp in this version')]
    assert result['notes'][1]['evidence'][0]['line'] == 3
    assert audit.record_version(tmp_path, 1) == result
    assert json.loads((tmp_path/'v1/memory_use.json').read_text()) == result


def test_unreadable_corrupt_missing_and_unsafe_evidence_are_visible(tmp_path):
    private, name = synthetic_run(tmp_path)
    (private/name).write_text('not JSON\n')
    (tmp_path/'v1/policy.py').unlink()
    result = audit.analyse_version(tmp_path, 1)
    assert len(result['errors']) == 2
    assert all(n['referenced'] == 'none' for n in result['notes'])
    write(private, 'memory/v1/memory-use-end.json', {'../../outside': 0})
    assert 'invalid event cursor path' in audit.analyse_version(tmp_path, 1)['errors'][0]['error']
    (private/name).unlink()
    (private/name).symlink_to(tmp_path/'not-followed')
    write(private, 'memory/v1/memory-use-end.json', {name: 10})
    assert audit.analyse_version(tmp_path, 1)['errors']


def test_multiline_comments_not_strings():
    code = 'text = "mem-grasp"\n# After closing lift two\n# centimetres and verify the object follows.\n'
    result = audit.analyse(SYNTHETIC['notes'][:1], audit._comments('policy.py', code))
    assert result['notes'][0]['referenced'] == 'explicit'
    assert result['notes'][0]['evidence'][0]['line'] == 2


def test_failed_start_cursor_cannot_count_text_from_before_delivery(tmp_path):
    private, _ = synthetic_run(tmp_path)
    write(private, 'memory/v1/memory-use-start.json', dict(error='cursor read failed'))
    result = audit.analyse_version(tmp_path, 1)
    assert result['notes'][0]['referenced'] == 'none'
    assert 'cursor read failed' in result['errors'][0]['error']


def test_legacy_receipts_isolate_versions_and_ignore_rejected_submit(tmp_path):
    private = tmp_path/'agent/private'
    def command(identity, receipt=None):
        item = dict(type='command_execution', id=identity)
        if receipt is not None:
            item['aggregated_output'] = json.dumps(receipt)
        return dict(type='item.completed' if receipt is not None else 'item.started', item=item)
    events = [event('agent_message', 'mem-grasp is from the previous version'),
        command('rejected'), command('rejected', dict(op='submit', status='rejected')),
        command('one'), command('one', dict(op='submit', version_id='v0001')),
        event('agent_message', 'Use mem-clearance for version two'),
        command('two'), command('two', dict(op='submit', version_id='v0002')),
        event('agent_message', 'mem-grasp is in a later version')]
    write(private, 'turns/t000001/events.jsonl', b''.join(json.dumps(e).encode()+b'\n' for e in events))
    start, end = audit._legacy_window(private, 2)
    result = audit.analyse(SYNTHETIC['notes'], audit._events(private, start, end))
    assert [n['referenced'] for n in result['notes']] == ['none', 'explicit', 'none']


def test_actual_delivery_projection_caps_and_screening():
    from benchmarks.attention_harness.dev_v2_loop import prompt_memory_view
    from benchmarks.attention_harness.dev_agent_eval import feedback
    entries = [dict(memory_id=f'n{i}', title=f'Title {i}', content='Guidance '+str(i),
                    trusted_here=True, layer='scene', level='L1') for i in range(10)]
    entries[0]['content'] = 'object_in_sink'  # withheld by the agent boundary
    view = feedback(prompt_memory_view(dict(memory=entries)))
    notes = audit.delivered_notes(entries, view)
    assert len(notes) == 3
    assert notes[0]['content'] == '[withheld]'
    assert notes[0]['level'] == 'L1'
    assert notes[-1]['memory_id'] == 'n2'


def test_copied_real_run_read_only_delivered_but_no_strict_reference():
    before = {str(p.relative_to(REAL)): hashlib.sha256(p.read_bytes()).hexdigest() for p in REAL.rglob('*') if p.is_file()}
    provenance = json.loads((REAL/'provenance.json').read_text())
    for entry in provenance['files']:
        assert before[entry['file']] == entry['sha256']
    result = audit.analyse_version(REAL, 1)
    assert result == json.loads((REAL/'v1/memory_use.json').read_text())
    assert result == dict(notes=[dict(memory_id='p-68eff35e4efe868eb6f7d31a', title='Unvalidated: note',
        level='scene', delivered=True, referenced='none', evidence=[])])
    assert before == {str(p.relative_to(REAL)): hashlib.sha256(p.read_bytes()).hexdigest() for p in REAL.rglob('*') if p.is_file()}


def run_export(f):
    exporter = Exporter(f.store, 'cell', f.ws, f.config, f.ledger)
    session = fake_session(f.ws, ["emit({'type':'item.completed','item':{'type':'agent_message','text':'Following n0 before this submission.'}});tool('submit')", 'pass'])
    return drive(f.ws, session, f.ledger, executor=Executor(), prober=FakeProber(),
                 memory=f.memory, exporter=exporter, completion_reader=lambda *a: False)


def test_export_produces_audit_and_projection_surfaces_analysis_errors(fixture):
    f = fixture
    run_export(f)
    path = f.root/'v1/memory_use.json'
    result = json.loads(path.read_text())
    assert len(result['notes']) == 2 and 'errors' not in result
    by_id = {n['memory_id']:n for n in result['notes']}
    assert (by_id['n0']['referenced'], by_id['n1']['referenced']) == ('explicit', 'none')
    assert by_id['n0']['evidence'][0]['excerpt'] == 'Following n0 before this submission.'
    path.write_text('{broken')
    view = projection(f.store, f.store.cell('cell'), now=1)
    assert any(e['source']=='Memory analysis' and e['error'].get('file')=='v1/memory_use.json' for e in view['agent']['errors'])
    path.write_text(json.dumps(dict(notes=[None])))
    view = projection(f.store, f.store.cell('cell'), now=2)
    assert view['agent']['memory_use'][0]['errors'][0]['error'] == 'invalid memory_use.json note'
    assert any(e['source'] == 'Memory analysis' for e in view['agent']['errors'])


def test_audit_write_failure_is_persisted_and_visible(fixture, monkeypatch):
    f = fixture
    def fail(*args):
        raise OSError('synthetic audit disk failure')
    monkeypatch.setattr(audit, 'record_version', fail)
    run_export(f)
    assert f.store.cell('cell')['memory_use_errors'][0]['error'] == 'OSError: synthetic audit disk failure'
    view = projection(f.store, f.store.cell('cell'), now=1)
    assert any('synthetic audit disk failure' in str(e) for e in view['agent']['errors'])


def test_cleared_memory_cannot_reuse_stale_delivery(fixture):
    f = fixture; f.memory.start(); f.memory.clear(1, '')
    saved = json.loads((f.ws.private/'memory/v1/memory-delivered.json').read_text())
    assert saved == dict(notes=[])
