"""UI-only persistence and authenticated API checks; workers are stubbed."""
import json
import re
import threading
from contextlib import contextmanager
from dataclasses import replace
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest
from benchmarks.attention_harness.core.models import RunStatus
from benchmarks.attention_harness.core.store import AttentionStore, StateConflictError
from benchmarks.attention_harness.dev_loop import DEFAULT_BUDGET
from benchmarks.attention_harness.tests.test_attention_store import records
from benchmarks.attention_harness.ui_history import UIHistory, name_suffix
from benchmarks.attention_harness.ui_server import create_server


def run(store, status=RunStatus.COMPLETED):
    value=replace(records()[0],status=status)
    store.create_run(value)
    return value.run_id


def dev(store, state='stopped'):
    s=store.create_dev_session(locked={'config':{'suite':'robocasa365','task_id':'pick_place_counter_to_sink'}},budget=DEFAULT_BUDGET,artifact_dir='/tmp/ui-history-fixture',model='fixture',initial_source='pass',request_key='history-fixture-key')
    store.dev_state(s['session_id'],state)
    return store.get_dev_session(s['session_id'])


@pytest.mark.parametrize('kind',['run','dev'])
def test_delete_restore_survives_reopen_without_changing_execution_ledger(tmp_path,kind):
    store=AttentionStore(tmp_path/'store.sqlite3')
    record_id=run(store) if kind=='run' else dev(store)['session_id']
    table='runs' if kind=='run' else 'dev_sessions'
    with store._connect() as db:before=db.execute(f'SELECT payload FROM {table} WHERE id=?',(record_id,)).fetchone()[0]
    events=store.events()
    history=UIHistory(store)
    first=history.change(kind,record_id,deleted=True)
    assert first['history_deleted_at']
    assert history.change(kind,record_id,deleted=True)==first
    reopened=AttentionStore(store.path);history=UIHistory(reopened)
    value=reopened.get_run(record_id) if kind=='run' else reopened.get_dev_session(record_id)
    assert history.project(kind,value)['history_deleted_at']==first['history_deleted_at']
    history.change(kind,record_id,deleted=False)
    assert history.project(kind,value)['history_deleted_at'] is None
    with store._connect() as db:assert db.execute(f'SELECT payload FROM {table} WHERE id=?',(record_id,)).fetchone()[0]==before
    assert store.events()==events


@pytest.mark.parametrize('kind',['run','dev'])
def test_active_execution_cannot_be_deleted(tmp_path,kind):
    store=AttentionStore(tmp_path/'store.sqlite3')
    record_id=run(store,RunStatus.RUNNING) if kind=='run' else dev(store,'running')['session_id']
    with pytest.raises(StateConflictError,match='Stop'):
        UIHistory(store).change(kind,record_id,deleted=True)


@pytest.mark.parametrize('value',[None,42,'a'*81,'a\nb','a\x00b'])
def test_suffix_validation(value):
    with pytest.raises(ValueError):name_suffix(value)


def test_suffix_stays_outside_locked_configuration_and_follows_launch_binding(tmp_path):
    store=AttentionStore(tmp_path/'store.sqlite3');rid=run(store);history=UIHistory(store)
    locked={**store.get_run(rid),'task':'cube_lift','attention_policy':'reactive_help','approved_config_sha256':'a'*64}
    store.record_launch('launch:fixture',locked=locked,log_path='/tmp/fixture.log')
    history.assign_suffix('launch','launch:fixture','  lower-grasp-v2  ')
    store.bind_launch_run('launch:fixture',rid)
    assert history.project('run',store.get_run(rid))['name_suffix']=='lower-grasp-v2'
    assert store.list_launches()[0]['locked']==locked
    with pytest.raises(StateConflictError):history.assign_suffix('launch','launch:fixture','different')


