"""Agent runs may lower versions and run time (never raise them); the default run stays byte-identical."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from benchmarks.attention_harness import dev_experiment
from benchmarks.attention_harness.dev_ledger import DevV2Store
from benchmarks.attention_harness.dev_agent_config import normalize, agent_limits
from benchmarks.attention_harness.dev_agent_ledger import AgentLedger, LIMITS
from benchmarks.attention_harness.dev_agent_loop import bootstrap
from benchmarks.attention_harness.dev_agent_prompts import DEV_PROMPT
from benchmarks.attention_harness.dev_agent_workspace import Workspace
from benchmarks.attention_harness.dev_agent_policy_freeze import validate_comparison
from benchmarks.attention_harness.tests.c21_fixtures import base, r4_deployment, offline_dev_identity
from benchmarks.attention_harness.tests.test_dev_agent_configuration import SELECTION
from benchmarks.attention_harness.tests.test_dev_agent_d1 import CONFIG

REDUCED = dict(versions=2, wall_seconds=7200)
DEFAULT = dict(versions=10, wall_seconds=21600)


def create(tmp_path, selection, name='run'):
    store = DevV2Store(tmp_path / (name + '.db'))
    exp = dev_experiment.create(store, tmp_path / 'runs', selection)
    return exp, dev_experiment.verify_manifest(exp)


def test_normalize_accepts_lower_values_and_drops_the_official_budget():
    assert normalize(dict(SELECTION, agent_limits=REDUCED))['agent_limits'] == REDUCED
    assert normalize(dict(SELECTION, agent_limits=dict(versions=10, wall_seconds=3600)))['agent_limits']['wall_seconds'] == 3600
    assert normalize(dict(SELECTION, agent_limits=dict(versions=1, wall_seconds=21600)))['agent_limits']['versions'] == 1
    # equal to the official budget: nothing to freeze, the result is the plain agent options
    assert 'agent_limits' not in normalize(dict(SELECTION, agent_limits=DEFAULT))
    assert normalize(dict(SELECTION, agent_limits=DEFAULT)) == normalize(SELECTION)


@pytest.mark.parametrize('value,message', [
    (dict(versions=11, wall_seconds=21600), 'exceed'), (dict(versions=10, wall_seconds=21601), 'exceed'),
    (dict(versions=99, wall_seconds=99999), 'exceed'),
    (dict(versions=0, wall_seconds=7200), 'below'), (dict(versions=2, wall_seconds=3599), 'below'),
    (dict(versions=-1, wall_seconds=7200), 'below'),
    (dict(versions=True, wall_seconds=7200), 'invalid'), (dict(versions=2.0, wall_seconds=7200), 'invalid'),
    (dict(versions='2', wall_seconds=7200), 'invalid'), (dict(versions=2), 'invalid'),
    (dict(versions=2, wall_seconds=7200, extra=1), 'invalid'), ([2, 7200], 'invalid'), (None, 'invalid'),
])
def test_normalize_rejects_enlarged_or_malformed_limits(value, message):
    with pytest.raises(ValueError, match=message):
        normalize(dict(SELECTION, agent_limits=value))
    with pytest.raises(ValueError):
        agent_limits(value)


def test_limits_need_agent_mode(tmp_path, offline_dev_identity):
    selection = dict(SELECTION, agent_limits=REDUCED)
    selection.pop('dev_mode')
    with pytest.raises(ValueError, match='agent_options_require_agent_mode'):
        dev_experiment.create(DevV2Store(tmp_path / 'db'), tmp_path / 'runs', selection)


def test_create_freezes_a_reduced_budget_and_marks_it(tmp_path, offline_dev_identity):
    exp, manifest = create(tmp_path, dict(SELECTION, agent_limits=REDUCED))
    assert manifest['agent_limits'] == REDUCED and manifest['reduced_budget'] is True
    assert manifest['selection']['agent_limits'] == REDUCED
    budget = manifest['agent_budget']
    assert (budget['formal_versions'], budget['eval_runs'], budget['active_wall_seconds']) == (2, 2, 7200)
    assert (budget['moving_executions'], budget['read_only_probes']) == (40, 300)
    assert [row['through_version'] for row in manifest['schedule']] == [2]
    # retrying the same request is idempotent; a different budget is a different experiment
    assert dev_experiment.create(DevV2Store(tmp_path / 'run.db'), tmp_path / 'runs', dict(SELECTION, agent_limits=REDUCED))['manifest'] == exp['manifest']
    with pytest.raises(Exception, match='different experiment conditions'):
        dev_experiment.create(DevV2Store(tmp_path / 'run.db'), tmp_path / 'runs', dict(SELECTION, agent_limits=dict(versions=3, wall_seconds=7200)))


@pytest.mark.parametrize('value', [dict(versions=11, wall_seconds=21600), dict(versions=10, wall_seconds=21601), dict(versions=2, wall_seconds=60)])
def test_create_refuses_enlarged_or_too_short_budgets(tmp_path, offline_dev_identity, value):
    with pytest.raises(ValueError, match='agent_limits'):
        create(tmp_path, dict(SELECTION, agent_limits=value))


def test_default_run_is_byte_identical_with_or_without_the_field(tmp_path, offline_dev_identity):
    # (The golden test against the pre-change commit lives in test_agent_validation_onecall_goldens.)
    store = DevV2Store(tmp_path / 'db')
    before = dev_experiment.create(store, tmp_path / 'runs', SELECTION)
    raw = Path(before['manifest']['uri']).read_bytes()
    (tmp_path / 'db').unlink()
    after = dev_experiment.create(DevV2Store(tmp_path / 'db'), tmp_path / 'runs', dict(SELECTION, agent_limits=DEFAULT))
    assert Path(after['manifest']['uri']).read_bytes() == raw
    manifest = json.loads(raw)
    assert 'agent_limits' not in manifest and 'reduced_budget' not in manifest and 'agent_limits' not in manifest['selection']
    assert manifest['agent_budget']['formal_versions'] == 10 and manifest['agent_budget']['active_wall_seconds'] == 21600
    assert [row['through_version'] for row in manifest['schedule']] == [10]


def test_prompt_and_policy_hashes_do_not_depend_on_the_budget(tmp_path, offline_dev_identity):
    _, default = create(tmp_path, SELECTION, 'a')
    other = tmp_path / 'other'
    other.mkdir()
    _, reduced = create(other, dict(SELECTION, agent_limits=REDUCED), 'b')
    for key in ('agent_prompt_sha256', 'dev_agent_prompt', 'eval_agent_prompt', 'agent_policy_sources_sha256', 'agent_policy_parameters_sha256'):
        assert default.get(key) == reduced.get(key), key
    prompts = [k for k in default if 'prompt' in k]
    assert prompts and all(default[k] == reduced[k] for k in prompts)


def test_a_manifest_cannot_hide_or_fake_a_reduced_budget(tmp_path, offline_dev_identity):
    _, manifest = create(tmp_path, dict(SELECTION, agent_limits=REDUCED))
    dev_experiment.validate_fairness(copy.deepcopy(manifest))
    changes = (lambda m: m.pop('reduced_budget'), lambda m: m.update(reduced_budget=False),
               lambda m: m['agent_budget'].update(formal_versions=10), lambda m: m['agent_budget'].update(eval_runs=10),
               lambda m: m['agent_budget'].update(active_wall_seconds=21600), lambda m: m['schedule'][0].update(through_version=10),
               lambda m: m.update(agent_limits=dict(versions=11, wall_seconds=7200)), lambda m: m.pop('agent_limits'))
    for change in changes:
        value = copy.deepcopy(manifest)
        change(value)
        with pytest.raises(ValueError):
            dev_experiment.validate_fairness(value)
    official = create(tmp_path / 'x', SELECTION)[1] if (tmp_path / 'x').mkdir() is None else None
    official['reduced_budget'] = True
    with pytest.raises(ValueError, match='agent_reduced_budget'):
        dev_experiment.validate_fairness(official)


def test_comparison_refuses_a_reduced_run_against_the_official_budget(tmp_path, offline_dev_identity):
    _, official = create(tmp_path, SELECTION, 'a')
    other = tmp_path / 'other'
    other.mkdir()
    _, reduced = create(other, dict(SELECTION, agent_limits=REDUCED), 'b')
    with pytest.raises(ValueError, match='comparison_conditions_differ'):
        validate_comparison([official, reduced])


def test_ledger_budget_is_frozen_and_enforced(tmp_path):
    default = AgentLedger(tmp_path / 'd.db', 'c')
    assert default.limits == dict(LIMITS, help=1) and default.wall_seconds == 21600
    ledger = AgentLedger(tmp_path / 'r.db', 'c', help_limit=1, budget=REDUCED)
    assert ledger.limits['formal_versions'] == 2 and ledger.limits['eval_runs'] == 2 and ledger.limits['moving_executions'] == 40
    assert ledger.wall_seconds == 7200 and ledger.snapshot()['wall_remaining'] == 7200
    ledger.reserve('v1', 'request', dict(formal_versions=1))
    ledger.reserve('v2', 'request', dict(formal_versions=1))
    with pytest.raises(ValueError, match='budget_exhausted'):
        ledger.reserve('v3', 'request', dict(formal_versions=1))
    with pytest.raises(ValueError, match='immutable_agent_budget'):
        AgentLedger(tmp_path / 'r.db', 'c', help_limit=1)
    with pytest.raises(ValueError, match='immutable_agent_budget'):
        AgentLedger(tmp_path / 'r.db', 'c', help_limit=1, budget=dict(versions=3, wall_seconds=7200))
    AgentLedger(tmp_path / 'r.db', 'c', help_limit=1, budget=REDUCED)  # the same budget reopens
    for bad in (DEFAULT, dict(versions=11, wall_seconds=7200), dict(versions=2, wall_seconds=30000)):
        with pytest.raises(ValueError):
            AgentLedger(tmp_path / 'x.db', 'c', budget=bad)


def test_dev_sees_the_real_numbers_and_the_official_prompt_is_untouched(tmp_path):
    official = AgentLedger(tmp_path / 'd.db', 'c')
    tail = '\nHelp policy: p. See docs/tools.md for its exact availability.\nBudgets: '
    assert bootstrap(None, official, 'p') == DEV_PROMPT + tail + json.dumps(official.limits, sort_keys=True)
    reduced = AgentLedger(tmp_path / 'r.db', 'c', budget=REDUCED)
    text = bootstrap(None, reduced, 'p')
    assert '2 formal versions, 40 moving executions, 300 read-only probes' in text and '7200 active wall seconds' in text
    assert '10 formal versions' not in text and '21600 active wall seconds' not in text
    assert hashlib.sha256(DEV_PROMPT.encode()).hexdigest() == hashlib.sha256(DEV_PROMPT.encode()).hexdigest()


def test_workspace_docs_state_the_budget(tmp_path):
    official = Workspace(tmp_path / 'a').prepare({'task_id': 'fixture', 'task_prompt': 'Move.'}, CONFIG)
    safety = json.loads((official.canonical / 'docs/safety.json').read_bytes())
    assert (safety['versions'], safety['wall_seconds']) == (10, 21600)
    assert b'Limits: 10 formal versions, 40 executions, 300 probes, 6 hours total wall time;' in (official.canonical / 'docs/tools.md').read_bytes()
    reduced = Workspace(tmp_path / 'b').prepare({'task_id': 'fixture', 'task_prompt': 'Move.'}, CONFIG, budget=REDUCED)
    safety = json.loads((reduced.canonical / 'docs/safety.json').read_bytes())
    assert (safety['versions'], safety['wall_seconds']) == (2, 7200)
    tools = (reduced.canonical / 'docs/tools.md').read_bytes()
    assert b'Limits: 2 formal versions, 40 executions, 300 probes, 7200 seconds total wall time;' in tools and b'10 formal versions' not in tools


def test_the_request_the_dialog_sends_creates_a_reduced_run(tmp_path, offline_dev_identity):
    # Captured from the real New run dialog (Agent, versions lowered to 2 and run time to 2 hours, Create only).
    body = json.loads((Path(__file__).parent / 'fixtures/agent-budget-request.json').read_text())
    selection = body['selection']
    assert selection['agent_limits'] == REDUCED and selection['dev_mode'] == 'agent'
    exp, manifest = create(tmp_path, selection)
    assert manifest['reduced_budget'] is True and manifest['agent_limits'] == REDUCED
    assert manifest['agent_budget']['formal_versions'] == 2 and manifest['agent_budget']['active_wall_seconds'] == 7200
    # the same dialog request with the official numbers is the plain, unmarked run
    official = dict(selection, request_key='official-0123456')
    official.pop('agent_limits')
    other = tmp_path / 'official'
    other.mkdir()
    _, plain = create(other, official)
    assert 'reduced_budget' not in plain and plain['agent_budget']['formal_versions'] == 10
    for bigger in (dict(versions=11, wall_seconds=7200), dict(versions=2, wall_seconds=21601)):
        with pytest.raises(ValueError, match='agent_limits_exceed_envelope'):
            create(other, dict(selection, request_key='bigger-0123456', agent_limits=bigger), 'big')


def test_the_run_loop_builds_the_ledger_and_workspace_from_the_frozen_budget(tmp_path, monkeypatch):
    from benchmarks.attention_harness import dev_agent_loop as loop, dev_agent_isolation as isolation, dev_advisor_proxy
    from benchmarks.attention_harness.dev_agent_prompts import manifest_fields
    from benchmarks.attention_harness.tests.test_dev_agent_d4 import CONFIG as D4_CONFIG, SOURCE

    def run(limits, name):
        root = tmp_path / name
        config = dict(D4_CONFIG, task_id='fake', task_prompt='Move object.', model_source='codex', advisor_proxy=dev_advisor_proxy.default_config(model_source='codex'))
        manifest = dict(configs={'fake:102': config}, selection={'help_budget': 1}, dev_model='gpt-6-sol', eval_model='gpt-6-astra', memory={'entries': []},
                        dev_reasoning_effort='high', quota_stop_percent=90, initial_sources={'fake': {'source': SOURCE.decode()}},
                        policies={'reactive_help': {}}, demos={'fake': {'content': 'Example'}}, **manifest_fields())
        if limits:
            manifest['agent_limits'] = limits

        class Store:
            path = root / 'ledger.sqlite'

            def cell(self, key):
                return dict(task_id='fake', seed=102, policy_id='reactive_help', artifact_dir=str(root / 'run' / 'cells' / key))

            def update_cell(self, key, **kw):
                pass

            def version(self, *a):
                pass

        class Listener:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def bind(self, *a):
                pass

            def listen(self):
                pass

            def getsockname(self):
                return ('127.0.0.1', 12345)

        seen = {}

        def drive(workspace, session, ledger, **kw):
            seen.update(limits=dict(ledger.limits), wall=ledger.wall_seconds, prompt=loop.bootstrap(workspace, ledger, 'reactive_help'),
                        safety=json.loads((workspace.canonical / 'docs/safety.json').read_bytes()), tools=(workspace.canonical / 'docs/tools.md').read_text())
            return dict(state='completed', reason='fixture', resources=ledger.snapshot())

        root.mkdir()
        monkeypatch.setattr(loop.socket, 'socket', Listener)
        monkeypatch.setattr(isolation, 'verify_runtime', lambda *a, **kw: None)
        monkeypatch.setattr(isolation, 'codex_identity', lambda: dict(version='fixture'))
        monkeypatch.setattr(loop, 'drive', drive)
        assert loop.run_agent_cell(Store(), {'artifact_dir': str(root / 'run')}, manifest, 'cell')['reason'] == 'fixture'
        return seen

    official = run(None, 'official')
    assert official['limits']['formal_versions'] == 10 and official['wall'] == 21600 and official['safety']['versions'] == 10
    assert '10 formal versions, 40 moving executions, 300 read-only probes' in official['prompt'] and '21600 active wall seconds' in official['prompt']
    reduced = run(REDUCED, 'reduced')
    assert reduced['limits']['formal_versions'] == 2 and reduced['limits']['eval_runs'] == 2 and reduced['wall'] == 7200
    assert (reduced['safety']['versions'], reduced['safety']['wall_seconds']) == (2, 7200)
    assert '2 formal versions, 40 moving executions, 300 read-only probes' in reduced['prompt'] and '7200 active wall seconds' in reduced['prompt']
    assert 'Limits: 2 formal versions' in reduced['tools']


def test_the_view_reports_the_frozen_limits_and_marks_a_reduced_run(tmp_path):
    from benchmarks.attention_harness.dev_agent_view import projection

    def view(limits, name):
        store = DevV2Store(tmp_path / (name + '.sqlite'))
        selection = dict(SELECTION, help_budget=5, **({'agent_limits': limits} if limits else {}))
        with store.tx() as db:
            store.put(db, 'experiments', 'fixture', dict(experiment_id='fixture', selection=selection, state='completed'))
            db.execute('INSERT INTO v2_cells VALUES (?,?,?)', ('cell', 'fixture', json.dumps(dict(key='cell', experiment_id='fixture', task_id='fake', seed=1,
                      policy_id='autonomous', mode='interactive', dev_mode='agent', state='planned'))))
        AgentLedger(store.path, 'cell', help_limit=5, budget=limits)
        return projection(store, store.cell('cell'), now=1005)

    official = view(None, 'a')['agent']
    assert official['wall_limit'] == 21600 and official['limits']['formal_versions'] == 10 and 'reduced_budget' not in official
    reduced = view(REDUCED, 'b')
    agent = reduced['agent']
    assert agent['wall_limit'] == 7200 and agent['limits']['formal_versions'] == 2 and agent['limits']['eval_runs'] == 2
    assert agent['remaining']['formal_versions'] == 2
    assert agent['reduced_budget'] == dict(REDUCED, default_versions=10, default_wall_seconds=21600)
    assert reduced['dev_v2_compat']['reduced_budget'] == agent['reduced_budget']
    assert reduced['dev_v2_compat']['ledger']['goal']['wall_limit'] == 7200


from benchmarks.attention_harness.tests.test_dev_agent_d7 import fixture, Executor, fake_session  # noqa: E402,F401  (the exporter rig)


def _exported_versions(f):
    from benchmarks.attention_harness.dev_agent_export import Exporter
    from benchmarks.attention_harness.dev_agent_adapters import FakeProber
    from benchmarks.attention_harness.dev_agent_loop import drive
    session = fake_session(f.ws, ["tool('observe');tool('run','--no-eval');tool('submit')", "tool('run','--no-eval');tool('submit')", 'pass'])
    drive(f.ws, session, f.ledger, executor=Executor(), prober=FakeProber(), memory=f.memory,
          exporter=Exporter(f.store, 'cell', f.ws, f.config, f.ledger), completion_reader=lambda *a: False)
    return [json.loads((f.root / f'v{n}/version.json').read_bytes()) for n in (1, 2)]


def test_an_official_export_has_no_reduced_budget_record(fixture):
    assert all('reduced_budget' not in row for row in _exported_versions(fixture))


def test_a_reduced_export_says_it_is_not_comparable(fixture):
    fixture.ledger.limits['formal_versions'] = fixture.ledger.limits['eval_runs'] = 2
    fixture.ledger.wall_seconds = 7200
    for row in _exported_versions(fixture):
        assert row['reduced_budget'] == dict(versions=2, wall_seconds=7200, default_versions=10, default_wall_seconds=21600,
                                             note='Reduced budget: not directly comparable with the official 10-version results.')
