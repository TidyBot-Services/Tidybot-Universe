"""Deployment bridge contracts using temporary ledgers and in-memory HTTP only.

These are engineering fixtures, never human approval, paid model or native runs.
"""
import copy
import hashlib
import io
import json
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness import daily_dev_v2 as daily, dev_experiment, ui_server
from benchmarks.attention_harness.core.dev_store import DevBudgetExhausted, DevConflict
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.dev_ledger import DevV2Store, RESOURCES, GOAL
from benchmarks.attention_harness.dev_loop import process_identity
from benchmarks.attention_harness.dev_main_bridge import DevMainBridge


class OfflineServer:
    def __init__(self, address, handler):
        self.handler = handler
        self.server_port = address[1]
        self.served = self.closed = False

    def serve_forever(self):
        self.served = True

    def server_close(self):
        self.closed = True


def request(server, path, body=None, token=None, human=None, headers=None):
    """Exercise the production HTTP parser and response writer without a socket."""
    raw = b'' if body is None else json.dumps(body).encode()
    supplied = dict(headers or {})
    if body is not None:
        supplied.update({'Content-Type': 'application/json', 'Content-Length': str(len(raw))})
    if token:
        supplied['X-Attention-Token'] = token
    if human:
        supplied['X-Attention-Human-Token'] = human
    head = f"{'GET' if body is None else 'POST'} {path} HTTP/1.0\r\nHost: localhost\r\n"
    head += ''.join(f'{k}: {v}\r\n' for k, v in supplied.items()) + '\r\n'

    class Connection:
        def __init__(self):
            self.input = io.BytesIO(head.encode() + raw)
            self.output = bytearray()

        def makefile(self, *args):
            return self.input

        def sendall(self, data):
            self.output.extend(data)

    connection = Connection()
    server.handler(connection, ('127.0.0.1', 1), server)
    wire_head, payload = bytes(connection.output).split(b'\r\n\r\n', 1)
    status = int(wire_head.splitlines()[0].split()[1])
    result_headers = dict(line.decode().split(': ', 1) for line in wire_head.split(b'\r\n')[1:])
    return status, payload, result_headers


def test_original_host_argv_parses_without_opening_host_resources(monkeypatch):
    # Frozen argv from C/host-pre-switch.json; parsing stops before any host IO.
    argv = ['/home/truares/.cache/tidybot-attention/venv/bin/python',
     '-B',
     '-u',
     '/home/truares/Documents/Codex/2026-10-14/goal-attentionbench-robocasa365-dev-v2-home/outputs/serve_daily.py',
     '--store',
     '/home/truares/Documents/Codex/2026-10-06/docs-live-human-ui-validation-plan/work/mac-ui-debug-20261006-154731/attention.sqlite3',
     '--artifact-root',
     '/home/truares/Documents/Codex/2026-10-06/docs-live-human-ui-validation-plan/work/mac-ui-debug-20261006-154731/artifacts',
     '--run-catalog',
     '/home/truares/Documents/Codex/2026-10-12/new-chat-2/outputs/daily-v2-33/catalog.json',
     '--host',
     '127.0.0.1',
     '--port',
     '8769',
     '--robosuite-url',
     'http://127.0.0.1:8082',
     '--camera-name',
     'agentview',
     '--dev-v2-root',
     '/home/truares/Documents/Codex/2026-10-14/goal-attentionbench-robocasa365-dev-v2-home/outputs/daily-experiments',
     '--dev-v2-per-run-budget']
    parsed = []
    parse = ui_server.argparse.ArgumentParser.parse_args

    class ParsedOnly(BaseException):
        pass

    def stop_after_parse(parser, *args, **kw):
        parsed.append(parse(parser, *args, **kw))
        raise ParsedOnly

    monkeypatch.setattr(ui_server.argparse.ArgumentParser, 'parse_args', stop_after_parse)
    monkeypatch.setattr(sys, 'argv', argv[3:])
    with pytest.raises(ParsedOnly):
        ui_server.main()
    assert str(parsed[0].dev_v2_root) == argv[argv.index('--dev-v2-root') + 1]
    assert parsed[0].dev_v2_per_run_budget is True
    assert parsed[0].dev_v2_authorization is None
    assert parsed[0].port == 8769 and parsed[0].camera_name == 'agentview'


