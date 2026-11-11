"""Campus admission and offline cleanup, exercised without sockets or external services."""
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from remote_gateway import __main__ as cli, policy, server
from remote_gateway.config import Config, Limits, load
from remote_gateway.prune import directory, prune
from remote_gateway.store import Store
from remote_gateway.deploy import run_host_harness as launcher

DEPLOY = Path(__file__).resolve().parents[1] / 'deploy/campus'
CODEX = dict(model='gpt-6.1-sol', effort='high', runs_per_day=2, stop_percent=77)
OPTIONS = dict(dev_v2=dict(profiles=[dict(task_id='task', enabled=True)], agent_policies=dict(policies=[dict(policy_id='autonomous')])))
LAB = dict(credential='lab', provider='parcc', model='parcc/GLM')
LAB_CODEX = dict(credential='lab', provider='openai', model=CODEX['model'])


def body(key='request-1', **changes):
    return dict(selection=dict(request_key=key, tasks=['task'], seeds=[101], policies=['autonomous'], mode='interactive', help_budget=2,
                               agent_provider=dict(roles={role: dict(LAB) for role in ('dev', 'eval', 'advisor')}), **changes), start=True)


def codex_body(key='request-1', *, roles=('dev',), **changes):
    value = body(key, **changes)
    for role in roles:value['selection']['agent_provider']['roles'][role] = dict(LAB_CODEX)
    return value


@pytest.fixture
def app(tmp_path):
    now = [1700000000.0]
    ui = tmp_path / 'ui';ui.mkdir()
    (ui / 'index.html').write_text('<html><head></head><body data-mode="live"></body></html>')
    gateway = server.Gateway(Config(state_dir=tmp_path / 'state', upstream='http://127.0.0.1:1', ui_dir=ui), secret=b's' * 48, clock=lambda: now[0])
    rows, calls = [], []

    def upstream(user, method, path, value=None):
        calls.append((user, method, path, value))
        if path == '/api/runs':return 200, dict(dev_sessions=rows)
        if path == '/api/run-options':return 200, OPTIONS
        if path == '/api/provider/credentials':return 200, []
        if path == '/api/dev-v2/experiments':
            eid = 'devexp-' + f'{len(rows) + 1:024x}'
            rows.append(dict(experiment_id=eid, state='planned', created_at=now[0]))
            return 200, dict(experiment_id=eid)
        if path.endswith('/run'):
            eid = path.split('/')[-2]
            next(row for row in rows if row['experiment_id'] == eid)['state'] = 'running'
            return 200, dict(worker_started=True)
        raise AssertionError(path)

    gateway.upstream.json = upstream
    return SimpleNamespace(gateway=gateway, now=now, rows=rows, calls=calls)


def request(app, method, path, value=None, *, headers=None, peer='127.0.0.1', raw=None):
    """Run the real handler, including routing, cookies, guards and audit, with memory streams."""
    handler = server.Handler.__new__(server.Handler)
    data = raw if raw is not None else json.dumps(value).encode() if value is not None else b''
    handler.server = SimpleNamespace(gateway=app.gateway)
    handler.command, handler.path, handler.client_address = method, path, (peer, 1)
    handler.headers = {'Content-Length': str(len(data)), 'Content-Type': 'application/json', **(headers or {})}
    handler.rfile, handler.wfile = io.BytesIO(data), io.BytesIO()
    result = SimpleNamespace(status=None, headers={})
    handler.send_response = lambda status:setattr(result, 'status', status)
    handler.send_header = lambda name, value:result.headers.update({name: value})
    handler.end_headers = lambda:None
    handler.serve()
    result.raw = handler.wfile.getvalue()
    result.value = json.loads(result.raw) if result.headers.get('Content-Type', '').startswith('application/json') else None
    return result


def login(app, code, email='tester@upenn.edu', **kwargs):
    return request(app, 'POST', '/login', raw=urlencode(dict(code=code, email=email)).encode(), **kwargs)


def person(app):
    _, code = app.gateway.store.create_invite()
    response = login(app, code)
    cookie = response.headers['Set-Cookie'].split(';')[0]
    session = app.gateway.store.session(cookie.split('=')[1])
    return {'Cookie': cookie, 'X-Attention-Token': session['csrf']}, session['user_id']


def settings(app, **changes):app.gateway.config = replace(app.gateway.config, **changes)


def test_proxy_clients_have_separate_login_and_anonymous_budgets(app):
    settings(app, trusted_proxy=True)
    app.gateway.rate.limits = Limits(logins_per_hour=1, request_burst=1)
    first, second = {'X-Real-IP': '192.0.2.1'}, {'X-Real-IP': '192.0.2.2'}
    assert login(app, 'bad-a', headers=first).status == 303
    _, code = app.gateway.store.create_invite()
    assert login(app, code, headers=first).status == 429
    assert login(app, code, headers=second).status == 303
    assert request(app, 'GET', '/api/notices', headers=first).status == 200
    assert request(app, 'GET', '/api/notices', headers=first).status == 429
    assert request(app, 'GET', '/api/notices', headers=second).status == 200
    audit = app.gateway.config.audit_file.read_text()
    assert '192.0.2.' not in audit and code not in audit and 'bad-a' not in audit


