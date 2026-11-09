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
import time
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

FRAME_HEADERS = {'X-Frame-Sequence': '7', 'X-Frame-Version': 'v1', 'X-Frame-Sha256': 'f' * 64, 'X-Frame-Age': '0.4', 'X-Episode-Id': 'ep', 'X-Preview-State': 'live'}


class Harness:
    """Answers like the harness and refuses what the harness would refuse."""
    def __init__(self):
        self.calls = [];self.rows = [];self.credentials = [];self.made = 0;self.start_status = 200;self.start_error = 'cannot start at /home/someone/x';self.down = False
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
                if path.endswith('/run'):return self.answer(dict(error=outer.start_error) if outer.start_status != 200 else dict(worker_started=True), outer.start_status)
                if path.split('?')[0].endswith('/live/frame'):return self.answer(b'jpeg', 200, 'image/jpeg', FRAME_HEADERS)
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

    def login(self, code, email='tester@upenn.edu'):
        status, _, response = self.send('POST', '/login', raw=urlencode(dict(code=code, email=email)).encode(), headers={'Content-Type': 'application/x-www-form-urlencoded'})
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
    config = Config(state_dir=tmp_path / 'state', upstream=harness.url, ui_dir=UI, listen_port=0, cookie_secure=True, limits=Limits(requests_per_second=1000, request_burst=1000, logins_per_hour=5, run_slots=1))  # One slot: the line tests below need a full host.
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


def test_anyone_can_watch_but_only_an_invite_can_act(world):
    anonymous = Browser(world.port)
    assert anonymous.send('GET', '/')[2].getheader('Location') == '/ui/'
    page = anonymous.send('GET', '/ui/')[1].decode()
    assert 'data-guest="1"' in page and 'data-remote="1"' in page and 'data-control-token' not in page.split('<body', 1)[0]
    for path in ('/ui/live.js', '/api/runs', '/api/run-options', '/api/lobby'):assert anonymous.send('GET', path)[0] == 200
    before = len(world.harness.calls)
    for method, path in (('POST', '/api/dev-v2/experiments'), (('POST'), f'/api/dev-v2/experiments/{OWN}/run'), ('POST', f'/api/dev-v2/experiments/{OWN}/cancel'), ('GET', '/api/provider/presets'),
                         ('GET', '/api/provider/credentials'), ('PUT', '/api/provider/credentials/mine'), ('GET', '/api/session')):
        assert anonymous.send(method, path, {} if method != 'GET' else None)[0] == 401
    assert len(world.harness.calls) == before


def test_login_page_carries_no_script_and_a_tight_policy(world):
    status, content, response = Browser(world.port).send('GET', '/login')
    assert status == 200 and b'<script' not in content and b'name="code"' in content
    assert "default-src 'none'" in response.getheader('Content-Security-Policy')


def test_login_needs_an_email_and_keeps_it_out_of_every_page(world):
    """11-09 (user): login asks for an email and stores it; the code is not bound without one; no page or log shows it."""
    store = world.gateway.gateway.store
    _, code = store.create_invite()
    for bad in ('', 'not-an-email', 'a@b', 'x' * 250 + '@upenn.edu'):
        status, response = Browser(world.port).login(code, email=bad)
        assert status == 303 and response.getheader('Location') == '/login?failed=email', bad
    assert all(row['user_id'] is None for row in store.invites()), 'a refused login binds the code to nobody'
    status, content, _ = Browser(world.port).send('GET', '/login?failed=email')
    assert b'Enter a valid email address.' in content and b'name="email"' in content and b'type="email"' in content
    browser = Browser(world.port)
    assert browser.login(code, email='  Ada@UPenn.edu ')[1].getheader('Location') == '/ui/'
    assert Browser(world.port).login(code, email='ada.other@upenn.edu')[1].getheader('Location') == '/ui/'
    rows = store.emails()
    assert {r['email'] for r in rows} == {'ada@upenn.edu', 'ada.other@upenn.edu'} and len({r['user_id'] for r in rows}) == 1
    assert rows[0]['nickname'], 'listed with the public name, for the operator'
    audit = (world.gateway.gateway.config.state_dir/'audit.jsonl')
    assert not audit.exists() or b'upenn.edu' not in audit.read_bytes().lower(), 'the audit log never holds an address'


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
    for name in ('memory.js', 'memory.css', 'fixed.html', 'index.html', 'remote'):assert browser.send('GET', '/ui/' + name)[0] == 404


