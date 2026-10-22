"""Provisional Memory entry, importer and layered Dev delivery (plan 0d, 2026-10-22).

Offline only: the Memory Service runs in-process on a temporary store; no HTTP,
model, simulator or live service is used.
"""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness import dev_planner_memory as memory
from benchmarks.attention_harness import dev_v2_loop as loop
from benchmarks.attention_harness import memory_auto_import as importer
from benchmarks.attention_harness import memory_provisional as provisional
from benchmarks.attention_harness.dev_experiment import create
from benchmarks.attention_harness.dev_ledger import DevV2Store
from benchmarks.attention_harness.dev_loop import immutable, process_identity, sha
from openarm365_dev_v2.scene_contract import registered_contract_ref

SOURCE = 'from robot_sdk import left_arm\nleft_arm.move_delta(0,0,0.01)\n'
URL = 'http://127.0.0.1:1'
SETTINGS = dict(base_url=URL, store_id='test-authority', namespace='controlled-test-only',
                url_sha256=hashlib.sha256(URL.encode()).hexdigest())
API_KEY, IMPORTER_KEY = 'online-secret-value-123456', 'importer-secret-value-654321'


def feedback(error, events):
    return {'schema_version': 'attentionbench.dev-public-feedback.v1', 'execution_status': 'failed',
            'public_error': error, 'action_trace': [{'source': s, 'operation': o, 'status': st} for s, o, st in events]}


REJECTED = feedback('RuntimeError: PlanningRejected: cuRoboV2 planning rejected: official_motion_planner_non_success',
                    [('sensors', 'find_objects', 'completed'), ('left_gripper', 'open', 'completed'),
                     ('left_arm', 'move_delta', 'failed')])
TIMEOUT = feedback('RuntimeError: TimeoutError: timed out',
                   [('sensors', 'get_observation', 'completed'), ('right_arm', 'move_delta', 'failed')])
FURTHER = feedback(REJECTED['public_error'],
                   [('sensors', 'find_objects', 'completed'), ('left_gripper', 'open', 'completed'),
                    ('base', 'move_delta', 'completed'), ('left_arm', 'move_delta', 'failed')])
SAME = feedback(REJECTED['public_error'], [('sensors', 'find_objects', 'completed'), ('left_arm', 'move_delta', 'failed')])


@pytest.fixture(autouse=True)
def missing_model_copy_fixture(monkeypatch):
    from openarm365_dev_v2 import dev_config
    monkeypatch.setattr(dev_config, 'model_sources', lambda *args, **kwargs: {})


def make(tmp_path, policy, *, key=None, **extra):
    selection = dict(request_key=key or ('provisional-' + policy), tasks=['open_cabinet'], seeds=[113],
                     policies=[policy], mode='interactive', target_depth=1, target_valid_executions=1,
                     max_versions=3, help_budget=1, scene_contract=registered_contract_ref())
    selection.update(extra)
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = DevV2Store(tmp_path / 'ledger.sqlite3')
    experiment = create(store, tmp_path / 'artifacts', selection)
    cell = experiment['cells'][0]
    root = Path(experiment['artifact_dir']) / 'cells' / cell['key']
    root.mkdir(parents=True)
    manifest = json.loads(Path(experiment['manifest']['uri']).read_bytes())
    config = manifest['configs']['open_cabinet:113']
    immutable(root / 'config.json', config)
    store.update_cell(cell['key'], state='running', owner=process_identity(), artifact_dir=str(root))
    return store, store.cell(cell['key']), config, manifest, root


def write_version(root, version, *, public=None, help_value=None, source=SOURCE):
    directory = root / f'v{version}'
    directory.mkdir(exist_ok=True)
    src = immutable(directory / 'policy.py', source)
    model_input = immutable(directory / 'model-input.json', {'messages': [{'role': 'user', 'content': 'public'}]})
    if help_value is not None:
        immutable(directory / 'help.json', help_value)
    immutable(directory / 'version.json', {'version': version, 'source': src, 'model_input': model_input})
    if public is not None:
        immutable(directory / 'public-feedback.json', public)
    return directory