@pytest.mark.parametrize('trusted,peer,header,expected', [
    (False, '127.0.0.1', '192.0.2.1', '127.0.0.1'),
    (True, '192.0.2.2', '192.0.2.1', '192.0.2.2'),
    (True, '127.0.0.1', 'not an IP', '127.0.0.1'),
    (True, '127.0.0.1', '192.0.2.1, 192.0.2.2', '127.0.0.1'),
    (True, '127.0.0.1', '', '127.0.0.1'),
    (True, '::1', '2001:db8::1', '2001:db8::1')])
def test_forwarded_address_requires_opt_in_loopback_and_one_ip(app, trusted, peer, header, expected):
    settings(app, trusted_proxy=trusted)
    assert login(app, 'bad-code', peer=peer, headers={'X-Real-IP': header}).status == 303
    assert (('address', expected), int(app.now[0] // 3600)) in app.gateway.rate.failures


def test_untrusted_header_cannot_evade_login_limits(app):
    app.gateway.rate.limits = Limits(logins_per_hour=1)
    assert login(app, 'bad-a', headers={'X-Real-IP': '192.0.2.1'}).status == 303
    assert login(app, 'bad-b', headers={'X-Real-IP': '192.0.2.2'}).status == 429


def test_code_prefix_locks_across_addresses_without_retaining_clear_prefix(app):
    settings(app, trusted_proxy=True)
    app.gateway.rate.limits = Limits(logins_per_code_per_hour=2)
    for n in range(2):assert login(app, f'prefix-guess-{n}', headers={'X-Real-IP': f'192.0.2.{n + 1}'}).status == 303
    assert login(app, 'prefix-another', headers={'X-Real-IP': '192.0.2.3'}).status == 429
    assert login(app, 'other-code', headers={'X-Real-IP': '192.0.2.3'}).status == 303
    assert 'prefix' not in repr(app.gateway.rate.failures)


def test_global_failure_cap_and_hour_reset(app):
    settings(app, trusted_proxy=True)
    app.gateway.rate.limits = Limits(logins_global_per_hour=2)
    for n in range(2):assert login(app, f'bad-{n}', headers={'X-Real-IP': f'192.0.2.{n + 1}'}).status == 303
    _, code = app.gateway.store.create_invite()
    assert login(app, code, headers={'X-Real-IP': '192.0.2.3'}).status == 429
    app.now[0] += 3600
    assert login(app, code).status == 303


def test_successful_logins_do_not_spend_failure_budgets(app):
    app.gateway.rate.limits = Limits(logins_global_per_hour=1, logins_per_code_per_hour=1)
    _, code = app.gateway.store.create_invite()
    for _ in range(3):assert login(app, code).status == 303
    assert app.gateway.rate.failures == {}


def test_concurrent_failures_cannot_overrun_global_cap(app):
    settings(app, trusted_proxy=True)
    app.gateway.rate.limits = Limits(logins_global_per_hour=1)
    ready = threading.Barrier(8);statuses = []
    def attempt(n):
        ready.wait()
        statuses.append(login(app, f'bad-{n}', headers={'X-Real-IP': f'192.0.2.{n + 1}'}).status)
    threads = [threading.Thread(target=attempt, args=(n,)) for n in range(8)]
    for thread in threads:thread.start()
    for thread in threads:thread.join(timeout=5)
    assert sorted(statuses) == [303] + [429] * 7


def test_gateway_nicknames_follow_store_order_even_when_listing_is_reversed_or_missing_first(app):
    app.gateway.listing_ttl = 0  # the test changes the fake host behind the gateway's back, so the shared run list must not be reused
    headers, user = person(app)
    first = request(app, 'POST', '/api/dev-v2/experiments', dict(body(), start=False), headers=headers).value['experiment_id']
    second = request(app, 'POST', '/api/dev-v2/experiments', dict(body('request-2'), start=False), headers=headers).value['experiment_id']
    app.rows.reverse()
    cards = request(app, 'GET', '/api/lobby').value['recent']
    nickname = app.gateway.store.nicknames()[user]
    assert {c['experiment_id']: c['nickname'] for c in cards} == {first: nickname, second: nickname + ' · run 2'}
    app.rows[:] = app.rows[:1]
    assert request(app, 'GET', '/api/lobby').value['recent'][0]['nickname'] == nickname + ' · run 2'


def test_board_numbers_runs_per_person_oldest_first():
    rows = [dict(experiment_id=eid, state='completed', created_at=at) for eid, at in [('b', 2), ('c', 3), ('a', 1)]]
    board = policy.board(dict(dev_sessions=rows), owners={'a': 'u', 'b': 'v', 'c': 'u'}, nicknames={'u': 'Fox-1', 'v': 'Panda-2'}, queue=[], viewer=None, limits=Limits())
    assert {c['run_id']: c['nickname'] for c in board['recent']} == {'a': 'Fox-1', 'b': 'Panda-2', 'c': 'Fox-1 · run 2'}


def test_low_disk_refuses_create_and_start_holds_queue_and_shows_reason(app, monkeypatch, tmp_path):
    headers, user = person(app)
    made = request(app, 'POST', '/api/dev-v2/experiments', dict(body(), start=False), headers=headers).value['experiment_id']
    app.gateway.store.enqueue(made, user)
    settings(app, disk_path=tmp_path, min_free_gb=150)
    monkeypatch.setattr(server.shutil, 'disk_usage', lambda path:SimpleNamespace(free=149 * 1024 ** 3))
    for path, data in [('/api/dev-v2/experiments', body('request-2')), (f'/api/dev-v2/experiments/{made}/run', {})]:
        response = request(app, 'POST', path, data, headers=headers)
        assert response.status == 503 and response.value['code'] == 'disk_low'
    assert app.gateway.dispatch() is None and app.gateway.store.queue()[0]['experiment_id'] == made
    assert request(app, 'GET', '/api/lobby').value['availability']['code'] == 'disk_low'
    page = request(app, 'GET', '/ui/')
    assert b'free disk space is below' in page.raw and b'gateway-availability' in page.raw
    assert 'sha256-' in page.headers['Content-Security-Policy']
    monkeypatch.setattr(server.shutil, 'disk_usage', lambda path:SimpleNamespace(free=150 * 1024 ** 3))
    assert app.gateway.dispatch()[:2] == (made, 200)
    assert app.gateway.store.queue() == []


def test_disk_check_is_opt_in_and_check_errors_fail_closed(app, monkeypatch, tmp_path):
    def failed(path):raise OSError('cannot inspect disk')
    monkeypatch.setattr(server.shutil, 'disk_usage', failed)
    assert app.gateway.unavailable() is None
    settings(app, disk_path=tmp_path)
    assert app.gateway.unavailable().code == 'disk_low'


def test_paused_notice_uses_same_path_as_disk_notice(app):
    app.gateway.config.pause_file.touch()
    assert request(app, 'GET', '/api/lobby').value['availability']['code'] == 'runs_paused'
    assert b'New runs are paused for now; runs already going keep going.' in request(app, 'GET', '/ui/').raw


def test_lab_codex_off_keeps_existing_refusal(app):
    headers, _ = person(app)
    offered = request(app, 'GET', '/api/run-options').value
    assert 'lab_codex' not in offered['hosted'] and 'codex' not in json.dumps(offered).lower()
    assert b'Codex' not in request(app, 'GET', '/ui/').raw
    response = request(app, 'POST', '/api/dev-v2/experiments', codex_body(), headers=headers)
    assert response.status == 400 and response.value['code'] == 'lab_source_not_offered'
    assert not any(call[1:3] == ('POST', '/api/dev-v2/experiments') for call in app.calls)


def test_test_gateway_codex_still_works_without_campus_lab_codex(app, monkeypatch):
    from remote_gateway.simtest import codex_gateway
    # install() wraps global policy functions; restore all three even on failure.
    for name in ('_roles', 'rebuild', 'filter_options'):
        monkeypatch.setattr(policy, name, getattr(policy, name))
    codex_gateway.install()
    assert app.gateway.config.lab_codex is None
    headers, _ = person(app)
    response = request(app, 'POST', '/api/dev-v2/experiments', codex_body(), headers=headers)
    assert response.status == 200
    eid = response.value['experiment_id']
    selected = next(call[3]['selection'] for call in app.calls if call[1:3] == ('POST', '/api/dev-v2/experiments'))
    assert selected['agent_provider']['roles']['dev'] == LAB_CODEX
    assert next(row for row in app.rows if row['experiment_id'] == eid)['state'] == 'running'
    assert app.gateway.store.codex_run(eid) is False
    assert app.gateway.store.codex_today() == 0


@pytest.mark.parametrize('model', ['gpt-6.1-sol', 'gpt-6-astra'])
def test_lab_codex_options_offer_the_configured_model_next_to_parcc(app, model):
    settings(app, lab_codex=dict(CODEX, model=model))
    offered = request(app, 'GET', '/api/run-options').value
    assert offered['hosted']['lab_codex'] == dict(model=model, reasoning_effort='high', quota_stop_percent=77, run_cap_points=5)
    assert 'limits' in offered['hosted'] and offered['dev_v2']['profiles'] == OPTIONS['dev_v2']['profiles']


@pytest.mark.parametrize('roles', [('dev',), ('eval',), ('advisor',), ('dev', 'eval', 'advisor')])
def test_lab_codex_preserves_each_role_and_sets_effort_and_stop_only_for_codex(app, roles):
    settings(app, lab_codex=CODEX)
    headers, _ = person(app)
    value = codex_body(roles=roles, dev_model='evil', eval_model='evil', advisor_model='evil', dev_reasoning_effort='low', quota_stop_percent=100)
    response = request(app, 'POST', '/api/dev-v2/experiments', value, headers=headers)
    assert response.status == 200
    selected = next(call[3]['selection'] for call in app.calls if call[1:3] == ('POST', '/api/dev-v2/experiments'))
    assert selected['agent_provider']['roles'] == value['selection']['agent_provider']['roles']
    assert all(selected[role + '_model'] == (CODEX['model'] if role in roles else LAB['model']) for role in ('dev', 'eval', 'advisor'))
    assert selected['dev_reasoning_effort'] == 'high' and selected['quota_stop_percent'] == 77
    assert app.gateway.store.codex_run(response.value['experiment_id']) and app.gateway.store.codex_today() == 1


@pytest.mark.parametrize('lab_codex', [None, CODEX])
def test_parcc_only_keeps_public_effort_and_stop_values(app, lab_codex):
    settings(app, lab_codex=lab_codex)
    headers, _ = person(app)
    value = body(dev_reasoning_effort='low', quota_stop_percent=100)
    response = request(app, 'POST', '/api/dev-v2/experiments', value, headers=headers)
    assert response.status == 200
    selected = next(call[3]['selection'] for call in app.calls if call[1:3] == ('POST', '/api/dev-v2/experiments'))
    assert selected['agent_provider']['roles'] == value['selection']['agent_provider']['roles']
    assert selected['quota_stop_percent'] == 90 and 'dev_reasoning_effort' not in selected
    assert not app.gateway.store.codex_run(response.value['experiment_id']) and app.gateway.store.codex_today() == 0


@pytest.mark.parametrize('role', ['dev', 'eval', 'advisor'])
@pytest.mark.parametrize('model', ['gpt-6-astra', ' gpt-6.1-sol ', '', None])
def test_lab_codex_wrong_model_is_refused(app, role, model):
    settings(app, lab_codex=CODEX)
    headers, _ = person(app)
    value = codex_body(roles=(role,))
    value['selection']['agent_provider']['roles'][role]['model'] = model
    response = request(app, 'POST', '/api/dev-v2/experiments', value, headers=headers)
    assert response.status == 400 and response.value['code'] == 'lab_model_not_offered'
    assert not any(call[1:3] == ('POST', '/api/dev-v2/experiments') for call in app.calls)


@pytest.mark.parametrize('change,code', [(dict(credential='stolen'), 'key_not_found'), (dict(provider='fake'), 'lab_source_not_offered')])
def test_lab_codex_does_not_bypass_other_role_validation(app, change, code):
    settings(app, lab_codex=CODEX)
    headers, _ = person(app)
    value = codex_body()
    value['selection']['agent_provider']['roles']['dev'].update(change)
    response = request(app, 'POST', '/api/dev-v2/experiments', value, headers=headers)
    assert response.status == 400 and response.value['code'] == code


def test_disabling_lab_codex_blocks_previously_created_codex_runs(app):
    settings(app, lab_codex=CODEX)
    headers, user = person(app)
    made = request(app, 'POST', '/api/dev-v2/experiments', dict(codex_body(), start=False), headers=headers).value['experiment_id']
    app.gateway.store.enqueue(made, user)
    settings(app, lab_codex=None)
    assert app.gateway.dispatch() is None and app.gateway.store.codex_today() == 0
    assert app.gateway.board(None)['availability']['code'] == 'lab_source_not_offered'
    response = request(app, 'POST', f'/api/dev-v2/experiments/{made}/run', {}, headers=headers)
    assert response.value['code'] == 'lab_source_not_offered'


def test_codex_cap_is_global_persistent_and_uses_utc_calendar_day(app):
    settings(app, lab_codex=CODEX)
    app.now[0] = 20000 * 86400 + 86390
    for n in range(2):
        headers, _ = person(app)
        assert request(app, 'POST', '/api/dev-v2/experiments', codex_body(f'request-{n}'), headers=headers).status == 200
    headers, _ = person(app)
    response = request(app, 'POST', '/api/dev-v2/experiments', codex_body('request-next'), headers=headers)
    assert response.status == 429 and response.value['code'] == 'codex_daily_limit'
    assert app.gateway.board(None)['availability']['code'] == 'codex_daily_limit'
    reopened = Store(app.gateway.config.database, clock=lambda: app.now[0])
    assert reopened.codex_today() == 2
    app.now[0] += 11
    assert reopened.codex_today() == 0
    assert request(app, 'POST', '/api/dev-v2/experiments', codex_body('request-next'), headers=headers).status == 200


@pytest.mark.parametrize('own_key', [False, True])
def test_codex_cap_does_not_block_parcc_or_own_keys_at_creation_or_manual_start(app, own_key):
    settings(app, lab_codex=dict(CODEX, runs_per_day=1))
    headers, _ = person(app)
    assert request(app, 'POST', '/api/dev-v2/experiments', codex_body(), headers=headers).status == 200
    headers, _ = person(app)
    value = body('request-non-codex', dev_reasoning_effort='low', quota_stop_percent=100)
    if own_key:
        original = app.gateway.upstream.json
        app.gateway.upstream.json = lambda user, method, path, value=None:(200, [dict(credential_id='mine', provider='openai')]) if path == '/api/provider/credentials' else original(user, method, path, value)
        value['selection']['agent_provider']['roles'] = {role:dict(LAB_CODEX, credential='mine') for role in ('dev', 'eval', 'advisor')}
    response = request(app, 'POST', '/api/dev-v2/experiments', dict(value, start=False), headers=headers)
    assert response.status == 200
    eid = response.value['experiment_id']
    assert not app.gateway.store.codex_run(eid)
    selected = [call[3]['selection'] for call in app.calls if call[1:3] == ('POST', '/api/dev-v2/experiments')][-1]
    assert selected['agent_provider']['roles'] == value['selection']['agent_provider']['roles']
    assert selected['quota_stop_percent'] == 90 and 'dev_reasoning_effort' not in selected
    assert request(app, 'POST', f'/api/dev-v2/experiments/{eid}/run', {}, headers=headers).status == 200
    assert next(row for row in app.rows if row['experiment_id'] == eid)['state'] == 'running'
    assert app.gateway.store.codex_today() == 1
    notice = request(app, 'GET', '/api/lobby').value['availability']
    assert notice['code'] == 'codex_daily_limit'
    assert 'PARCC and your own keys remain available.' in notice['message'] and 'New runs are paused' not in notice['message']
    assert notice['message'].encode() in request(app, 'GET', '/ui/').raw
    for start in (False, True):
        response = request(app, 'POST', '/api/dev-v2/experiments', dict(codex_body('request-capped'), start=start), headers=person(app)[0])
        assert response.status == 429 and response.value['code'] == 'codex_daily_limit'
    assert len(app.rows) == 2


def test_cap_rechecked_at_dispatch_and_manual_start_keeps_place(app):
    settings(app, lab_codex=dict(CODEX, runs_per_day=1))
    people = [person(app) for _ in range(3)]
    made = []
    for n, (headers, _) in enumerate(people):
        value = (codex_body if n < 2 else body)(f'request-{n}')
        made.append(request(app, 'POST', '/api/dev-v2/experiments', dict(value, start=False), headers=headers).value['experiment_id'])
    for eid, (_, user) in zip(made, people):app.gateway.store.enqueue(eid, user)
    assert app.gateway.dispatch()[:2] == (made[0], 200)
    assert app.gateway.dispatch() is None
    assert [r['experiment_id'] for r in app.gateway.store.queue()] == made[1:]
    assert next(row for row in app.rows if row['experiment_id'] == made[2])['state'] == 'planned'
    assert not app.gateway.store.codex_run(made[2])
    response = request(app, 'POST', f'/api/dev-v2/experiments/{made[1]}/run', {}, headers=people[1][0])
    assert response.value['code'] == 'codex_daily_limit'
    app.now[0] += 86400
    assert app.gateway.dispatch()[:2] == (made[1], 200)
    assert app.gateway.dispatch()[:2] == (made[2], 200)


def test_failed_start_and_non_codex_runs_do_not_use_codex_count(app):
    headers, _ = person(app)
    assert request(app, 'POST', '/api/dev-v2/experiments', body(), headers=headers).status == 200
    assert app.gateway.store.codex_today() == 0
    settings(app, lab_codex=CODEX)
    original = app.gateway.upstream.json
    app.gateway.upstream.json = lambda user, method, path, value=None:(500, dict(error='start failed')) if path.endswith('/run') else original(user, method, path, value)
    headers, _ = person(app)
    assert request(app, 'POST', '/api/dev-v2/experiments', codex_body('request-next'), headers=headers).status == 500
    assert app.gateway.store.codex_today() == 0


@pytest.fixture
def retention(tmp_path):
    now = [1000000.0];store = Store(tmp_path / 'state/gateway.sqlite3', clock=lambda:now[0])
    root = tmp_path / 'runs';root.mkdir()
    ids = ['devexp-' + c * 24 for c in 'abcdef']
    for eid in ids:
        (root / eid).mkdir();(root / eid / 'data').write_bytes(b'abc')
    for eid in ids[:5]:store.record_run(eid, 'u', 'local')
    rows = [dict(experiment_id=eid, state=state) for eid, state in zip(ids[:5], ['completed', 'running', 'planned', 'failed', 'unknown'])]
    store.observe_finished(dict(dev_sessions=rows), policy.ENDED_STATES)
    now[0] += 8 * 86400
    # A resumed run must lose its retention clock even if its old directory is untouched.
    store.enqueue(ids[3], 'u')
    store.start_failed(ids[0], 'old failure')
    return SimpleNamespace(store=store, root=root, now=now, ids=ids, rows=rows)


def test_prune_dry_run_lists_size_age_total_and_changes_nothing(retention, capsys):
    r = retention
    before = r.store.path.read_bytes()
    assert prune(r.store, r.root, days=7) == 1
    output = capsys.readouterr().out
    assert str(r.root / r.ids[0]) in output and '3 bytes' in output and '8.00 days' in output and '1 candidates' in output and 'dry run' in output
    assert all((r.root / eid).exists() for eid in r.ids)
    assert r.store.path.read_bytes() == before and r.store.known(r.ids[0])


def test_prune_delete_removes_only_old_known_finished_directory_and_gateway_rows(retention):
    r = retention
    assert prune(r.store, r.root, days=7, delete=True) == 1
    assert not (r.root / r.ids[0]).exists() and not r.store.known(r.ids[0])
    assert r.ids[0] not in r.store.start_failures()
    assert all((r.root / eid).exists() for eid in r.ids[1:])
    assert len(r.store.queue()) == 1


def test_prune_recent_end_unknown_end_and_mixed_states_are_kept(retention):
    r = retention
    r.store.observe_finished(dict(dev_sessions=[dict(experiment_id=r.ids[0], state='running')]), policy.ENDED_STATES)
    r.store.observe_finished(dict(dev_sessions=[dict(experiment_id=r.ids[0], state='completed')]), policy.ENDED_STATES)
    assert prune(r.store, r.root, days=7) == 0
    r.now[0] += 8 * 86400
    r.store.observe_finished(dict(dev_sessions=[dict(experiment_id=r.ids[0], state='completed'), dict(experiment_id=r.ids[0], state='running')]), policy.ENDED_STATES)
    assert prune(r.store, r.root, days=7) == 0


@pytest.mark.parametrize('inside', [False, True])
def test_prune_refuses_run_symlinks_even_with_safe_targets(retention, inside, tmp_path):
    r = retention;path = r.root / r.ids[0]
    shutil.rmtree(path)
    target = r.root / 'target' if inside else tmp_path / 'outside'
    target.mkdir();(target / 'keep').write_text('keep')
    path.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):prune(r.store, r.root, days=7, delete=True)
    assert (target / 'keep').read_text() == 'keep' and r.store.known(r.ids[0])


def test_prune_refuses_escaping_paths_root_symlink_and_nested_symlinks(retention, tmp_path):
    r = retention;outside = tmp_path / 'outside';outside.mkdir()
    with pytest.raises(ValueError, match='unsafe'):directory(outside, r.root)
    alias = tmp_path / 'alias';alias.symlink_to(r.root, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):prune(r.store, alias, days=7)
    (r.root / r.ids[0] / 'link').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):prune(r.store, r.root, days=7, delete=True)
    assert r.store.known(r.ids[0])


