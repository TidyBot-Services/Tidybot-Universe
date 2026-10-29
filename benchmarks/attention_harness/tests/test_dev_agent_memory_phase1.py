"""D7.1 offline evidence, interval selection and in-process importer acceptance."""
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness import dev_agent_memory as agent
from benchmarks.attention_harness import dev_planner_memory as memory
from benchmarks.attention_harness import memory_auto_import as importer
from benchmarks.attention_harness import memory_provisional as provisional
from benchmarks.attention_harness.dev_agent_export import Exporter
from benchmarks.attention_harness.dev_v2_loop import immutable
from benchmarks.attention_harness.tests.test_dev_agent_d7 import fixture, Executor, events
from benchmarks.attention_harness.tests.test_memory_provisional import REJECTED, TIMEOUT, FURTHER, ADVICE
from attention_memory_service.core.store import StateConflictError
from attention_memory_service.dev_v2 import SCOPE
from attention_memory_service.memory_service import MemoryService
from attention_memory_service.memory_service_api import create_app


def context():
    return dict({k: 'fake-' + k for k in SCOPE}, seed=102, camera_names=['left'],
                sdk_sha256='a'*64, sdk_interface_sha256='b'*64, perception_mode='sim_gt',
                task_prompt='Move cup', scene_contract_ref=dict(uri='fixture://scene', sha256='c'*64))


def prepare(f, monkeypatch):
    monkeypatch.setattr(memory, 'context', lambda *a: context())
    f.store.update_cell('cell', policy_id='reactive_help', run_notes=True)
    immutable(f.root / 'config.json', f.config)


def formal(f, number, public):
    directory = f.root / f'v{number}'
    source = immutable(f.ws.private / f'sources/r{number}.py', f'# formal {number}\n')
    immutable(directory / 'policy.py', Path(source['uri']).read_text())
    immutable(directory / 'public-feedback.json', public)
    response = dict(op='submit', request_id=f'r{number}', version_id=f'v{number:04d}', execution_id=f'e{number}',
                    result=dict(outcome=dict(status='failed')))
    jid = 'request:r' + str(number)
    resources = dict(formal_versions=1, moving_executions=1)
    f.ledger.reserve(jid, 'request', resources, execution=True); f.ledger.claim(jid)
    f.ledger.record_execution(jid, dict(sdk_calls=1, control_steps=2, wall_seconds=.1))
    f.ledger.settle(jid, response)
    job = next(j for j in f.ledger.jobs() if j['id'] == jid)
    record = dict(version=number, valid=True, source=source, formal_eligible=False,
                  agent=dict(valid=True, execution_id=f'e{number}'))
    immutable(directory / 'version.json', record)
    f.store.version('cell', record)
    mirrored = dict(cell='cell', version=number, kind='execute', receipt=dict(bundle=dict(uri='unused'),
        public_feedback=provisional._ref(directory / 'public-feedback.json'), agent_request_id=jid,
        accounting='agent_ledger_mirror'), **{k: job[k] for k in ('reserved_at', 'claimed_at', 'settled_at')})
    with f.store.tx() as db:
        db.execute('INSERT INTO v2_jobs VALUES (?,?,?,?,?,?)', (jid, 'cell', number, 'execute', 'settled', json.dumps(mirrored)))
    f.store.update_cell('cell', agent_export='v1')
    incoming = f.ws.canonical / f'versions/v{number-1:04d}/help.json'
    immutable(directory / 'help.json', json.loads(incoming.read_bytes()) if incoming.is_file() else dict(status='not_requested'))
    return directory


def help_interval(f, number, *, proactive=False, **changes):
    vid = f'v{number:04d}'
    answer = dict(ADVICE, status='delivered', delivered=True, version_id=vid, cache_hit=False,
                  system_injected=proactive, decision=dict(action='request'))
    answer.update(changes)
    immutable(f.ws.private / f'help/{vid}/request-view.json', dict(question='How to lift?', frames=[]))
    immutable(f.ws.private / f'help/{vid}/advisor.json', dict(status='completed', kind='hint',
        feedback=answer['content'], cache_hit=False, usage=dict(input_tokens=1, output_tokens=1)))
    jid = 'help:' + vid
    f.ledger.reserve(jid, 'help', dict(help=1)); f.ledger.claim(jid); f.ledger.settle(jid, answer)
    f.ws.publish(f'versions/{vid}/help.json', answer)
    f.ws.publish(f'rpc/responses/h{number}.json', dict(op='ask_help', status='completed', result=answer))
    return answer


