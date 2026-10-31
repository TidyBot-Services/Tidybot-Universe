"""Offline run-note lifecycle: local evidence, immutable delivery and no Service writes."""
from benchmarks.attention_harness.workspace_paths import get
import hashlib
import io
import json
from pathlib import Path

import pytest

from benchmarks.attention_harness import daily_dev_v2 as daily
from benchmarks.attention_harness import dev_configuration as configuration
from benchmarks.attention_harness import dev_experiment as experiments
from benchmarks.attention_harness import dev_memory_view, memory_monitor, memory_run_notes as notes, ui_server
from benchmarks.attention_harness.tests.test_memory_provisional import (
    ADVICE, FURTHER, REJECTED, SAME, SETTINGS, SOURCE, TIMEOUT, events, importer,
    loop, make, memory, missing_model_copy_fixture, populated, process_identity,
    provisional, sha, write_version,
)


def learned(tmp_path, **extra):
    store, cell, config, manifest, root = make(tmp_path, 'autonomous', run_notes=True, **extra)
    write_version(root, 1, public=REJECTED)
    directory = write_version(root, 2, public=TIMEOUT, help_value=ADVICE)
    ref = notes.create(store, cell['key'], 2, directory)
    assert ref
    return store, cell, config, manifest, root, json.loads(Path(ref['uri']).read_bytes())


def test_effective_creation_immutable_idempotent_and_changed_answer(tmp_path):
    store, cell, _, _, root, note = learned(tmp_path)
    assert note['schema_version'] == notes.SCHEMA
    assert note['note_id'] == 'r-' + hashlib.sha256(f"{cell['experiment_id']}:{cell['key']}:2".encode()).hexdigest()[:24]
    assert note['effective_reason'] == 'signature_changed'
    assert (note['origin_progress'], note['help_progress']) == (2, 1)
    assert note['run_local'] and note['unvalidated'] and note['excluded_from_results']
    assert note['content'] == ADVICE['content'] and note['title'] == ADVICE['content']
    assert all(sha(ref['uri']) == ref['sha256'] for ref in note['refs'].values())
    ref = notes.create(store, cell['key'], 2, root/'v2')
    assert sha(ref['uri']) == ref['sha256']
    assert len(events(store, notes.PREFIX+'created')) == 1
    (root/'v2/help.json').chmod(0o644)
    (root/'v2/help.json').write_text(json.dumps(dict(ADVICE, content='Changed hint.')))
    assert notes.create(store, cell['key'], 2, root/'v2') is None
    assert events(store, notes.PREFIX+'skipped')[-1]['reason'] == 'run_note_already_differs'
    assert json.loads((root/'v2/memory-run-note.json').read_bytes()) == note
    (root/'v2/help.json').write_text(json.dumps(ADVICE))
    target=root/'v2/memory-run-note.json'; target.chmod(0o644)
    for changed in ('{}', '{invalid', json.dumps(dict(note,policy_id='reactive_help'))):
        target.write_text(changed)
        assert notes.create(store,cell['key'],2,root/'v2') is None
        assert events(store,notes.PREFIX+'skipped')[-1]['reason']=='run_note_already_differs'


@pytest.mark.parametrize('answer,origin,helped,reason', [
    (None, REJECTED, TIMEOUT, None),
    ({'provider':'human','content':'Hint.'}, REJECTED, TIMEOUT, 'provider_not_source'),
    (dict(ADVICE, rejected=True), REJECTED, TIMEOUT, 'help_rejected_or_empty'),
    (dict(ADVICE, content=' '), REJECTED, TIMEOUT, 'help_rejected_or_empty'),
    (ADVICE, REJECTED, SAME, 'not_effective'),
    (ADVICE, {}, TIMEOUT, 'no_public_error'),
    (ADVICE, REJECTED, None, 'missing_evidence:public-feedback.json'),
])
def test_nonqualifying_answers(tmp_path, answer, origin, helped, reason):
    store, cell, _, _, root = make(tmp_path, 'autonomous', run_notes=True)
    write_version(root, 1, public=origin)
    directory = write_version(root, 2, public=helped, help_value=answer)
    assert notes.create(store, cell['key'], 2, directory) is None
    assert not (directory/'memory-run-note.json').exists()
    assert [e['reason'] for e in events(store, notes.PREFIX+'skipped')] == ([] if reason is None else [reason])
    assert events(store, notes.PREFIX+'created') == []