ADVICE = {'provider': 'advisor', 'content': 'Lift the cup a little before moving the left arm sideways.'}


def events(store, kind):
    with store.tx() as db:
        return [json.loads(raw) for (raw,) in db.execute('SELECT payload FROM v2_events WHERE kind=? ORDER BY seq', (kind,))]


# --- signature, effectiveness, eval reason -------------------------------------------------

def test_signature_derivation_for_real_error_formats():
    assert provisional.derive_signature(REJECTED) == {'public_error_class': 'RuntimeError: PlanningRejected',
                                                      'operation': 'left_arm.move_delta'}
    assert provisional.derive_signature(TIMEOUT) == {'public_error_class': 'RuntimeError: TimeoutError',
                                                     'operation': 'right_arm.move_delta'}
    from attention_memory_service.dev_v2 import validate_signature
    validate_signature(provisional.derive_signature(REJECTED), REJECTED)
    for value, reason in ((feedback('', [('left_arm', 'move_delta', 'failed')]), 'no_public_error'),
                          (feedback('RuntimeError: x', [('left_arm', 'move_delta', 'completed')]), 'no_failed_event'),
                          (feedback('timed out after 3 s', [('left_arm', 'move_delta', 'failed')]), 'signature_not_derivable')):
        with pytest.raises(provisional.NotStaged, match=reason):
            provisional.derive_signature(value)


def test_effective_test_matches_service_rule():
    from attention_memory_service import provisional_notes
    signature = provisional.derive_signature(REJECTED)
    assert provisional.effective_reason(REJECTED, TIMEOUT, signature) == 'signature_changed'
    assert provisional.effective_reason(REJECTED, FURTHER, signature) == 'progressed'
    with pytest.raises(provisional.NotStaged, match='not_effective'):
        provisional.effective_reason(REJECTED, SAME, signature)
    for value in (REJECTED, TIMEOUT, FURTHER, SAME):
        assert provisional.progress(value) == provisional_notes.progress(value)


def test_eval_reason_class_is_small_and_deterministic():
    cases = {'The arm timed out before reaching': 'timeout', 'Planning rejected the goal pose': 'planner_rejected',
             'Hit a joint limit on the wrist': 'joint_limit', 'Task not completed: cup still on counter': 'not_completed',
             'Gripper closed early': 'other'}
    for text, expected in cases.items():
        assert provisional.eval_reason_class({'passed': False, 'feedback': text}) == expected
    assert provisional.eval_reason_class(None) == provisional.eval_reason_class({'passed': None, 'feedback': ''}) == 'none'
    assert set(cases.values()) | {'none'} <= set(provisional.EVAL_CLASSES)


# --- staging -------------------------------------------------------------------------------

def test_reactive_without_planner_features_stages_proposal(tmp_path):
    store, cell, config, manifest, root = make(tmp_path, 'reactive_help')
    assert not cell.get('planner_features')
    write_version(root, 1, public=REJECTED)
    directory = write_version(root, 2, public=TIMEOUT, help_value=ADVICE)
    ref = provisional.stage_safely(store, cell['key'], 2, directory)
    proposal = json.loads(Path(ref['uri']).read_bytes())
    assert proposal['schema_version'] == provisional.SCHEMA and proposal['status'] == 'pending_import'
    assert (proposal['policy_id'], proposal['origin_version'], proposal['help_version']) == ('reactive_help', 1, 2)
    assert proposal['effective_reason'] == 'signature_changed' and proposal['eval_reason_class'] == 'none'
    assert set(proposal['refs']) == set(importer.REF_KEYS)
    assert all(sha(r['uri']) == r['sha256'] for r in proposal['refs'].values())
    assert 'namespace' not in proposal['context'] and proposal['context']['sdk_interface_sha256']
    queued = events(store, 'planner_memory_offline_queued')
    assert [(e['kind'], e['version']) for e in queued] == [('provisional', 2)]
    assert provisional.stage_safely(store, cell['key'], 2, directory) == ref
    assert len(events(store, 'planner_memory_offline_queued')) == 1