def stage(f, n):
    return agent.stage_provisional(f.store, 'cell', n, f.root / f'v{n}', f.ws, f.ledger)


@pytest.mark.parametrize('proactive', [False, True])
def test_helped_interval_selection(fixture, monkeypatch, proactive):
    f = fixture; prepare(f, monkeypatch)
    formal(f, 1, REJECTED)
    f.ws.publish('versions/v0001/help.json', dict(status='policy_denied', delivered=False))
    formal(f, 2, REJECTED)
    assert stage(f, 1) is None and stage(f, 2) is None
    help_interval(f, 2, proactive=proactive)
    formal(f, 3, TIMEOUT)
    ref = stage(f, 3)
    assert ref is not None, events(f, 'planner_memory_provisional_skipped')
    proposal = json.loads(Path(ref['uri']).read_bytes())
    assert (proposal['origin_version'], proposal['help_version']) == (2, 3)
    assert proposal['provider'] == 'advisor' and proposal['origin']['channel'] == 'dev_agent'
    assert set(proposal['refs']) == set(importer.REF_KEYS) - {'help_model_input'} | {'agent_help_evidence'}
    assert stage(f, 3) == ref
    assert len(events(f, 'planner_memory_offline_queued')) == 1
    assert proposal['from_run_note']['version'] == 3


@pytest.mark.parametrize('changes', [dict(provider='demo'), dict(provider='cache'), dict(provider='human'),
    dict(provider='human_proxy'), dict(cache_hit=True), dict(delivered=False), dict(rejected=True),
    dict(status='policy_denied')])
def test_unqualified_help_never_stages(fixture, monkeypatch, changes):
    f = fixture; prepare(f, monkeypatch); formal(f, 1, REJECTED)
    help_interval(f, 1, **changes); formal(f, 2, TIMEOUT)
    assert stage(f, 2) is None and not events(f, 'planner_memory_offline_queued')
    assert not events(f, 'planner_memory_run_note_created')


@pytest.fixture
def proposal(fixture, monkeypatch):
    f = fixture; prepare(f, monkeypatch); formal(f, 1, REJECTED)
    help_interval(f, 1); formal(f, 2, TIMEOUT)
    ref = stage(f, 2)
    assert ref, events(f, 'planner_memory_provisional_skipped')
    return f, json.loads(Path(ref['uri']).read_bytes())


@pytest.mark.parametrize('name', ['request_view', 'advisor', 'help_job', 'delivery', 'help'])
def test_digest_drift_rejected_by_importer_and_service(proposal, tmp_path, name):
    f, value = proposal
    payload = importer.provisional_payload(value, 'test')
    texts = payload['agent_evidence']['refs'] if name == 'help' else payload['agent_evidence']['help']
    texts[name] += '\n'
    with pytest.raises(StateConflictError, match='digest drift'):
        MemoryService(tmp_path / 'svc.sqlite').create_provisional_note(**payload)
    evidence = json.loads(Path(value['refs']['agent_help_evidence']['uri']).read_bytes())
    ref = value['refs']['help'] if name == 'help' else evidence['refs'][name]
    path = Path(ref['uri']); path.chmod(0o644); path.write_bytes(path.read_bytes() + b'\n')
    with pytest.raises(importer.Refused, match='digest drift'):
        importer.provisional_payload(value, 'test')


@pytest.mark.parametrize('change', ['flag', 'unsettled', 'unexported', 'origin', 'provenance', 'validation',
                                    'positive', 'manifest_validation', 'manifest_positive', 'config_control', 'version'])
def test_auto_import_admission(proposal, change):
    f, value = proposal
    assert len(list(importer.pending(f.store.path))) == 1
    if change == 'flag': f.store.update_cell('cell', agent_export=None)
    elif change == 'unsettled':
        with f.store.tx() as db: db.execute("UPDATE v2_jobs SET state='claimed' WHERE version=2")
    elif change == 'unexported':
        with f.store.tx() as db: db.execute('DELETE FROM v2_jobs WHERE version=2')
    elif change in ('origin', 'provenance', 'version'):
        if change == 'version': value['help_version'] = 3
        else: value[change]['channel'] = 'dev_v2'
        path = f.root / 'bad-proposal.json'; ref = immutable(path, value)
        with f.store.tx() as db:
            db.execute("DELETE FROM v2_events WHERE kind='planner_memory_offline_queued'")
            f.store.event(db, 'planner_memory_offline_queued', dict(cell='cell', version=2, kind='provisional', queue=ref))
    elif change in ('validation', 'positive'):
        f.store.update_cell('cell', **{('validation_experiment' if change == 'validation' else 'positive_control'): True})
    elif change == 'config_control':
        path=f.root/'config.json'; path.chmod(0o644); path.write_text(json.dumps(dict(f.config, origin_dev_lock=True)))
    else:
        with f.store.tx() as db:
            experiment=f.store.get(db,'experiments','exp')
            old=json.loads(Path(experiment['manifest']['uri']).read_bytes())
            old['validation_experiment' if change == 'manifest_validation' else 'positive_control'] = True
            experiment['manifest']=immutable(f.root/'control-manifest.json',old)
            f.store.put(db,'experiments','exp',experiment)
    with pytest.raises(importer.Refused, match='unsupported'):
        list(importer.pending(f.store.path))