def test_listing_persists_completion_but_never_moves_an_observed_end_later(app):
    app.gateway.listing_ttl = 0  # the test changes the fake host behind the gateway's back, so the shared run list must not be reused
    eid = 'devexp-' + 'a' * 24
    app.gateway.store.record_run(eid, 'u', 'local')
    app.rows.append(dict(experiment_id=eid, state='completed'))
    app.gateway.listing();app.now[0] += 8 * 86400;app.gateway.listing()
    assert eid in app.gateway.store.finished_before(app.now[0] - 7 * 86400)
    app.rows[0]['state'] = 'running';app.gateway.listing()
    assert app.gateway.store.finished_before(app.now[0]) == {}


def test_explicit_terminal_end_time_can_establish_age_without_waiting_again(app):
    eid = 'devexp-' + 'a' * 24
    app.gateway.store.record_run(eid, 'u', 'local')
    app.rows.append(dict(experiment_id=eid, state='completed', ended_at=app.now[0] - 8 * 86400))
    app.gateway.listing()
    assert eid in app.gateway.store.finished_before(app.now[0] - 7 * 86400)


def test_old_gateway_database_gets_metadata_without_losing_rows(tmp_path):
    import sqlite3
    path = tmp_path / 'gateway.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE runs(experiment_id TEXT PRIMARY KEY,user_id TEXT,host_id TEXT,created_at REAL)')
        db.execute('INSERT INTO runs VALUES(?,?,?,?)', ('old', 'u', 'local', 1))
    store = Store(path)
    assert store.owns('u', 'old') and store.codex_today() == 0 and store.finished_before(100) == {}
    assert Store(path).run_order() == ['old']


