"""8769 daily admission, real worker key pipes, and immutable Lab contracts."""
import io
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import urllib.error
import urllib.request

import pytest

from benchmarks.attention_harness import daily_dev_v2 as daily, dev_experiment as experiments
from benchmarks.attention_harness import dev_agent_provider as provider, dev_agent_run_launch as admission
from benchmarks.attention_harness import ui_server
from benchmarks.attention_harness.canary_scan import scan
from benchmarks.attention_harness.core.dev_store import DevConflict
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.dev_agent_key_vault import ProviderKeyVault, KEY_ENV
from benchmarks.attention_harness.dev_agent_ledger import AgentLedger
from benchmarks.attention_harness.dev_agent_secret import Secret
from benchmarks.attention_harness.dev_ledger import DevV2Store
from benchmarks.attention_harness.dev_main_bridge import DevMainBridge
from benchmarks.attention_harness.tests.c21_fixtures import base, r4_deployment, offline_dev_identity
from benchmarks.attention_harness.tests.fakes.byok_http import MemoryServer, exchange, loopback
from benchmarks.attention_harness.tests.fakes.fake_provider import FakeProvider


def selection(*, byok=True):
    value=dict(request_key='daily-keys-offline-001',goal_key='daily-keys-offline-goal',
        tasks=['open_cabinet'],seeds=[102],policies=['autonomous'],mode='interactive',
        dev_mode='agent',model_source='codex',agent_limits=dict(versions=2,wall_seconds=3600))
    if byok:
        value['agent_provider']=dict(roles={role:dict(credential='mine',provider='openai-compatible',
            model='fake-'+role,base_url_host='127.0.0.1') for role in ('dev','eval','advisor')})
    return value


@pytest.fixture
def materialize(monkeypatch, offline_dev_identity):
    """Same synthetic source identity as other daily tests; real create/ledger code."""
    original=subprocess.run
    def create(argv, **kwargs):
        if 'benchmarks.attention_harness.dev_experiment' not in argv or 'create' not in argv:
            return original(argv,**kwargs)
        def argument(name):return Path(argv[argv.index(name)+1])
        store=DevV2Store(argument('--store'))
        made=experiments.create(store,argument('--artifacts'),json.loads(argument('--selection').read_bytes()))
        argument('--output').write_text(json.dumps(made))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(daily.subprocess,'run',create)


def memory_service(tmp_path, monkeypatch, vault):
    monkeypatch.setattr(ui_server,'ThreadingHTTPServer',MemoryServer)
    return ui_server.create_server(store=AttentionStore(tmp_path/'ui.sqlite3'),port=0,dev_v2_root=tmp_path/'daily',dev_v2_per_run_budget=True,
        vault=vault,allow_custom=True,allow_private_hosts=('127.0.0.1','localhost'))


def control(service):
    status,html,_=exchange(service,'GET','/ui/')
    assert status==200
    return re.search(rb'data-control-token="([^"]+)"',html)[1].decode()


def call(service,token,method,path,body=None):
    status,raw,_=exchange(service,method,path,body,{'X-Attention-Token':token})
    return status,json.loads(raw)


def create_memory(service,token,chosen=None,start=False):
    status,value=call(service,token,'POST','/api/dev-v2/experiments',
        dict(selection=chosen or selection(),start=start))
    return status,value


def store_for(tmp_path,eid):return DevV2Store(tmp_path/'daily'/eid/'ledger.sqlite3')


def fake_probe_transport(*, fail=False, blind=False, canary=''):
    """Real probe parser, deterministic upstream bytes; no socket or DNS traffic."""
    def send(req,**kwargs):
        if fail:
            raise urllib.error.HTTPError(req.full_url,401,'Unauthorized',{},io.BytesIO(canary.encode()))
        if req.data is None:return io.BytesIO(b'{"data":[]}')
        body=json.loads(req.data)
        if body.get('stream'):
            return io.BytesIO(b'data: {"choices":[{"delta":{"tool_calls":[{"function":{"name":"ping"}}]}}]}\n\ndata: [DONE]\n')
        answer='ok' if blind and body.get('model')=='fake-eval' else 'HELLO 4821'
        return io.BytesIO(json.dumps(dict(choices=[dict(message=dict(content=answer))])).encode())
    return send