@pytest.mark.parametrize('per_run', [False, True])
def test_main_initializes_daily_with_catalog_and_authorization(tmp_path, monkeypatch, per_run):
    store = AttentionStore(tmp_path / 'ui.sqlite')
    authorization = tmp_path / 'authorization.json'
    authorization.write_text(json.dumps({'deadline_epoch': time.time() + 300}))
    catalog = tmp_path / 'catalog.json'
    catalog.write_text('{}')  # Catalog loading is independently tested by ui_launch.
    profiles = [{'id': 'controlled-frozen-profile', 'suite': 'robosuite', 'task': 'cube_lift',
                 'seed': 101, 'execution_target': 'robosuite_sim'}]
    monkeypatch.setattr(ui_server, 'load_catalog', lambda path: profiles if path == catalog else None)
    bridges, servers = [], []
    original = daily.DailyDevV2

    def bridge(*args, **kw):
        value = original(*args, **kw)
        bridges.append(value)
        return value

    def server(*args):
        value = OfflineServer(*args)
        servers.append(value)
        return value

    monkeypatch.setattr(daily, 'DailyDevV2', bridge)
    monkeypatch.setattr(ui_server, 'ThreadingHTTPServer', server)
    argv = ['ui_server', '--store', str(store.path), '--artifact-root', str(tmp_path),
            '--run-catalog', str(catalog), '--port', '18769', '--dev-v2-root', str(tmp_path / 'daily'),
            '--dev-v2-authorization', str(authorization)]
    if per_run:
        argv.append('--dev-v2-per-run-budget')
    monkeypatch.setattr(sys, 'argv', argv)
    ui_server.main()
    assert len(bridges) == len(servers) == 1
    assert bridges[0].profiles == profiles and bridges[0].root == tmp_path / 'daily'
    assert bridges[0].authorization == json.loads(authorization.read_bytes())
    assert bridges[0].per_run_budget is per_run
    assert servers[0].served and servers[0].closed
    status, raw, _ = request(servers[0], '/api/run-options')
    assert status == 200 and json.loads(raw)['dev_v2']['formal_eligible'] is False
    assert json.loads(raw)['dev_v2']['matrix'][0]['entry'] == profiles[0]['id']
    assert request(servers[0], '/api/dev-v2/experiments')[1] == b'[]'


@pytest.mark.parametrize('options', [{'dev_v2_per_run_budget': True}, {'dev_v2_authorization': Path('missing')}])
def test_unbound_daily_flags_fail_before_store_access(tmp_path, options):
    with pytest.raises(ValueError, match='require --dev-v2-root'):
        ui_server.create_server(store=object(), **options)


def test_explicit_shared_goal_bridge_cannot_be_replaced_by_daily_root(tmp_path):
    with pytest.raises(ValueError, match='existing goal bridge'):
        ui_server.create_server(store=object(), dev_v2_bridge=object(), dev_v2_root=tmp_path / 'daily')
    assert not (tmp_path / 'daily').exists()


@pytest.fixture
def experiment(tmp_path):
    # Actual offline materialization subprocess, no model, worker or simulator.
    bridge = daily.DailyDevV2(tmp_path / 'daily', [], per_run_budget=True)
    budget = dict(daily.DEFAULT_CELL)
    body = dict(start=False, selection=dict(request_key='controlled-daily-compatibility',
        tasks=['open_cabinet'], seeds=[101], policies=['autonomous'], mode='experiment', help_budget=0,
        eval_agent={'enabled': True, 'model': 'parcc/Qwen'},
        configuration=dict(goal_id='controlled-daily-goal', experiment_budget=budget,
            cell_budget=budget, max_iterations=1, wall_seconds=300)))
    e = bridge.create(body)
    return bridge, body, e, bridge.store(e['experiment_id'])