@pytest.mark.parametrize('help_value,public,reason', [
    (None, TIMEOUT, None),
    ({'provider': 'demo', 'content': 'demo source'}, TIMEOUT, None),
    ({'provider': 'human', 'content': 'live human'}, TIMEOUT, None),
    ({'provider': 'advisor', 'content': 'x', 'rejected': True}, TIMEOUT, 'help_rejected_or_empty'),
    ({'provider': 'human_proxy', 'content': '   '}, TIMEOUT, 'help_rejected_or_empty'),
    (ADVICE, SAME, 'not_effective'),
    (ADVICE, None, 'missing_evidence:public-feedback.json'),
])
def test_nothing_staged_without_effective_advisor_answer(tmp_path, help_value, public, reason):
    store, cell, config, manifest, root = make(tmp_path, 'reactive_help')
    write_version(root, 1, public=REJECTED)
    directory = write_version(root, 2, public=public, help_value=help_value)
    assert provisional.stage_safely(store, cell['key'], 2, directory) is None
    assert events(store, 'planner_memory_offline_queued') == []
    skipped = events(store, 'planner_memory_provisional_skipped')
    assert [e['reason'] for e in skipped] == ([] if reason is None else [reason])


def test_staging_exception_never_breaks_the_run(tmp_path):
    store, cell, config, manifest, root = make(tmp_path, 'reactive_help')
    write_version(root, 1, public=REJECTED)
    directory = write_version(root, 2, public=TIMEOUT)
    (directory / 'help.json').write_text('{not json')
    assert provisional.stage_safely(store, cell['key'], 2, directory) is None
    assert events(store, 'planner_memory_provisional_skipped')[0]['reason'] == 'error:JSONDecodeError'


# --- in-process Service gateway ------------------------------------------------------------

class ServiceGateway:
    """The HTTP client's shape over an in-process MemoryService on a temp store."""

    def __init__(self, path):
        from attention_memory_service.memory_service import MemoryService
        self.svc = MemoryService(path)
        self.calls = []

    def store_id(self):
        return SETTINGS['store_id']

    def retrieve(self, context, now):
        return self.svc.retrieve(context, now=now)

    def retrieve_entries(self, context, now, match_levels=None):
        from attention_memory_service.applicability import marker
        from attention_memory_service.memory_v2 import trusted_here
        from attention_memory_service.seeded_notes import flags
        rows = []
        for m, level in self.svc.retrieve_levels(context, now=now, levels=tuple(match_levels or ['same_scene'])):
            provenance = self.svc.v2._provenance(m.memory_id) or {}
            rows.append({**m.artifact(), **flags(provenance), **marker(level, trusted_here(m, level)),
                         'source_seed': provenance.get('source_seed')})
        return json.loads(json.dumps(rows))

    def list_dev_v2_memories(self, *, status=None, context=None):
        return json.loads(json.dumps(self.svc.list_dev_v2_memories(status=status, context=context)))

    def authorize_dev_use(self, **kw):
        return json.loads(json.dumps(self.svc.authorize_dev_use(**kw)))

    def get_dev_use(self, grant_id):
        return json.loads(json.dumps(self.svc.get_dev_use(grant_id)))

    def create_provisional_note(self, **kw):
        self.calls.append(('create', kw['memory_id']))
        return self.svc.create_provisional_note(**kw)

    def record_provisional_outcome(self, **kw):
        self.calls.append(('outcome', kw['grant_id']))
        return self.svc.record_provisional_outcome(**kw)