def fake_process(monkeypatch):
    calls=[]
    def popen(argv,**kwargs):
        calls.append((argv,kwargs))
        return SimpleNamespace(pid=99999999,poll=lambda:None)
    monkeypatch.setattr(daily.subprocess,'Popen',popen)
    return calls


@pytest.mark.parametrize('failure',['missing','expired','provider','host','401','blind'])
@pytest.mark.parametrize('auto_start',[False,True])
def test_daily_http_refuses_before_dispatch_and_remains_startable(tmp_path,monkeypatch,materialize,failure,auto_start):
    now=[1000.0];vault=ProviderKeyVault(clock=lambda:now[0],default_ttl=10)
    service=memory_service(tmp_path,monkeypatch,vault);token=control(service)
    key=Secret('sk-'+secrets.token_urlsafe(32))
    # Save exactly through the browser's REST route, except the missing case.
    if failure!='missing':
        status,_=call(service,token,'PUT','/api/provider/credentials/mine',dict(
            provider='openai-compatible',key=key.reveal(),base_url='http://127.0.0.1:12345/v1'))
        assert status==200
    if failure=='expired':now[0]+=11
    if failure=='provider':
        assert call(service,token,'PUT','/api/provider/credentials/mine',dict(provider='deepseek',key=key.reveal()))[0]==200
    if failure=='host':
        assert call(service,token,'PUT','/api/provider/credentials/mine',dict(provider='openai-compatible',key=key.reveal(),base_url='http://localhost:12345/v1'))[0]==200
    monkeypatch.setattr(provider,'open_request',fake_probe_transport(fail=failure=='401',blind=failure=='blind',canary=key.reveal()))
    launched=fake_process(monkeypatch)
    status,value=create_memory(service,token,start=auto_start)
    if not auto_start:
        assert status==200,value
        eid=value['experiment_id']
        status,value=call(service,token,'POST',f'/api/dev-v2/experiments/{eid}/run',{})
    else:
        eid=next((tmp_path/'daily').glob('devexp-*')).name
    assert status==409 and isinstance(value['error'],str),value
    code='provider_key_missing' if failure in ('missing','expired') else 'provider_mismatch' if failure in ('provider','host') else 'provider_preflight_failed'
    assert code in value['error'] and value['detail']['code']==code
    if failure in ('401','blind'):
        assert value['checks'] and value['errors']
        if failure=='401':assert value['detail']['redacted'] and key.reveal() not in json.dumps(value)
        else:assert value['detail']['role']=='eval' and value['checks']['vision']=='fail'
    directory=tmp_path/'daily'/eid
    assert not launched and not list(directory.glob('scheduler-process-*.json'))
    assert not (directory/'run-authorization.json').exists() and not (directory/'scheduler.log').exists()
    snapshot=experiments.snapshot(store_for(tmp_path,eid),eid)
    assert snapshot['state']=='planned' and all(c['state']=='planned' for c in snapshot['cells'])
    assert not any(c.get('jobs') for c in snapshot['cells'])
    # Saving a valid key later retries the same immutable experiment successfully.
    assert call(service,token,'PUT','/api/provider/credentials/mine',dict(provider='openai-compatible',
        key=key.reveal(),base_url='http://127.0.0.1:12345/v1'))[0]==200
    monkeypatch.setattr(provider,'open_request',fake_probe_transport())
    status,value=call(service,token,'POST',f'/api/dev-v2/experiments/{eid}/run',{})
    assert status==200 and value['worker_started'] and len(launched)==1,value
    argv,kwargs=launched[0]
    assert '--key-fd' in argv and kwargs['close_fds'] is True and kwargs['pass_fds']
    assert not scan([key],roots=[directory],sqlite_paths=[directory/'ledger.sqlite3'],
        extra_blobs=[json.dumps(argv),json.dumps(kwargs['env'])],include_processes=False)