@pytest.mark.parametrize('change', ['invalid', 'not_executed', 'outside_interval'])
def test_unexecuted_or_misnumbered_help_is_not_staged(fixture, monkeypatch, change):
    f = fixture; prepare(f, monkeypatch); formal(f, 1, REJECTED)
    help_interval(f, 1); directory = formal(f, 2, TIMEOUT)
    if change == 'invalid':
        path = directory/'version.json'; value=json.loads(path.read_bytes()); value['valid']=False
        path.chmod(0o644); path.write_text(json.dumps(value))
    elif change == 'not_executed':
        with f.store.tx() as db: db.execute("DELETE FROM v2_jobs WHERE version=2")
    else:
        with f.store.tx() as db:
            raw, = db.execute("SELECT payload FROM v2_agent_jobs WHERE cell='cell' AND id='help:v0001'").fetchone()
            value=json.loads(raw); value['settled_at'] = 1e20
            db.execute("UPDATE v2_agent_jobs SET payload=? WHERE cell='cell' AND id='help:v0001'", (json.dumps(value),))
    assert stage(f, 2) is None
    assert not events(f, 'planner_memory_offline_queued')
    assert not events(f, 'planner_memory_run_note_created')


def test_live_help_job_drift_is_refused(proposal):
    f, value = proposal
    with f.store.tx() as db:
        state, raw = db.execute("SELECT state,payload FROM v2_agent_jobs WHERE cell='cell' AND id='help:v0001'").fetchone()
        job = json.loads(raw); job['receipt']['content'] = 'Changed help'
        db.execute("UPDATE v2_agent_jobs SET payload=? WHERE cell='cell' AND id='help:v0001'", (json.dumps(job),))
    with pytest.raises(importer.Refused, match='digest drift'):
        list(importer.pending(f.store.path))


def test_staging_failure_is_nonfatal_and_notes_outcomes(proposal, monkeypatch):
    f, value = proposal
    f.store.update_cell('cell', run_notes=True)
    note = json.loads((f.root / 'v2/memory-run-note.json').read_bytes())
    immutable(f.root / 'v3/memory-run-notes-input.json', dict(entries=[note]))
    formal(f, 3, FURTHER)
    assert stage(f, 3) is None
    assert events(f, 'planner_memory_run_note_outcome')[0]['note_id'] == note['note_id']
    monkeypatch.setattr(f.ledger, 'jobs', lambda: (_ for _ in ()).throw(RuntimeError('broken')))
    assert stage(f, 2) is None
    assert events(f, 'planner_memory_provisional_skipped')[-1]['reason'] == 'error:RuntimeError'


def sync_request(app, path, payload, headers):
    import inspect
    from starlette.requests import Request
    from starlette.routing import Match
    scope = dict(type='http', method='POST', path=path, root_path='', headers=[])
    for route in app.routes:
        matched, child = route.matches(scope)
        if matched is not Match.FULL: continue
        scope.update(child)
        def invoke(dependant):
            args = {field.name: headers.get(field.alias.lower()) for field in dependant.header_params}
            for field in dependant.body_params:
                annotation = inspect.get_annotations(dependant.call, eval_str=True)[field.name]
                args[field.name] = annotation.model_validate(payload)
            if dependant.request_param_name: args[dependant.request_param_name] = Request(scope)
            for dependency in dependant.dependencies:
                result = invoke(dependency)
                if dependency.name: args[dependency.name] = result
            return dependant.call(**args)
        return invoke(route.dependant)
    raise AssertionError(path)


