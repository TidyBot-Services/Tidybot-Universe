"""The gateway's own behaviour against a stand-in harness that checks every signature."""
import hashlib
import hmac
import http.client
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import stat
import threading
from urllib.parse import urlencode

import pytest

from benchmarks.attention_harness.tests.fakes.byok_http import loopback
from remote_gateway import server as gateway_server
from remote_gateway.config import Config, Limits
from remote_gateway.upstream import control_token

SECRET = b's' * 48
OWN = 'devexp-' + 'a' * 24
OTHER = 'devexp-' + 'b' * 24
CELL = 'c' * 64
UI = Path(__file__).resolve().parents[2] / 'benchmarks/attention_harness/ui'
PARCC = lambda: dict(credential='lab', provider='parcc', model='parcc/GLM')
SELECTION = dict(request_key='client-key-1', tasks=['open_cabinet'], seeds=[102], policies=['autonomous'], mode='interactive', help_budget=2,
                 agent_provider=dict(roles=dict(dev=PARCC(), eval=PARCC(), advisor=PARCC())))
OPTIONS = dict(dev_v2=dict(profiles=[dict(task_id='open_cabinet', enabled=True)], agent_policies=dict(policies=[dict(policy_id='autonomous')])), profiles=[dict(id='formal')])


class Harness:
    """Answers like the harness and refuses what the harness would refuse."""
    def __init__(self):
        self.calls = [];self.rows = [];self.credentials = [];self.made = 0;self.start_status = 200;self.down = False
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):pass
            def answer(self, value, status=200, kind='application/json', headers=None):
                raw = value if isinstance(value, bytes) else json.dumps(value).encode()
                self.send_response(status);self.send_header('Content-Type', kind);self.send_header('Content-Length', str(len(raw)))
                for name, text in (headers or {}).items():self.send_header(name, text)
                self.end_headers();self.wfile.write(raw)
            def serve(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                owner, auth = self.headers.get('X-Attention-Owner'), self.headers.get('X-Attention-Gateway-Auth', '')
                match = re.fullmatch(r'v1\.(\d+)\.([a-f0-9]{64})', auth)
                expected = match and hmac.new(SECRET, '\n'.join((owner, match[1], self.command, self.path, hashlib.sha256(body).hexdigest())).encode(), hashlib.sha256).hexdigest()
                if not match or expected != match[2] or self.headers.get('X-Attention-Token') != control_token(SECRET):return self.answer(dict(error='unsigned'), 401)
                outer.calls.append((self.command, self.path, owner, body, dict(self.headers)))
                path = self.path
                if path == '/api/run-options':return self.answer(OPTIONS)
                if path == '/api/runs':return self.answer(dict(runs=[dict(run_id='operator')], launches=[1], dev_sessions=outer.rows))
                if path == '/api/provider/credentials':return self.answer(outer.credentials)
                if path == '/api/provider/presets':return self.answer([dict(id='openai'), dict(id='openai-compatible')])
                if path == '/api/dev-v2/experiments' and self.command == 'POST':
                    outer.made += 1
                    return self.answer(dict(experiment_id=OWN[:-1] + str(outer.made), cells=[dict(key=CELL, artifact_dir='/home/someone/run')], owner='operator'))
                if path.endswith('/run'):return self.answer(dict(error='cannot start at /home/someone/x') if outer.start_status != 200 else dict(worker_started=True), outer.start_status)
                if path.endswith('/video.mp4'):
                    if self.headers.get('Range') == 'bytes=2-5':return self.answer(b'2345', 206, 'video/mp4', {'Content-Range': 'bytes 2-5/10', 'Accept-Ranges': 'bytes'})
                    return self.answer(b'0123456789', 200, 'video/mp4')
                return self.answer(dict(path=path, artifact_dir='/home/someone/run', state='running'))
            do_GET = do_POST = do_PUT = do_DELETE = serve
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self):return 'http://127.0.0.1:%d' % self.server.server_address[1]
    def paths(self):return [call[1] for call in self.calls]
    def close(self):self.server.shutdown();self.server.server_close()