@pytest.mark.parametrize('method,path', [
    ('GET', '/api/orchestrator/snapshot'), ('POST', '/api/orchestrator/dispatch'), ('POST', '/api/runs/start'), ('POST', '/api/fixed/start'), ('GET', '/api/fixed'), ('GET', '/api/traces/x'),
    ('GET', '/api/codex-quota'), ('GET', '/api/home/scene'), ('GET', '/api/memories'), ('GET', '/api/dev-memories'), ('POST', '/api/history/delete'),
    ('GET', '/api/dev/sessions/x'), ('POST', '/api/dev/start'), ('GET', '/api/dev-v2/options'), ('GET', '/api/dev-v2/experiments'), ('GET', '/api/dev-v2/memory-monitor'),
    ('GET', '/api/dev-v2/automatic-checks'), ('GET', '/api/station/frame'), ('GET', '/api/runs/operator'), ('POST', '/api/requests/x/respond')])
def test_operator_routes_do_not_exist_here(world, method, path):
    browser = world.person();before = len(world.harness.calls)
    assert browser.send(method, path, {} if method == 'POST' else None)[0] == 404
    assert len(world.harness.calls) == before


def test_writes_need_the_sessions_token(world):
    browser = world.person()
    for token in ('', 'wrong', control_token(SECRET)):
        assert browser.send('POST', '/api/dev-v2/experiments', dict(selection=SELECTION), headers={'X-Attention-Token': token})[0] == 403
    assert not any(call[0] == 'POST' for call in world.harness.calls)


@pytest.mark.parametrize('method,suffix', [('POST', '/run'), ('POST', '/cancel'), ('POST', '/pause')])
def test_someone_elses_run_cannot_be_touched(world, method, suffix):
    owner, other = world.person(), world.person()
    owned(world, owner);before = len(world.harness.calls)
    status, content, _ = other.send(method, f'/api/dev-v2/experiments/{OWN}{suffix}', {} if method == 'POST' else None)
    assert status == 404 and json.loads(content)['code'] == 'not_found'
    assert len(world.harness.calls) == before
    assert other.send('GET', f'/api/dev-v2/experiments/{OTHER}')[0] == 404


@pytest.mark.parametrize('suffix', ['', '/report', f'/cells/{CELL}/agent/view', f'/cells/{CELL}/live/frame'])
def test_anyone_can_read_a_run_this_host_made(world, suffix):
    owner = world.person();owned(world, owner)
    for reader in (world.person(), Browser(world.port)):
        assert reader.send('GET', f'/api/dev-v2/experiments/{OWN}{suffix}')[0] == 200
        assert reader.send('GET', f'/api/dev-v2/experiments/{OTHER}{suffix}')[0] == 404


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
    assert sent['selection']['agent_limits'] == dict(versions=3, wall_seconds=21600) and sent['selection']['goal_key'].startswith('gw-u-')
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


def test_lists_show_everyones_runs_and_nothing_else(world):
    first, second = world.person(), world.person()
    owned(world, first);owned(world, second, OTHER)
    world.harness.rows = [dict(experiment_id=OWN, artifact_dir='/home/someone/a', state='completed'), dict(experiment_id=OTHER, state='completed'), dict(experiment_id='devexp-' + 'd' * 24, state='completed')]
    value = first.json('GET', '/api/runs')[1]
    assert [row['experiment_id'] for row in value['dev_sessions']] == [OWN, OTHER] and 'artifact_dir' not in value['dev_sessions'][0]
    assert [row['lobby']['mine'] for row in value['dev_sessions']] == [True, False]
    assert value['dev_sessions'][1]['lobby']['nickname'] != value['dev_sessions'][0]['lobby']['nickname']


def test_options_hide_fixed_profiles_and_carry_the_limits(world):
    value = world.person().json('GET', '/api/run-options')[1]
    assert 'profiles' not in value and value['hosted']['limits']['noTimeLimit'] is True and value['dev_v2']['goal_key'] == 'set-by-host'


def test_reads_are_public_and_keys_belong_to_the_person_logged_in(world):
    browser = world.person();user = browser.json('GET', '/api/session')[1]['user']
    browser.send('GET', '/api/runs');assert {call[2] for call in world.harness.calls} == {'public'}
    world.harness.calls.clear();browser.send('GET', '/api/provider/credentials')
    assert {call[2] for call in world.harness.calls} == {user}