def test_export_stage_auto_import_service_e2e(fixture, monkeypatch, tmp_path):
    from benchmarks.attention_harness.dev_agent_loop import drive
    from benchmarks.attention_harness.dev_agent_adapters import FakeProber
    from benchmarks.attention_harness.dev_agent_attention import Helper
    from benchmarks.attention_harness.tests.test_dev_agent_d2 import fake_session
    f = fixture; prepare(f, monkeypatch)
    f.memory.run_notes = True
    class Moving(Executor):
        def execute(self, *args, **kwargs):
            value = super().execute(*args, **kwargs)
            trace = REJECTED if self.calls == 1 else TIMEOUT
            trace_path = self.episode / 'trace.json'; trace_path.chmod(0o644)
            trace_path.write_text(json.dumps(dict(status='failed', error=trace['public_error'], sdk_events=trace['action_trace'])))
            self.result['status'] = 'failed'
            return dict(value, status='failed', error=trace['public_error'])
    class GenerationHelper(Helper):
        def boundary(self, execution, **kwargs):
            assert f.memory.number == int(execution['version_id'][1:]) + 1
            return super().boundary(execution, **kwargs)
    helper = GenerationHelper(f.ws, f.ledger, policy='reactive_help', config=dict(advisor_proxy={}),
        view_builder=lambda *a, **kw: dict(question='How?', reasoning='', diff='', steps=[],
            eval_review=dict(passed=False, reason='', reference_suggestion=''), frames=[], shown_video_seconds=0, task='Move cup'),
        ask_provider=lambda *a, **kw: dict(status='completed', kind='hint', feedback=ADVICE['content'], usage={}))
    session = fake_session(f.ws, ["tool('submit')", "tool('ask_help','--question','How to lift?');tool('submit')", 'pass'])
    exporter = Exporter(f.store, 'cell', f.ws, f.config, f.ledger)
    result = drive(f.ws, session, f.ledger, executor=Moving(), prober=FakeProber(), helper=helper,
                   memory=f.memory, exporter=exporter, completion_reader=lambda *a: False)
    assert not result.get('export_errors'), result
    queued = events(f, 'planner_memory_offline_queued')
    proposals = [q for q in queued if q['kind'] == 'provisional']
    assert len(proposals) == 1, (result, events(f, 'planner_memory_provisional_skipped'), json.loads((f.ws.ws/'versions/v0001/help.json').read_bytes()))
    assert json.loads((f.ws.ws/'versions/v0001/help.json').read_bytes())['provider'] == 'advisor'
    assert (f.root/'v2/policy.py').read_bytes() == Path(f.store.cell('cell')['versions'][1]['source']['uri']).read_bytes()
    assert (f.root/'v2/memory-run-note.json').is_file()
    packet = json.loads((f.ws.private/'memory/v3/memory-input.json').read_bytes())
    notes = json.loads((f.ws.private/'memory/v3/memory-run-notes-input.json').read_bytes())
    assert notes['entries'] and packet
    svc = MemoryService(tmp_path / 'service.sqlite')
    app = create_app(tmp_path / 'service.sqlite', api_key='online-test-key-123', importer_key='offline-test-key-123')
    class Client:
        def create_provisional_note(self, **payload):
            return sync_request(app, '/dev-v2/provisional-notes', payload,
                                {'x-memory-service-key': 'online-test-key-123', 'x-memory-importer-key': 'offline-test-key-123'})
    counts = importer.run([f.store.path], client=Client(), namespace='test', out=lambda *a: None)
    assert counts['created'] == 1 and counts['refused'] == counts['deferred'] == 0, counts
    value = json.loads(Path(proposals[0]['queue']['uri']).read_bytes())
    mid = provisional.memory_id(value['experiment_id'], 'cell', 2)
    p = svc.v2.provenance(mid)
    assert p['channel'] == p['origin']['channel'] == 'dev_agent'
    assert p['unvalidated'] and p['excluded_from_results'] and p['trust_scope'] == 'provisional'
    assert svc.store.get_memory(mid).status.value == 'provisional'
    assert svc.retrieve(dict(context(), namespace='test'), now=__import__('time').time())


def onecall_fixture(root, monkeypatch):
    from benchmarks.attention_harness.dev_ledger import DevV2Store
    from benchmarks.attention_harness.tests.test_memory_provisional import write_version
    store = DevV2Store(root / 'ledger.sqlite3')
    with store.tx() as db:
        db.execute('INSERT INTO v2_cells VALUES (?,?,?)', ('cell', 'exp', json.dumps(dict(
            experiment_id='exp', policy_id='reactive_help', artifact_dir=str(root), mode='experiment'))))
    immutable(root / 'config.json', {})
    monkeypatch.setattr(memory, 'context', lambda *a: context())
    write_version(root, 1, public=REJECTED)
    directory = write_version(root, 2, public=TIMEOUT, help_value=ADVICE)
    return store, directory


