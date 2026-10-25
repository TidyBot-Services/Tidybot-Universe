"""Continue from an attempt (plan A): offline; no simulator, model or Service is started."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from benchmarks.attention_harness.tests.r3_fixtures import r3_deployment
pytestmark = pytest.mark.usefixtures("r3_deployment")

from benchmarks.attention_harness import daily_dev_v2 as daily, dev_experiment, dev_fork, dev_v2_loop as loop
from benchmarks.attention_harness.dev_ledger import DevV2Store
from benchmarks.attention_harness.dev_loop import immutable, process_identity
from benchmarks.attention_harness.dev_tasks import get_task
from benchmarks.attention_harness.tests.test_memory_provisional import REJECTED, SOURCE, missing_model_copy_fixture
from openarm365_dev_v2 import scene_contract
from openarm365_dev_v2.scene_contract import registered_contract_ref

TASK = 'open_cabinet'
REASON = 'The left-arm move was rejected by the motion planner; the door stayed closed.'


def eid_for(key):
    return 'devexp-' + hashlib.sha256(key.encode()).hexdigest()[:24]


def source_run(tmp_path, *, valid=True, executed=True, legacy_grasp=False, versions=2):
    """A finished daily run laid out as on 8769: root/<eid>/ledger.sqlite3, artifacts and cell evidence."""
    root = tmp_path / 'daily'
    key = 'fork-source-run-102'
    eid = eid_for(key)
    selection = dict(request_key=key, tasks=[TASK], seeds=[102], policies=['reactive_help'], mode='interactive',
                     target_depth=1, target_valid_executions=1, max_versions=3, help_budget=1,
                     scene_contract=registered_contract_ref(), reproducible_reset=True)
    (root / eid).mkdir(parents=True)
    store = DevV2Store(root / eid / 'ledger.sqlite3')
    experiment = dev_experiment.create(store, root / eid / 'artifacts', selection)
    cell = experiment['cells'][0]
    manifest = json.loads(Path(experiment['manifest']['uri']).read_bytes())
    config = copy.deepcopy(manifest['configs'][f'{TASK}:102'])
    if legacy_grasp:
        config['contact_exempt_objects'] = 'none'
        for k in ('planner_mode', 'ik_candidates', 'ik_max_joint_jump'):
            config.pop(k, None)
    cells = Path(experiment['artifact_dir']) / 'cells' / cell['key']
    cells.mkdir(parents=True)
    immutable(cells / 'config.json', config)
    store.update_cell(cell['key'], state='failed', artifact_dir=str(cells))
    system = get_task(TASK).system_prompt(config)
    for v in range(1, versions + 1):
        d = cells / f'v{v}'
        src = immutable(d / 'policy.py', SOURCE + f'# version {v}\n')
        mi = immutable(d / 'model-input.json', {'messages': [
            {'role': 'system', 'content': system + (dev_fork.OPINION + '{"passed": false}' if v > 1 else '')},
            {'role': 'user', 'content': '{}'}]})
        ok = valid or v < versions
        store.version(cell['key'], {'version': v, 'source': src, 'diff': src, 'reason': 'fixture', 'valid': ok,
                                    'validation_error': None if ok else 'fixture validator error',
                                    'model_input': mi, 'formal_eligible': False})
        if ok and (executed or v < versions):
            public = immutable(d / 'public-feedback.json', REJECTED)
            review = immutable(d / 'eval-review.json', {'status': 'completed', 'passed': False, 'reason': REASON})
            with store.tx() as db:
                for kind, receipt in (('execute', {'public_feedback': public}), ('eval', {'eval_review': review})):
                    job = {'job_id': f'{cell["key"]}-{v}-{kind}', 'cell': cell['key'], 'version': v, 'kind': kind,
                           'reservation': {}, 'actual': {}, 'receipt': receipt}
                    db.execute('INSERT INTO v2_jobs VALUES (?,?,?,?,?,?)',
                               (job['job_id'], cell['key'], v, kind, 'settled', json.dumps(job)))
    return SimpleNamespace(root=root, eid=eid, cell=cell['key'], store=store, config=config, dir=cells)


def child_body(source, key='fork-child-run-1', **fork):
    budget = dict(daily.DEFAULT_CELL)
    request = dict(experiment_id=source.eid, cell=source.cell, version=2, carry='feedback')
    request.update(fork)
    return dict(start=False, selection=dict(
        request_key=key, tasks=[TASK], seeds=[102], policies=['reactive_help'], mode='interactive', help_budget=1,
        eval_agent={'enabled': True, 'model': 'parcc/Qwen'}, fork=request,
        configuration=dict(goal_id=key + '-goal', experiment_budget=budget, cell_budget=budget,
                           max_iterations=2, wall_seconds=21600)))


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    def materialize(argv, **kwargs):
        def arg(name):return Path(argv[argv.index(name) + 1])
        selection = json.loads(arg('--selection').read_bytes())
        c = selection['configuration']
        store = DevV2Store(arg('--store'), configuration={'goal_id': c['goal_id'], 'limits': c['experiment_budget'],
                                                          'wall_seconds': c['wall_seconds']})
        arg('--output').write_text(json.dumps(dev_experiment.create(store, arg('--artifacts'), selection)))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(daily.subprocess, 'run', materialize)
    return lambda root: daily.DailyDevV2(root, [], per_run_budget=True)


def ids(rows, level=None):
    return [r['id'] for r in rows if level is None or r['level'] == level]


def test_preview_lists_attempts_checks_and_what_v1_reads(tmp_path):
    s = source_run(tmp_path)
    shown = dev_fork.preview(s.root, s.eid, s.cell, 2, 'feedback')
    assert not shown['blocked']
    assert [(a['version'], a['outcome'], a['reason']) for a in shown['attempts']] == [(1, 'unsuccessful', REASON), (2, 'unsuccessful', REASON)]
    assert ids(shown['checks'], 'ok') == ['scene', 'sdk']
    assert ids(shown['checks'], 'warn') == ['excluded']  # same frozen defaults: no grasp warning
    assert shown['prompt_includes'][0].startswith('policy.py from Attempt 2 (3 lines)')
    assert any('reviewer' in line for line in shown['prompt_includes'])
    assert dev_fork.preview(s.root, s.eid, s.cell, 2, 'code')['prompt_includes'][1] == 'no earlier feedback or trace'


def test_preview_blocks_and_warns_in_plain_words(tmp_path, monkeypatch):
    s = source_run(tmp_path / 'invalid', valid=False)
    shown = dev_fork.preview(s.root, s.eid, s.cell, 2, 'code')
    assert shown['blocked'] and 'source_valid' in ids(shown['checks'], 'block')
    s = source_run(tmp_path / 'unexecuted', executed=False)
    assert 'source_executed' in ids(dev_fork.preview(s.root, s.eid, s.cell, 2, 'feedback')['checks'], 'block')
    assert not dev_fork.preview(s.root, s.eid, s.cell, 2, 'code')['blocked']
    s = source_run(tmp_path / 'legacy', legacy_grasp=True)
    grasp = next(r for r in dev_fork.preview(s.root, s.eid, s.cell, 2)['checks'] if r['id'] == 'grasp_settings')
    assert grasp['level'] == 'warn' and "contact_exempt_objects none → movable_objects" in grasp['title'] and 'planner_mode not set → ik_path' in grasp['title']
    with pytest.raises(ValueError, match='Attempt 7 does not exist'):
        dev_fork.preview(s.root, s.eid, s.cell, 7)
    # A re-registered contract whose seed-102 entry differs blocks the continuation.
    ref = registered_contract_ref()
    changed = json.loads(Path(ref['uri']).read_bytes())
    entry = changed['tasks'][TASK]['seeds']['102']
    changed['tasks'][TASK]['seeds']['102'] = {**entry, 'fixture_changed': True}
    path = tmp_path / 'contract.json'
    path.write_text(json.dumps(changed))
    monkeypatch.setattr(scene_contract, 'registered_contract_ref', lambda: {'uri': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    shown = dev_fork.preview(s.root, s.eid, s.cell, 2)
    assert shown['blocked'] and ids(shown['checks'], 'block') == ['scene_contract']


def test_create_continues_from_the_attempt_and_is_idempotent(tmp_path, bridge):
    s = source_run(tmp_path)
    daily_bridge = bridge(s.root)
    created = daily_bridge.create(child_body(s))
    manifest = json.loads(Path(created['manifest']['uri']).read_bytes())
    selection = json.loads((s.root / created['experiment_id'] / 'selection.json').read_bytes())
    assert manifest['initial_sources'][TASK]['source'] == SOURCE + '# version 2\n'
    assert manifest['fork']['version'] == 2 and manifest['fork']['carry'] == 'feedback' and manifest['fork']['feedback']
    assert not [r for r in manifest['fork']['checks'] if r['level'] == 'block']
    assert selection['fork']['record']['sha256'] == dev_fork._ref(selection['fork']['record']['uri'])['sha256']
    config = manifest['configs'][f'{TASK}:102']
    assert config['scene_contract'] == registered_contract_ref() and config['scene_reset_seed'] == 102
    assert created['cells'][0]['experiment_eligible'] is False and created['cells'][0]['mode'] == 'interactive'
    assert created['selection']['fork']['experiment_id'] == s.eid
    carried = dev_fork.initial_feedback(manifest)
    assert carried['public_error'] == REJECTED['public_error'] and carried['eval_review']['reason'] == REASON
    assert daily_bridge.create(child_body(s))['experiment_id'] == created['experiment_id']
    dev_experiment.verify_manifest(created)
    # The source run is only read.
    assert s.store.cell(s.cell)['state'] == 'failed'


@pytest.mark.parametrize('change,message', [
    (dict(selection={'mode': 'experiment'}), 'interactive'),
    (dict(selection={'seeds': [101]}), 'source task and seed'),
    (dict(fork={'version': 9}), 'Attempt 9 does not exist'),
    (dict(fork={'carry': 'notes'}), 'carry must be one of'),
])
def test_create_refuses_in_plain_words(tmp_path, bridge, change, message):
    s = source_run(tmp_path)
    body = child_body(s, **change.get('fork', {}))
    body['selection'].update(change.get('selection', {}))
    with pytest.raises(ValueError, match=message):
        bridge(s.root).create(body)
    assert not (s.root / eid_for('fork-child-run-1') / 'fork').exists()


def test_fork_requires_frozen_record(tmp_path):
    s = source_run(tmp_path)
    with pytest.raises(ValueError, match='fork record missing'):
        dev_fork.load(dict(experiment_id=s.eid, cell=s.cell, version=2, carry='code'))


def test_v1_prompt_reads_source_code_and_feedback_but_decides_like_a_fresh_v1(tmp_path, bridge):
    from openarm365_dev_v2.tests.fakes import Client
    s = source_run(tmp_path)
    created = bridge(s.root).create(child_body(s))
    store = DevV2Store(s.root / created['experiment_id'] / 'ledger.sqlite3')
    manifest = json.loads(Path(created['manifest']['uri']).read_bytes())
    key = created['cells'][0]['key']
    client = Client(invalid_first=True)
    loop.run_cell(store, dev_experiment.snapshot(store, created['experiment_id']), manifest, key, 1, process_identity(), client=client)
    packet = json.loads(client.inputs[0]['messages'][-1]['content'])
    assert packet['previous_complete_source'] == SOURCE + '# version 2\n'
    assert packet['public_feedback']['public_error'] == REJECTED['public_error']
    assert REASON in client.inputs[0]['messages'][0]['content']
    v1 = Path(store.cell(key)['artifact_dir']) / 'v1'
    decision = json.loads((v1 / 'decision.json').read_bytes())
    assert decision['action'] != 'request' and not (v1 / 'help.json').exists()
    # The fake's first answer is invalid, so v1 keeps its starting code: the source attempt's.
    assert (v1 / 'policy.py').read_text() == SOURCE + '# version 2\n'


def test_code_only_v1_has_no_feedback(tmp_path, bridge):
    from openarm365_dev_v2.tests.fakes import Client
    s = source_run(tmp_path)
    created = bridge(s.root).create(child_body(s, key='fork-child-code-only', carry='code'))
    manifest = json.loads(Path(created['manifest']['uri']).read_bytes())
    assert manifest['fork']['feedback'] is None and dev_fork.initial_feedback(manifest) is None
    store = DevV2Store(s.root / created['experiment_id'] / 'ledger.sqlite3')
    client = Client(invalid_first=True)
    loop.run_cell(store, dev_experiment.snapshot(store, created['experiment_id']), manifest, created['cells'][0]['key'], 1, process_identity(), client=client)
    packet = json.loads(client.inputs[0]['messages'][-1]['content'])
    assert packet['previous_complete_source'] == SOURCE + '# version 2\n' and packet['public_feedback'] == {}


def test_preview_endpoint(tmp_path, bridge):
    s = source_run(tmp_path)
    daily_bridge = bridge(s.root)
    created = daily_bridge.create(child_body(s))  # gives the source root an authorization-backed sibling
    (s.root / s.eid / 'authorization.json').write_text('{}')
    value, kind = daily_bridge.get(f'/api/dev-v2/experiments/{s.eid}/fork-preview?cell={s.cell}&version=1&carry=code')
    assert kind == 'application/json' and value['version'] == 1 and value['carry'] == 'code'
    with pytest.raises(ValueError, match='version'):
        daily_bridge.get(f'/api/dev-v2/experiments/{s.eid}/fork-preview?cell={s.cell}&version=0')
    assert daily_bridge.options()['continue_from_attempt'] is True
    assert created['experiment_id'] != s.eid