def test_progress_and_invalid_signature_and_public_screen(tmp_path, monkeypatch):
    store, cell, _, _, root = make(tmp_path, 'autonomous', run_notes=True)
    write_version(root, 1, public=REJECTED)
    directory = write_version(root, 2, public=FURTHER, help_value=dict(ADVICE, provider='human_proxy'))
    ref = notes.create(store, cell['key'], 2, directory)
    assert json.loads(Path(ref['uri']).read_bytes())['effective_reason'] == 'progressed'
    import attention_memory_service.dev_v2 as service
    monkeypatch.setattr(service, 'validate_signature', lambda *a: (_ for _ in ()).throw(ValueError()))
    assert notes.create(store, cell['key'], 2, directory) is None
    assert events(store, notes.PREFIX+'skipped')[-1]['reason'] == 'signature_invalid'
    assert notes.title('  First   sentence. Second sentence.\nThird') == 'First sentence.'
    assert notes.title('x'*100) == 'Hint'  # Key-like text fails public title validation.


def test_no_scene_contract_stages_local_note_but_keeps_proposal_refusal(tmp_path, monkeypatch):
    store, cell, _, _, root = make(tmp_path, 'reactive_help', seeds=[104], run_notes=True)
    write_version(root, 1, public=REJECTED)
    directory = write_version(root, 2, public=TIMEOUT, help_value=ADVICE)
    monkeypatch.setattr(memory, 'context', lambda *a: pytest.fail('run note must not request Memory context'))
    monkeypatch.setattr(memory, 'client_for', lambda *a: pytest.fail('run note must not contact Service'))
    assert provisional.stage_safely(store, cell['key'], 2, directory) is None
    assert (directory/'memory-run-note.json').exists()
    assert events(store, 'planner_memory_provisional_skipped')[-1]['reason'] == 'no_scene_contract'
    assert events(store, 'planner_memory_offline_queued') == []


def test_delivery_start_cap_resume_off_and_digest_drift(tmp_path):
    store, cell, _, _, root, first = learned(tmp_path)
    key = cell['key']
    for version in (1, 2):
        assert notes.deliver(store, key, version, root/f'v{version}') == []
    for version in range(3, 6):
        write_version(root, version, public=REJECTED if version%2 else TIMEOUT,
                      help_value=dict(ADVICE, content=f'Hint {version}.'))
        assert notes.create(store, key, version, root/f'v{version}')
    packet = notes.deliver(store, key, 6, root/'v6')
    assert [n['help_version'] for n in packet] == [5, 4, 3]
    saved = (root/'v6/memory-run-notes-input.json').read_bytes()
    assert json.loads(saved)['omitted'] == 1
    assert json.loads(saved)['schema'] == notes.INPUT_SCHEMA
    notes.turn_off(store, key, packet[0]['note_id'])
    notes.turn_off(store, key, packet[0]['note_id'])
    assert len(events(store, notes.PREFIX+'off')) == 1
    assert notes.deliver(store, key, 6, root/'v6') == packet
    assert (root/'v6/memory-run-notes-input.json').read_bytes() == saved
    assert len(events(store, 'planner_memory_run_notes_delivered')) == 1
    assert len(events(store, 'planner_memory_unvalidated_used')) == 1
    assert [e['help_version'] for e in notes.deliver(store, key, 7, root/'v7')] == [4, 3, 2]
    (root/'v4/memory-run-note.json').chmod(0o644)
    (root/'v4/memory-run-note.json').write_text('{}')
    assert [e['help_version'] for e in notes.deliver(store, key, 8, root/'v8')] == [3, 2]


@pytest.mark.parametrize('policy', ['autonomous','demo_first','reactive_help','retry_k_then_ask',
                                   'budget_matched_random_escalation','trace_aware_hint_only',
                                   'full_trace_aware_attention_planner'])