def test_prune_cli_uses_config_retention_and_requires_explicit_delete(retention, tmp_path, monkeypatch, capsys):
    r = retention;config = tmp_path / 'gateway.json'
    config.write_text(json.dumps(dict(state_dir=str(r.store.path.parent), upstream='http://127.0.0.1:1', ui_dir=str(tmp_path), retention_days=9)))
    monkeypatch.setattr(cli, 'Store', lambda path:r.store)
    args = ['remote_gateway', '--config', str(config), 'prune', '--runs-root', str(r.root)]
    monkeypatch.setattr(sys, 'argv', args);cli.main()
    assert '0 candidates' in capsys.readouterr().out
    monkeypatch.setattr(sys, 'argv', args + ['--days', '7']);cli.main()
    assert 'dry run' in capsys.readouterr().out and (r.root / r.ids[0]).exists()
    monkeypatch.setattr(sys, 'argv', args + ['--days', '7', '--delete']);cli.main()
    assert not (r.root / r.ids[0]).exists()


def test_campus_example_loads_with_frozen_limits():
    config = load(DEPLOY / 'gateway.json.example')
    assert config.cookie_secure and config.trusted_proxy and config.listen_host == '127.0.0.1'
    assert (config.limits.versions, config.limits.runs_per_day, config.limits.help_requests, config.limits.active_runs, config.limits.run_slots, config.limits.logins_per_hour) == (3, 5, 5, 2, 5, 10)
    assert config.upstream == 'http://127.0.0.1:__HARNESS_PORT__'
    assert config.disk_path == Path('__HARNESS_DATA__') and config.min_free_gb == 150 and config.retention_days == 7
    assert config.lab_codex == dict(CODEX, runs_per_day=6)
    assert len(config.stop_files) == 1 and 'no-new-runs' not in config.stop_files[0]


