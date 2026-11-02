"""Lobby HTTP on ephemeral loopback ports; no real dispatch or provider."""
import hashlib
import json
import re
import sqlite3
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.tests.test_demo_engine import engine, EID, KEY, DEV, ANSWER
from benchmarks.attention_harness.tests.test_lobby import Clock
from benchmarks.attention_harness.ui_server import create_server


def call(base, path, *, method='GET', body=None, token=None, cookie=None):
    headers = {'Content-Type':'application/json'}
    if token:headers['X-Attention-Token'] = token
    if cookie:headers['Cookie'] = cookie
    data = json.dumps(body).encode() if body is not None else None
    try:response = urlopen(Request(base+path,data=data,method=method,headers=headers))
    except HTTPError as error:response = error
    with response:
        raw = response.read()
        return response.status,raw,dict(response.headers)


@pytest.fixture
def served(tmp_path):
    clock = Clock();demo = engine(tmp_path/'lobby',clock)
    store = AttentionStore(tmp_path/'store.sqlite3')
    server = create_server(store=store,port=0,dev_v2_bridge=demo.bridge,lobby=demo)
    thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    token = re.search(rb'data-control-token="([^"]+)"',call(base,'/ui/')[1])[1].decode()
    try:yield base,token,demo,clock
    finally:
        server.shutdown();server.server_close();thread.join(2)


def identity(base):
    status,raw,headers = call(base,'/api/lobby')
    assert status == 200 and headers['Cache-Control'] == 'no-store'
    cookie = headers['Set-Cookie']
    assert 'HttpOnly' in cookie and 'SameSite=Lax' in cookie and 'Path=/' in cookie and 'Domain=' not in cookie
    return cookie.split(';')[0],json.loads(raw)


def start(base, token, cookie):
    status,raw,_ = call(base,'/api/lobby/runs',method='POST',token=token,cookie=cookie,
                        body=dict(task_key='lift',strategy='autonomous',dev_source='own_key',share_replies=False))
    assert status == 201
    return json.loads(raw)


def test_cookie_owner_controls_and_no_sensitive_response_or_storage(served,capsys):
    base,token,demo,clock = served
    capsys.readouterr()
    cookie,snap = identity(base);other,_ = identity(base)
    repeat = call(base,'/api/lobby',cookie=cookie)
    assert 'Set-Cookie' not in repeat[2] and json.loads(repeat[1])['me'] == snap['me']
    run = start(base,token,cookie)
    rid = run['run_id']
    for action,body in (('cancel',{}),('reply',{'outcome':'skipped'}),('share',{'share':True})):
        status,raw,_ = call(base,f'/api/lobby/runs/{rid}/{action}',method='POST',token=token,cookie=other,body=body)
        assert status == 403 and json.loads(raw)['error']['code'] == 'not_owner'
    for path,body in (('/api/lobby/runs',{'task_key':'lift'}),('/api/lobby/clock',{'speed':5}),
                       (f'/api/lobby/runs/{rid}/cancel',{}),(f'/api/lobby/runs/{rid}/share',{'share':True}),
                       (f'/api/lobby/runs/{rid}/reply',{'outcome':'skipped'})):
        status,raw,_ = call(base,path,method='POST',cookie=cookie,body=body)
        assert status == 403 and json.loads(raw)['error']['code'] == 'control_token_required'
    for marker,body in [('TEST-KEY',{'task_key':'lift','key':'TEST-KEY'}),('192.0.2.9',{'task_key':'192.0.2.9'}),
                         (token,{'task_key':token})]:
        status,raw,_ = call(base,'/api/lobby/runs',method='POST',token=token,cookie=cookie,body=body)
        assert status in (400,409) and marker.encode() not in raw
    raw = call(base,'/api/lobby',cookie=cookie)[1]
    for marker in (token,cookie.split('=')[1],'TEST-KEY','192.0.2.9','127.0.0.1'):
        assert marker.encode() not in raw
        with sqlite3.connect(demo.lobby.path) as db:
            dump = '\n'.join(db.iterdump())
            assert marker not in dump
    logs = capsys.readouterr()
    assert token not in logs.err and '127.0.0.1' not in logs.err
    assert call(base,f'/api/lobby/runs/{rid}/cancel',method='POST',token=token,cookie=cookie,body={})[0] == 200


def test_help_reply_sharing_and_body_validation(served):
    base,token,demo,clock = served
    cookie,_ = identity(base);other,_ = identity(base)
    run = start(base,token,cookie);rid = run['run_id'];demo.tick();clock.step(60)
    own = json.loads(call(base,'/api/lobby',cookie=cookie)[1])
    public = json.loads(call(base,'/api/lobby',cookie=other)[1])
    assert own['robot']['running']['waiting']['question']
    assert public['robot']['running']['waiting'] is None
    for content in ('',None,7,['bad'],'x'*4001,token,'api_key=TEST-KEY','192.0.2.9'):
        status,raw,_ = call(base,f'/api/lobby/runs/{rid}/reply',method='POST',token=token,cookie=cookie,
                            body={'outcome':'answered','content':content})
        assert status == 400 and json.loads(raw)['error']['code'] == 'invalid_body'
    status,raw,_ = call(base,f'/api/lobby/runs/{rid}/reply',method='POST',token=token,cookie=cookie,
                        body={'outcome':'answered','category':'position','content':'MY PRIVATE ANSWER'})
    assert status == 200 and b'MY PRIVATE ANSWER' not in raw
    assert b'MY PRIVATE ANSWER' not in call(base,'/api/lobby',cookie=cookie)[1]
    path = f'/api/lobby/runs/{rid}/replies'
    assert b'MY PRIVATE ANSWER' in call(base,path,cookie=cookie)[1]
    assert call(base,path,cookie=other)[0] == 403
    call(base,f'/api/lobby/runs/{rid}/share',method='POST',token=token,cookie=cookie,body={'share':True})
    assert call(base,path,cookie=other)[0] == 403
    clock.step(60);demo.tick()
    call(base,f'/api/lobby/runs/{rid}/reply',method='POST',token=token,cookie=cookie,body={'outcome':'skipped'})
    clock.step(60);demo.tick()
    assert b'MY PRIVATE ANSWER' in call(base,path,cookie=other)[1]