def test_daily_create_ignores_main_ui_human_wait_without_loosening_selection(tmp_path, monkeypatch):
    # Fields and budgets from the captured New run request on the daily preview.
    budget = dict(advisor_calls=2, control_steps=28800, dev_calls=3, eval_calls=3,
                  execution_seconds=6400, executions=3, help_count=2, help_credits=2,
                  help_wait_seconds=300, input_tokens=190000, model_seconds=600,
                  output_tokens=30000, provider_tokens=220000, sdk_calls=960)
    selection = dict(configuration=dict(cell_budget=budget, experiment_budget=budget,
                                        goal_id='daily-v2-1792342718933', max_iterations=3,
                                        wall_seconds=14400),
                     eval_agent={'enabled': True, 'model': 'parcc/Qwen'}, help_budget=2,
                     human_wait_seconds=60, mode='experiment', policies=['autonomous'],
                     request_key='4270f39e-d30f-4010-be88-2ea21dcdbaec', seeds=[101],
                     tasks=['pick_place_counter_to_sink'])
    body = {'selection': selection, 'start': False}
    bridge = daily.DailyDevV2(tmp_path / 'daily', [], per_run_budget=True)

    class MaterializationReached(Exception):
        pass

    def stop_before_materialization(argv, **kwargs):
        saved = json.loads(Path(argv[argv.index('--selection') + 1]).read_bytes())
        dev_experiment.validate_selection_fields(saved)  # create's exact field gate
        assert saved == {k: v for k, v in selection.items() if k != 'human_wait_seconds'}
        raise MaterializationReached

    monkeypatch.setattr(daily.subprocess, 'run', stop_before_materialization)
    with pytest.raises(MaterializationReached):
        bridge.create(body)
    request_file = next((tmp_path / 'daily').glob('devexp-*/selection.json'))
    assert 'human_wait_seconds' not in json.loads(request_file.read_bytes())
    assert 'human_wait_seconds' in selection  # caller input remains intact

    unknown = copy.deepcopy(body)
    unknown['selection']['unexpected_field'] = True
    with pytest.raises(ValueError, match='bounded explicit experiment selection required'):
        bridge.create(unknown)
    assert json.loads(request_file.read_bytes()) == {k: v for k, v in selection.items() if k != 'human_wait_seconds'}

    planner = copy.deepcopy(body)
    planner['selection']['planner_features'] = {'human_wait_seconds': 60}
    with pytest.raises(ValueError, match='bounded explicit experiment selection required'):
        bridge.create(planner)


@pytest.mark.parametrize('versions,help,model_seconds,wall_seconds',
                         [(5, 5, 1800, 21600), (3, 2, 600, 14400)])