def test_order_labels_and_decision_isolation_every_policy(tmp_path, monkeypatch, policy):
    store, cell, config, manifest, root, note = learned(tmp_path, memory_authority=SETTINGS)
    store.update_cell(cell['key'], owner=None)
    experiments.switch(store, cell['key'], policy)
    gateway, _ = populated(tmp_path, config)
    class GuardGateway:
        def __getattr__(self, name):
            def call(*args, **kwargs):
                sent=json.dumps([args,kwargs],default=str)
                assert note['note_id'] not in sent and note['content'] not in sent
                return getattr(gateway,name)(*args,**kwargs)
            return call
    monkeypatch.setattr(memory, 'client_for', lambda _: GuardGateway())
    original_decide = loop.decide
    def decide(*args, **kwargs):
        assert note['note_id'] not in json.dumps(kwargs['memory'])
        assert note['content'] not in json.dumps(kwargs['memory'])
        return original_decide(*args, **kwargs)
    monkeypatch.setattr(loop, 'decide', decide)
    directory = root/'v3'; directory.mkdir()
    packet, _, _ = loop.attention(store, cell['key'], 3, {}, SOURCE, 0, [], manifest, process_identity(), directory, None)
    assert [e.get('note_id', e.get('memory_id')) for e in packet['memory']] == ['seed-here', note['note_id'], 'p-new', 'p-old']
    prompt = loop.prompt_memory_view(packet)['memory']
    assert [e['title'] for e in prompt] == ['Trusted here', 'This run (v2) · Unvalidated: '+note['title'],
                                         'Unvalidated: New unvalidated', 'Unvalidated: Old unvalidated']
    saved = json.loads((directory/'memory-input.json').read_bytes())
    assert note['note_id'] not in json.dumps(saved)
    memory.DevMemory(gateway, SETTINGS, config, layered=True).recheck(saved)
    assert dev_memory_view.projection(store, store.cell(cell['key']))['unvalidated_used']
    legacy = [{'memory_id':'z','content':'Last'},{'memory_id':'a','content':'First'}]
    assert notes.merge(legacy, [packet['memory'][1]]) == legacy+[packet['memory'][1]]
    assert [e['content'] for e in loop.compact_memory(notes.merge(legacy, [packet['memory'][1]]))[0]] == ['Last','First',note['content']]


def test_unbound_prompt_survives_interactive_switch(tmp_path):
    store, cell, _, manifest, root, note = learned(tmp_path)
    store.update_cell(cell['key'], owner=None)
    switched = experiments.switch(store, cell['key'], 'reactive_help')
    assert switched['run_notes'] is True and switched['key'] == cell['key']
    directory = root/'v3'; directory.mkdir()
    packet, _, _ = loop.attention(store, cell['key'], 3, {}, SOURCE, 0, [], manifest, process_identity(), directory, None)
    assert [e['note_id'] for e in packet['memory']] == [note['note_id']]
    assert not (directory/'memory-input.json').exists()
    assert notes.create(store, cell['key'], 2, root/'v2')
    assert json.loads((root/'v2/memory-run-note.json').read_bytes())['policy_id'] == 'autonomous'


def test_outcomes_and_condition_invalid(tmp_path):
    store, cell, _, _, root, note = learned(tmp_path)
    for version, feedback in [(3, REJECTED),(4,TIMEOUT),(5,{'execution_status':'condition_invalid'})]:
        directory = root/f'v{version}'
        notes.deliver(store, cell['key'], version, directory)
        notes.outcomes(store, cell['key'], version, directory, feedback)
        notes.outcomes(store, cell['key'], version, directory, feedback)
    outcomes = events(store, notes.PREFIX+'outcome')
    assert [(e['signature_present'],e['failure_gone'],e['progress'],e['help_progress']) for e in outcomes] == [(True,False,2,1),(False,True,1,1)]
    assert all(e['note_id'] == note['note_id'] for e in outcomes)
    assert events(store, notes.PREFIX+'skipped')[-1]['reason'] == 'condition_invalid'


def test_results_excluded_provisional_outcomes_preserved_and_importer_isolated(tmp_path, monkeypatch):
    store, cell, config, _, root, note = learned(tmp_path, memory_authority=SETTINGS)
    gateway, _ = populated(tmp_path, config)
    monkeypatch.setattr(memory, 'client_for', lambda _: gateway)
    directory = root/'v3'; directory.mkdir()
    notes.deliver(store, cell['key'], 3, directory)
    packet, _ = memory.load_memory(store, cell['key'], 3, SOURCE, config, SETTINGS, directory, True)
    feedback = notes.immutable(directory/'public-feedback.json', TIMEOUT)
    source = notes.immutable(directory/'policy.py', SOURCE)
    memory.journal_result(store, cell['key'], 3, packet, source, feedback, directory)
    queues = events(store, 'planner_memory_offline_queued')
    assert [e['kind'] for e in queues] == ['provisional_outcome']
    assert events(store, 'planner_memory_result_skipped')[-1]['reason'] == 'run_note_excluded_from_results'
    # Only provisional use evidence may reach the importer. Local ids/content never do.
    class Guard:
        def record_provisional_outcome(self, **kw):
            assert note['note_id'] not in json.dumps(kw) and note['content'] not in json.dumps(kw)
            return gateway.record_provisional_outcome(**kw)
        def __getattr__(self, name):
            pytest.fail('unexpected Memory import: '+name)
    assert importer.run([store.path], client=Guard(), namespace=SETTINGS['namespace'])['outcomes'] == 2
    # Even a normal, countable trusted packet must not queue a result after local guidance.
    trusted = dict(packet, entries=[dict(packet['entries'][0], excluded_from_results=False, seeded_for_tests=False)], excluded_from_results=False)
    # Trusted outcomes now have an observational queue too. Rebinding this
    # already sealed version to a different grant set must remain impossible.
    from benchmarks.attention_harness.core.dev_store import DevConflict
    with pytest.raises(DevConflict,match='immutable recovery artifact conflict'):
        memory.journal_result(store, cell['key'], 3, trusted, source, feedback, directory)
    assert not (directory/'memory-result.json').exists()


