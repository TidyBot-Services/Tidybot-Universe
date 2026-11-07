"""The crowd test's own parts, without a browser. The browser run itself is `python -m remote_gateway.simtest.run_sim`."""
import hashlib
import hmac
import http.client
import json
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

from remote_gateway.config import Limits, load
from remote_gateway.simtest import cleanup, fake_harness, monitor, run_sim, sim_users
from remote_gateway.upstream import control_token

SECRET = b's' * 48
BEGIN = dict(kind='begin', at=1, people=[dict(lane='U1', name='甲', story='认真回复'), dict(lane='G1', name='乙', story='访客')])
SELECTION = dict(request_key='client-key-1', policies=['reactive_help'], seeds=[101], tasks=['open_cabinet'], mode='interactive', human_reply_seconds=30)


def state(card, **extra):
    return dict(kind='state', lane='U1', at=2, run_id='r', page={**dict(lobby=True, card=card, reply=False, viewing='r'), **extra})


def test_a_lane_follows_what_the_page_said():
    folded = monitor.fold([BEGIN, state(dict(state='queued', position=3)), dict(state(dict(state='running')), at=5), dict(state(dict(state='running'), reply=True), at=6)])
    lane = folded['lanes'][0]
    assert lane['status'] == 'reply' and [s['status'] for s in lane['segments']] == ['queued', 'running', 'reply']
    assert folded['lanes'][0]['segments'][0]['to'] == 5


def test_a_reply_form_on_someone_elses_run_is_not_my_wait():
    row = dict(state(dict(state='queued', position=1), reply=True));row['page']['viewing'] = 'other'
    assert monitor.fold([BEGIN, row])['lanes'][0]['status'] == 'queued'


@pytest.mark.parametrize('row', [
    dict(kind='problem', lane='U1', at=2, source='x', message='y'),
    dict(kind='http', lane='U1', at=2, status=429, route='/api/lobby', method='GET', expected=False),
    dict(kind='check', lane='U1', at=2, check='c', ok=False),
    dict(kind='done', lane='U1', at=2, ok=False, error='e')])
def test_every_kind_of_failure_shows_and_fails_the_test(row):
    folded = monitor.fold([BEGIN, row, dict(kind='end', at=3)])
    assert folded['problems'] and folded['lanes'][0]['problems']
    assert monitor.verdict(folded)['verdict'] == 'fail'


def test_expected_refusals_and_frame_races_are_counted_but_do_not_fail():
    rows = [BEGIN, dict(kind='http', lane='U1', at=2, status=404, route='/x', method='POST', expected=True), dict(kind='http', lane='U1', at=2, status=409, route='/live/frame', method='GET', expected=False, level='notice'),
            dict(kind='done', lane='U1', at=3, ok=True), dict(kind='done', lane='G1', at=3, ok=True), dict(kind='end', at=4)]
    totals = monitor.verdict(monitor.fold(rows))
    assert totals['verdict'] == 'pass' and totals['expected_refusals'] == 1 and totals['notices'] == 1


def test_no_verdict_before_the_end_and_a_violation_always_fails():
    folded = monitor.fold([BEGIN])
    assert monitor.verdict(folded)['verdict'] == 'running'
    assert monitor.verdict(folded, [dict(started='a')])['verdict'] == 'fail'


def test_a_stopped_test_says_why():
    assert monitor.fold([BEGIN, dict(kind='guard', at=2, message='磁盘低于底线')])['problems'][0]['source'] == '测试叫停'


def test_reading_the_sim_lock_never_takes_it(tmp_path):
    import fcntl
    path = tmp_path / 'sim.lock';path.touch()
    assert monitor.lock_holder(path)['state'] == '空闲'
    with open(path, 'a') as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)  # Would raise if the reader had kept it.
        assert monitor.lock_holder(path)['state'] == '占用中'
    assert monitor.lock_holder(None)['state'] == '未使用'


def test_invite_codes_never_reach_the_log(tmp_path):
    log = sim_users.Log(tmp_path / 'e.jsonl', ['secret-code-123'])
    log.write(lane='U1', kind='problem', message='bad code secret-code-123')
    assert 'secret-code-123' not in (tmp_path / 'e.jsonl').read_text(encoding='utf-8')