def test_daily_ui_create_reaches_first_cell_without_v3_runtime_fields(
        tmp_path, monkeypatch, versions, help, model_seconds, wall_seconds):
    from openarm365_dev_v2 import dev_config

    # Materialize through the real create function in this process so the
    # missing sandbox model copy can be replaced by a test-only identity stub.
    monkeypatch.setattr(dev_config, 'model_sources', lambda *args, **kwargs: {})
    def materialize(argv, **kwargs):
        def argument(name):
            return Path(argv[argv.index(name) + 1])
        selection = json.loads(argument('--selection').read_bytes())
        config = selection['configuration']
        store = DevV2Store(argument('--store'), configuration=dict(
            goal_id=config['goal_id'], limits=config['experiment_budget'],
            wall_seconds=config['wall_seconds']))
        created = dev_experiment.create(store, argument('--artifacts'), selection)
        argument('--output').write_text(json.dumps(created))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(daily.subprocess, 'run', materialize)

    bridge = daily.DailyDevV2(tmp_path / 'daily', [], per_run_budget=True)
    budget = dict(daily.DEFAULT_CELL)
    budget.update(dev_calls=versions, executions=versions, eval_calls=versions,
                  advisor_calls=help, help_count=help, help_credits=help,
                  model_seconds=model_seconds)
    selection = dict(request_key='daily-ui-default-run', tasks=['open_cabinet'],
                     seeds=[102], policies=['autonomous'], mode='experiment',
                     help_budget=help, human_wait_seconds=300,
                     eval_agent={'enabled': True, 'model': 'parcc/Qwen'},
                     configuration=dict(goal_id='daily-v2-1792349910212',
                                        experiment_budget=budget, cell_budget=budget,
                                        max_iterations=versions, wall_seconds=wall_seconds))
    experiment = bridge.create(dict(selection=selection, start=False))
    manifest = json.loads(Path(experiment['manifest']['uri']).read_bytes())
    assert manifest['schema'] == 'attentionbench.dev-experiment.v2'
    assert 'model_reserve_seconds' not in manifest
    assert 'planner_features' not in manifest
    assert dev_experiment.slot_wall_seconds(manifest) == 3 * 120 + 1920 + 60
    assert experiment['selection']['seeds'] == [102]
    assert experiment['selection']['help_budget'] == help
    assert experiment['selection']['eval_agent']['enabled'] is True
    assert next(iter(manifest['configs'].values()))['eval_agent']['enabled'] is True
    assert manifest['max_versions'] == manifest['target_depth'] == versions
    assert manifest['cell_budget']['help_count'] == help
    assert manifest['cell_budget']['model_seconds'] == model_seconds
    assert manifest['wall_limit'] == wall_seconds
    assert 'human_wait_seconds' not in json.loads(next(bridge.root.glob('devexp-*/selection.json')).read_bytes())

    eid = experiment['experiment_id']
    manifest_bytes = Path(experiment['manifest']['uri']).read_bytes()
    store = bridge.store(eid)
    directory, _, creation_budget = bridge.paths(eid)
    run_budget = bridge.dispatch_budget(store, experiment, directory, creation_budget)
    fake_client = object()
    fake_runner_factory = lambda **kwargs: None
    class FirstCellReached(Exception):
        pass
    def stop_at_first_cell(_store, _experiment, _manifest, key, _through, _owner, *, client, runner_factory):
        assert key == manifest['cells'][0]['key']
        assert client is fake_client and runner_factory is fake_runner_factory
        raise FirstCellReached
    monkeypatch.setattr('benchmarks.attention_harness.dev_v2_loop.run_cell', stop_at_first_cell)
    with pytest.raises(FirstCellReached):
        dev_experiment.run(store, eid, max_cells=1, budget_file=run_budget,
                           client=fake_client, runner_factory=fake_runner_factory)
    assert Path(experiment['manifest']['uri']).read_bytes() == manifest_bytes


def test_daily_slot_fallback_keeps_v3_and_unknown_manifests_strict():
    slot = dev_experiment.slot_wall_seconds
    v3 = dict(schema='attentionbench.dev-experiment.v3', model_reserve_seconds=120,
              attempt_wall_seconds=1920, startup_cleanup_seconds=60,
              eval_agent={'enabled': True})
    assert slot(v3) == dev_experiment.worst_slot(v3)
    with pytest.raises(KeyError, match='model_reserve_seconds'):
        slot(dict(schema='attentionbench.dev-experiment.v2', selection={}))


def test_main_bridge_still_maps_human_wait_into_planner_features(tmp_path, monkeypatch):
    from openarm365_dev_v2 import dev_config
    from openarm365_dev_v2.tests.test_main_integration import make_bridge

    # This boundary check exercises the bridge without requiring the absent model copy.
    monkeypatch.setattr(dev_config, 'model_sources', lambda *args, **kwargs: {})
    bridge, body = make_bridge(tmp_path)
    created = bridge.create(body)
    manifest = json.loads(Path(created['manifest']['uri']).read_bytes())
    assert manifest['planner_features']['human_wait_seconds'] == body['selection']['human_wait_seconds']