def test_onecall_proposal_and_payload_golden(tmp_path, monkeypatch):
    root = tmp_path / 'golden'; root.mkdir()
    store, directory = onecall_fixture(root, monkeypatch)
    ref = provisional.stage(store, 'cell', 2, directory)
    raw = Path(ref['uri']).read_text()
    payload = importer.provisional_payload(json.loads(raw), 'test')
    golden = json.loads((Path(__file__).parent / 'fixtures/agent_memory_phase1_onecall.json').read_text())
    assert raw.replace(str(root), '<ROOT>') == golden['proposal']
    assert json.dumps(payload, sort_keys=True, ensure_ascii=False).replace(str(root), '<ROOT>') == golden['payload']


def test_real_data_offline_copy(tmp_path, monkeypatch):
    import shutil
    from benchmarks.attention_harness.dev_ledger import DevV2Store
    from benchmarks.attention_harness.dev_agent_ledger import AgentLedger
    source = Path('/home/truares/Documents/Codex/2026-10-14/goal-attentionbench-robocasa365-dev-v2-home/outputs/daily-experiments/devexp-2656176e6fa79c9bbee569bf')
    if not (source / 'ledger.sqlite3').is_file(): pytest.skip('optional historical run unavailable')
    copied = tmp_path / 'ledger.sqlite3'
    with sqlite3.connect('file:' + str(source / 'ledger.sqlite3') + '?mode=ro', uri=True) as src, sqlite3.connect(copied) as dst:
        src.backup(dst)
    store = DevV2Store(copied)
    with store.tx() as db:
        key, raw = db.execute('SELECT id,payload FROM v2_cells').fetchone()
    cell = json.loads(raw); old = Path(cell['artifact_dir']); root = tmp_path / 'cell'; root.mkdir()
    ws = SimpleNamespace(private=root/'agent/private', canonical=root/'agent/private/public')
    shutil.copyfile(old / 'config.json', root / 'config.json')
    from openarm365_dev_v2 import scene_contract
    config = json.loads((root/'config.json').read_bytes())
    monkeypatch.setattr(scene_contract, 'registered_contract_ref', lambda: config['scene_contract'])
    for n in (1, 2, 3):
        directory = root / f'v{n}'; directory.mkdir()
        for name in ('public-feedback.json',): shutil.copyfile(old/f'v{n}'/name, directory/name)
        record = json.loads((old/f'v{n}/version.json').read_bytes())
        source_file = Path(record['source']['uri'])
        target = ws.private/'sources'/source_file.name; target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_file, target)
        record['source'] = provisional._ref(target)
        shutil.copyfile(target, directory/'policy.py')
        immutable(directory/'version.json', record)
        with store.tx() as db:
            db.execute('UPDATE v2_versions SET payload=? WHERE cell=? AND version=?', (json.dumps(record), key, n))
        answer = dict(status='not_requested')
        if n > 1:
            answer = json.loads((old/f'agent/ws/versions/v{n-1:04d}/help.json').read_bytes())
            answer['provider'] = 'advisor'  # Historical snapshots predate D7.1; only the copy is migrated.
        immutable(directory/'help.json', answer)
    for n in (1, 2):
        for name in ('advisor.json', 'request-view.json'):
            target=ws.private/f'help/v{n:04d}'/name; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(old/f'agent/private/help/v{n:04d}'/name, target)
    responses=ws.canonical/'rpc/responses'; responses.mkdir(parents=True)
    for path in (old/'agent/ws/rpc/responses').glob('*.json'):
        value=json.loads(path.read_bytes())
        if value.get('op') == 'ask_help': shutil.copyfile(path, responses/path.name)
    store.update_cell(key, artifact_dir=str(root))
    ledger = AgentLedger(copied, key, help_limit=10)
    proposals=[]
    for n in (1, 2, 3):
        ref=agent.stage_provisional(store,key,n,root/f'v{n}',ws,ledger)
        if ref: proposals.append(json.loads(Path(ref['uri']).read_bytes()))
    with store.tx() as db:
        skipped=[json.loads(raw) for raw, in db.execute("SELECT payload FROM v2_events WHERE kind='planner_memory_provisional_skipped'")]
    assert [p['help_version'] for p in proposals] == [2, 3], skipped
    for value in proposals:
        assert value['origin_version'] == value['help_version'] - 1
        importer.provisional_payload(value, 'offline-real-copy')
        print('REAL_PROPOSAL ' + json.dumps(value, sort_keys=True, ensure_ascii=False))
    # Leave the full proposal JSON in pytest's disposable directory for inspection.
    (tmp_path/'proposals.json').write_text(json.dumps(proposals, ensure_ascii=False, indent=2)+'\n')