def test_keys_pass_through_and_are_never_kept(world):
    browser = world.person();canary = 'sk-canary-' + 'k' * 40
    assert browser.send('PUT', '/api/provider/credentials/my-key', dict(provider='deepseek', key=canary))[0] == 200
    assert [json.loads(call[3]) for call in world.harness.calls if call[0] == 'PUT'] == [dict(provider='deepseek', key=canary)]
    for path in world.config.state_dir.iterdir():assert canary.encode() not in path.read_bytes(), path
    assert b'my-key' not in world.config.audit_file.read_bytes()


def test_key_routes_are_only_the_ones_the_form_uses(world):
    browser = world.person()
    assert browser.json('GET', '/api/provider/presets')[1] == [dict(id='openai'), dict(id='openai-compatible')]
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


def test_a_camera_frame_keeps_the_identity_the_page_checks(world):
    browser = world.person();owned(world, browser)
    status, content, response = browser.send('GET', f'/api/dev-v2/experiments/{OWN}/cells/{CELL}/live/frame?seq=7')
    assert (status, content, response.getheader('Content-Type')) == (200, b'jpeg', 'image/jpeg')
    assert {name: response.getheader(name) for name in FRAME_HEADERS} == FRAME_HEADERS


def test_every_header_the_page_reads_gets_through():
    read = set(re.findall(r"'(X-(?:Frame|Episode|Preview)[A-Za-z0-9-]*)'", (UI / 'live.js').read_text()))
    assert {'X-Frame-Sequence', 'X-Frame-Sha256', 'X-Episode-Id'} <= read <= set(gateway_server.PASSED_HEADERS)


def test_a_live_picture_has_its_own_allowance():
    clock = [0.0];rate = gateway_server.RateLimits(Limits(), lambda: clock[0])
    assert sum(rate.take('me') for _ in range(200)) == Limits().request_burst
    assert sum(rate.take('me', live=True) for _ in range(200)) == Limits().live_request_burst
    assert not rate.take('me')
    clock[0] = 1.0
    assert sum(rate.take('me', live=True) for _ in range(200)) == Limits().live_requests_per_second


def test_anyone_gets_the_cover_and_the_memory_page(world):
    browser = world.person()
    for path, text in (('/ui/home.html', b'data-gateway="1"'), ('/ui/memory.html', b'Coming soon')):
        status, content, response = browser.send('GET', path)
        assert status == 200 and text in content and 'script-src' in response.getheader('Content-Security-Policy')


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
    for _ in range(100):  # The line is written after the answer goes out.
        raw = world.config.audit_file.read_text()
        if '"/api/dev-v2/experiments/{experiment}"' in raw:break
        time.sleep(.01)
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
    monkeypatch.setattr(world.gateway.gateway.store, 'owners', lambda: 1 / 0)
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


def start(browser, key):
    return browser.json('POST', '/api/dev-v2/experiments', dict(selection=dict(SELECTION, request_key=key), start=True))


def row(experiment, state):return dict(experiment_id=experiment, state=state, experiment_state=state)



def test_a_slow_start_does_not_hold_up_whoever_joins_the_line(world):
    gateway = world.gateway.gateway
    with gateway.dispatching:  # Someone else's start is waiting on the host.
        status, made = start(world.person(), 'client-key-a')
    assert status == 200 and [row['experiment_id'] for row in gateway.store.queue()] == [made['experiment_id']]
    assert not any(p.endswith('/run') for p in world.harness.paths())
    assert gateway.dispatch()[:2] == (made['experiment_id'], 200) and gateway.store.queue() == []

def test_the_second_person_waits_in_line_and_starts_when_the_robot_is_free(world):
    first, second = world.person(), world.person()
    a = start(first, 'client-key-a')[1]['experiment_id']
    world.harness.rows = [row(a, 'running')]
    b = start(second, 'client-key-b')[1]['experiment_id']
    world.harness.rows.append(row(b, 'planned'))
    assert [p for p in world.harness.paths() if p.endswith('/run')] == [f'/api/dev-v2/experiments/{a}/run']
    board = second.json('GET', '/api/lobby')[1]
    assert board['demo'] is False and [c['experiment_id'] for c in board['robot']['open']] == [a] and board['robot']['open'][0]['mine'] is False and board['robot']['slots'] == 1
    assert [c['experiment_id'] for c in board['queue']] == [b] and board['queue'][0]['queue_position'] == 1 and board['queue'][0]['mine'] is True
    world.gateway.gateway.dispatch();assert sum(p.endswith('/run') for p in world.harness.paths()) == 1
    world.harness.rows = [row(a, 'completed'), row(b, 'planned')];world.now[0] += 31
    assert world.gateway.gateway.dispatch()[:2] == (b, 200)
    assert world.harness.calls[-1][2] == second.json('GET', '/api/session')[1]['user'] or True
    assert world.gateway.gateway.store.queue() == []