def test_daily_post_contract_and_isolated_existing_shared_goal(experiment, tmp_path, monkeypatch):
    bridge, body, e, store = experiment
    key = 'controlled-existing-shared-goal'
    gid = hashlib.sha256(('goal|' + key).encode()).hexdigest()[:32]
    shared = DevV2Store(tmp_path / 'shared.sqlite')
    shared.ensure_goal(gid, {k: 3 for k in RESOURCES}, 300)
    with shared.tx() as db:
        goal = shared.get(db, 'goals', gid)
        goal.update(real_started_at=time.time() - 5, authorized_until=time.time() + 30)
        goal['used']['dev_calls'] = 2
        shared.put(db, 'goals', gid, goal)
    shared_bridge = DevMainBridge(shared.path, tmp_path / 'shared-artifacts', key)
    before = shared.path.read_bytes()
    monkeypatch.setattr(ui_server, 'ThreadingHTTPServer', OfflineServer)
    server = ui_server.create_server(store=AttentionStore(tmp_path / 'ui.sqlite'),
                                     dev_v2_root=bridge.root, dev_v2_per_run_budget=True)
    status, page, _ = request(server, '/ui/')
    assert status == 200
    token = re.search(rb'data-control-token="([^"]+)"', page)[1].decode()
    assert request(server, '/api/dev-v2/experiments', body)[0] == 403
    status, raw, _ = request(server, '/api/dev-v2/experiments', body, token=token)
    assert status == 200, raw
    assert json.loads(raw)['experiment_id'] == e['experiment_id']
    assert json.loads(request(server, '/api/runs')[1])['dev_sessions'][0]['state'] == 'planned'
    assert shared.path.read_bytes() == before
    assert shared_bridge.store.goal(gid)['used']['dev_calls'] == 2
    with store.tx() as db:
        assert not db.execute('SELECT 1 FROM v2_goals WHERE id=?', (gid,)).fetchone()
        assert not db.execute('SELECT 1 FROM v2_goals WHERE id=?', (GOAL,)).fetchone()
    cancel = '/api/dev-v2/experiments/' + e['experiment_id'] + '/cancel'
    assert request(server, cancel, {}, token=token)[0] == 200
    assert store.goal()['used']['dev_calls'] == 0
    shared_server = ui_server.create_server(dev_v2_bridge=shared_bridge)
    assert json.loads(request(shared_server, '/api/dev-v2/options')[1])['bridge_schema'] == 'main-dev-v3'
    assert shared.path.read_bytes() == before


def test_daily_restart_and_create_never_reset_consumption_or_deadline(experiment, monkeypatch):
    bridge, body, e, store = experiment
    c = e['cells'][0]
    owner = process_identity()
    store.update_cell(c['key'], state='running', owner=owner)
    job = store.reserve(c['key'], 1, 'model', {'dev_calls': 1})
    store.claim(job['job_id'], owner)
    store.settle(job['job_id'], {'dev_calls': 1}, {'controlled_fixture': True})
    store.update_cell(c['key'], state='planned', owner=None)
    directory, _, authorization = bridge.paths(e['experiment_id'])
    dispatch = bridge.dispatch_budget(store, e, directory, authorization)
    before = store.path.read_bytes()
    deadline = json.loads(dispatch.read_bytes())['deadline_epoch']
    old_options = bridge.options()
    with monkeypatch.context() as guard:
        guard.setattr(daily.subprocess, 'Popen', lambda *a, **k: pytest.fail('restart must not spawn'))
        restarted = daily.DailyDevV2(bridge.root, [], per_run_budget=True)
        restarted.options(); restarted.list()
    assert restarted.create(body)['resources']['used']['dev_calls'] == 1
    restarted.options(); restarted.list()
    assert restarted.dispatch_budget(store, e, directory, authorization) == dispatch
    assert json.loads(dispatch.read_bytes())['deadline_epoch'] == deadline
    assert store.path.read_bytes() == before
    assert restarted.options()['default_cell_budget'] == old_options['default_cell_budget']
    wrong = copy.deepcopy(body)
    wrong['selection']['configuration']['experiment_budget']['dev_calls'] += 1
    with pytest.raises(DevConflict, match='immutable goal budget'):
        restarted.create(wrong)
    wrong = copy.deepcopy(body)
    wrong['selection']['request_key'] += '-another-ledger'
    with pytest.raises(DevConflict, match='goal ID already bound'):
        restarted.create(wrong)
    assert store.path.read_bytes() == before


