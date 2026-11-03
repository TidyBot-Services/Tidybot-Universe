"""TurnOnMicrowave / Counter → Cabinet presets: Agent-mode creation through the real UI server, offline.

A loopback fake provider stands in for the user's key; nothing starts a simulator or a worker.
"""
import json
import re
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from benchmarks.attention_harness import dev_experiment
from benchmarks.attention_harness.dev_ledger import DevV2Store
from benchmarks.attention_harness.tests.c21_fixtures import base, r4_deployment, offline_dev_identity
from benchmarks.attention_harness.tests.fakes.fake_provider import FakeProvider
from benchmarks.attention_harness.tests.test_attention_store import populated_store
from benchmarks.attention_harness.ui_server import create_server

KEY = 'canary-key-0123456789abcdef'
PROFILE = 'r5-fixture-20261031'
PRESETS = ('turn_on_microwave', 'pick_place_counter_to_cabinet')


@pytest.fixture
def served(tmp_path, monkeypatch, offline_dev_identity):
    original = subprocess.run
    created = []

    def execute(argv, *args, **kwargs):
        # Creation normally shells out to dev_experiment; run the same code in-process under the offline identity.
        if 'benchmarks.attention_harness.dev_experiment' not in argv:
            return original(argv, *args, **kwargs)
        def arg(name):
            return argv[argv.index(name) + 1]
        selection = json.loads(Path(arg('--selection')).read_bytes())
        created.append(selection)
        dev_experiment.create(DevV2Store(arg('--store')), Path(arg('--artifacts')), selection)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, 'run', execute)
    store, _ = populated_store(tmp_path / 'attention.sqlite3')
    with FakeProvider(KEY) as fake:
        server = create_server(store=store, port=0, dev_v2_root=tmp_path / 'daily', dev_v2_per_run_budget=True,
                               allow_custom=True, allow_private_hosts=('127.0.0.1',))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f'http://127.0.0.1:{server.server_port}'
        with urlopen(url + '/ui/') as response:
            token = re.search(r'data-control-token="([^"]+)"', response.read().decode())[1]
        try:
            yield SimpleNamespace(url=url, token=token, fake=fake, created=created, root=tmp_path / 'daily')
        finally:
            server.shutdown()
            server.server_close()


def call(s, method, path, body=None):
    head = {'X-Attention-Token': s.token, **({'Content-Type': 'application/json'} if body is not None else {})}
    try:
        with urlopen(Request(s.url + path, method=method, data=json.dumps(body).encode() if body is not None else None, headers=head)) as r:
            return r.status, r.read().decode()
    except HTTPError as error:
        return error.code, error.read().decode()


def launch_selection(s, task, seed=101):
    """The body live.js sends for Dev automatic iteration + Agent: agentLaunchSelection over the fixture selection."""
    status, raw = call(s, 'GET', '/api/dev-v2/options')
    assert status == 200, raw
    options = json.loads(raw)
    profile = next(p for p in options['profiles'] if p['id'] == 'dev-v2-r5-' + task)
    policy = options['agent_policies']['policies'][0]['policy_id']
    host = f'127.0.0.1:{s.fake.server.server_port}'
    return profile, dict(
        request_key='task-preset-' + task.replace('_', '-'), tasks=[profile['task_id']], seeds=[seed], policies=[policy],
        mode='interactive', help_budget=5, rules_profile=profile['rules_profile'],
        dev_mode='agent', model_source='codex', budget_tier='dev', dev_model='gpt-6.1-sol', eval_model='gpt-6.1-sol',
        advisor_model='gpt-6.1-sol', quota_stop_percent=90, goal_key='task-preset-goal-' + task, wall_limit=21600,
        agent_provider=dict(roles=dict(dev=dict(credential='mine', provider='openai-compatible', model='fake-model', base_url_host=host))))