@pytest.mark.parametrize('field,value', [('logins_per_hour', 0), ('logins_per_code_per_hour', -1), ('logins_global_per_hour', True), ('logins_global_per_hour', 1.5)])
def test_login_limits_are_positive_integers(field, value):
    with pytest.raises(ValueError):Limits(**{field: value})


@pytest.mark.parametrize('change', [dict(trusted_proxy=1), dict(min_free_gb=-1), dict(min_free_gb=float('nan')), dict(min_free_gb=float('inf')),
    dict(retention_days=0), dict(retention_days=True), dict(lab_codex={}), dict(lab_codex=dict(CODEX, stop_percent=101)),
    dict(lab_codex=dict(CODEX, runs_per_day=0)), dict(lab_codex=dict(CODEX, effort='bogus')), dict(lab_codex=dict(CODEX, effort='low')), dict(lab_codex=dict(CODEX, model='unsupported'))])
def test_new_config_validation(change, tmp_path):
    with pytest.raises(ValueError):Config(state_dir=tmp_path, upstream='http://127.0.0.1:1', ui_dir=tmp_path, **change)


@pytest.mark.parametrize('days', [0, -1, True])
def test_prune_rejects_invalid_days(retention, days):
    with pytest.raises(ValueError):prune(retention.store, retention.root, days=days)


