"""All-mode Memory regression: offline manifests, fake grants, no live processes."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness import daily_dev_v2 as daily, dev_experiment as experiments
from benchmarks.attention_harness import dev_planner_memory as memory, dev_v2_loop as loop
from benchmarks.attention_harness import memory_provisional as provisional, memory_monitor, dev_probe
from benchmarks.attention_harness import memory_auto_import as importer
from benchmarks.attention_harness.dev_help import POLICY_IDS
from benchmarks.attention_harness.dev_memory_view import projection
from benchmarks.attention_harness.dev_ledger import DevV2Store
from benchmarks.attention_harness.dev_loop import immutable, process_identity
from benchmarks.attention_harness.tests.test_daily_memory_wiring import _body, _memory
from benchmarks.attention_harness.tests.test_memory_provisional import events, write_version, ADVICE, REJECTED, TIMEOUT
from benchmarks.attention_harness.tests import test_memory_provisional as provisional_tests
from openarm365_dev_v2.scene_contract import registered_contract_ref

SOURCE = 'from robot_sdk import sensors\nobservation = sensors.get_observation()\n'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    from openarm365_dev_v2 import dev_config
    monkeypatch.setattr(dev_config, 'model_sources', lambda *a, **k: {})
    def materialize(argv, **kwargs):
        def arg(name): return Path(argv[argv.index(name) + 1])
        selection = json.loads(arg('--selection').read_bytes())
        old = selection.get('configuration')
        store = DevV2Store(arg('--store'), **({'configuration': dict(goal_id=old['goal_id'],
            limits=old['experiment_budget'], wall_seconds=old['wall_seconds'])} if old else {}))
        result = experiments.create(store, arg('--artifacts'), selection)
        arg('--output').write_text(json.dumps(result))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(daily.subprocess, 'run', materialize)


def make(path, *, mode='interactive', policy='autonomous', seed=102, bound=True):
    body = _body(policy)
    body['selection'].update(mode=mode, seeds=[seed])
    body['selection']['configuration']['max_iterations'] = 3
    bridge = daily.DailyDevV2(path, [], per_run_budget=True, memory=_memory() if bound else None)
    exp = bridge.create(body)
    store = bridge.store(exp['experiment_id'])
    cell = store.cell(exp['cells'][0]['key'])
    manifest = json.loads(Path(exp['manifest']['uri']).read_bytes())
    config = manifest['configs'][f'open_cabinet:{seed}']
    root = Path(exp['artifact_dir']) / 'cells' / cell['key']
    root.mkdir(parents=True, exist_ok=True)
    immutable(root / 'config.json', config)
    store.update_cell(cell['key'], state='running', owner=None, artifact_dir=str(root))
    return bridge, store, exp, store.cell(cell['key']), config, manifest, root


class Gateway:
    """Return both valid notes and hostile rows to exercise consumer filtering."""
    def __init__(self, config):
        self.context = memory.context(config, _memory()['namespace'])
        self.levels, self.grants = [], {}
        self.rows = []
        for i, (status, level) in enumerate([('trusted', 'same_scene'), ('provisional', 'same_task'),
                                            ('trusted', 'same_task'), ('provisional', 'same_task')]):
            self.rows.append(dict(memory_id=f'n{i}', version=1, guidance=f'Guidance {i}',
                status=status, match_level=level, trusted_here=i == 0, source_seed=102,
                applicability=self.context, created_at=10-i))
    def store_id(self): return _memory()['store_id']
    def retrieve_entries(self, context, now, match_levels):
        self.levels.append(match_levels)
        # Deliberately ignore requested levels: the caller must enforce them too.
        return copy.deepcopy(self.rows)
    def list_dev_v2_memories(self, **kwargs):
        return [dict(row, title=row['guidance']) for row in self.rows]
    def authorize_dev_use(self, **kw):
        row = next(r for r in self.rows if r['memory_id'] == kw['memory_id'])
        grant = {k: v for k, v in kw.items() if k != 'now'}
        grant.update(grant_id=kw['development_id']+row['memory_id'], guidance=row['guidance'],
            source_trace_id='fixture', evidence_refs=[], match_level=row['match_level'],
            trusted_here=row['trusted_here'], source_seed=102, unvalidated=row['status']=='provisional',
            trust_scope='provisional' if row['status']=='provisional' else 'trusted',
            excluded_from_results=not row['trusted_here'])
        self.grants[grant['grant_id']] = grant
        return copy.deepcopy(grant)
    def get_dev_use(self, grant_id): return copy.deepcopy(self.grants[grant_id])


def prompt(store, cell, config, manifest, root, version=1):
    directory = root / f'v{version}'
    directory.mkdir(exist_ok=True)
    packet, _, _ = loop.attention(store, cell['key'], version, {}, SOURCE, 0, [], manifest,
                                  process_identity(), directory, None)
    messages = loop.model_messages(config, SOURCE, {}, packet, {'dev_calls': 1})
    notes = json.loads(messages[-1]['content'])['attention_control']['memory']
    return json.loads((directory / 'memory-input.json').read_bytes()), notes


def monitor_scope(bridge, store, exp):
    # The monitor consumes the same bridge/ledger as the main page, without HTTP.
    return memory_monitor.project(bridge, SimpleNamespace(dev_catalogue=lambda: []), exp['experiment_id'])['run']['memory_scope']


@pytest.mark.parametrize('failure', ['drifted', 'missing', 'unreadable', 'missing_config'])
def test_unavailable_manifest_scope_before_first_use(tmp_path, monkeypatch, failure):
    from benchmarks.attention_harness.core.dev_store import DevConflict
    from benchmarks.attention_harness.dev_memory_view import _evidence
    from benchmarks.attention_harness.dev_loop import sha

    bridge, store, exp, cell, _, manifest, _ = make(tmp_path)
    path = Path(exp['manifest']['uri'])
    assert events(store, 'planner_memory_scope') == []
    if failure == 'missing':
        path.unlink()
    elif failure == 'unreadable':
        read_bytes = Path.read_bytes
        def unavailable(self):
            if self == path:
                raise PermissionError('Manifest unreadable')
            return read_bytes(self)
        monkeypatch.setattr(Path, 'read_bytes', unavailable)
    else:
        path.chmod(0o600)
        if failure == 'drifted':
            path.write_text('not valid JSON: must fail digest validation before parsing')
            with pytest.raises(DevConflict, match='Memory display evidence digest mismatch'):
                _evidence(exp['manifest'])
        else:
            manifest['configs'].pop(f"{cell['task_id']}:{cell['seed']}")
            path.write_text(json.dumps(manifest))
            with store.tx() as db:
                record = store.get(db, 'experiments', exp['experiment_id'])
                record['manifest']['sha256'] = sha(path)
                store.put(db, 'experiments', exp['experiment_id'], record)
    expected = {'level': 'unknown', 'reason': 'manifest_unavailable'}
    assert projection(store, cell)['scope'] == expected
    assert monitor_scope(bridge, store, exp) == expected
    assert events(store, 'planner_memory_scope') == []


@pytest.mark.parametrize('mode', ['interactive', 'diagnostic', 'experiment'])
@pytest.mark.parametrize('policy', POLICY_IDS)
def test_daily_binding_prompt_and_budgets(tmp_path, monkeypatch, mode, policy):
    bridge, store, exp, cell, config, manifest, root = make(tmp_path/'bound', mode=mode, policy=policy)
    _, _, _, plain, _, plain_manifest, _ = make(tmp_path/'plain', mode=mode, policy=policy, bound=False)
    assert cell['memory_authority'] == manifest['memory_authority'] == _memory()
    assert cell['budget'] == plain['budget'] == daily.DEFAULT_CELL
    assert manifest['cell_budget'] == plain_manifest['cell_budget']
    assert cell['run_notes'] is plain['run_notes'] is True
    if mode != 'experiment':
        assert not manifest['selection']['advisor_proxy']['enabled']
        assert not cell['experiment_eligible']
    assert projection(store, cell)['scope'] == {'level': 'scene'}
    gateway = Gateway(config)
    monkeypatch.setattr(memory, 'client_for', lambda _: gateway)
    # demo_first claims a local demo job while constructing the prompt.
    store.update_cell(cell['key'], owner=process_identity())
    packet, notes = prompt(store, cell, config, manifest, root)
    assert [n['title'] for n in notes] == ['Guidance 0', 'Unvalidated reference (scene 102): Guidance 1',
                                          'Reference (scene 102): Guidance 2']
    assert [e['trusted_here'] for e in packet['entries']] == [True, False, False]
    prompt(store, cell, config, manifest, root)  # Resume rechecks without a duplicate scope event.
    assert events(store, 'planner_memory_scope') == [dict(cell=cell['key'], version=1, level='scene', reason=None, seed=102)]
    assert gateway.levels and all(x == ['same_scene', 'same_task'] for x in gateway.levels)
    assert projection(store, cell)['unvalidated_used'] is True
    assert monitor_scope(bridge, store, exp)['level'] == 'scene'


def test_interactive_daily_provisional_use_is_imported_without_result(tmp_path, monkeypatch):
    _, store, _, cell, config, manifest, root = make(tmp_path/'scene', mode='interactive', seed=102)
    # Reuse the real in-process Service gateway with the daily bridge's authority.
    monkeypatch.setattr(provisional_tests, 'SETTINGS', _memory())
    gateway, _ = provisional_tests.populated(tmp_path, config)
    monkeypatch.setattr(memory, 'client_for', lambda _: gateway)

    def note():
        return next(row for row in gateway.svc.list_dev_v2_memories() if row['memory_id'] == 'p-new')

    assert note()['uses'] == dict(grants=0, outcomes=0, failure_gone=0)
    packet, notes = prompt(store, cell, config, manifest, root)
    entry = next(e for e in packet['entries'] if e['memory_id'] == 'p-new')
    assert projection(store, cell)['scope']['level'] == 'scene'
    assert entry['match_level'] == 'same_scene' and entry['trust_scope'] == 'provisional'
    assert any(n['title'] == 'Unvalidated: New unvalidated' and
               n['content'] == 'Newest unvalidated guidance.' for n in notes)
    assert note()['uses'] == dict(grants=1, outcomes=0, failure_gone=0)
    directory = root/'v1'
    public = immutable(directory/'public-feedback.json', TIMEOUT)
    source = immutable(directory/'policy.py', SOURCE)
    memory.journal_result(store, cell['key'], 1, packet, source, public, directory)
    queued = events(store, 'planner_memory_offline_queued')
    assert [e['kind'] for e in queued] == ['provisional_outcome']
    item = json.loads(Path(queued[0]['queue']['uri']).read_bytes())
    assert item['kind'] == 'provisional_outcome'
    assert item['evidence']['grants'] == [
        {k: e[k] for k in ('grant_id', 'memory_id', 'development_id')}
        for e in packet['entries'] if e.get('trust_scope') == 'provisional']
    assert item['evidence']['public_feedback'] == public
    assert not (directory/'memory-result.json').exists()
    assert events(store, 'planner_memory_result_skipped')[-1]['reason'] == 'not_trusted_here_excluded_from_results'
    assert gateway.calls == []
    assert note()['uses'] == dict(grants=1, outcomes=0, failure_gone=0)

    assert importer.run([store.path], client=gateway, namespace=_memory()['namespace']) == dict(
        created=0, outcomes=2, refused=0, deferred=0, planned=0)
    assert gateway.calls == [('outcome', e['grant_id']) for e in packet['entries'] if e.get('trust_scope') == 'provisional']
    assert note()['uses'] == dict(grants=1, outcomes=1, failure_gone=1)
    assert note()['status'] == 'provisional'
    receipt = next(e for e in events(store, importer.RECEIPT) if e['memory_id'] == 'p-new')
    assert receipt['kind'] == 'provisional_outcome' and receipt['memory_id'] == 'p-new'
    assert receipt['grant_id'] == entry['grant_id'] and receipt['signature_present'] is False

    # The same stored provisional note is readable at seed 104, but reference-only
    # use must not produce an outcome or change the Service's outcome counters.
    _, ref_store, _, ref_cell, ref_config, ref_manifest, ref_root = make(
        tmp_path/'reference', mode='interactive', seed=104)
    ref_packet, ref_notes = prompt(ref_store, ref_cell, ref_config, ref_manifest, ref_root)
    assert ref_packet['scope'] == 'reference_only' and ref_packet['scope_reason'] == 'no_scene_contract'
    ref_entry = next(e for e in ref_packet['entries'] if e['memory_id'] == 'p-new')
    assert ref_entry['match_level'] == 'same_task' and ref_entry['trust_scope'] == 'provisional'
    assert any(n['title'] == 'Unvalidated reference (scene 102): New unvalidated' for n in ref_notes)
    before = note()['uses']
    ref_directory = ref_root/'v1'
    ref_public = immutable(ref_directory/'public-feedback.json', TIMEOUT)
    ref_source = immutable(ref_directory/'policy.py', SOURCE)
    memory.journal_result(ref_store, ref_cell['key'], 1, ref_packet, ref_source, ref_public, ref_directory)
    assert events(ref_store, 'planner_memory_offline_queued') == []
    assert not (ref_directory/'memory-provisional-outcome.json').exists()
    assert not (ref_directory/'memory-result.json').exists()
    assert events(ref_store, 'planner_memory_result_skipped')[-1]['reason'] == 'reference_only_no_scene_contract'
    assert importer.run([store.path, ref_store.path], client=gateway, namespace=_memory()['namespace']) == dict(
        created=0, outcomes=0, refused=0, deferred=0, planned=0)
    assert events(ref_store, importer.RECEIPT) == []
    assert gateway.calls == [('outcome', e['grant_id']) for e in packet['entries'] if e.get('trust_scope') == 'provisional']
    assert note()['uses'] == before


@pytest.mark.parametrize('mode', ['interactive', 'diagnostic'])
def test_uncovered_reference_only_no_proposal_or_outcome(tmp_path, monkeypatch, mode):
    bridge, store, exp, cell, config, manifest, root = make(tmp_path, mode=mode, seed=104)
    assert projection(store, cell)['scope'] == dict(level='reference_only', reason='no_scene_contract', seed=104)
    assert monitor_scope(bridge, store, exp)['level'] == 'reference_only'
    gateway = Gateway(config)
    for trusted in (True, None, 'false'):
        gateway.rows.append(dict(gateway.rows[1], memory_id=f'bad-{trusted}', trusted_here=trusted, created_at=100))
    missing_trust = dict(gateway.rows[1], memory_id='bad-missing', created_at=100)
    missing_trust.pop('trusted_here'); gateway.rows.append(missing_trust)
    monkeypatch.setattr(memory, 'client_for', lambda _: gateway)
    packet, notes = prompt(store, cell, config, manifest, root)
    assert packet['scope'] == 'reference_only' and packet['scope_reason'] == 'no_scene_contract'
    assert packet['excluded_from_results'] is True
    assert [e['memory_id'] for e in packet['entries']] == ['n1', 'n2']
    assert all(e['match_level'] == 'same_task' and e['trusted_here'] is False for e in packet['entries'])
    assert len(notes) == memory.NOT_TRUSTED_HERE_CAP
    assert notes[0]['title'].startswith('Unvalidated reference (scene 102):')
    assert notes[1]['title'].startswith('Reference (scene 102):')
    prompt(store, cell, config, manifest, root)
    assert all(levels == ['same_task'] for levels in gateway.levels)
    scope = events(store, 'planner_memory_scope')
    assert scope == [dict(cell=cell['key'], version=1, level='reference_only', reason='no_scene_contract', seed=104)]
    assert projection(store, cell)['scope'] == scope[0]
    assert projection(store, cell)['unvalidated_used'] is True
    write_version(root, 1, public=REJECTED)
    directory = write_version(root, 2, public=TIMEOUT, help_value=ADVICE)
    assert provisional.stage_safely(store, cell['key'], 2, directory) is None
    assert not (directory/'memory-provisional-proposal.json').exists()
    assert events(store, 'planner_memory_provisional_skipped')[-1]['reason'] == 'no_scene_contract'
    memory.journal_result(store, cell['key'], 1, packet, {}, {}, root/'v1')
    assert events(store, 'planner_memory_offline_queued') == []
    assert events(store, 'planner_memory_result_skipped')[-1]['reason'] == 'reference_only_no_scene_contract'
    # Exact grant binding and use-time rechecks still fail closed.
    service = memory.DevMemory(gateway, _memory(), config, layered=True, mode=mode)
    service.recheck(packet)
    gateway.grants[packet['entries'][0]['grant_id']]['source_policy_sha256'] = 'changed'
    with pytest.raises(PermissionError, match='no longer matches'):
        service.recheck(packet)


@pytest.mark.parametrize('ref,reason', [(None, 'scene_contract_missing'), ({'uri':'unregistered', 'sha256':'0'*64}, 'scene_contract_not_registered')])
def test_missing_or_unregistered_contract_is_empty_and_visible(tmp_path, monkeypatch, ref, reason):
    _, store, _, cell, config, _, root = make(tmp_path, seed=104)
    config = {k:v for k,v in config.items() if k != 'scene_contract'}
    if ref is not None: config['scene_contract'] = ref
    monkeypatch.setattr(memory, 'client_for', lambda _: pytest.fail('no valid context: no service access'))
    directory = root/'v1'; directory.mkdir()
    packet, service = memory.load_memory(store, cell['key'], 1, SOURCE, config, _memory(), directory, True)
    assert packet['entries'] == [] and packet['scope_reason'] == reason
    assert packet['excluded_from_results'] is True
    service.recheck(packet)
    assert events(store, 'planner_memory_scope')[-1]['reason'] == reason
    assert projection(store, cell)['scope']['reason'] == reason
    memory.journal_result(store, cell['key'], 1, packet, {}, {}, directory)
    assert events(store, 'planner_memory_result_skipped')[-1]['reason'] == 'reference_only_no_scene_contract'
    # Experiment mode still uses context's original strict rejection.
    with pytest.raises(ValueError, match='scene_contract'):
        memory.DevMemory(Gateway({**config, 'scene_contract':registered_contract_ref()}), _memory(), config, layered=True)


@pytest.mark.parametrize('mode', ['interactive', 'experiment'])
def test_old_selections_and_binding_errors(tmp_path, mode):
    selection = dict(_body()['selection'], mode=mode)
    old = tmp_path/'selection.json'; old.write_text(json.dumps(selection))
    assert daily.bind_daily_memory(selection, _memory(), old) == selection
    planner = dict(selection, planner_features={'memory':_memory(), 'approval_rule':'none',
                                                'human_wait_seconds':0, 'background_seconds':0})
    old.write_text(json.dumps(planner))
    assert daily.bind_daily_memory(planner, _memory(), old) == daily._bind_planner_memory(planner, _memory())
    with pytest.raises(ValueError, match='not configured'):
        daily.bind_daily_memory(dict(selection, memory_authority=_memory()), None)
    with pytest.raises(ValueError, match='differs from server configuration'):
        daily.bind_daily_memory(dict(selection, memory_authority=dict(_memory(), namespace='other')), _memory())


def test_switch_before_and_while_owned_keeps_next_dev_prompt(tmp_path, monkeypatch):
    from openarm365_dev_v2.tests.fakes import Client
    _, store, exp, cell, config, manifest, root = make(tmp_path, policy='reactive_help')
    gateway = Gateway(config)
    monkeypatch.setattr(memory, 'client_for', lambda _: gateway)
    monkeypatch.setattr(loop, 'execute', lambda *a, **k: pytest.fail('invalid fixture output must not execute'))
    monkeypatch.setattr(loop, 'decide', lambda *a, **k: dict(action='continue', reason='fixture'))
    switched = experiments.switch(store, cell['key'], 'autonomous')
    assert switched['memory_authority'] == _memory() and switched['policy_id'] == 'autonomous'
    first = Client(invalid_first=True)
    loop.run_cell(store, exp, manifest, cell['key'], 1, process_identity(), client=first)
    assert len(first.inputs) == 1
    store.update_cell(cell['key'], owner=process_identity())
    pending = experiments.switch(store, cell['key'], 'reactive_help')
    assert pending['memory_authority'] == _memory() and pending['pending_policy'] == 'reactive_help'
    assert pending['policy_id'] == 'autonomous'
    second = Client(invalid_first=True)
    loop.run_cell(store, exp, manifest, cell['key'], 2, process_identity(), client=second)
    current = store.cell(cell['key'])
    assert current['policy_id'] == 'reactive_help' and current['pending_policy'] is None
    assert current['memory_authority'] == _memory()
    assert current['policy_segments'] == ['reactive_help', 'autonomous', 'reactive_help']
    assert current['mixed_policy'] and not current['experiment_eligible']
    for client in (first, second):
        notes = json.loads(client.inputs[0]['messages'][-1]['content'])['attention_control']['memory']
        assert len(notes) == 3 and notes[0]['content'] == 'Guidance 0'


def test_probe_cells_never_bind_or_retrieve_memory(tmp_path, monkeypatch):
    from benchmarks.attention_harness.dev_tasks import TASKS
    from benchmarks.attention_harness.dev_configuration import normalize_configuration, configured_budget
    selection = dict(request_key='memory-probe-parent', tasks=list(TASKS), seeds=[102],
        policies=['autonomous'], mode='experiment', eval_agent={'enabled':True}, reproducible_reset=True,
        target_depth=1, target_valid_executions=1, max_versions=1, help_budget=1, scene_contract=registered_contract_ref(), memory_authority=_memory())
    budget = configured_budget(normalize_configuration(selection))
    reserve = dev_probe.probe_budget('dev')
    selection['goal_limits'] = {k: 2*v + reserve.get(k, 0) for k, v in budget.items()}
    store = DevV2Store(tmp_path/'ledger.sqlite')
    parent = experiments.create(store, tmp_path/'artifacts', selection)
    probe = dev_probe.create_probe(store, parent['experiment_id'])
    assert len(probe['cells']) == 2
    for cell in probe['cells']:
        assert cell['mode'] == 'probe' and memory.cell_memory(cell) is None
        assert projection(store, cell)['scope'] is None
    monkeypatch.setattr(memory, 'client_for', lambda _: pytest.fail('probe must not contact Memory'))
    monkeypatch.setattr(memory, 'load_memory', lambda *a, **k: pytest.fail('probe must not retrieve Memory'))
    calls, executed = [], []
    monkeypatch.setattr(dev_probe, 'provider_call', lambda *a, **k: calls.append(a[4]))
    def execute(*args, **kwargs):
        executed.append(Path(args[3]['source']['uri']).read_text())
    # Each execution is a stub; return a settled record on the subsequent lookup.
    completed = set()
    def execute_once(*args, **kwargs):
        execute(*args, **kwargs); completed.add(args[1])
    monkeypatch.setattr(dev_probe, 'execute', execute_once)
    monkeypatch.setattr(dev_probe, 'job_for', lambda cell, *a: dict(state='settled', receipt={'execution_status':'completed'}) if cell['key'] in completed else None)
    dev_probe.run_probe(store, parent['experiment_id'], client=object(), runner_factory=object())
    assert executed == [dev_probe.SOURCE, dev_probe.SOURCE]
    assert calls == [[{'role':'system', 'content':'Return OK. This is a minimal connectivity probe.'},
                      {'role':'user', 'content':'{"request":"Reply OK"}'}]]
    assert events(store, 'planner_memory_retrieved') == events(store, 'planner_memory_scope') == []


def test_reference_only_execution_rechecks_keep_mode_and_skip_results(tmp_path, monkeypatch):
    from openarm365_dev_v2.tests.fakes import Runner
    _, store, _, cell, config, manifest, root = make(tmp_path, seed=104)
    gateway = Gateway(config)
    monkeypatch.setattr(memory, 'client_for', lambda _: gateway)
    prompt(store, cell, config, manifest, root)
    owner = process_identity()
    store.update_cell(cell['key'], owner=owner)
    directory = root/'v1'
    source = immutable(directory/'policy.py', SOURCE)
    loop.execute(store, cell['key'], 1, {'source':source}, config, owner, directory, Runner)
    assert len(gateway.levels) >= 3 and all(levels == ['same_task'] for levels in gateway.levels)
    assert events(store, 'planner_memory_offline_queued') == []
    assert events(store, 'planner_memory_result_skipped')[-1]['reason'] == 'reference_only_no_scene_contract'


def test_missing_contract_scope_is_visible_before_first_dev_call(tmp_path):
    selection = daily.bind_daily_memory(dict(_body()['selection'], mode='interactive'), _memory())
    selection.pop('scene_contract')
    store = DevV2Store(tmp_path/'ledger.sqlite')
    exp = experiments.create(store, tmp_path/'artifacts', selection)
    cell = exp['cells'][0]
    assert events(store, 'planner_memory_scope') == []
    assert projection(store, cell)['scope'] == dict(level='reference_only', reason='scene_contract_missing', seed=102)
    assert events(store, 'planner_memory_scope') == []  # The projection remains read-only.