def seed(gateway, context, memory_id, *, title, guidance, seeded=False):
    if seeded:
        return gateway.svc.create_test_seeded_note(actor='t', reason='fixture', memory_id=memory_id, context=context,
                                                   guidance=guidance, title=title)
    origin, helped = json.dumps(REJECTED), json.dumps(TIMEOUT)
    digest = lambda text: hashlib.sha256(text.encode()).hexdigest()
    return gateway.svc.create_provisional_note(
        actor='t', memory_id=memory_id, context=context, guidance=guidance, title=title,
        failure_signature=provisional.derive_signature(REJECTED),
        origin=dict(experiment_id='devexp-fixture', cell='cell', policy_id='reactive_help', origin_version=1,
                    help_version=2, provider='advisor', effective_reason='signature_changed', eval_reason_class='none',
                    digests=dict(origin_public_feedback=digest(origin), origin_source='1' * 64, help='2' * 64,
                                 help_model_input='3' * 64, help_source='4' * 64, help_public_feedback=digest(helped))),
        origin_public_feedback_text=origin, help_public_feedback_text=helped)


def populated(tmp_path, config):
    gateway = ServiceGateway(tmp_path / 'memory.sqlite')
    here = memory.context(config, SETTINGS['namespace'])
    other = {**here, 'seed': 101, 'camera_names': ['robot0_agentview_left'], 'scene_id': 'other-scene'}
    seed(gateway, here, 'seed-here', title='Trusted here', guidance='Open the gripper fully first.', seeded=True)
    seed(gateway, here, 'p-old', title='Old unvalidated', guidance='Old unvalidated guidance.')
    seed(gateway, other, 'seed-other', title='Other scene', guidance='Approach the handle from the left.', seeded=True)
    seed(gateway, here, 'p-new', title='New unvalidated', guidance='Newest unvalidated guidance.')
    return gateway, here


@pytest.mark.parametrize('policy', ['autonomous', 'demo_first', 'reactive_help', 'retry_k_then_ask',
                                    'budget_matched_random_escalation', 'trace_aware_hint_only',
                                    'full_trace_aware_attention_planner'])
def test_every_policy_prompt_gets_notes_with_cap_order_and_labels(tmp_path, monkeypatch, policy):
    store, cell, config, manifest, root = make(tmp_path, policy, memory_authority=SETTINGS)
    assert cell['memory_authority'] == SETTINGS and not cell.get('planner_features')
    gateway, here = populated(tmp_path, config)
    monkeypatch.setattr(memory, 'client_for', lambda settings: gateway)
    directory = root / 'v1'; directory.mkdir()
    packet, _, _ = loop.attention(store, cell['key'], 1, {}, SOURCE, 0, [], manifest, process_identity(), directory, None)
    assert [(e['memory_id'], e['match_level'], e['trusted_here']) for e in packet['memory']] == [
        ('seed-here', 'same_scene', True), ('p-new', 'same_scene', False), ('seed-other', 'same_task', False)]
    assert sum(not e['trusted_here'] for e in packet['memory']) == memory.NOT_TRUSTED_HERE_CAP
    messages = loop.model_messages(config, SOURCE, {}, packet, {'dev_calls': 1})
    notes = json.loads(messages[1]['content'])['attention_control']['memory']
    assert [n['title'] for n in notes] == ['Trusted here', 'Unvalidated: New unvalidated', 'Reference (scene 101): Other scene']
    assert 'grant_id' not in json.dumps(notes) and 'trusted_here' not in json.dumps(notes)
    flagged = events(store, 'planner_memory_unvalidated_used')
    assert flagged == [dict(cell=cell['key'], version=1, memory_ids=['p-new', 'seed-other'],
                            match_levels=['same_scene', 'same_task'], excluded_from_results=True)]
    retrieved = events(store, 'planner_memory_retrieved')[0]
    assert retrieved['not_trusted_here'] is True and retrieved['unvalidated'] is True
    from benchmarks.attention_harness.dev_memory_view import projection
    view = projection(store, cell)
    assert view['unvalidated_used'] is True
    assert [(e['memory_id'], e['trusted'], e['match_level']) for e in view['retrievals'][0]['entries']] == [
        ('seed-here', True, 'same_scene'), ('p-new', False, 'same_scene'), ('seed-other', False, 'same_task')]
    # Delivery never changes a policy's own help decision; the planner's
    # retrieve_memory decision is only logged (no help record, no cost).
    if policy != 'full_trace_aware_attention_planner':
        assert packet['action'] == {'demo_first': 'use_demo'}.get(policy, 'continue')
    elif packet['action'] == 'retrieve_memory':
        assert not (directory / 'help.json').exists()
    saved = json.loads((directory / 'memory-input.json').read_bytes())
    memory.DevMemory(gateway, SETTINGS, config, layered=True).recheck(saved)
    provenance = loop.memory_provenance(saved)
    assert provenance['not_trusted_here'] is True and provenance['excluded_from_results'] is True