def test_absent_flag_has_no_local_files_events_or_prompt_change(tmp_path):
    store, cell, _, manifest, root = make(tmp_path, 'autonomous')
    write_version(root, 1, public=REJECTED)
    directory = write_version(root, 2, public=TIMEOUT, help_value=ADVICE)
    assert notes.create(store, cell['key'], 2, directory) is None
    next_dir = root/'v3'; next_dir.mkdir()
    packet, _, _ = loop.attention(store, cell['key'], 3, {}, SOURCE, 0, [], manifest, process_identity(), next_dir, None)
    assert 'memory' not in packet
    assert list(root.glob('v*/memory-run-note*.json')) == []
    assert not any(k.startswith(notes.PREFIX) for k, _ in notes.events(store, cell['key']))


def test_configuration_daily_binding_and_freeze(tmp_path):
    assert configuration.MAIN_MATRIX_RUN_NOTES is False
    with pytest.raises(ValueError, match='freeze'):
        configuration.normalize_configuration({'budget_tier':'legacy','run_notes':True})
    for value in (1, None, 'true'):
        with pytest.raises(ValueError, match='bool'):
            configuration.normalize_configuration({'run_notes':value})
    old = tmp_path/'selection.json'
    for mode in ('experiment','interactive','diagnostic'):
        for policy in ('autonomous','reactive_help','full_trace_aware_attention_planner'):
            selection = dict(mode=mode,policies=[policy],budget_tier='dev')
            assert daily.bind_daily_run_notes(selection, old)['run_notes'] is True
            assert daily.bind_daily_run_notes(dict(selection,run_notes=False), old)['run_notes'] is False
    for original in ({},{'run_notes':False},{'run_notes':True}):
        old.write_text(json.dumps(original))
        assert daily.bind_daily_run_notes({'run_notes':True}, old) == original


def bridge_for(store, cell):
    bridge = object.__new__(daily.DailyDevV2)
    bridge.store = lambda eid: store
    bridge.root = store.path.parent
    return bridge


def test_monitor_and_kill_switch_endpoint_auth(tmp_path, monkeypatch):
    store, cell, _, _, root, note = learned(tmp_path)
    bridge = bridge_for(store, cell)
    bridge.get = lambda path: (experiments.snapshot(store, cell['experiment_id']), 'application/json')
    notes.deliver(store, cell['key'], 3, root/'v3')
    notes.outcomes(store, cell['key'], 3, root/'v3', TIMEOUT)
    payload = memory_monitor.project(bridge, None, cell['experiment_id'])
    assert [v['note_ids'] for v in payload['run']['versions']] == [[],[],[note['note_id']]]
    assert payload['run_notes'] == [dict(id=note['note_id'],cell=cell['key'],layer='run',
        path=f"Learned in {cell['experiment_id'][:15]}… v2 → this run only",title=note['title'],help_version=2,from_version=3,uses=1,failure_gone=1,off=False)]
    assert any(e['text'] == 'v2 learned: '+note['title'] for e in payload['events'])
    path = '/api/dev-v2/experiments/'+cell['experiment_id']+'/run-note-off'
    body = dict(cell=cell['key'],note_id=note['note_id'])
    with pytest.raises(ValueError, match='does not exist'):
        bridge.post(path, dict(body,note_id='r-missing'))
    assert bridge.post(path, body)['off'] is True
    assert bridge.post(path, body)['off'] is True
    assert notes.deliver(store, cell['key'], 4, root/'v4') == []
    assert notes.deliver(store, cell['key'], 3, root/'v3')[0]['note_id'] == note['note_id']
    payload = memory_monitor.project(bridge, None, cell['experiment_id'])
    assert payload['run_notes'][0]['off'] is True
    assert any(e['text'] == 'turned off for this run: '+note['title'] for e in payload['events'])
    # Exercise the real HTTP handler without binding any port.
    monkeypatch.setattr(ui_server, 'ThreadingHTTPServer', lambda address, handler: handler)
    monkeypatch.setattr(ui_server.secrets, 'token_urlsafe', lambda _: 'fixture-control-token')
    handler_type = ui_server.create_server(dev_v2_bridge=bridge)
    handler = object.__new__(handler_type)
    handler.path = path; handler.headers = {'X-Attention-Token':'wrong'}
    response = {}; handler._json = lambda value, status=200: response.update(value=value,status=status)
    handler.do_POST()
    assert response['status'] == 403
    raw=json.dumps(body).encode()
    handler.headers={'X-Attention-Token':'fixture-control-token','Content-Length':str(len(raw)),'Content-Type':'application/json'}
    handler.rfile=io.BytesIO(raw)
    handler.do_POST()
    assert response['status'] == 200 and response['value']['off'] is True