def test_unstarted_agent_view_returns_frozen_configuration_readonly(tmp_path,monkeypatch,materialize):
    service=memory_service(tmp_path,monkeypatch,ProviderKeyVault());token=control(service)
    status,made=create_memory(service,token)
    assert status==200,made
    assert made['selection']['model_source']=='byok'
    eid=made['experiment_id'];cell=made['cells'][0];store=store_for(tmp_path,eid)
    assert 'artifact_dir' not in store.cell(cell['key'])  # The original KeyError trigger.
    before=store.path.read_bytes()
    status,view=call(service,token,'GET',f'/api/dev-v2/experiments/{eid}/cells/{cell["key"]}/view')
    assert status==200,view
    assert view['state']=='created' and view['status']=='not started' and view['versions']==[]
    assert view['configuration']['model_source']=='byok'
    assert view['agent']['model_source']=='byok' and view['agent']['dev_model']=='fake-dev'
    assert view['agent']['eval_model']=='fake-eval' and view['agent']['advisor_model']=='fake-advisor'
    assert view['agent']['quota']=={} and view['agent']['phase']=='not started'
    compat=view['dev_v2_compat']
    assert compat['run_controls']['can_start'] and compat['lifecycle']['phase']=='prepared' and compat['lifecycle']['can_start'] and not compat['lifecycle']['can_stop'] and compat['lifecycle']['scope_count']==1 and compat['lifecycle']['start_label']=='Start run'
    assert store.path.read_bytes()==before
    status,runs=call(service,token,'GET','/api/runs')
    assert status==200 and runs['dev_sessions'][0]['locked']['config']['model_source']=='byok'


@pytest.mark.parametrize('bridge_kind',['daily','main'])
def test_lab_popen_argv_env_are_unchanged(tmp_path,monkeypatch,materialize,bridge_kind):
    bridge=daily.DailyDevV2(tmp_path/'daily',[],per_run_budget=True)
    made=bridge.create(dict(selection=selection(byok=False),start=False));eid=made['experiment_id']
    store=bridge.store(eid);experiment=experiments.snapshot(store,eid)
    manifest=json.loads(Path(experiment['manifest']['uri']).read_bytes())
    assert manifest['selection']['model_source']=='codex'
    assert 'agent_provider' not in manifest
    if bridge_kind=='main':
        bridge=DevMainBridge(store.path,tmp_path/'daily'/eid/'artifacts',manifest['selection']['goal_key'])
    monkeypatch.setattr(admission,'preflight',lambda *a,**k:pytest.fail('Lab must not probe/take'))
    class NoVault:
        def __getattr__(self,name):pytest.fail('Lab must not use credential context: '+name)
    calls=fake_process(monkeypatch)
    result=bridge.start(eid,keys=NoVault())
    assert result['worker_started'] and len(calls)==1
    argv,kwargs=calls[0]
    from openarm365_dev_v2.dev_config import ROOT
    if bridge_kind=='daily':
        expected=[sys.executable,'-B','-u','-m',daily.__name__,'--worker',eid,'--root',str(bridge.root),
            '--budget',str(tmp_path/'daily'/eid/'run-authorization.json')]
        env={**os.environ,'PYTHONPATH':str(ROOT),'PYTHONDONTWRITEBYTECODE':'1'}
    else:
        from benchmarks.attention_harness.dev_capabilities import execution_environment
        expected=[sys.executable,'-B','-u','-m','benchmarks.attention_harness.dev_experiment','run',
            '--store',str(store.path),'--artifacts',str(bridge.artifacts),'--experiment',eid,'--max-cells','1']
        env=execution_environment(ROOT,{'ATTENTION_PLANNER_UI_URL':'','ATTENTION_PLANNER_APPROVAL_API_PREFIX':'/api/dev-v2'})
    assert argv==expected and kwargs['env']==env
    assert kwargs['cwd']==ROOT and kwargs['stdout'] is kwargs['stderr']
    assert kwargs['start_new_session'] is True
    assert set(kwargs)=={'cwd','env','stdout','stderr','start_new_session'}


@pytest.mark.parametrize('condition',['alive','completed','cancelled','review','pause','receipt','other_worker'])
def test_ineligible_daily_requests_never_take_or_probe(tmp_path,monkeypatch,materialize,condition):
    bridge=daily.DailyDevV2(tmp_path/'daily',[],per_run_budget=True)
    made=bridge.create(dict(selection=selection(),start=False));eid=made['experiment_id'];store=bridge.store(eid)
    class NoKeys:
        def __getattr__(self,name):pytest.fail('ineligible request touched keys: '+name)
    if condition in ('alive','pause'):bridge.workers[eid]=SimpleNamespace(poll=lambda:None)
    if condition=='pause':
        with store.tx() as db:
            e=store.get(db,'experiments',eid);e['pause_requested_at']=time.time();store.put(db,'experiments',eid,e)
    if condition in ('completed','cancelled'):
        with store.tx() as db:
            e=store.get(db,'experiments',eid)
            e.update(state='completed' if condition=='completed' else 'planned',cancelled=condition=='cancelled')
            store.put(db,'experiments',eid,e)
    if condition=='review':store.update_cell(made['cells'][0]['key'],state='needs_review')
    if condition=='receipt':
        from benchmarks.attention_harness.dev_loop import process_identity
        (tmp_path/'daily'/eid/'scheduler-process-test.json').write_text(json.dumps(process_identity()))
    if condition=='other_worker':bridge.workers['other']=SimpleNamespace(poll=lambda:None)
    if condition in ('cancelled','review','other_worker'):
        with pytest.raises(DevConflict):bridge.start(eid,keys=NoKeys())
    else:
        result=bridge.start(eid,keys=NoKeys())
        assert result.get('pause_withdrawn') if condition=='pause' else result.get('deduplicated')