def test_journal_queues_provisional_outcome_never_a_result(tmp_path, monkeypatch):
    store, cell, config, manifest, root = make(tmp_path, 'autonomous', memory_authority=SETTINGS)
    gateway, here = populated(tmp_path, config)
    monkeypatch.setattr(memory, 'client_for', lambda settings: gateway)
    directory = root / 'v1'; directory.mkdir()
    packet, _ = memory.load_memory(store, cell['key'], 1, SOURCE, config, SETTINGS, directory, True)
    public = immutable(directory / 'public-feedback.json', TIMEOUT)
    src = immutable(directory / 'policy.py', SOURCE)
    memory.journal_result(store, cell['key'], 1, packet, src, public, directory)
    queued = events(store, 'planner_memory_offline_queued')
    assert [e['kind'] for e in queued] == ['provisional_outcome']
    item = json.loads(Path(queued[0]['queue']['uri']).read_bytes())
    assert [g['memory_id'] for g in item['evidence']['grants']] == ['p-new']
    assert events(store, 'planner_memory_result_skipped')[0]['reason'] == 'not_trusted_here_excluded_from_results'
    assert not (directory / 'memory-result.json').exists()
    counts = importer.run([store.path], client=gateway, namespace=SETTINGS['namespace'])
    assert counts['outcomes'] == 1
    row = next(r for r in gateway.svc.list_dev_v2_memories() if r['memory_id'] == 'p-new')
    assert row['uses'] == {'grants': 1, 'outcomes': 1, 'failure_gone': 1}
    assert row['status'] == 'provisional'


def test_sdk_implementation_change_keeps_notes_interface_change_drops_them(tmp_path, monkeypatch):
    store, cell, config, manifest, root = make(tmp_path, 'autonomous', memory_authority=SETTINGS)
    gateway, here = populated(tmp_path, config)
    original = memory.sdk_identity
    monkeypatch.setattr(memory, 'sdk_identity', lambda cfg: {**original(cfg), 'implementation': {'sdk.py': '0' * 64}})
    rebuilt = memory.DevMemory(gateway, SETTINGS, config, layered=True)
    assert rebuilt.context['sdk_sha256'] != here['sdk_sha256']
    assert rebuilt.context['sdk_interface_sha256'] == here['sdk_interface_sha256']
    assert [n.memory_id for n in rebuilt.selected()] == ['seed-here', 'p-new', 'seed-other']
    monkeypatch.setattr(memory, 'sdk_identity', lambda cfg: {**original(cfg), 'prompt': 'changed SDK text'})
    assert memory.DevMemory(gateway, SETTINGS, config, layered=True).selected() == []


