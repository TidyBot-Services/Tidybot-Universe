"""Lifecycle fences and HTTP media/security regression, without physical dispatch."""
import json,os,re,threading
from types import SimpleNamespace
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import pytest
from benchmarks.attention_harness.fixed_ui import FixedUI,atomic_json
from benchmarks.attention_harness.ui_server import create_server

@pytest.fixture
def manager(tmp_path,monkeypatch):
    m=FixedUI(tmp_path);monkeypatch.setattr(m,'_reap',lambda *a:None)
    monkeypatch.setattr('benchmarks.attention_harness.fixed_ui.subprocess.Popen',lambda *a,**k:SimpleNamespace(pid=os.getpid()))
    return m

def selection(**kw):return {'scene':'obstacles','arms':'both','request_id':'request-00000001',**kw}

def test_idempotent_start_and_busy_fence(manager):
    r=manager.start(selection());assert manager.start(selection())['run_id']==r['run_id']
    with pytest.raises(RuntimeError,match='active'):manager.start(selection(request_id='request-00000002'))
    with pytest.raises(ValueError,match='another selection'):manager.start(selection(arms='left'))
    assert len(manager.snapshot()['runs'])==1

def test_single_use_execute_and_persistent_cancel(manager):
    r=manager.start(selection());directory=manager.directory(r['run_id'])
    with pytest.raises(RuntimeError,match='ready'):manager.command(r['run_id'],{'action':'execute','request_id':'execute1'})
    r['status']='ready';atomic_json(directory/'status.json',r)
    command={'action':'execute','request_id':'execute1'};manager.command(r['run_id'],command);manager.command(r['run_id'],command)
    with pytest.raises(RuntimeError,match='dispatched'):manager.command(r['run_id'],{**command,'request_id':'execute2'})
    manager.command(r['run_id'],{'action':'cancel','request_id':'cancel01'})
    restarted=FixedUI(manager.root.parent)
    assert restarted.snapshot()['active_run_id']==r['run_id']
    assert json.loads((directory/'interrupt.json').read_text())['action']=='cancel'
    assert len(list(directory.glob('execute.json')))==1

@pytest.mark.parametrize('body',[selection(arms='mobile'),selection(scene='arbitrary'),selection(scene='shared',arms='left'),selection(request_id='bad')])
def test_only_frozen_selection(manager,body):
    with pytest.raises(ValueError):manager.start(body)
    assert manager.snapshot()['runs']==[]

def test_terminal_run_cannot_execute(manager):
    r=manager.start(selection());r['status']='failed';atomic_json(manager.directory(r['run_id'])/'status.json',r)
    with pytest.raises(RuntimeError):manager.command(r['run_id'],{'action':'execute','request_id':'newcmd'})
    assert manager.command(r['run_id'],{'action':'stop','request_id':'stopcmd'})['status']=='failed'

def test_http_token_range_and_traversal(tmp_path):
    from benchmarks.attention_harness.core.store import AttentionStore
    store=AttentionStore(tmp_path/'history.sqlite3')
    server=create_server(port=0,store=store,artifact_root=tmp_path);threading.Thread(target=server.serve_forever,daemon=True).start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        with pytest.raises(HTTPError) as retired:urlopen(base+'/ui/fixed')
        assert retired.value.code==410
        page=urlopen(base+'/ui').read().decode();token=re.search('data-control-token="([^"]+)"',page)[1]
        assert 'href="/ui/fixed"' not in page
        with pytest.raises(HTTPError) as error:urlopen(Request(base+'/api/fixed/start',data=b'{}',headers={'Content-Type':'application/json'}))
        assert error.value.code==403
        with pytest.raises(HTTPError) as error:urlopen(Request(base+'/api/fixed/start',data=json.dumps(selection(scene='invalid')).encode(),headers={'Content-Type':'application/json','X-Attention-Token':token}))
        assert error.value.code==410
        m=FixedUI(tmp_path);rid='fixed-'+'a'*24;directory=m.root/rid;directory.mkdir();(directory/'physics').mkdir();(directory/'physics/simulation.mp4').write_bytes(b'0123456789')
        atomic_json(directory/'status.json',{'run_id':rid,'status':'completed','recording':'saved','created_at':1})
        with urlopen(Request(base+f'/api/fixed/runs/{rid}/video',headers={'Range':'bytes=2-5'})) as r:
            assert r.status==206 and r.read()==b'2345' and r.headers['Content-Range']=='bytes 2-5/10'
        with pytest.raises(HTTPError) as error:urlopen(Request(base+f'/api/fixed/runs/{rid}/video',headers={'Range':'bytes=99-'}))
        assert error.value.code==416
        with pytest.raises(HTTPError) as error:urlopen(base+f'/api/fixed/runs/{rid}/files/%2e%2e/status.json')
        assert error.value.code==404
    finally:server.shutdown();server.server_close();store.close()



def test_launch_history_does_not_parse_physical_trace_events(tmp_path,monkeypatch):
    from benchmarks.attention_harness.core.store import AttentionStore
    store=AttentionStore(tmp_path/'launch.sqlite3')
    store.record_launch('launch1',locked={},log_path='log')
    store.record_launch_failed('launch1',reason='startup failure')
    store.record_launch_started('launch1',pid=1)
    monkeypatch.setattr(store,'events',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('launch history parsed full traces')))
    assert store.list_launches()[0]['state']=='failed'
    assert store.list_launches()[0]['pid']==1