@pytest.mark.parametrize('bridge_kind',['daily','main'])
def test_preflight_runs_outside_mutex_and_rechecks_conflicts(tmp_path,monkeypatch,materialize,bridge_kind):
    bridge=daily.DailyDevV2(tmp_path/'daily',[],per_run_budget=True)
    made=bridge.create(dict(selection=selection(),start=False));eid=made['experiment_id']
    if bridge_kind=='main':
        bridge=DevMainBridge(bridge.store(eid).path,tmp_path/'daily'/eid/'artifacts',selection()['goal_key'])
    vault=ProviderKeyVault();vault.put('local','mine','openai-compatible','sk-offline',base_url='http://127.0.0.1:12345/v1')
    probes=[]
    def probe(*a,role,**kwargs):
        assert bridge.mutex.acquire(blocking=False)
        bridge.mutex.release();probes.append(role)
        # A concurrent request changes dispatch eligibility while the probe runs.
        if role=='advisor':experiments.cancel(bridge.store if bridge_kind=='main' else bridge.store(eid),eid)
        return provider.ProbeResult(dict.fromkeys(('auth','chat','tools','streaming','vision'),'ok'),[])
    monkeypatch.setattr(admission,'probe',probe)
    launched=fake_process(monkeypatch)
    with pytest.raises(DevConflict):
        bridge.start(eid,keys=admission.RunCredentials(vault,'local',True,('127.0.0.1',)))
    assert probes==['advisor','dev','eval'] or set(probes)=={'dev','eval','advisor'}
    assert not launched and not list((tmp_path/'daily'/eid).glob('scheduler-process-*.json'))


def test_main_bridge_byok_launch_uses_shared_pipe(tmp_path,monkeypatch,materialize):
    daily_bridge=daily.DailyDevV2(tmp_path/'daily',[],per_run_budget=True)
    made=daily_bridge.create(dict(selection=selection(),start=False));eid=made['experiment_id']
    bridge=DevMainBridge(daily_bridge.store(eid).path,tmp_path/'daily'/eid/'artifacts',selection()['goal_key'])
    with pytest.raises(provider.ProviderError,match='Missing or expired credential'):
        bridge.start(eid)
    assert not bridge.workers and not list(bridge.artifacts.glob('devexp-*/scheduler-process-*.json'))
    vault=ProviderKeyVault();vault.put('local','mine','openai-compatible','sk-test',base_url='http://127.0.0.1:12345/v1')
    monkeypatch.setattr(provider,'open_request',fake_probe_transport())
    calls=fake_process(monkeypatch)
    assert bridge.start(eid,keys=admission.RunCredentials(vault,'local',True,('127.0.0.1',)))['worker_started']
    argv,kwargs=calls[0]
    assert '--key-fd' in argv and kwargs['pass_fds'] and kwargs['close_fds'] is True
    assert not set(KEY_ENV)&set(kwargs['env'])