def test_campus_scripts_parse_and_ufw_default_only_prints():
    for path in DEPLOY.glob('*.sh'):subprocess.run(['sh', '-n', str(path)], check=True, capture_output=True)
    result = subprocess.run(['sh', str(DEPLOY / 'ufw-rules.sh'), '192.0.2.1', '192.0.2.0/24', '198.51.100.0/24'], check=True, text=True, capture_output=True)
    assert result.stdout.splitlines() == ['ufw allow from 192.0.2.0/24 to 192.0.2.1 port 443 proto tcp', 'ufw allow from 198.51.100.0/24 to 192.0.2.1 port 443 proto tcp']
    result = subprocess.run(['sh', str(DEPLOY / 'ufw-rules.sh'), '192.0.2.1', 'bad;command'], text=True, capture_output=True)
    assert result.returncode != 0 and not result.stdout


def test_ufw_apply_refuses_without_root(monkeypatch):
    script = (DEPLOY / 'ufw-rules.sh').read_text().split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
    monkeypatch.setattr(sys, 'argv', ['-', '--apply', '192.0.2.1', '192.0.2.0/24'])
    monkeypatch.setattr('os.geteuid', lambda:1000)
    def forbidden(*args, **kwargs):raise AssertionError('must not execute ufw')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    with pytest.raises(SystemExit, match='requires root'):exec(compile(script, 'ufw-rules.sh', 'exec'), {})