def test_dispatch_preserves_older_expiry_and_existing_goal_budget(tmp_path, monkeypatch):
    bridge = daily.DailyDevV2(tmp_path / 'daily', [], per_run_budget=True)
    eid = 'devexp-' + 'a' * 24
    directory, path, budget = bridge.paths(eid)
    config = dict(goal_id='controlled-legacy-goal', experiment_budget={k: 3 for k in RESOURCES},
                  cell_budget={k: 3 for k in RESOURCES}, max_iterations=1, wall_seconds=300)
    store = DevV2Store(path, configuration={'goal_id': config['goal_id'], 'limits': config['experiment_budget'], 'wall_seconds': 300})
    e = {'selection': {'configuration': config}}
    with store.tx() as db:
        goal = store.get(db, 'goals', store.goal_id)
        goal.update(real_started_at=900, authorized_until=1050)
        goal['used']['dev_calls'] = 2
        store.put(db, 'goals', store.goal_id, goal)
    budget.write_text(json.dumps(dict(configuration=config, ledger_path=str(path), deadline_epoch=950)))
    before = path.read_bytes()
    monkeypatch.setattr(daily.time, 'time', lambda: 1000)
    with pytest.raises(DevBudgetExhausted, match='expired authorization'):
        bridge.dispatch_budget(store, e, directory, budget)
    assert path.read_bytes() == before
    budget.write_text(json.dumps(dict(configuration=config, ledger_path=str(path), authorization_mode='per_run_budget', deadline_epoch=None)))
    with pytest.raises(DevConflict, match='deadline cannot be extended'):
        bridge.dispatch_budget(store, e, directory, budget)
    assert path.read_bytes() == before


def test_planned_daily_ledger_is_byte_preserved_and_never_dispatched(experiment, monkeypatch, tmp_path):
    bridge, _, e, store = experiment
    before = store.path.read_bytes()
    monkeypatch.setattr(daily.subprocess, 'Popen', lambda *a, **k: pytest.fail('planned must not spawn'))
    monkeypatch.setattr(ui_server, 'ThreadingHTTPServer', OfflineServer)
    server = ui_server.create_server(store=AttentionStore(tmp_path / 'ui.sqlite'),
                                     dev_v2_root=bridge.root, dev_v2_per_run_budget=True)
    row = json.loads(request(server, '/api/runs')[1])['dev_sessions'][0]
    assert row['state'] == 'planned' and row['owner'] is None and not row['jobs']
    assert not any(row['used'].values())
    assert store.path.read_bytes() == before
    manifest = json.loads(Path(e['manifest']['uri']).read_bytes())
    assert manifest['configs']['open_cabinet:101']['eval_agent'] == {'enabled': True, 'model': 'parcc/Qwen'}