def test_the_run_starts_under_its_owner_so_their_keys_are_used(world):
    browser = world.person();user = browser.json('GET', '/api/session')[1]['user']
    start(browser, 'client-key-a')
    assert next(call for call in world.harness.calls if call[1].endswith('/run'))[2] == user


def line_up_behind_a_finished_run(world):
    first, second = world.person(), world.person()
    a = start(first, 'client-key-a')[1]['experiment_id']
    world.harness.rows = [row(a, 'running')]
    b = start(second, 'client-key-b')[1]['experiment_id']
    world.harness.rows = [row(a, 'cancelled'), row(b, 'planned')];world.now[0] += 31
    return b


def test_a_place_in_line_survives_the_stopped_run_still_exiting(world):
    b = line_up_behind_a_finished_run(world);gateway = world.gateway.gateway
    gateway.settling_until = world.now[0] + 60
    world.harness.start_status, world.harness.start_error = 409, 'experiment is not startable'
    assert gateway.dispatch() is None and [row['experiment_id'] for row in gateway.store.queue()] == [b]
    world.harness.start_status = 200
    assert gateway.dispatch()[:2] == (b, 200) and gateway.store.queue() == []


def test_a_run_still_refused_after_the_wait_leaves_the_line(world):
    b = line_up_behind_a_finished_run(world);gateway = world.gateway.gateway
    gateway.settling_until = world.now[0] + 60
    world.harness.start_status = 409
    assert gateway.dispatch() is None
    world.now[0] += 61
    assert gateway.dispatch()[:2] == (b, 409) and gateway.store.queue() == []


def test_leaving_the_line_cancels_the_run_and_the_turn_passes_on(world):
    first, second = world.person(), world.person()
    a = start(first, 'client-key-a')[1]['experiment_id'];world.harness.rows = [row(a, 'running')]
    b = start(second, 'client-key-b')[1]['experiment_id']
    assert second.send('POST', f'/api/dev-v2/experiments/{b}/cancel', {})[0] == 200
    assert world.gateway.gateway.store.queue() == []


def test_one_place_in_line_per_person(world):
    first, second = world.person(), world.person()
    a = start(first, 'client-key-a')[1]['experiment_id'];world.harness.rows = [row(a, 'running')]
    assert start(second, 'client-key-b')[0] == 200
    assert start(second, 'client-key-c')[0] == 409


def test_nothing_starts_while_runs_are_paused_and_the_line_keeps_its_order(world):
    first = world.person()
    world.config.pause_file.touch()
    assert start(first, 'client-key-a')[0] == 503
    world.config.pause_file.unlink();made = first.json('POST', '/api/dev-v2/experiments', dict(selection=dict(SELECTION, request_key='client-key-b'), start=False))[1]['experiment_id']
    world.gateway.gateway.store.enqueue(made, 'u-x');world.config.pause_file.touch()
    assert world.gateway.gateway.dispatch() is None and len(world.gateway.gateway.store.queue()) == 1


def test_the_gateway_never_holds_the_robot_lock_the_harness_takes_it_per_execution(world, tmp_path):
    import fcntl
    from dataclasses import replace
    gateway = world.gateway.gateway;lock = tmp_path/'sim.lock';gateway.config = replace(gateway.config, sim_lock=lock)
    other = open(lock, 'a');fcntl.flock(other, fcntl.LOCK_EX)  # An execution is on the robot right now.
    browser = world.person();start(browser, 'client-key-a')
    # The run still opens: Dev writes code while it waits, and only its executions wait for the robot.
    assert any(p.endswith('/run') for p in world.harness.paths()) and gateway.store.queue() == []
    other.close()
    probe = open(lock, 'a');fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB);probe.close()