@pytest.fixture
def launcher_home(tmp_path, monkeypatch):
    home = tmp_path / 'home';home.mkdir()
    (home / '.litellm-key').write_text('decoy-home-key')
    monkeypatch.setenv('HOME', str(home))
    for name in ('PARCC_API_KEY', 'LITELLM_KEY', 'ATTENTION_NO_HOME_LAB_KEY', 'CREDENTIALS_DIRECTORY'):
        monkeypatch.delenv(name, raising=False)
    # Use the actual installed-workspace environment builder, without an install.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'scripts/attentionbench'))
    return home


@pytest.mark.parametrize('source', ['stdin', 'credential', 'none'])
@pytest.mark.parametrize('present', [False, True])
def test_launcher_explicit_key_sources_never_read_home(launcher_home, tmp_path, monkeypatch, source, present):
    credentials = tmp_path / 'credentials';credentials.mkdir()
    (credentials / 'parcc-key').write_text('credential-key\n' if present else '')
    monkeypatch.setenv('CREDENTIALS_DIRECTORY', str(credentials))
    monkeypatch.setattr(sys, 'stdin', io.StringIO('stdin-key\nignored-second-line\n' if present else ''))
    monkeypatch.setenv('PARCC_API_KEY', 'inherited-parcc-decoy')
    monkeypatch.setenv('LITELLM_KEY', 'inherited-litellm-decoy')
    hidden = os.pathsep.join([str(tmp_path / 'gateway-state'), str(credentials / 'parcc-key')])
    monkeypatch.setenv('ATTENTION_PREFLIGHT_HIDDEN_PATHS', hidden)
    original = Path.read_text
    def read(path, *args, **kwargs):
        assert path != launcher_home / '.litellm-key', 'must not read the home key'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read)
    env = launcher.build_env(tmp_path, key_source=source)
    assert env['ATTENTION_NO_HOME_LAB_KEY'] == '1'
    assert env['ATTENTION_PREFLIGHT_HIDDEN_PATHS'] == hidden
    assert 'LITELLM_KEY' not in env
    expected = source + '-key' if present and source != 'none' else None
    assert env.get('PARCC_API_KEY') == expected
    assert os.environ['PARCC_API_KEY'] == 'inherited-parcc-decoy'
    assert all(value != 'decoy-home-key' for value in env.values())


@pytest.mark.parametrize('directory', [None, ''])
def test_launcher_credential_requires_systemd_directory(launcher_home, tmp_path, monkeypatch, directory):
    if directory is not None:monkeypatch.setenv('CREDENTIALS_DIRECTORY', directory)
    with pytest.raises(SystemExit, match='requires CREDENTIALS_DIRECTORY'):
        launcher.build_env(tmp_path, key_source='credential')