def test_frozen_planner_memory_manifest_keeps_exact_trusted_only_behaviour(tmp_path, monkeypatch):
    store, cell, config, manifest, root = make(
        tmp_path, 'full_trace_aware_attention_planner', key='provisional-legacy-planner',
        planner_features={'memory': SETTINGS, 'human_wait_seconds': 0, 'background_seconds': 0, 'approval_rule': 'none'})
    assert 'memory_authority' not in cell and cell['planner_features']['memory'] == SETTINGS
    gateway = ServiceGateway(tmp_path / 'memory.sqlite')
    legacy = memory.context(config, SETTINGS['namespace']); legacy.pop('sdk_interface_sha256')
    seed(gateway, legacy, 'seed-legacy', title='Legacy note', guidance='Legacy guidance.', seeded=True)
    seed(gateway, memory.context(config, SETTINGS['namespace']), 'p-new', title='New', guidance='Unvalidated.')
    monkeypatch.setattr(memory, 'client_for', lambda settings: gateway)
    directory = root / 'v1'; directory.mkdir()
    packet, _, _ = loop.attention(store, cell['key'], 1, {}, SOURCE, 0, [], manifest, process_identity(), directory, None)
    assert [e['memory_id'] for e in packet['memory']] == ['seed-legacy']
    entry = packet['memory'][0]
    assert 'trusted_here' not in entry and 'sdk_interface_sha256' not in entry['context']
    assert events(store, 'planner_memory_unvalidated_used') == []
    from benchmarks.attention_harness.dev_memory_view import projection
    assert projection(store, cell)['unvalidated_used'] is False
    assert set(loop.memory_provenance(json.loads((directory / 'memory-input.json').read_bytes()))) == {
        'seeded_for_tests', 'excluded_from_results', 'entries'}


# --- importer ------------------------------------------------------------------------------

def staged(tmp_path, help_public=TIMEOUT, version=2):
    store, cell, config, manifest, root = make(tmp_path, 'reactive_help')
    write_version(root, 1, public=REJECTED)
    directory = write_version(root, 2, public=help_public, help_value=ADVICE)
    ref = provisional.stage_safely(store, cell['key'], 2, directory)
    return store, cell, ref, root


def test_importer_builds_payload_and_real_service_accepts_it_idempotently(tmp_path):
    store, cell, ref, root = staged(tmp_path)
    gateway = ServiceGateway(tmp_path / 'memory.sqlite')
    lines = []
    assert importer.run([store.path], client=gateway, namespace=SETTINGS['namespace'], out=lines.append)['created'] == 1
    memory_id = provisional.memory_id(cell['experiment_id'], cell['key'], 2)
    note = gateway.svc.store.get_memory(memory_id)
    assert note.status.value == 'provisional'
    assert gateway.svc.v2.provenance(memory_id)['title'] == 'Avoid PlanningRejected on left_arm.move_delta'
    receipts = events(store, importer.RECEIPT)
    assert receipts[0]['memory_id'] == memory_id and receipts[0]['status'] == 'provisional'
    assert importer.run([store.path], client=gateway, namespace=SETTINGS['namespace']) == dict(
        created=0, outcomes=0, refused=0, deferred=0, planned=0)
    assert gateway.calls == [('create', memory_id)]


def test_importer_digest_drift_and_service_refusal_are_terminal(tmp_path):
    store, cell, ref, root = staged(tmp_path)
    proposal = json.loads(Path(ref['uri']).read_bytes())
    Path(proposal['refs']['help_source']['uri']).chmod(0o644)
    Path(proposal['refs']['help_source']['uri']).write_text('changed')
    calls = []
    client = SimpleNamespace(create_provisional_note=lambda **kw: calls.append(kw))
    assert importer.run([store.path], client=client, namespace='ns')['refused'] == 1
    assert calls == [] and 'digest drift' in events(store, importer.REFUSED)[0]['message']
    assert importer.run([store.path], client=client, namespace='ns')['refused'] == 0

    store2, cell2, ref2, root2 = staged(tmp_path / 'b')
    class StateConflictError(Exception):
        pass
    def refuse(**kw):
        raise StateConflictError('intervention not effective')
    lines = []
    assert importer.run([store2.path], client=SimpleNamespace(create_provisional_note=refuse), namespace='ns',
                        out=lines.append)['refused'] == 1
    assert events(store2, importer.REFUSED)[0]['error_class'] == 'StateConflictError'
    assert importer.run([store2.path], client=SimpleNamespace(create_provisional_note=refuse), namespace='ns')['refused'] == 0