def test_agent_eval_uses_real_agent_ledger_with_zero_legacy_eval_calls(tmp_path,monkeypatch,materialize):
    bridge=daily.DailyDevV2(tmp_path/'daily',[],per_run_budget=True)
    chosen=selection();chosen.update(help_budget=5,agent_limits=dict(versions=2,wall_seconds=21600))
    made=bridge.create(dict(selection=chosen,start=False));eid=made['experiment_id'];store=bridge.store(eid)
    e=experiments.snapshot(store,eid);m=json.loads(Path(e['manifest']['uri']).read_bytes())
    assert m['cell_budget']['eval_calls']==0 and m['agent_budget']['eval_runs']==2
    assert m['budget_accounting_version']=='free-model-tokens-eval-calls-v2'
    ledger=AgentLedger(store.path,e['cells'][0]['key'],help_limit=m['selection']['help_budget'],budget=m['agent_limits'])
    ledger.reserve('submit:v0001','request',{'formal_versions':1});ledger.claim('submit:v0001')
    ledger.settle('submit:v0001',{'version_id':'v0001'})
    ledger.reserve('eval:v0001','eval',{'eval_runs':1});ledger.claim('eval:v0001')
    ledger.settle('eval:v0001',{'passed':False},usage={'input_tokens':3,'output_tokens':2,'total_tokens':5})
    snapshot=ledger.snapshot()
    assert snapshot['used']['eval_runs']==1 and snapshot['remaining']['eval_runs']==1
    assert ledger.jobs()[-1]['state']=='settled' and ledger.jobs()[-1]['usage']['total_tokens']==5
    assert not store.cell(e['cells'][0]['key'])['jobs']  # No legacy v2 Eval reservation.


def test_real_daily_worker_pipe_without_socket_permission(tmp_path,monkeypatch,materialize):
    key=Secret('sk-'+secrets.token_urlsafe(32));vault=ProviderKeyVault()
    service=memory_service(tmp_path,monkeypatch,vault);token=control(service)
    assert call(service,token,'PUT','/api/provider/credentials/mine',dict(provider='openai-compatible',
        key=key.reveal(),base_url='http://127.0.0.1:12345/v1'))[0]==200
    monkeypatch.setattr(provider,'open_request',fake_probe_transport())
    original_popen=subprocess.Popen;processes=[];launches=[]
    wrapper=Path(__file__).parent/'fakes/daily_byok_worker.py'
    def popen(argv,**kwargs):
        if 'benchmarks.attention_harness.daily_dev_v2' not in argv:return original_popen(argv,**kwargs)
        launches.append((argv,kwargs));i=argv.index('-m')
        process=original_popen([*argv[:i],str(wrapper.resolve()),'--memory-provider',*argv[i+2:]],**kwargs)
        processes.append(process);return process
    monkeypatch.setattr(daily.subprocess,'Popen',popen)
    directory=None
    try:
        status,value=create_memory(service,token,start=True)
        assert status==200,value
        directory=tmp_path/'daily'/value['experiment_id']
        deadline=time.monotonic()+15
        while not (directory/'offline-result.json').exists() and time.monotonic()<deadline:
            assert processes[0].poll() is None,(directory/'scheduler.log').read_text()
            time.sleep(.02)
        result=json.loads((directory/'offline-result.json').read_bytes())
        assert set(result['roles'])=={'dev','eval','advisor'} and result['hardened']
        assert result['argv_clean'] and result['environ_clean']
        argv,kwargs=launches[0]
        assert '--key-fd' in argv and kwargs['pass_fds'] and kwargs['close_fds'] is True
        assert not set(KEY_ENV)&set(kwargs['env'])
        assert not scan([key],roots=[directory],sqlite_paths=[directory/'ledger.sqlite3'],include_processes=True,
            extra_blobs=[json.dumps(argv),json.dumps(kwargs['env'])])
        (directory/'offline-release').touch()
        assert processes[0].wait(timeout=10)==0,(directory/'scheduler.log').read_text()
    finally:
        if directory:(directory/'offline-release').touch()
        for process in processes:
            if process.poll() is None:process.kill();process.wait()


def test_daily_worker_without_key_fd_keeps_run_arguments(tmp_path,monkeypatch,materialize):
    bridge=daily.DailyDevV2(tmp_path/'daily',[],per_run_budget=True)
    made=bridge.create(dict(selection=selection(byok=False),start=False));eid=made['experiment_id'];store=bridge.store(eid)
    directory,_,creation=bridge.paths(eid)
    budget=bridge.dispatch_budget(store,experiments.snapshot(store,eid),directory,creation)
    calls=[]
    monkeypatch.setattr(experiments,'run',lambda *args,**kwargs:calls.append((args,kwargs)))
    from benchmarks.attention_harness import memory_auto_import
    monkeypatch.setattr(memory_auto_import,'launch_after_run',lambda *args:None)
    monkeypatch.setattr(sys,'argv',['daily','--worker',eid,'--root',str(bridge.root),'--budget',str(budget)])
    daily.main()
    assert len(calls)==1 and calls[0][0][1]==eid
    assert calls[0][1]==dict(max_cells=1,budget_file=budget)