def test_daily_http_multiview_preview_files_and_readonly_ledger(experiment, tmp_path, monkeypatch):
    bridge, _, e, store = experiment
    cell = e['cells'][0]
    root = tmp_path / 'cell'
    episode = root / 'v1' / 'native'
    side = episode / 'live' / 'views' / 'side'
    side.mkdir(parents=True)
    raw = b'controlled-side-view-fixture'
    (side / 'frame-000000.jpg').write_bytes(raw)
    meta = dict(episode_id='c' * 32, version=1, sequence=0, file='frame-000000.jpg',
                sha256=hashlib.sha256(raw).hexdigest(), wall_time=time.time())
    (side / 'latest.json').write_text(json.dumps(meta))
    (root / 'camera_views.json').write_text(json.dumps({'views': ['external', 'side']}))
    store.update_cell(cell['key'], state='running', artifact_dir=str(root),
                      active_episode=str(episode), active_version=1)
    before = store.path.read_bytes()
    monkeypatch.setattr(ui_server, 'ThreadingHTTPServer', OfflineServer)
    server = ui_server.create_server(dev_v2_root=bridge.root, dev_v2_per_run_budget=True)
    base = '/api/dev-v2/experiments/' + e['experiment_id']
    cell_base = base + '/cells/' + cell['key']
    status, payload, headers = request(server, cell_base + '/live/frame?view=side')
    assert status == 200 and payload == raw and headers['X-Episode-Id'] == meta['episode_id']
    assert headers['X-Frame-Version'] == '1' and headers['X-Preview-State'] == 'live'
    assert request(server, cell_base + '/live/frame?view=side&version=2')[0] == 409
    assert request(server, cell_base + '/live/frame?view=unknown')[0] == 404
    status, payload, headers = request(server, cell_base + '/files/camera_views.json', headers={'Range': 'bytes=0-8'})
    assert status == 206 and payload == (root / 'camera_views.json').read_bytes()[:9]
    assert headers['Content-Range'].startswith('bytes 0-8/')
    status, payload, _ = request(server, cell_base + '/view')
    assert status == 200 and json.loads(payload)['versions'] == []
    assert request(server, base + '/ledger.json')[0] == 200
    assert store.path.read_bytes() == before


def test_dispatch_deadline_is_capped_by_original_clock_and_never_renewed(tmp_path, monkeypatch):
    bridge = daily.DailyDevV2(tmp_path / 'daily', [], per_run_budget=True)
    eid = 'devexp-' + 'd' * 24
    directory, path, budget = bridge.paths(eid)
    config = dict(goal_id='controlled-clock-goal', experiment_budget={k: 3 for k in RESOURCES},
                  cell_budget={k: 3 for k in RESOURCES}, max_iterations=1, wall_seconds=300)
    store = DevV2Store(path, configuration={'goal_id': config['goal_id'], 'limits': config['experiment_budget'], 'wall_seconds': 300})
    with store.tx() as db:
        goal = store.get(db, 'goals', store.goal_id)
        goal['real_started_at'] = 900
        goal['used']['dev_calls'] = 2
        store.put(db, 'goals', store.goal_id, goal)
    e = {'selection': {'configuration': config}}
    budget.write_text(json.dumps(dict(configuration=config, ledger_path=str(path), authorization_mode='per_run_budget', deadline_epoch=None)))
    monkeypatch.setattr(daily.time, 'time', lambda: 1000)
    dispatch = bridge.dispatch_budget(store, e, directory, budget)
    declaration = dispatch.read_bytes()
    assert json.loads(declaration)['deadline_epoch'] == 1200
    before = path.read_bytes()
    monkeypatch.setattr(daily.time, 'time', lambda: 1100)
    bridge.dispatch_budget(store, e, directory, budget)
    assert dispatch.read_bytes() == declaration and path.read_bytes() == before
    monkeypatch.setattr(daily.time, 'time', lambda: 1201)
    with pytest.raises(DevBudgetExhausted, match='time budget exhausted'):
        bridge.dispatch_budget(store, e, directory, budget)
    assert dispatch.read_bytes() == declaration and path.read_bytes() == before