def test_generation_persists_local_ids_and_execution_records_observations(tmp_path, monkeypatch):
    from openarm365_dev_v2.tests.fakes import Client, Runner
    store, cell, config, manifest, root, note = learned(tmp_path, reproducible_reset=True)
    for version in (1, 2):
        record = json.loads((root/f'v{version}/version.json').read_bytes())
        store.version(cell['key'], dict(record, valid=False, validation_error='fixture failure', formal_eligible=False))
    exp = experiments.snapshot(store, cell['experiment_id'])
    client = Client(invalid_first=True)
    loop.run_cell(store, exp, manifest, cell['key'], 3, process_identity(), client=client)
    record = json.loads((root/'v3/version.json').read_bytes())
    assert record['run_notes'] == dict(note_ids=[note['note_id']], excluded_from_results=True)
    assert store.cell(cell['key'])['versions'][-1]['run_notes'] == record['run_notes']
    assert note['content'] in json.dumps(client.inputs)
    assert dev_memory_view.projection(store, store.cell(cell['key']))['unvalidated_used']
    # Invoke execution with the in-process fixture runner, without a simulator.
    store.update_cell(cell['key'],state='running',owner=process_identity())
    loop.execute(store,cell['key'],3,record,config,process_identity(),root/'v3',Runner)
    assert len(events(store, notes.PREFIX+'outcome')) == 1
    assert events(store,'planner_memory_result_skipped')[-1]['reason'] == 'run_note_excluded_from_results'
    assert events(store,'planner_memory_offline_queued') == []


def test_offline_replay_recorded_run(tmp_path, capsys):
    """M-6 replay: read only the saved public feedback and hints; no paid work."""
    source = get('ui.dev_v2_root') / 'devexp-4a6bb2d8387428ca4f32b849/artifacts/devexp-4a6bb2d8387428ca4f32b849/cells/fdd6e0fd2d1326ccce276502403e4520d2aaa8a721b474ddce8dcbd1770c37c2'
    if not source.is_dir():
        pytest.skip('recorded run unavailable on this machine')
    store, cell, _, _, root = make(tmp_path,'reactive_help',run_notes=True)
    report = []
    for version in range(1,6):
        original = source/f'v{version}'
        public = json.loads((original/'public-feedback.json').read_bytes())
        hint = json.loads((original/'help.json').read_bytes()) if (original/'help.json').exists() else None
        directory = write_version(root,version,public=public,help_value=hint)
        entries = notes.deliver(store,cell['key'],version,directory)
        notes.outcomes(store,cell['key'],version,directory,public)
        ref = notes.create(store,cell['key'],version,directory)
        report.append(dict(version=version,read=[e['help_version'] for e in entries],learned=bool(ref)))
    assert report[0] == dict(version=1,read=[],learned=False)
    assert report[1]['learned'] and report[2]['read'] == [2]
    assert all(all(h < row['version'] for h in row['read']) for row in report)
    assert events(store,'planner_memory_offline_queued') == []
    from attention_memory_service.note_title import validate_title
    headings = [json.loads((root/f'v{v}'/'memory-run-note.json').read_bytes())['title'] for v in (2,4,5)]
    assert len({h.casefold() for h in headings}) == 3
    assert all(validate_title(h) == h and not h.startswith('Avoid PlanningRejected') for h in headings)
    # This is a replay of recorded evidence, not a counterfactual execution.
    with capsys.disabled():
        print('\nOffline replay devexp-4a6bb2d8387428ca4f32b849:',json.dumps(report))
