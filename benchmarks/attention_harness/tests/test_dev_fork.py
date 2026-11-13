"""Continue from an attempt (plan A): offline; no simulator, model or Service is started."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from benchmarks.attention_harness.tests.c21_fixtures import base, r4_deployment
from benchmarks.attention_harness.tests.r3_fixtures import r3_deployment
pytestmark = pytest.mark.usefixtures("r3_deployment")

from benchmarks.attention_harness import daily_dev_v2 as daily, dev_experiment, dev_fork, dev_v2_loop as loop
from benchmarks.attention_harness.dev_ledger import DevV2Store
from benchmarks.attention_harness.dev_loop import immutable, process_identity
from benchmarks.attention_harness.dev_tasks import get_task
from benchmarks.attention_harness.tests.test_memory_provisional import REJECTED, SOURCE, missing_model_copy_fixture
from benchmarks.attention_harness.tests.test_dev_agent_d7 import fixture as agent_memory_fixture
from openarm365_dev_v2 import scene_contract
from openarm365_dev_v2.scene_contract import registered_contract_ref

TASK = 'open_cabinet'
REASON = 'The left-arm move was rejected by the motion planner; the door stayed closed.'


def eid_for(key):
    return 'devexp-' + hashlib.sha256(key.encode()).hexdigest()[:24]


def source_run(tmp_path, *, valid=True, executed=True, legacy_grasp=False, versions=2, agent=False, source_padding='',fixture=False,arm_motion='short',config_transform=None):
    """A finished daily run laid out as on 8769: root/<eid>/ledger.sqlite3, artifacts and cell evidence."""
    root = tmp_path / 'daily'
    key = 'fork-source-run-102'
    eid = eid_for(key)
    task='categorize_condiments' if fixture else TASK
    selection = dict(request_key=key, tasks=[task], seeds=[102], policies=['reactive_help'], mode='interactive',
                     target_depth=1, target_valid_executions=1, max_versions=3, help_budget=1,
                     scene_contract=registered_contract_ref(), reproducible_reset=True)
    if agent:selection['dev_mode']='agent'
    if fixture:
        from benchmarks.attention_harness.dev_fixture_tasks import PROFILE
        selection.pop('scene_contract');selection.pop('reproducible_reset');selection['rules_profile']=PROFILE
    if arm_motion=='long':selection['arm_motion']='long'
    (root / eid).mkdir(parents=True)
    store = DevV2Store(root / eid / 'ledger.sqlite3')
    experiment = dev_experiment.create(store, root / eid / 'artifacts', selection)
    cell = experiment['cells'][0]
    manifest = json.loads(Path(experiment['manifest']['uri']).read_bytes())
    config = copy.deepcopy(manifest['configs'][f'{task}:102'])
    if legacy_grasp:
        config['contact_exempt_objects'] = 'none'
        for k in ('planner_mode', 'ik_candidates', 'ik_max_joint_jump'):
            config.pop(k, None)
    if config_transform:config_transform(config)
    cells = Path(experiment['artifact_dir']) / 'cells' / cell['key']
    cells.mkdir(parents=True)
    immutable(cells / 'config.json', config)
    store.update_cell(cell['key'], state='failed', artifact_dir=str(cells))
    system = get_task(task).system_prompt(config)
    for v in range(1, versions + 1):
        d = cells / f'v{v}'
        src = immutable(d / 'policy.py', SOURCE + f'# version {v}\n'+source_padding)
        mi = immutable(d / 'model-input.json', {'messages': [
            {'role': 'system', 'content': system + (dev_fork.OPINION + '{"passed": false}' if v > 1 else '')},
            {'role': 'user', 'content': '{}'}]})
        ok = valid or v < versions
        if agent:
            # Incoming snapshot versus a mutable outgoing ref containing future help.
            immutable(d/'help.json',dict(status='delivered',delivered=True,content=f'help-marker-{v}',hint_human_seconds=37))
            outgoing=immutable(d/'outgoing-help.json',dict(status='delivered',delivered=True,content=f'help-marker-{v+1}'))
            row=dict(execution_id=f'e{v}' if executed or v<versions else None,native_success=False,
                     eval_verdict='fail',eval=dict(passed=False,reason=f'eval-marker-{v}',advice=f'advice-marker-{v}'))
            store.version(cell['key'],dict(version=v,source=src,help=outgoing,valid=ok,summary=f'summary-marker-{v}',
                                         reason=f'reason-marker-{v}',agent=row,formal_eligible=False))
            continue
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
        c = selection.get('configuration')
        store = DevV2Store(arg('--store'), **({'configuration':{'goal_id': c['goal_id'], 'limits': c['experiment_budget'],
                                                          'wall_seconds': c['wall_seconds']}} if c else {}))
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


@pytest.mark.parametrize('old_settings,new_settings,detail', [
    ({'profile':'r5-fixture-20261031','planner_deployment':{'lock':'old'}},
     {'profile':'r5-fixture-20261031','planner_deployment':{'lock':'new'}},
     'planner_deployment changed'),
    ({'profile':'r4-20261027','planner_deployment':{'lock':'old'}},
     {'profile':'r5-fixture-20261031','planner_deployment':{'lock':'new'}},
     'profile r4-20261027 → r5-fixture-20261031, planner_deployment changed'),
    (None, {'profile':'r5-fixture-20261031'}, 'profile legacy → r5-fixture-20261031'),
    ({'profile':'r4-20261027'}, None, 'profile r4-20261027 → legacy'),
    ({'fixture_telemetry_hz':10}, {'fixture_telemetry_hz':20}, 'fixture_telemetry_hz 10 → 20'),
    (None, {}, 'execution_settings changed'),
])
def test_execution_settings_warning_names_changed_keys(old_settings,new_settings,detail):
    source={'seed':102}
    target={'seed':102}
    if old_settings is not None:source['execution_settings']=old_settings
    if new_settings is not None:target['execution_settings']=new_settings
    rows=dev_fork.checks(source,None,target,carry='code',executed=True,valid=True)
    warnings=[r for r in rows if r['id']=='execution_settings']
    assert warnings==[dict(id='execution_settings',level='warn',
        title='Execution settings differ from the source run: '+detail+'.',
        body='The code will run with the new settings and may behave differently.')]
    assert not ids(rows,'block')


@pytest.mark.parametrize('seal', ['same','changed','missing','prompt_only','combined_only'])
def test_agent_sdk_check_uses_recorded_seals_without_model_input(tmp_path,seal):
    def transform(config):
        if seal=='changed':config['dev_sdk_prompt_sha256']='0'*64
        elif seal=='missing':
            for key in list(config):
                if key.startswith('dev_sdk_'):config.pop(key)
        elif seal in ('prompt_only','combined_only'):
            keep='dev_sdk_prompt_sha256' if seal=='prompt_only' else 'dev_sdk_sha256'
            for key in list(config):
                if key.startswith('dev_sdk_') and key!=keep:config.pop(key)
    s=source_run(tmp_path,agent=True,config_transform=transform)
    source=dev_fork.Source(s.root,s.eid,s.cell,2)
    assert source.system_prompt() is None
    rows=dev_fork.preview(s.root,s.eid,s.cell,2)['checks']
    row=next(r for r in rows if r['id']=='sdk')
    assert row['level']==('warn' if seal in ('changed','missing') else 'ok')
    assert row['title']==('The SDK text the AI sees changed after the source run.' if seal=='changed' else
        'The SDK text of the source attempt was not recorded.' if seal=='missing' else
        'The SDK text the AI sees is the same as in the source attempt.')
    fork=dev_fork.materialize(s.root,tmp_path/'child',dict(experiment_id=s.eid,cell=s.cell,version=2,carry='feedback'))
    record=dev_fork.load(fork)
    assert rows==dev_fork.apply(record,{f'{TASK}:102':dev_fork.preview_config(source.config)}, {},mode='interactive',tasks=[TASK],seeds=[102])


@pytest.mark.parametrize('arm_motion',['short','long'])
def test_fixture_preview_matches_creation_checks(tmp_path,bridge,arm_motion):
    from benchmarks.attention_harness.dev_fixture_tasks import PROFILE
    s=source_run(tmp_path,agent=True,fixture=True,arm_motion=arm_motion)
    shown=dev_fork.preview(s.root,s.eid,s.cell,2)
    preview=dev_fork.preview_config(s.config)
    assert preview['execution_settings']['profile']==PROFILE
    assert preview['execution_settings']['fixture_telemetry_hz']==20
    assert not shown['blocked'] and ids(shown['checks'],'ok')==['scene','sdk']
    assert ids(shown['checks'],'warn')==['excluded']
    body=agent_child_body(s)
    body['selection'].update(tasks=['categorize_condiments'],rules_profile=PROFILE)
    if arm_motion=='long':body['selection']['arm_motion']='long'
    created=bridge(s.root).create(body)
    store=DevV2Store(s.root/created['experiment_id']/'ledger.sqlite3')
    manifest=dev_experiment.verify_manifest(dev_experiment.snapshot(store,created['experiment_id']))
    assert shown['checks']==manifest['fork']['checks']
    config=manifest['configs']['categorize_condiments:102']
    fields=(*dev_fork.SCENE_FIELDS,*dev_fork.GRASP_FIELDS,'execution_settings','planner_source',
        'fixture_scene_contract','fixture_success_condition','scene_verification','dev_sdk_prompt_sha256','dev_sdk_sha256')
    assert {k:preview.get(k) for k in fields}=={k:config.get(k) for k in fields}


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


@pytest.fixture(autouse=True)
def offline_r4_snapshot(r4_deployment):
    """New dev defaults bind an isolated authenticated snapshot."""


def agent_child_body(source, **fork):
    body=child_body(source,**fork)
    selection=body['selection']
    selection.pop('configuration');selection.pop('eval_agent')
    selection.update(dev_mode='agent',goal_key='fork-agent-goal')
    return body


def test_agent_history_cut_at_n_and_fresh_workspace(tmp_path,bridge):
    from benchmarks.attention_harness.dev_agent_workspace import Workspace
    from benchmarks.attention_harness.dev_agent_loop import bootstrap
    s=source_run(tmp_path,agent=True,versions=6)
    shown=dev_fork.preview(s.root,s.eid,s.cell,3)
    assert len(shown['attempts'])==6 and not shown['blocked']
    assert shown['attempts'][2]['executed'] and shown['attempts'][2]['reason']=='eval-marker-3'
    assert any('Attempts 1–3' in text for text in shown['prompt_includes'])
    assert shown['prompt_includes']==[f"policy.py from Attempt 3 · {shown['prompt_includes'][0].split(' · ')[1]}",
        'What each of Attempts 1–3 changed, how it ended, and Eval’s verdict',
        'Help hints received before those attempts · text only, not counted','Eval’s suggestion for Attempt 3']
    assert shown['prompt_includes'][0].endswith(' lines, as version 1')
    assert dev_fork.preview(s.root,s.eid,s.cell,1)['prompt_includes'][1]=='What each of Attempt 1 changed, how it ended, and Eval’s verdict'
    code_only=dev_fork.preview(s.root,s.eid,s.cell,3,'code')['prompt_includes']
    assert len(code_only)==1 and code_only[0].startswith('policy.py from Attempt 3 · ')
    assert not any('previous-run' in t or 'fresh Dev' in t or 'Memory' in t for t in shown['prompt_includes'])
    created=bridge(s.root).create(agent_child_body(s,version=3))
    store=DevV2Store(s.root/created['experiment_id']/'ledger.sqlite3')
    manifest=dev_experiment.verify_manifest(dev_experiment.snapshot(store,created['experiment_id']))
    assert manifest['initial_sources'][TASK]['source']==SOURCE+'# version 3\n'
    assert created['selection']['mode']=='interactive' and not created['cells'][0]['experiment_eligible']
    fork=manifest['fork'];history=dev_fork._json(fork['history'])
    assert [row['version'] for row in history]==[1,2,3]
    assert [row['help'] for row in history]==[[f'help-marker-{v}'] for v in (1,2,3)]
    assert 'reference_suggestion' not in history[0]['eval']
    assert history[2]['eval']['reference_suggestion']=='advice-marker-3'
    assert history[2]['outcome']==dict(valid=True,executed=True,native_success=False)
    workspace=Workspace(tmp_path/'new-agent')
    dev_fork.publish_history(workspace,manifest)
    document=(workspace.ws/'docs/previous-run.md').read_text()
    assert document.startswith('# Earlier run up to Attempt 3')
    assert 'summary-marker-1' in document and 'advice-marker-3' in document
    text='\n'.join(p.read_text() for p in (s.root/created['experiment_id']/'fork').iterdir())+document
    for v in (4,5,6):
        for kind in ('summary','reason','eval','help','advice'):assert f'{kind}-marker-{v}' not in text
    assert 'advice-marker-1' not in text and 'advice-marker-2' not in text
    assert 'hint_human_seconds' not in text
    prompt=bootstrap(workspace,SimpleNamespace(limits={'formal_versions':10},wall_seconds=21600),'reactive_help')
    assert 'Read docs/previous-run.md' in prompt
    assert not (workspace.private/'session.json').exists()
    assert sum(p.stat().st_size for p in (s.root/created['experiment_id']/'fork').iterdir())<100000


def test_agent_code_only_has_no_history_or_feedback(tmp_path,bridge):
    from benchmarks.attention_harness.dev_agent_workspace import Workspace
    s=source_run(tmp_path,agent=True,versions=6)
    created=bridge(s.root).create(agent_child_body(s,version=3,carry='code'))
    store=DevV2Store(s.root/created['experiment_id']/'ledger.sqlite3')
    manifest=dev_experiment.verify_manifest(dev_experiment.snapshot(store,created['experiment_id']))
    assert manifest['fork']['history'] is None and manifest['fork']['feedback'] is None
    workspace=Workspace(tmp_path/'new-agent')
    dev_fork.publish_history(workspace,manifest)
    assert not (workspace.ws/'docs/previous-run.md').exists()
    files=list((s.root/created['experiment_id']/'fork').iterdir())
    assert {p.name for p in files}=={'source.py','fork.json'}
    text='\n'.join(p.read_text() for p in files)
    assert not any(f'{kind}-marker-' in text for kind in ('summary','reason','eval','help','advice'))


@pytest.mark.parametrize('mode',['experiment','diagnostic'])
def test_agent_fork_requires_interactive(tmp_path,bridge,mode):
    s=source_run(tmp_path,agent=True)
    body=agent_child_body(s);body['selection']['mode']=mode
    with pytest.raises(ValueError,match='interactive'):bridge(s.root).create(body)
    assert not (s.root/eid_for(body['selection']['request_key'])/'fork').exists()


def test_agent_fork_preview_endpoint(tmp_path,bridge):
    s=source_run(tmp_path,agent=True)
    (s.root/s.eid/'authorization.json').write_text('{}')
    value,kind=bridge(s.root).get(f'/api/dev-v2/experiments/{s.eid}/fork-preview?cell={s.cell}&version=1&carry=feedback')
    assert kind=='application/json' and value['attempts'][0]['executed'] and not value['blocked']


def bind_memory_fork(f,carry='feedback'):
    request=dict(experiment_id='devexp-'+'a'*24,cell='b'*64,version=3,carry=carry)
    record=immutable(f.root/'fork.json',dict(schema=dev_fork.SCHEMA,**request,source_cells=[request['cell'],'c'*64]))
    with f.store.tx() as db:
        experiment=f.store.get(db,'experiments','exp')
        experiment['selection']['fork']=dict(request,record=record)
        f.store.put(db,'experiments','exp',experiment)
    return request


@pytest.mark.parametrize('carry',['code','feedback'])
def test_agent_fork_memory_excludes_entire_source_run_before_publication(agent_memory_fixture,carry):
    f=agent_memory_fixture;request=bind_memory_fork(f,carry)
    origins={'n0':dict(source_run_id='run:other'),'n1':dict(origin=dict(experiment_id='other',cell='other'))}
    for v in range(1,7):
        row=dict(f.gateway.rows[0],memory_id=f'source-{v}',guidance=f'future-memory-marker-{v}')
        f.gateway.rows.append(row)
        origins[row['memory_id']]=(dict(source_run_id='run:'+request['cell']) if v%2 else
                                   dict(origin=dict(experiment_id=request['experiment_id'],cell='c'*64,help_version=v)))
    f.gateway.provenance=lambda mid:origins[mid]
    f.memory.start()
    assert {e['memory_id'] for e in f.memory.packet['entries']}=={'n0','n1'}
    assert {g['memory_id'] for g in f.gateway.grants.values()}=={'n0','n1'}
    text=(f.ws.ws/'memory/v1.json').read_text()+(f.ws.private/'memory/v1/memory-input.json').read_text()
    assert 'future-memory-marker' not in text
    # Restoring a cached generation rechecks origin rather than trusting old delivery.
    origins['n0']=dict(source_run_id='run:'+request['cell'])
    with pytest.raises(PermissionError):f.memory.start()


def test_agent_fork_memory_missing_provenance_stops_delivery(agent_memory_fixture):
    f=agent_memory_fixture;bind_memory_fork(f)
    f.gateway.provenance=lambda mid:{}
    with pytest.raises(PermissionError,match='provenance'):f.memory.start()
    assert not (f.ws.ws/'memory/v1.json').exists()
    assert not f.gateway.grants


def test_agent_history_screens_prose(tmp_path):
    s=source_run(tmp_path,agent=True)
    source=dev_fork.Source(s.root,s.eid,s.cell,2)
    source.versions[1]['summary']='reward SECRET /tmp/example'
    assert source.history()[0]['summary']=='[withheld]'


def test_agent_fork_start_publishes_code_and_cut_history_before_any_session(tmp_path,bridge,monkeypatch):
    from benchmarks.attention_harness import dev_agent_loop,dev_agent_isolation
    s=source_run(tmp_path,agent=True,versions=6)
    created=bridge(s.root).create(agent_child_body(s,version=3))
    store=DevV2Store(s.root/created['experiment_id']/'ledger.sqlite3')
    exp=dev_experiment.snapshot(store,created['experiment_id']);manifest=dev_experiment.verify_manifest(exp)
    inspected=[]
    def preflight(ws,private,**kwargs):
        inspected.append(ws)
        assert (ws/'work/main.py').read_text()==SOURCE+'# version 3\n'
        text=(ws/'docs/previous-run.md').read_text()
        assert '# Earlier run up to Attempt 3' in text and 'help-marker-3' in text
        assert not any(f'-marker-{n}' in text for n in (4,5,6))
        assert not (private/'session.json').exists()
        raise dev_agent_isolation.IsolationUnavailable('offline_preflight_stop')
    monkeypatch.setattr(dev_agent_isolation,'verify_runtime',preflight)
    monkeypatch.setattr(dev_agent_loop,'CodexAgentSession',lambda *a,**kw:pytest.fail('must not start Codex'))
    class Listener:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def bind(self,*a):pass
        def listen(self):pass
        def getsockname(self):return ('127.0.0.1',12345)
    monkeypatch.setattr(dev_agent_loop.socket,'socket',Listener)
    result=dev_agent_loop.run_agent_cell(store,exp,manifest,exp['cells'][0]['key'],runner_factory=lambda **kw:pytest.fail('must not start simulator'))
    assert inspected and result['reason']=='isolation_unavailable'


def test_missing_incoming_help_blocks_feedback_but_allows_code_only(tmp_path):
    s=source_run(tmp_path,agent=True,versions=6)
    (s.dir/'v2/help.json').unlink()
    shown=dev_fork.preview(s.root,s.eid,s.cell,3)
    assert shown['blocked'] and 'source_history' in ids(shown['checks'],'block')
    assert not dev_fork.preview(s.root,s.eid,s.cell,3,'code')['blocked']


def test_agent_frozen_bundle_stays_under_100kb_without_truncation(tmp_path):
    s=source_run(tmp_path,agent=True,versions=1,source_padding='# large source\n'*8000)
    shown=dev_fork.preview(s.root,s.eid,s.cell,1,'code')
    assert shown['blocked'] and 'fork_size' in ids(shown['checks'],'block')
    with pytest.raises(dev_fork.ForkTooLarge):
        dev_fork.materialize(s.root,tmp_path/'child',dict(experiment_id=s.eid,cell=s.cell,version=1,carry='code'))
    assert not (tmp_path/'child/fork').exists()