def test_no_limit_lives_in_the_test_instance_and_nowhere_else(tmp_path):
    path = run_sim.gateway_config(tmp_path, port=8871, upstream='http://127.0.0.1:8872', wall_seconds=1200)
    limits = load(path).limits
    assert (limits.runs_per_day, limits.versions, limits.help_requests) == (100000, 10, 10)
    assert (limits.requests_per_second, limits.active_runs, limits.logins_per_hour) == (Limits().requests_per_second, 1, Limits().logins_per_hour)
    assert (Limits().runs_per_day, Limits().versions, Limits().help_requests) == (3, 3, 3)


def sign(owner, method, target, body=b''):
    now = str(int(time.time()))
    mac = hmac.new(SECRET, '\n'.join((owner, now, method, target, hashlib.sha256(body).hexdigest())).encode(), hashlib.sha256).hexdigest()
    return {'X-Attention-Owner': owner, 'X-Attention-Gateway-Auth': f'v1.{now}.{mac}', 'X-Attention-Token': control_token(SECRET), 'Content-Type': 'application/json'}


@pytest.fixture
def fake():
    world = fake_harness.World(speed=100, secret=SECRET, tries=1, try_seconds=50, think_seconds=1)
    server = fake_harness.create_server(0, world)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield world, server.server_address[1]
    server.shutdown();server.server_close()


def call(port, method, path, value=None, *, owner='u-1', signed=True):
    body = json.dumps(value).encode() if value is not None else b''
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    connection.request(method, path, body=body or None, headers=sign(owner, method, path, body) if signed else {})
    response = connection.getresponse();raw = response.read();connection.close()
    return response.status, json.loads(raw)


def made(port, key, owner='u-1'):
    return call(port, 'POST', '/api/dev-v2/experiments', dict(selection=dict(SELECTION, request_key=key)), owner=owner)[1]['experiment_id']


def test_the_fake_host_refuses_anything_unsigned(fake):
    assert call(fake[1], 'GET', '/api/runs', signed=False)[0] == 401


def test_a_second_run_on_the_robot_is_a_violation(fake):
    world, port = fake
    first, second = made(port, 'client-key-1'), made(port, 'client-key-2', 'u-2')
    assert call(port, 'POST', f'/api/dev-v2/experiments/{first}/run', {})[0] == 200
    assert call(port, 'POST', f'/api/dev-v2/experiments/{second}/run', {}, owner='u-2')[0] == 409
    assert world.summary()['violations'][0]['started'] == second


def test_a_run_asks_then_takes_one_reply_only(fake):
    world, port = fake
    run = made(port, 'client-key-1');call(port, 'POST', f'/api/dev-v2/experiments/{run}/run', {})
    deadline = time.time() + 5
    while time.time() < deadline and not world.runs[run].wait:time.sleep(.02)
    body = dict(cell='c' * 64, execution_id='e000001', content='lower')
    assert call(port, 'POST', f'/api/dev-v2/experiments/{run}/help-reply', body)[0] == 200
    assert call(port, 'POST', f'/api/dev-v2/experiments/{run}/help-reply', body)[0] == 409
    assert world.runs[run].helped[0]['provider'] == 'human'


def test_cleanup_touches_only_this_tests_runs(tmp_path):
    database = tmp_path / 'g.sqlite3'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE runs(experiment_id TEXT, created_at REAL)')
        db.executemany('INSERT INTO runs VALUES(?,1)', [('devexp-' + 'a' * 24,), ('../../home',)])
    root = tmp_path / 'runs';(root / ('devexp-' + 'a' * 24)).mkdir(parents=True);(root / ('devexp-' + 'b' * 24)).mkdir()
    command = [sys.executable, '-m', 'remote_gateway.simtest.cleanup', '--database', str(database), '--run-root', str(root)]
    assert '没有删除' in subprocess.run(command, capture_output=True, text=True, check=True).stdout
    assert (root / ('devexp-' + 'a' * 24)).exists()
    subprocess.run(command + ['--delete'], capture_output=True, text=True, check=True)
    assert not (root / ('devexp-' + 'a' * 24)).exists() and (root / ('devexp-' + 'b' * 24)).exists()


@pytest.mark.parametrize('extra, words', [([], '没有放行'), (['--release-commit', 'deadbeef'], '没有放行')])
def test_real_mode_needs_a_release(tmp_path, extra, words):
    done = subprocess.run([sys.executable, '-m', 'remote_gateway.simtest.run_sim', '--dir', str(tmp_path), '--real', *extra], capture_output=True, text=True)
    assert done.returncode != 0 and words in done.stderr