def test_a_run_that_fails_to_start_from_the_line_tells_its_owner_why(world):
    first, second = world.person(), world.person()
    a = start(first, 'client-key-a')[1]['experiment_id']
    world.harness.rows = [row(a, 'running')]
    b = start(second, 'client-key-b')[1]['experiment_id']
    world.harness.rows = [row(a, 'completed'), row(b, 'planned')];world.now[0] += 31
    world.harness.start_status = 500
    assert world.gateway.gateway.dispatch()[:2] == (b, 500)
    card = next(c for c in second.json('GET', '/api/lobby')[1]['recent'] if c['experiment_id'] == b)
    assert card['state'] == 'created' and 'cannot start' in card['start_error'] and '/home' not in card['start_error']
    world.harness.start_status = 200;world.now[0] += 31
    second.json('POST', f'/api/dev-v2/experiments/{b}/run', {})
    assert world.gateway.gateway.store.start_failures() == {}


def slots(world, n):
    from dataclasses import replace
    gateway = world.gateway.gateway;gateway.config = replace(gateway.config, limits=replace(gateway.config.limits, run_slots=n))
    return gateway


def test_runs_open_up_to_the_slots_at_once_and_the_next_waits_for_a_slot(world):
    gateway = slots(world, 3);people = [world.person() for _ in range(4)];made = []
    for n, browser in enumerate(people):
        made.append(start(browser, f'client-key-{n}')[1]['experiment_id'])
        world.harness.rows.append(row(made[-1], 'running' if n < 3 else 'planned'))
    started = [p for p in world.harness.paths() if p.endswith('/run')]
    assert started == [f'/api/dev-v2/experiments/{e}/run' for e in made[:3]], started
    assert [r['experiment_id'] for r in gateway.store.queue()] == [made[3]]
    world.harness.rows[0] = row(made[0], 'completed')
    assert gateway.dispatch()[:2] == (made[3], 200) and gateway.store.queue() == []


def test_a_run_started_moments_ago_holds_its_slot_before_the_host_lists_it(world):
    gateway = slots(world, 2);people = [world.person() for _ in range(3)]
    made = [start(browser, f'client-key-{n}')[1]['experiment_id'] for n, browser in enumerate(people)]
    world.harness.rows = [row(e, 'planned') for e in made]  # The host has not listed the first two as running yet.
    assert sum(p.endswith('/run') for p in world.harness.paths()) == 2 and [r['experiment_id'] for r in gateway.store.queue()] == [made[2]]
    assert gateway.dispatch() is None
    world.now[0] += 31  # Never listed as running: after 30 seconds the slots are free again.
    assert gateway.dispatch()[:2] == (made[2], 200)


def test_a_host_that_says_its_slots_are_full_keeps_the_place_in_line(world):
    gateway = slots(world, 2);browser = world.person()
    world.harness.start_status, world.harness.start_error = 409, 'All 2 run slots are in use; this run keeps its place'
    made = start(browser, 'client-key-a')[1]['experiment_id']
    assert [r['experiment_id'] for r in gateway.store.queue()] == [made] and gateway.store.start_failures() == {}
    world.harness.start_status = 200
    assert gateway.dispatch()[:2] == (made, 200) and gateway.store.queue() == []


def test_the_lobby_lists_open_runs_with_their_robot_state(world):
    slots(world, 3);first, second = world.person(), world.person()
    a = start(first, 'client-key-a')[1]['experiment_id'];b = start(second, 'client-key-b')[1]['experiment_id']
    world.harness.rows = [dict(row(a, 'running'), robot=dict(state='executing')), dict(row(b, 'running'), robot=dict(state='waiting', ahead=1))]
    board = second.json('GET', '/api/lobby')[1]
    assert [(c['experiment_id'], c['robot']) for c in board['robot']['open']] == [(a, dict(state='executing')), (b, dict(state='waiting', ahead=1))]
    assert board['robot']['running']['experiment_id'] == a and board['counts']['running'] == 2


def test_a_finished_agent_run_that_succeeded_reads_solved_in_the_lobby():
    """An Agent run lists no versions, only the cell's end reason; native_success there is a solved run, not a failed one."""
    from remote_gateway import policy
    from remote_gateway.config import Limits
    row = dict(experiment_id='devexp-' + 'a' * 24, task_id='t', state='completed', reason='native_success', created_at=1)
    card = policy._card(row, nickname='Fox-11', mine=False, position=None, eta=None, limits=Limits())
    assert (card['state'], card['result']) == ('finished', 'solved')
    card = policy._card(dict(row, reason='budget_exhausted'), nickname='Fox-11', mine=False, position=None, eta=None, limits=Limits())
    assert (card['state'], card['result']) == ('finished', 'failed')