def test_launcher_missing_credential_does_not_fall_back(launcher_home, tmp_path, monkeypatch):
    monkeypatch.setenv('CREDENTIALS_DIRECTORY', str(tmp_path))
    with pytest.raises(FileNotFoundError):launcher.build_env(tmp_path, key_source='credential')


@pytest.mark.parametrize('source', [None, 'home'])
def test_launcher_legacy_and_explicit_home_source(launcher_home, tmp_path, source):
    # As before this change: the launcher reads no key file; the harness may still fall back to ~/.litellm-key itself.
    env = launcher.build_env(tmp_path, key_source=source)
    assert 'PARCC_API_KEY' not in env
    assert 'ATTENTION_NO_HOME_LAB_KEY' not in env


def test_launcher_legacy_environment_key_precedes_home(launcher_home, tmp_path, monkeypatch):
    monkeypatch.setenv('PARCC_API_KEY', 'environment-key')
    assert launcher.build_env(tmp_path)['PARCC_API_KEY'] == 'environment-key'


@pytest.mark.parametrize('explicit_secret', [False, True])
def test_launcher_cli_secret_fd_and_separate_harness_data(launcher_home, tmp_path, monkeypatch, capsys, explicit_secret):
    data = tmp_path / 'harness-data';data.mkdir()
    state = tmp_path / 'gateway-state';state.mkdir(mode=0o700)
    secret = (state if explicit_secret else data) / 'gateway.secret'
    secret.write_text('gateway-secret-canary');secret.chmod(0o600)
    workspace = tmp_path / 'workspace'
    calls, child = [], {}
    monkeypatch.setattr(sys, 'stdin', io.StringIO('stdin-key-canary\nnot-a-key\n'))
    def execute(python, argv, env):child.update(python=python, argv=argv, env=env)
    monkeypatch.setattr(launcher, 'os', SimpleNamespace(
        environ=os.environ, O_RDONLY=os.O_RDONLY,
        open=lambda path, mode:(calls.append(('open', path, mode)), 9)[1],
        dup2=lambda *args:calls.append(('dup2', *args)), close=lambda fd:calls.append(('close', fd)),
        set_inheritable=lambda *args:calls.append(('inheritable', *args)),
        chdir=lambda path:calls.append(('chdir', path)), execve=execute))
    argv = [str(workspace), str(data), '--key-stdin']
    if explicit_secret:argv = [str(workspace), str(data), '8870', '--secret', str(secret), '--key-source', 'stdin']
    launcher.cli(argv)
    assert ('open', secret, os.O_RDONLY) in calls
    assert ('dup2', 9, 3) in calls and ('close', 9) in calls and ('inheritable', 3, True) in calls
    options = child['argv']
    for flag, expected in [('--store', data / 'attention.sqlite3'), ('--artifact-root', data / 'artifacts'),
                           ('--dev-v2-root', data / 'runs'), ('--sim-lock', data / 'sim.lock'),
                           ('--gateway-secret-fd', '3'), ('--port', '8870' if explicit_secret else '8860')]:
        assert options[options.index(flag) + 1] == str(expected)
    assert child['env']['PARCC_API_KEY'] == 'stdin-key-canary'
    assert child['env']['ATTENTION_NO_HOME_LAB_KEY'] == '1'
    assert 'stdin-key-canary' not in repr(options) + repr(os.environ) + repr(capsys.readouterr())
    assert 'gateway-secret-canary' not in repr(child)
    assert list(data.iterdir()) == ([] if explicit_secret else [secret])


@pytest.mark.parametrize('mode', [0o640, 0o604])
def test_launcher_secret_must_remain_owner_only(launcher_home, tmp_path, mode):
    secret = tmp_path / 'gateway.secret';secret.write_text('secret');secret.chmod(mode)
    with pytest.raises(SystemExit, match='owner only'):
        launcher.main(tmp_path, tmp_path / 'harness-data', secret=secret, key_source='none')


def test_campus_unit_wires_separate_data_credentials_and_hidden_paths():
    import shlex
    unit = (DEPLOY / 'attentionbench-public-harness.service').read_text()
    command = shlex.split(next(line.split('=', 1)[1] for line in unit.splitlines() if line.startswith('ExecStart=')))
    assert command[command.index('__WORKSPACE__') + 1:command.index('__WORKSPACE__') + 3] == ['__HARNESS_DATA__', '__PORT__']
    assert command[command.index('--secret') + 1] == '__DATA__/gateway-state/gateway.secret'
    assert command[command.index('--key-source') + 1] == 'credential'
    assert 'LoadCredential=parcc-key:__KEY_FILE__' in unit
    assert 'ATTENTION_PREFLIGHT_HIDDEN_PATHS=__DATA__/gateway-state:__DATA__/gateway-state/gateway.secret:%d/parcc-key' in unit


def test_paused_notice_says_why_when_the_stop_file_has_a_line(app):
    # 11-10: abctl writes one plain line while it deploys; visitors see that instead of a bare "paused".
    app.gateway.config.pause_file.write_text('Remote is updating. New runs open again in about a minute.\n')
    assert request(app, 'GET', '/api/lobby').value['availability']['message'] == 'Remote is updating. New runs open again in about a minute.'
    page = request(app, 'GET', '/ui/').raw
    assert b'Remote is updating.' in page and b'#211a0d' not in page and b'border-radius:999px' in page