@pytest.mark.parametrize('auto_start',[False,True])
def test_real_daily_server_worker_pipe_and_three_role_chats(tmp_path,monkeypatch,materialize,loopback,auto_start):
    """The actual 8769 HTTP -> DailyDevV2.start -> daily.main worker path."""
    key=Secret('sk-'+secrets.token_urlsafe(32));processes=[];launches=[];responses=[]
    original_popen=subprocess.Popen
    wrapper=Path(__file__).parent/'fakes/daily_byok_worker.py'
    def popen(argv,**kwargs):
        if 'benchmarks.attention_harness.daily_dev_v2' not in argv:return original_popen(argv,**kwargs)
        launches.append((list(argv),dict(kwargs)))
        i=argv.index('-m')
        rewritten=[*argv[:i],str(wrapper.resolve()),*argv[i+2:]]
        process=original_popen(rewritten,**kwargs);processes.append(process);return process
    monkeypatch.setattr(daily.subprocess,'Popen',popen)
    with FakeProvider(key.reveal(),vision=True) as fake:
        service=ui_server.create_server(store=AttentionStore(tmp_path/'ui.sqlite3'),port=0,dev_v2_root=tmp_path/'daily',dev_v2_per_run_budget=True,
            allow_custom=True,allow_private_hosts=('127.0.0.1',))
        thread=threading.Thread(target=service.serve_forever,daemon=True);thread.start()
        def request(method,path,body=None,token=None):
            req=urllib.request.Request(f'http://127.0.0.1:{service.server_port}'+path,
                data=None if body is None else json.dumps(body).encode(),method=method,
                headers={'Content-Type':'application/json',**({'X-Attention-Token':token} if token else {})})
            try:response=urllib.request.urlopen(req,timeout=20)
            except urllib.error.HTTPError as exc:response=exc
            with response:
                raw=response.read();responses.append(raw)
                return response.status,raw
        directory=None
        try:
            _,html=request('GET','/ui/');token=re.search(rb'data-control-token="([^"]+)"',html)[1].decode()
            assert request('PUT','/api/provider/credentials/mine',dict(provider='openai-compatible',key=key.reveal(),base_url=fake.base_url),token)[0]==200
            chosen=selection()
            # Same role descriptors as keysSelection() in live.js: no URL/host.
            for desc in chosen['agent_provider']['roles'].values():desc.pop('base_url_host')
            status,raw=request('POST','/api/dev-v2/experiments',dict(selection=chosen,start=auto_start),token)
            assert status==200,raw
            eid=json.loads(raw)['experiment_id'];directory=tmp_path/'daily'/eid
            if not auto_start:
                status,raw=request('POST',f'/api/dev-v2/experiments/{eid}/run',{},token)
                assert status==200 and json.loads(raw)['worker_started'],raw
            assert len(processes)==1
            deadline=time.monotonic()+15
            while not (directory/'offline-result.json').exists() and time.monotonic()<deadline:
                assert processes[0].poll() is None,(directory/'scheduler.log').read_text()
                time.sleep(.02)
            result=json.loads((directory/'offline-result.json').read_bytes())
            assert set(result['roles'])=={'dev','eval','advisor'} and result['hardened']
            assert result['argv_clean'] and result['environ_clean']
            argv,kwargs=launches[0]
            assert '--key-fd' in argv and kwargs['pass_fds'] and kwargs['close_fds'] is True
            assert not (set(KEY_ENV)&set(kwargs['env']))
            assert not scan([key],roots=[directory],sqlite_paths=[directory/'ledger.sqlite3'],
                extra_blobs=[*responses,json.dumps(argv),json.dumps(kwargs['env'])],include_processes=True)
            chats=[b for _,b in fake.requests if any(m.get('content','').startswith('daily offline ') for m in b.get('messages',[]) if isinstance(m.get('content'),str))]
            assert {b['model'] for b in chats}=={'fake-dev','fake-eval','fake-advisor'}
            assert fake.authorizations and all(a=='Bearer '+key.reveal() for a in fake.authorizations)
            (directory/'offline-release').touch()
            assert processes[0].wait(timeout=10)==0,(directory/'scheduler.log').read_text()
        finally:
            if directory:(directory/'offline-release').touch()
            for process in processes:
                if process.poll() is None:process.kill();process.wait()
            service.shutdown();service.server_close();thread.join()