class Browser:
    def __init__(self, port):self.port, self.cookie, self.csrf = port, '', ''

    def send(self, method, path, body=None, headers=None, raw=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        send = {**({'Cookie': self.cookie} if self.cookie else {}), **({'Content-Type': 'application/json'} if body is not None else {}), **(headers or {})}
        if method != 'GET' and self.csrf and 'X-Attention-Token' not in send:send['X-Attention-Token'] = self.csrf
        connection.putrequest(method, path, skip_accept_encoding=True)
        send['Content-Length'] = str(len(data or b''))
        for name, value in send.items():connection.putheader(name, value)
        connection.endheaders(data)
        response = connection.getresponse();content = response.read();connection.close()
        return response.status, content, response

    def login(self, code):
        status, _, response = self.send('POST', '/login', raw=urlencode(dict(code=code)).encode(), headers={'Content-Type': 'application/x-www-form-urlencoded'})
        cookie = response.getheader('Set-Cookie')
        if cookie:
            self.cookie = cookie.split(';')[0]
            page = self.send('GET', '/ui/')[1].decode()
            self.csrf = re.search(r'data-control-token="([^"]+)"', page)[1]
        return status, response

    def json(self, method, path, body=None, **kw):
        status, content, _ = self.send(method, path, body, **kw)
        return status, json.loads(content)


@pytest.fixture
def world(tmp_path, loopback):
    harness = Harness()
    config = Config(state_dir=tmp_path / 'state', upstream=harness.url, ui_dir=UI, listen_port=0, cookie_secure=True, limits=Limits(requests_per_second=1000, request_burst=1000, logins_per_hour=5))
    config.state_dir.mkdir()
    now = [1000000.0]
    gateway = gateway_server.create_server(config, secret=SECRET, clock=lambda: now[0])
    thread = threading.Thread(target=gateway.serve_forever, daemon=True);thread.start()
    port = gateway.server_address[1]

    class World:
        def person(self):
            browser = Browser(port);_, code = gateway.gateway.store.create_invite()
            assert browser.login(code)[0] == 303
            return browser
    value = World();value.harness, value.gateway, value.config, value.now, value.port = harness, gateway, config, now, port
    # Upstream signatures use the gateway's clock; the stand-in does not check freshness, the real harness test does.
    yield value
    gateway.shutdown();gateway.server_close();harness.close()


def owned(world, browser, experiment=OWN):
    user = browser.json('GET', '/api/session')[1]['user']
    world.gateway.gateway.store.record_run(experiment, user, 'local')
    return user


def test_nothing_is_open_before_login(world):
    anonymous = Browser(world.port)
    for path in ('/ui/', '/ui/live.js', '/'):
        status, _, response = anonymous.send('GET', path)
        assert status == 303 and response.getheader('Location') == '/login'
    for method, path in (('GET', '/api/runs'), ('GET', '/api/run-options'), ('GET', f'/api/dev-v2/experiments/{OWN}'), ('POST', '/api/dev-v2/experiments'), ('GET', '/api/provider/presets')):
        assert anonymous.send(method, path)[0] == 401
    assert world.harness.calls == []


def test_login_page_carries_no_script_and_a_tight_policy(world):
    status, content, response = Browser(world.port).send('GET', '/login')
    assert status == 200 and b'<script' not in content and b'name="code"' in content
    assert "default-src 'none'" in response.getheader('Content-Security-Policy')


def test_wrong_codes_are_refused_then_limited(world):
    browser = Browser(world.port)
    for _ in range(5):
        status, response = browser.login('wrong-code')
        assert status == 303 and response.getheader('Location') == '/login?failed=1'
    assert browser.login('wrong-code')[0] == 429
    _, code = world.gateway.gateway.store.create_invite()
    assert browser.login(code)[0] == 429                       # Limited even with a good code, until the hour turns.
    world.now[0] += 3600
    assert browser.login(code)[0] == 303


def test_the_session_cookie_cannot_be_read_or_sent_cross_site(world):
    _, code = world.gateway.gateway.store.create_invite()
    _, response = Browser(world.port).login(code)
    cookie = response.getheader('Set-Cookie')
    assert 'HttpOnly' in cookie and 'SameSite=Strict' in cookie and 'Secure' in cookie
    assert code not in json.dumps(world.gateway.gateway.store.invites())


def test_one_code_is_one_person_and_revoking_ends_the_session(world):
    store = world.gateway.gateway.store;invite, code = store.create_invite()
    first, second = Browser(world.port), Browser(world.port)
    first.login(code);second.login(code)
    assert first.json('GET', '/api/session')[1]['user'] == second.json('GET', '/api/session')[1]['user']
    assert store.revoke_invite(invite)
    assert first.send('GET', '/api/session')[0] == 401
    assert Browser(world.port).login(code)[1].getheader('Location') == '/login?failed=1'


def test_an_expired_code_and_an_expired_session_do_not_open(world):
    store = world.gateway.gateway.store;_, code = store.create_invite(days=1)
    world.now[0] += 2 * 86400
    assert Browser(world.port).login(code)[1].getheader('Location') == '/login?failed=1'
    browser = world.person()
    world.now[0] += 25 * 3600
    assert browser.send('GET', '/api/session')[0] == 401


def test_the_page_is_the_main_ui_without_any_harness_token(world):
    browser = world.person()
    status, content, response = browser.send('GET', '/ui/')
    page = content.decode()
    assert status == 200 and 'data-gateway="1"' in page and f'data-control-token="{browser.csrf}"' in page
    assert control_token(SECRET) not in page and 'onload=' not in page.split('</head>')[0]
    policy = response.getheader('Content-Security-Policy')
    assert "script-src 'self' 'sha256-" in policy and "frame-ancestors 'none'" in policy and "'unsafe-inline' https" in policy
    assert response.getheader('X-Content-Type-Options') == 'nosniff' and response.getheader('Referrer-Policy') == 'no-referrer'
    assert browser.send('GET', '/ui/live.js')[0] == 200
    for name in ('memory.html', 'home.html', 'fixed.html', 'index.html', 'remote'):assert browser.send('GET', '/ui/' + name)[0] == 404


@pytest.mark.parametrize('method,path', [
    ('GET', '/api/orchestrator/snapshot'), ('POST', '/api/orchestrator/dispatch'), ('POST', '/api/runs/start'), ('POST', '/api/fixed/start'), ('GET', '/api/fixed'), ('GET', '/api/traces/x'),
    ('GET', '/api/codex-quota'), ('GET', '/api/home/status'), ('GET', '/api/home/scene'), ('GET', '/api/memories'), ('GET', '/api/dev-memories'), ('POST', '/api/history/delete'),
    ('GET', '/api/dev/sessions/x'), ('POST', '/api/dev/start'), ('GET', '/api/dev-v2/options'), ('GET', '/api/dev-v2/experiments'), ('GET', '/api/dev-v2/memory-monitor'),
    ('GET', '/api/dev-v2/automatic-checks'), ('GET', '/api/station/frame'), ('GET', '/api/lobby'), ('GET', '/api/runs/operator'), ('POST', '/api/requests/x/respond')])
def test_operator_routes_do_not_exist_here(world, method, path):
    browser = world.person();before = len(world.harness.calls)
    assert browser.send(method, path, {} if method == 'POST' else None)[0] == 404
    assert len(world.harness.calls) == before


def test_writes_need_the_sessions_token(world):
    browser = world.person()
    for token in ('', 'wrong', control_token(SECRET)):
        assert browser.send('POST', '/api/dev-v2/experiments', dict(selection=SELECTION), headers={'X-Attention-Token': token})[0] == 403
    assert not any(call[0] == 'POST' for call in world.harness.calls)


@pytest.mark.parametrize('method,suffix', [('GET', ''), ('GET', '/report'), ('GET', f'/cells/{CELL}/agent/view'), ('GET', f'/cells/{CELL}/live/frame'), ('POST', '/run'), ('POST', '/cancel'), ('POST', '/pause')])
def test_someone_elses_run_does_not_exist(world, method, suffix):
    owner, other = world.person(), world.person()
    owned(world, owner);before = len(world.harness.calls)
    status, content, _ = other.send(method, f'/api/dev-v2/experiments/{OWN}{suffix}', {} if method == 'POST' else None)
    assert status == 404 and json.loads(content)['code'] == 'not_found'
    assert len(world.harness.calls) == before
    assert other.send('GET', f'/api/dev-v2/experiments/{OTHER}')[0] == 404


@pytest.mark.parametrize('suffix', ['/ledger.json', '/export.csv', '/fork-preview', f'/cells/{CELL}/files/policy.py', f'/cells/{CELL}/agent/files/%2e%2e/%2e%2e/x',
                                    f'/cells/{CELL}/agent/files/%252e%252e/x', f'/cells/{CELL}/agent/files/a//b', f'/cells/{CELL}/agent/files/.env'])
def test_even_an_owner_reads_only_the_public_parts(world, suffix):
    browser = world.person();owned(world, browser);before = len(world.harness.calls)
    assert browser.send('GET', f'/api/dev-v2/experiments/{OWN}{suffix}')[0] == 404
    assert len(world.harness.calls) == before


@pytest.mark.parametrize('command', ['switch', 'run-note-off', 'approvals/x/decision'])
def test_an_owner_cannot_use_operator_commands(world, command):
    browser = world.person();owned(world, browser)
    assert browser.send('POST', f'/api/dev-v2/experiments/{OWN}/{command}', {})[0] == 404


def test_commands_take_no_settings(world):
    browser = world.person();owned(world, browser)
    assert browser.send('POST', f'/api/dev-v2/experiments/{OWN}/cancel', dict(force=True))[0] == 400
    assert browser.send('POST', f'/api/dev-v2/experiments/{OWN}/cancel', {})[0] == 200


def test_a_run_is_created_from_named_fields_then_started(world):
    browser = world.person()
    status, made = browser.json('POST', '/api/dev-v2/experiments', dict(selection={**SELECTION, 'quota_stop_percent': 1, 'execution_target': 'real_robot', 'goal_key': 'operators-goal'}, start=True))
    assert status == 200 and made['cells'][0]['key'] == CELL
    assert 'artifact_dir' not in made['cells'][0] and 'owner' not in made
    create = next(call for call in world.harness.calls if call[:2] == ('POST', '/api/dev-v2/experiments'))
    sent = json.loads(create[3])
    assert sent['start'] is False and sent['selection']['quota_stop_percent'] == 90 and 'execution_target' not in sent['selection']
    assert sent['selection']['agent_limits'] == dict(versions=3, wall_seconds=3600) and sent['selection']['goal_key'].startswith('gw-u-')
    assert world.harness.paths()[-1] == f'/api/dev-v2/experiments/{made["experiment_id"]}/run'
    assert browser.send('GET', f'/api/dev-v2/experiments/{made["experiment_id"]}')[0] == 200


def test_a_run_that_cannot_start_stays_owned_and_the_error_loses_its_paths(world):
    browser = world.person();world.harness.start_status = 409
    status, value = browser.json('POST', '/api/dev-v2/experiments', dict(selection=SELECTION, start=True))
    assert status == 409 and value['error'] == 'cannot start at [host path]'
    assert len(world.gateway.gateway.store.runs_of(browser.json('GET', '/api/session')[1]['user'])) == 1


def test_create_only_does_not_start(world):
    browser = world.person()
    assert browser.send('POST', '/api/dev-v2/experiments', dict(selection=SELECTION, start=False))[0] == 200
    assert not any(path.endswith('/run') for path in world.harness.paths())


def test_the_daily_limit_and_a_run_in_progress_are_refused(world):
    browser = world.person();key = lambda n: dict(selection={**SELECTION, 'request_key': f'client-key-{n}'}, start=False)
    for n in range(3):assert browser.send('POST', '/api/dev-v2/experiments', key(n))[0] == 200
    status, value = browser.json('POST', '/api/dev-v2/experiments', key(9))
    assert status == 429 and value['code'] == 'daily_limit'
    with world.gateway.gateway.store.tx() as db:db.execute('UPDATE runs SET created_at=created_at-86401')   # A day later.
    user = browser.json('GET', '/api/session')[1]['user']
    world.harness.rows = [dict(experiment_id=next(iter(world.gateway.gateway.store.runs_of(user))), experiment_state='running')]
    status, value = browser.json('POST', '/api/dev-v2/experiments', key(10))
    assert status == 409 and value['code'] == 'run_active'
    second = sorted(world.gateway.gateway.store.runs_of(user))[-1]
    world.harness.rows[0]['experiment_id'] = sorted(world.gateway.gateway.store.runs_of(user))[0]
    assert browser.json('POST', f'/api/dev-v2/experiments/{second}/run', {})[1]['code'] == 'run_active'


@pytest.mark.parametrize('how', ['pause', 'stop'])
def test_paused_means_no_new_run_anywhere(world, how, tmp_path):
    browser = world.person();owned(world, browser)
    if how == 'pause':world.config.pause_file.touch()
    else:
        flag = tmp_path / 'no-new-runs';flag.touch()
        object.__setattr__(world.config, 'stop_files', (str(flag),))
    assert browser.json('POST', '/api/dev-v2/experiments', dict(selection=SELECTION))[1]['code'] == 'runs_paused'
    assert browser.json('POST', f'/api/dev-v2/experiments/{OWN}/run', {})[1]['code'] == 'runs_paused'
    assert browser.send('POST', f'/api/dev-v2/experiments/{OWN}/cancel', {})[0] == 200


def test_lists_show_ones_own_runs_only(world):
    first, second = world.person(), world.person()
    owned(world, first);owned(world, second, OTHER)
    world.harness.rows = [dict(experiment_id=OWN, artifact_dir='/home/someone/a', state='completed'), dict(experiment_id=OTHER, state='completed')]
    value = first.json('GET', '/api/runs')[1]
    assert value == dict(runs=[], launches=[], dev_sessions=[dict(experiment_id=OWN, state='completed')])


def test_options_hide_fixed_profiles_and_carry_the_limits(world):
    value = world.person().json('GET', '/api/run-options')[1]
    assert 'profiles' not in value and value['hosted']['limits']['wall'] == 3600 and value['dev_v2']['goal_key'] == 'set-by-host'


def test_the_owner_on_every_call_is_the_person_logged_in(world):
    browser = world.person();user = browser.json('GET', '/api/session')[1]['user']
    browser.send('GET', '/api/runs');browser.send('GET', '/api/provider/credentials')
    assert {call[2] for call in world.harness.calls} == {user}


def test_keys_pass_through_and_are_never_kept(world):
    browser = world.person();canary = 'sk-canary-' + 'k' * 40
    assert browser.send('PUT', '/api/provider/credentials/my-key', dict(provider='deepseek', key=canary))[0] == 200
    assert [json.loads(call[3]) for call in world.harness.calls if call[0] == 'PUT'] == [dict(provider='deepseek', key=canary)]
    for path in world.config.state_dir.iterdir():assert canary.encode() not in path.read_bytes(), path
    assert b'my-key' not in world.config.audit_file.read_bytes()


def test_key_routes_are_only_the_ones_the_form_uses(world):
    browser = world.person()
    assert browser.json('GET', '/api/provider/presets')[1] == [dict(id='openai')]
    assert browser.send('PUT', '/api/provider/credentials/lab', dict(provider='parcc', key='x'))[0] == 400
    assert browser.send('GET', '/api/provider/credentials?all=1')[0] == 404
    assert browser.send('POST', '/api/provider/credentials/x', {})[0] == 404
    assert browser.send('GET', '/api/provider/credentials/x/test')[0] == 404
    assert browser.send('PUT', '/api/provider/credentials', dict(provider='deepseek', key='x'))[0] == 404
    assert browser.send('PUT', '/api/provider/credentials/x', raw=b'x' * 9000, headers={'Content-Type': 'application/json'})[0] == 413


def test_video_streams_with_range_and_is_not_rewritten(world):
    browser = world.person();owned(world, browser)
    path = f'/api/dev-v2/experiments/{OWN}/cells/{CELL}/agent/files/replays/e1/video.mp4'
    status, content, response = browser.send('GET', path)
    assert (status, content, response.getheader('Content-Type')) == (200, b'0123456789', 'video/mp4')
    status, content, response = browser.send('GET', path, headers={'Range': 'bytes=2-5'})
    assert (status, content, response.getheader('Content-Range')) == (206, b'2345', 'bytes 2-5/10')


def test_too_many_requests_are_slowed(world):
    browser = world.person()
    world.gateway.gateway.rate.limits = Limits(requests_per_second=1, request_burst=2)
    statuses = [browser.send('GET', '/api/session')[0] for _ in range(4)]
    assert statuses[-1] == 429 and 200 in statuses
    assert browser.send('GET', '/ui/live.js')[0] == 200        # The page itself is never rationed.


def test_a_lost_run_host_is_said_plainly(world):
    browser = world.person();world.harness.close()
    status, value = browser.json('GET', '/api/runs')
    assert status == 502 and value['code'] == 'run_host_unavailable' and 'Traceback' not in json.dumps(value)
    world.harness.close = lambda: None


def test_the_log_records_what_happened_and_nothing_said(world):
    _, code = world.gateway.gateway.store.create_invite()
    browser = Browser(world.port);browser.login(code)
    owned(world, browser);browser.send('GET', f'/api/dev-v2/experiments/{OWN}')
    raw = world.config.audit_file.read_text()
    assert code not in raw and browser.cookie.split('=')[1] not in raw and browser.csrf not in raw and OWN not in raw
    rows = [json.loads(line) for line in raw.splitlines()]
    assert rows[-1]['route'] == '/api/dev-v2/experiments/{experiment}' and rows[-1]['status'] == 200 and rows[-1]['user'].startswith('u-')
    assert set(rows[-1]) == {'at', 'user', 'method', 'route', 'status', 'ms'}


def test_malformed_requests_get_a_plain_answer(world):
    browser = world.person();owned(world, browser)
    for body in ([], 'x', dict(selection=[]), dict(selection=dict(tasks=[[]], seeds=[{}], policies=[None])), dict(selection={**SELECTION, 'agent_limits': 'x', 'agent_provider': 'x'})):
        status, value = browser.json('POST', '/api/dev-v2/experiments', body)
        assert status == 400 and set(value) == {'error', 'code'}, body
    assert browser.send('POST', f'/api/dev-v2/experiments/{OWN}/cancel?force=1', {})[0] == 404
    assert browser.send('POST', '/api/dev-v2/experiments', raw=b'{', headers={'Content-Type': 'application/json'})[0] == 400
    assert browser.send('POST', '/api/dev-v2/experiments', raw=b'{}', headers={'Content-Type': 'text/plain'})[0] == 400


def test_an_unexpected_failure_says_nothing_about_itself(world, monkeypatch):
    browser = world.person()
    monkeypatch.setattr(world.gateway.gateway.store, 'runs_of', lambda user: 1 / 0)
    status, value = browser.json('GET', '/api/runs')
    assert status == 500 and value == dict(error='Something went wrong.', code='internal')


def test_an_absolute_request_target_is_refused(world):
    browser = world.person();owned(world, browser)
    assert browser.send('GET', f'http://evil.example/api/dev-v2/experiments/{OWN}')[0] == 404


def test_signing_out_ends_the_session(world):
    browser = world.person()
    status, _, response = browser.send('DELETE', '/api/session')
    assert status == 200 and 'Max-Age=0' in response.getheader('Set-Cookie')
    assert browser.send('GET', '/api/session')[0] == 401


def test_state_files_are_private(world):
    for path in (world.config.database, world.config.audit_file):assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_group_readable_secret_is_refused(tmp_path):
    config = Config(state_dir=tmp_path, upstream='http://127.0.0.1:1', ui_dir=tmp_path)
    config.secret_file.write_bytes(b'x' * 48);config.secret_file.chmod(0o640)
    with pytest.raises(ValueError, match='readable by its owner'):config.secret()
    config.secret_file.chmod(0o600)
    assert config.secret() == b'x' * 48
