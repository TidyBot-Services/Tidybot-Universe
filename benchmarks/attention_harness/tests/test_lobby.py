"""Queue transitions use small schedules and a wall clock, never a simulator."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from benchmarks.attention_harness.lobby import Lobby, LobbyError


class Clock:
    def __init__(self):self.now = 10000.
    def __call__(self):return self.now
    def step(self, seconds):self.now += seconds


def sources(*, help=True):
    return {'recorded':dict(source_id='recorded', task_key='lift', title='Lift cup', duration=180,
        attempt_done_at=[60,120,180], limit=3, result='solved',
        helps={'1':dict(question='Where should I move?',failure='out of reach',delay=40)} if help else {})}


@pytest.fixture
def queue(tmp_path):
    clock = Clock()
    return Lobby(tmp_path,sources(),clock=clock,speed=1), clock


def join(lobby):return lobby.identify()[0]
def enqueue(lobby, viewer):return lobby.enqueue(viewer,task_key='lift')


def test_fifo_quotas_positions_and_cancellation(queue):
    lobby, clock = queue
    a,b,c = [join(lobby) for _ in range(3)]
    first = enqueue(lobby,a)
    with pytest.raises(LobbyError,match='already_queued'):enqueue(lobby,a)
    lobby.tick()
    second = enqueue(lobby,a)
    with pytest.raises(LobbyError,match='already_queued'):enqueue(lobby,a)
    third = enqueue(lobby,b)
    fourth = enqueue(lobby,c)
    assert [r['queue_position'] for r in lobby.snapshot(a)['queue']] == [1,2,3]
    with pytest.raises(LobbyError,match='not_owner'):lobby.cancel(first['run_id'],b)
    lobby.cancel(third['run_id'],b)
    assert [r['run_id'] for r in lobby.snapshot(a)['queue']] == [second['run_id'],fourth['run_id']]
    lobby.cancel(first['run_id'],a)
    assert lobby.snapshot(a)['robot']['running']['run_id'] == second['run_id']
    lobby.cancel(first['run_id'],a)
    with lobby.tx() as db:
        assert db.execute("SELECT count(*) FROM events WHERE type='cancelled' AND run_id=?",(first['run_id'],)).fetchone()[0] == 1
    assert lobby.snapshot(a)['recent'][0]['state'] == 'cancelled'


def test_eta_rounding_and_finished_median(tmp_path):
    clock = Clock()
    lobby = Lobby(tmp_path,sources(help=False),clock=clock,speed=1)
    a,b,c = [join(lobby) for _ in range(3)]
    enqueue(lobby,a);lobby.tick()
    enqueue(lobby,b);enqueue(lobby,c)
    assert [r['eta_start_seconds'] for r in lobby.snapshot(a)['queue']] == [180,360]
    clock.step(46)
    assert [r['eta_start_seconds'] for r in lobby.snapshot(a)['queue']] == [120,300]
    clock.step(134)
    assert lobby.snapshot(a)['recent'][0]['result'] == 'solved'
    assert lobby.snapshot(a)['queue'][0]['eta_start_seconds'] == 180


def test_help_timeout_real_seconds_and_public_question(queue):
    lobby, clock = queue
    owner, other = join(lobby),join(lobby)
    run = enqueue(lobby,owner);lobby.tick()
    clock.step(60);lobby.tick()
    assert lobby.card(run['run_id'],owner)['state'] == 'waiting_human'
    assert lobby.card(run['run_id'],owner)['waiting']['question'] == 'Where should I move?'
    assert lobby.card(run['run_id'],other)['waiting']['question'] == 'Where should I move?'
    lobby.set_speed(60)
    clock.step(599);lobby.tick()
    assert lobby.card(run['run_id'],owner)['state'] == 'waiting_human'
    clock.step(1);lobby.tick()
    assert lobby.replies(run['run_id'],owner)[0]['outcome'] == 'skipped'
    clock.step(2);lobby.tick()
    assert lobby.card(run['run_id'],owner)['result'] == 'solved'
    assert 'Where should I move?' not in json.dumps(lobby.snapshot(other))


def test_replies_are_public_at_once_and_never_in_events(queue):
    lobby,clock = queue
    owner,other = join(lobby),join(lobby)
    run = enqueue(lobby,owner);lobby.tick();clock.step(60);lobby.tick()
    lobby.reply(run['run_id'],owner,outcome='answered',category='reach',content='REPLY SENTINEL')
    assert lobby.replies(run['run_id'],other)[0]['content'] == 'REPLY SENTINEL'
    assert lobby.replies(run['run_id'],None)[0]['content'] == 'REPLY SENTINEL'
    assert 'REPLY SENTINEL' not in json.dumps(lobby.snapshot(owner))
    with pytest.raises(LobbyError,match='not_waiting'):lobby.reply(run['run_id'],owner,outcome='skipped')


def test_restart_recovers_cookie_clock_wait_and_queue(tmp_path):
    clock = Clock()
    lobby = Lobby(tmp_path,sources(),clock=clock,speed=20)
    owner,cookie = lobby.identify()
    run = enqueue(lobby,owner);lobby.tick();clock.step(3);lobby.tick()
    before = lobby.snapshot(owner)['clock']['sim_now']
    clock.step(10)
    restored = Lobby(tmp_path,sources(),clock=clock,speed=1)
    same,new_cookie = restored.identify(cookie)
    assert same == owner and new_cookie is None
    assert restored.snapshot(owner)['clock'] == dict(speed=20,sim_now=before+200)
    assert restored.card(run['run_id'],owner)['state'] == 'waiting_human'
    with sqlite3.connect(restored.path) as db:
        assert db.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        raw = json.dumps(db.execute('SELECT * FROM viewers').fetchall())
        assert cookie not in raw and owner['id'] in raw


def test_speed_changes_are_continuous_and_tick_is_idempotent(queue):
    lobby,clock = queue
    owner = join(lobby);enqueue(lobby,owner);lobby.tick()
    clock.step(10)
    assert lobby.set_speed(5)['sim_now'] == clock.now
    clock.step(2)
    assert lobby.snapshot(owner)['clock']['sim_now'] == 10020
    with lobby.tx() as db:before = db.execute('SELECT count(*) FROM events').fetchone()[0]
    lobby.tick();lobby.tick()
    with lobby.tx() as db:assert db.execute('SELECT count(*) FROM events').fetchone()[0] == before
    for speed in (True,0,2,100,'20'):
        with pytest.raises(LobbyError,match='invalid_speed'):lobby.set_speed(speed)


def test_scripted_invitees_loop_and_recover_without_duplication(tmp_path):
    clock = Clock()
    lobby = Lobby(tmp_path,sources(),clock=clock,speed=20,invitees=4,seed=7)
    snap = lobby.snapshot(None)
    assert snap['robot']['running'] and len(snap['queue']) == 3 and len(snap['recent']) == 6
    clock.step(3);snap = lobby.snapshot(None)
    assert snap['robot']['running']['state'] == 'waiting_human'
    clock.step(2);snap = lobby.snapshot(None)
    assert snap['robot']['running']['state'] == 'running'
    clock.step(100);snap = lobby.snapshot(None)
    assert snap['robot']['running'] and len(snap['recent']) > 4
    counts = snap['counts']
    restored = Lobby(tmp_path,sources(),clock=clock,speed=1,invitees=4,seed=7)
    assert restored.snapshot(None)['counts'] == counts


def test_concurrent_enqueue_and_tick_have_one_slot(queue):
    lobby,clock = queue
    viewers = [join(lobby) for _ in range(8)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda v:enqueue(lobby,v),viewers))
        list(pool.map(lambda _:lobby.tick(),range(8)))
    snap = lobby.snapshot(viewers[0])
    assert snap['counts']['running'] == 1 and snap['counts']['queued'] == 7


@pytest.mark.parametrize('content', ['api_key=TEST-KEY', 'control_token=TEST-TOKEN', '192.0.2.22', '2001:db8::1','::1'])
def test_sensitive_reply_rejected_without_persistence(queue,content):
    lobby,clock = queue
    owner = join(lobby);run = enqueue(lobby,owner);lobby.tick();clock.step(60);lobby.tick()
    with pytest.raises(LobbyError,match='invalid_body'):lobby.reply(run['run_id'],owner,outcome='answered',content=content)
    with lobby.tx() as db:assert not db.execute('SELECT * FROM replies').fetchall()


def test_cancel_waiting_and_late_tick_never_overlap(queue):
    lobby,clock = queue
    a,b = join(lobby),join(lobby)
    first = enqueue(lobby,a);lobby.tick()
    clock.step(60);lobby.tick()
    assert lobby.card(first['run_id'],a)['state'] == 'waiting_human'
    second = enqueue(lobby,b)
    clock.step(20)
    cancelled = lobby.cancel(first['run_id'],a)
    assert cancelled['waiting'] is None and cancelled['state'] == 'cancelled'
    assert lobby.snapshot(a)['robot']['running']['run_id'] == second['run_id']
    assert lobby.card(second['run_id'],b)['started_at'] >= cancelled['ended_at']


def test_running_catchup_on_restart_and_late_enqueue(tmp_path):
    clock = Clock();lobby = Lobby(tmp_path,sources(help=False),clock=clock,speed=5)
    a,b = join(lobby),join(lobby)
    clock.step(20)
    first = enqueue(lobby,a);lobby.tick()
    assert lobby.card(first['run_id'],a)['started_at'] == clock.now
    second = enqueue(lobby,b)
    clock.step(40)
    restored = Lobby(tmp_path,sources(help=False),clock=clock,speed=1)
    snap = restored.snapshot(a)
    assert snap['recent'][0]['run_id'] == first['run_id']
    assert snap['robot']['running']['run_id'] == second['run_id']
    assert snap['robot']['running']['started_at'] == 10056


def mixed_sources():
    def one(eid, result, attempts):
        return dict(source_id=eid, task_key='lift', title='Lift cup', duration=40*attempts,
            attempt_done_at=[40*(i+1) for i in range(attempts)], limit=5, result=result, helps={})
    return {name:one(name,result,attempts) for name,result,attempts in
            (('rec-a','solved',2),('rec-b','failed',3),('rec-c','limit',5),('rec-d','solved',1))}


def test_recordings_rotate_in_configured_order_and_show_their_origin(tmp_path):
    clock = Clock()
    lobby = Lobby(tmp_path,mixed_sources(),clock=clock,speed=60,invitees=3,seed=0)
    first = [r['source_id'] for r in lobby.records()]
    assert first[:4] == ['rec-a','rec-b','rec-c','rec-d']
    snap = lobby.snapshot(None)
    cards = snap['recent'] + snap['queue'] + ([snap['robot']['running']] if snap['robot']['running'] else [])
    assert {c['recording']['id'] for c in cards} <= set(mixed_sources())
    assert all(c['recording']['attempts'] >= 1 for c in cards)
    assert snap['demo_info']['recordings'] == 4 and snap['demo_info']['history_limit'] == 8
    assert snap['demo_info']['tasks'] == sorted({s['task_key'] for s in mixed_sources().values()})
    assert {c['result'] for c in snap['recent']} >= {'solved', 'failed', 'limit'}


def test_history_stays_bounded_however_long_the_demo_runs(tmp_path):
    clock = Clock()
    lobby = Lobby(tmp_path,mixed_sources(),clock=clock,speed=60,invitees=3,seed=1)
    viewer = join(lobby)
    for _ in range(400):
        clock.step(30);lobby.tick()
    snap = lobby.snapshot(viewer)
    finished = [r for r in lobby.records() if r['state'] in ('finished','failed','cancelled')]
    assert len(finished) <= 8 and snap['counts']['all'] <= 8 + 4
    with lobby.tx() as db:
        assert db.execute('SELECT count(*) FROM events').fetchone()[0] < 80
        assert db.execute('SELECT count(*) FROM replies').fetchone()[0] <= 8


def test_a_persons_latest_finished_run_is_not_pruned(tmp_path):
    clock = Clock()
    lobby = Lobby(tmp_path,mixed_sources(),clock=clock,speed=60,invitees=3,seed=1)
    me = join(lobby)
    card = lobby.enqueue(me,task_key='lift')
    for _ in range(600):
        clock.step(20);lobby.tick()
    mine = [r for r in lobby.records() if r['owner'] == me['id']]
    assert mine and mine[0]['state'] == 'finished' and mine[0]['run_id'] == card['run_id']