@pytest.mark.parametrize('task', PRESETS)
def test_agent_mode_creates_preset_task_at_seed_101(served, task):
    s = served
    status, raw = call(s, 'PUT', '/api/provider/credentials/mine',
                       dict(provider='openai-compatible', key=KEY, base_url=s.fake.base_url))
    assert status == 200 and KEY not in raw, raw
    status, raw = call(s, 'POST', '/api/provider/credentials/mine/test', dict(model='fake-model', role='dev'))
    assert status == 200 and json.loads(raw)['ok'] is True and KEY not in raw, raw
    profile, selection = launch_selection(s, task)
    # The preset the New run form offers: this task, fixture rules, first seed 101 enabled.
    assert profile['seeds'][0] == 101 and profile['seed'] == 101 and profile['enabled']
    assert [(r['seed'], r['enabled']) for r in profile['seed_options']][0] == (101, True)
    assert profile['seeds'] == ([101, 102, 107] if task == 'pick_place_counter_to_cabinet' else [101, 102, 103])
    status, raw = call(s, 'POST', '/api/dev-v2/experiments', dict(selection=selection, start=False))
    assert status in (200, 201), raw
    result = json.loads(raw)
    assert KEY not in raw and '/home/' not in raw
    sent = s.created[-1]
    assert sent['tasks'] == [task] and sent['seeds'] == [101] and sent['rules_profile'] == PROFILE
    assert sent['dev_mode'] == 'agent' and sent['mode'] == 'interactive'
    store = DevV2Store(s.root / result['experiment_id'] / 'ledger.sqlite3')
    manifest = dev_experiment.verify_manifest(dev_experiment.snapshot(store, result['experiment_id']))
    config = manifest['configs'][f'{task}:101']
    assert config['task_id'] == task and config['seed'] == config['scene_reset_seed'] == config['global_random_seed'] == 101
    assert config['dev_mode'] == 'agent' and config['scene_verification'] == 'fixture_contract_only'
    assert config['fixture_scene_contract']['sha256'] and config['fixture_success_condition']['sha256']
    assert 'scene_contract' not in config
    assert manifest['execution_profile'] == PROFILE and manifest['selection']['rules_profile'] == PROFILE
    assert manifest['selection']['tasks'] == [task] and manifest['selection']['seeds'] == [101]
    assert manifest['dev_mode'] == 'agent' and manifest['formal_eligible'] is False
    # The agent sees the success condition the task is judged by; nothing here was loosened.
    assert manifest['success_condition_dev_visible'] is True and f'{task}:101' in manifest['success_conditions']
    assert config['agent_provider']['roles']['dev']['provider'] == 'openai-compatible'
    assert [c['task_id'] for c in manifest['cells']] == [task] and [c['seed'] for c in manifest['cells']] == [101]
    assert KEY not in json.dumps(manifest)


@pytest.mark.parametrize('task', PRESETS)
def test_validation_is_unchanged_for_other_fixture_seeds_and_modes(served, task):
    s = served
    profile, selection = launch_selection(s, task)
    # The form locks the seed, but the server still checks every request on its own.
    status, raw = call(s, 'POST', '/api/dev-v2/experiments', dict(selection=dict(selection, seeds=[104]), start=False))
    assert status >= 400 and 'seed' in raw and not s.created
    status, raw = call(s, 'POST', '/api/dev-v2/experiments', dict(selection=dict(selection, mode='experiment'), start=False))
    assert status >= 400 and 'interactive only' in raw and not s.created


def test_cabinet_pick_place_has_only_the_fixture_profile(served):
    # Counter → Cabinet is explicit-only in dev_tasks: no legacy Dev v2 profile, just the fixture one the form offers.
    status, raw = call(served, 'GET', '/api/dev-v2/options')
    assert status == 200, raw
    assert [p['id'] for p in json.loads(raw)['profiles'] if p['task_id'] == 'pick_place_counter_to_cabinet'] == ['dev-v2-r5-pick_place_counter_to_cabinet']


@pytest.mark.parametrize('seed', (102, 107))
def test_cabinet_other_registered_seeds_are_still_accepted_by_the_server(served, seed):
    s = served
    status, raw = call(s, 'PUT', '/api/provider/credentials/mine', dict(provider='openai-compatible', key=KEY, base_url=s.fake.base_url))
    assert status == 200, raw
    _, selection = launch_selection(s, 'pick_place_counter_to_cabinet', seed)
    status, raw = call(s, 'POST', '/api/dev-v2/experiments', dict(selection=selection, start=False))
    assert status in (200, 201), raw
    assert s.created[-1]['seeds'] == [seed]