def test_importer_defers_unreachable_service_and_dry_run_writes_nothing(tmp_path):
    store, cell, ref, root = staged(tmp_path)
    def down(**kw):
        raise ConnectionRefusedError('connection refused')
    assert importer.run([store.path], client=SimpleNamespace(create_provisional_note=down), namespace='ns')['deferred'] == 1
    assert events(store, importer.RECEIPT) == events(store, importer.REFUSED) == []
    before = store.path.read_bytes()
    lines = []
    assert importer.run([store.path], client=None, namespace='ns', dry_run=True, out=lines.append)['planned'] == 1
    assert store.path.read_bytes() == before and lines[0].startswith('would submit')


def test_cli_never_prints_or_logs_keys(tmp_path, monkeypatch, capsys):
    store, cell, ref, root = staged(tmp_path)
    keys = tmp_path / 'keys.env'
    keys.write_text(f'ATTENTION_MEMORY_API_KEY={API_KEY}\nexport ATTENTION_MEMORY_IMPORTER_KEY="{IMPORTER_KEY}"\n')
    seen = {}

    class Client:
        def __init__(self, base_url, *, api_key, importer_key, timeout):
            seen.update(api_key=api_key, importer_key=importer_key)
        def store_id(self):
            return SETTINGS['store_id']
        def create_provisional_note(self, **kw):
            raise ValueError(f'refused while holding {IMPORTER_KEY}')

    from benchmarks.attention_harness import memory_service_client
    monkeypatch.setattr(memory_service_client, 'MemoryServiceClient', Client)
    code = importer.main(['--ledger', str(store.path), '--base-url', URL, '--store-id', SETTINGS['store_id'],
                          '--namespace', 'ns', '--keys', str(keys)])
    output = capsys.readouterr()
    assert code == 0 and seen == dict(api_key=API_KEY, importer_key=IMPORTER_KEY)
    ledger_text = json.dumps([events(store, importer.REFUSED), events(store, importer.RECEIPT)])
    for secret in (API_KEY, IMPORTER_KEY):
        assert secret not in output.out + output.err and secret not in ledger_text
    assert '[redacted]' in output.out
    assert importer.main(['--ledger', str(store.path), '--base-url', URL, '--store-id', 'other',
                          '--namespace', 'ns', '--keys', str(keys)]) == 2


def test_hook_is_off_without_env_and_launches_detached_with_it(tmp_path):
    store, cell, config, manifest, root = make(tmp_path, 'reactive_help', memory_authority=SETTINGS)
    directory = store.path.parent
    launched = []
    popen = lambda argv, **kw: launched.append((argv, kw)) or 'process'
    assert importer.launch_after_run(directory, environ={}, popen=popen) is None
    assert importer.launch_after_run(directory, environ={importer.ENV: str(tmp_path / 'missing')}, popen=popen) is None
    keys = tmp_path / 'keys.env'; keys.write_text('ATTENTION_MEMORY_API_KEY=x\n')
    assert importer.launch_after_run(directory, environ={importer.ENV: str(keys)}, popen=popen) == 'process'
    argv, kw = launched[0]
    assert argv[argv.index('--ledger') + 1] == str(store.path)
    assert argv[argv.index('--namespace') + 1] == SETTINGS['namespace'] and argv[argv.index('--keys') + 1] == str(keys)
    assert kw['start_new_session'] is True and (directory / 'memory-auto-import.log').exists()
    plain_store, _, _, _, _ = make(tmp_path / 'plain', 'reactive_help')
    assert importer.launch_after_run(plain_store.path.parent, environ={importer.ENV: str(keys)}, popen=popen) is None