@contextmanager
def api(store,tmp_path):
    server=create_server(store=store,port=0,artifact_root=tmp_path)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    token=re.search(r'data-control-token="([^"]+)"',urlopen(base+'/ui/').read().decode())[1]
    def request(path,body=None,authenticated=True):
        headers={'Content-Type':'application/json'}
        if authenticated:headers['X-Attention-Token']=token
        req=Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        try:
            with urlopen(req) as response:return response.status,json.load(response)
        except HTTPError as error:return error.code,json.load(error)
    try:yield request
    finally:server.shutdown();server.server_close();thread.join(2)


def test_history_api_auth_projection_and_restore_after_server_restart(tmp_path):
    store=AttentionStore(tmp_path/'store.sqlite3');rid=run(store)
    with api(store,tmp_path) as request:
        body={'kind':'run','record_id':rid}
        assert request('/api/history/delete',body,False)[0]==403
        assert request('/api/history/delete',body)[0]==200
        assert request('/api/runs')[1]['runs'][0]['history_deleted_at']
        assert request('/api/runs/'+rid)[1]['run']['history_deleted_at']
    with api(AttentionStore(store.path),tmp_path) as request:
        assert request('/api/runs')[1]['runs'][0]['history_deleted_at']
        assert request('/api/history/restore',body)[0]==200
        assert request('/api/runs')[1]['runs'][0]['history_deleted_at'] is None
        assert request('/api/history/delete',{'kind':'invalid','record_id':rid})[0]==400
        assert request('/api/history/delete',{'kind':'run','record_id':'missing'})[0]==409


def test_dev_start_suffix_persists_and_retry_cannot_rename_or_dispatch_deleted_session(tmp_path,monkeypatch):
    store=AttentionStore(tmp_path/'store.sqlite3');seen=[]
    from benchmarks.attention_harness import dev_loop
    def create(store,profiles,artifacts,selection):
        seen.append(selection.copy());return dev(store)
    monkeypatch.setattr(dev_loop,'create_session',create)
    monkeypatch.setattr(dev_loop,'spawn_worker',lambda *args:pytest.fail('worker must not start'))
    with api(store,tmp_path) as request:
        status,s=request('/api/dev/start',{'request_key':'fixture-key','name_suffix':' low grasp '})
        assert status==202 and s['name_suffix']=='low grasp'
        assert 'name_suffix' not in seen[0]
        assert request('/api/dev/sessions/'+s['session_id'])[1]['name_suffix']=='low grasp'
        assert request('/api/dev/start',{'request_key':'fixture-key','name_suffix':'changed'})[0]==409
        assert request('/api/history/delete',{'kind':'dev','record_id':s['session_id']})[0]==200
        assert request('/api/dev/start',{'request_key':'fixture-key','name_suffix':'low grasp'})[0]==409


def test_regular_start_suffix_attaches_to_launch_without_altering_execution_selection(tmp_path,monkeypatch):
    from benchmarks.attention_harness import ui_server
    store=AttentionStore(tmp_path/'store.sqlite3');rid=run(store);seen=[]
    monkeypatch.setattr(ui_server,'load_catalog',lambda _: [{'id':'fixture'}])
    def launch(selection,**kwargs):
        seen.append(selection.copy());store.record_launch('launch:fixture',locked={**store.get_run(rid),'task':'cube_lift','attention_policy':'reactive_help'},log_path='/tmp/fixture');store.bind_launch_run('launch:fixture',rid);return {'launch_id':'launch:fixture'}
    monkeypatch.setattr(ui_server,'launch_formal_run',launch)
    server=create_server(store=store,port=0,artifact_root=tmp_path,run_catalog=tmp_path/'unused')
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        base=f'http://127.0.0.1:{server.server_port}';token=re.search(r'data-control-token="([^"]+)"',urlopen(base+'/ui/').read().decode())[1]
        response=urlopen(Request(base+'/api/runs/start',data=json.dumps({'profile_id':'fixture','name_suffix':'comparison'}).encode(),headers={'Content-Type':'application/json','X-Attention-Token':token}))
        assert response.status==200
        assert seen==[{'profile_id':'fixture'}]
        assert json.load(urlopen(base+'/api/runs'))['runs'][0]['name_suffix']=='comparison'
    finally:server.shutdown();server.server_close();thread.join(2)