def test_sim_routes_and_digest_bound_read_only_evidence(served):
    base,token,demo,clock = served
    cookie,_ = identity(base);other,_ = identity(base)
    card = start(base,token,cookie);eid = card['experiment_id'];demo.tick();clock.step(60);demo.tick()
    run = demo.lobby.find(eid);key = demo.key(run)
    root = f'/api/dev-v2/experiments/{eid}'
    for suffix in ('',f'/cells/{key}',f'/cells/{key}/view'):
        status,raw,headers = call(base,root+suffix,cookie=other)
        assert status == 200 and headers['Cache-Control'] == 'no-store'
        for sentinel in (DEV,ANSWER,EID,KEY):assert sentinel.encode() not in raw
    for suffix in ('/pause','/resume','/run','/switch','/approvals',f'/cells/{key}/run'):
        for method in ('POST','PUT','DELETE','PATCH'):
            assert call(base,root+suffix,method=method,cookie=cookie,body={})[0] == 403
            status,raw,_ = call(base,root+suffix,method=method,cookie=cookie,token=token,body={})
            assert status == 409 and json.loads(raw)['error']['code'] == 'simulated_run'
    for viewer in (cookie,other):
        view = json.loads(call(base,root+f'/cells/{key}/view',cookie=viewer)[1])
        ref = view['dev_v2_compat']['jobs'][0]['receipt']['bundle']
        status,raw,_ = call(base,root+f'/cells/{key}/agent/files/'+ref['uri'].removeprefix('agent-view/'),cookie=viewer)
        assert status == 200 and hashlib.sha256(raw).hexdigest() == ref['sha256']
        bundle = json.loads(raw)
        assert bundle['run_id'] == 'run:'+key
    listing = json.loads(call(base,'/api/runs',cookie=other)[1])
    simulated = [s for s in listing['dev_sessions'] if s.get('lobby')]
    assert simulated[0]['experiment_id'] == eid and simulated[0]['lobby']['mine'] is False
    assert DEV not in json.dumps(simulated)
    assert call(base,root+'/cancel',method='POST',cookie=other,token=token,body={})[0] == 403
    assert call(base,root+'/cancel',method='POST',cookie=cookie,token=token,body={})[0] == 200


def test_without_lobby_runs_are_byte_identical(tmp_path):
    store = AttentionStore(tmp_path/'store.sqlite3')
    bridge = engine(tmp_path/'lobby').bridge
    expected = json.dumps(dict(runs=[],launches=[],dev_sessions=bridge.list()),ensure_ascii=False).encode()
    for kwargs in ({},{'lobby':None}):
        server = create_server(store=store,port=0,dev_v2_bridge=bridge,**kwargs)
        thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            base = f'http://127.0.0.1:{server.server_port}'
            assert call(base,'/api/runs')[1] == expected
            assert call(base,'/api/lobby')[0] == 404
        finally:server.shutdown();server.server_close();thread.join(2)


def test_cli_rejects_public_lobby_before_loading_paths(monkeypatch,capsys):
    from benchmarks.attention_harness.ui_server import main
    monkeypatch.setattr('sys.argv',['ui_server','--demo','--lobby-config','missing.json','--public-profile'])
    with pytest.raises(SystemExit) as error:main()
    assert error.value.code == 2 and '--lobby-config cannot be used with --public-profile' in capsys.readouterr().err


def test_remote_tab_and_page_exist_only_with_a_lobby(served, tmp_path):
    base, token, demo, clock = served
    run_page = call(base, '/ui/')[1].decode()
    remote_page = call(base, '/ui/remote')
    root = lambda html: re.search(r'<div id="attentionbench-clear"[^>]*>', html)[0]
    assert 'data-remote-tab="1"' in root(run_page) and 'data-remote="1"' not in root(run_page)
    assert remote_page[0] == 200
    remote = remote_page[1].decode()
    assert 'data-remote="1"' in root(remote) and 'data-control-token' in root(remote)
    assert b'data-remote-tab="1"' in call(base, '/ui/home.html')[1] and b'data-remote-tab="1"' in call(base, '/ui/memory.html')[1]
    plain = create_server(store=AttentionStore(tmp_path / 'plain.sqlite3'), port=0)
    thread = threading.Thread(target=plain.serve_forever, daemon=True); thread.start()
    try:
        plain_base = f'http://127.0.0.1:{plain.server_port}'
        assert call(plain_base, '/ui/remote')[0] == 404
        assert 'data-remote-tab' not in call(plain_base, '/ui/')[1].decode()
    finally:
        plain.shutdown(); plain.server_close(); thread.join(2)
