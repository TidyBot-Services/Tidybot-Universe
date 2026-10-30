"""Production factory/HTTP path with synthetic native execution and scripted Dev."""
import asyncio
import io
import json
import time
from pathlib import Path
from threading import Event
from urllib.error import HTTPError
from urllib.parse import urlsplit

import pytest

from benchmarks.attention_harness import memory_validation_runtime as runtime
from benchmarks.attention_harness import memory_validation_worker as worker
from benchmarks.attention_harness import dev_disk, memory_agent_replay
from benchmarks.attention_harness.memory_monitor import automatic_checks
from benchmarks.attention_harness.memory_validation_disk import GIB, gate, estimate
from benchmarks.attention_harness.tests.agent_validation_fakes import fixture, synthetic_creation
from benchmarks.attention_harness.tests.agent_origin_fakes import replay
from benchmarks.attention_harness.tests.autovalidate_fakes import outcome


def production(tmp_path, monkeypatch, **options):
    import socket
    from types import SimpleNamespace
    from benchmarks.attention_harness import dev_agent_loop
    from attention_memory_service.memory_service_api import create_app
    from attention_memory_service import memory_service_client
    from openarm365_dev_v2 import runner as native_runner
    real_socket=socket.socket
    f=fixture(tmp_path,monkeypatch,prepared=False,**options)
    listener=socket.socket
    monkeypatch.setattr(socket,'socket',real_socket)
    monkeypatch.setattr(dev_agent_loop,'socket',SimpleNamespace(socket=listener))
    f.runner.pool.shutdown()
    # The old phase-2 fixture omitted scheduler fields unused by its injected
    # activity callback. Supply the settled production ledger shape here.
    with f.store.tx() as db:
        experiment=f.store.get(db,'experiments','cell')
        experiment.update(experiment_id='cell',state='completed',artifact_dir=str(f.root))
        f.store.put(db,'experiments','cell',experiment)
    root=f.provision.state_root;root.mkdir()
    keys=tmp_path/'offline.env'
    keys.write_text('ATTENTION_MEMORY_API_KEY=phase4-synthetic-reader\nATTENTION_MEMORY_IMPORTER_KEY=phase4-synthetic-importer\n')
    roots=dict(executor=[str(f.native.root)],validator=[str(f.native.validator)],source=[str(tmp_path/'sources')],contract=[str(tmp_path/'contracts')])
    app=create_app(tmp_path/'service.sqlite',api_key='phase4-synthetic-reader',importer_key='phase4-synthetic-importer',dev_v2_registration_roots=roots)
    calls=[]
    def transport(request,timeout):
        url=urlsplit(request.full_url);calls.append((request.method,url.path))
        scope=dict(type='http',asgi={'version':'3.0'},http_version='1.1',method=request.method,
            scheme='http',path=url.path,raw_path=url.path.encode(),query_string=url.query.encode(),
            headers=[(k.lower().encode(),v.encode()) for k,v in request.header_items()],
            client=('127.0.0.1',12345),server=('offline.invalid',80))
        messages=[]
        async def receive():return dict(type='http.request',body=request.data or b'',more_body=False)
        async def send(message):messages.append(message)
        asyncio.run(app(scope,receive,send))
        status=next(m['status'] for m in messages if m['type']=='http.response.start')
        body=b''.join(m.get('body',b'') for m in messages if m['type']=='http.response.body')
        if status>=400:raise HTTPError(request.full_url,status,'offline service refusal',{},io.BytesIO(body))
        return io.BytesIO(body)
    monkeypatch.setattr(memory_service_client,'urlopen',transport)
    # Keep default adapter arguments: replace only the native execution boundary.
    monkeypatch.setattr(native_runner,'OpenArm365DevRunnerV2',f.runner.runner_factory)
    monkeypatch.setattr(memory_agent_replay,'replay_policy',lambda *a,**kw:replay(f,*a,**kw))
    monkeypatch.setattr(worker,'weekly_quota_snapshot',lambda *a:dict(used_percent=1,observed_at=time.time()))
    monkeypatch.setattr(dev_disk,'status',lambda root:dict(dev_disk.thresholds(),free_gb=200*GIB/1e9,used_percent=1))
    worker.atomic(root/'runtime.json',dict(enabled=False,base_url='http://localhost:18888',key_file=str(keys),
        experiments_root=str(f.native.root),validator_root=str(f.native.validator),activity_ledger_roots=[str(f.native.root)],activity_ledgers=[]))
    worker.atomic(root/'autovalidate.json',dict(enabled=True,daily_run_limit=None,weekly_quota_stop_percent=60,daily_quota_rise_stop_points=5))
    deps=runtime.build_runtime(root)
    deps.root=root;deps.clock=time.time;deps.save=lambda state:worker.atomic(root/'state.json',state)
    outcome(f.service,f.note['memory_id'],0,ctx=dict(f.context,namespace='offline-agent-origin'))
    return f,deps,calls


def until(state,deps,predicate,timeout=180):
    deadline=time.monotonic()+timeout
    while not predicate():
        worker.tick(state,deps)
        assert not (state.get('current') or {}).get('begin_error'),state
        assert time.monotonic()<deadline,state
        time.sleep(.01)


def test_production_worker_gates_resume_and_ten_arms(tmp_path,monkeypatch):
    # One formal execution per arm makes dispatch counts unambiguous; phase 3
    # separately covers the optional no-eval trial before formal submission.
    script=("tool('observe'); packet=json.loads(Path('memory/v1.json').read_text()); "
        "assert Path('work/main.py').read_bytes()==Path('origin/policy.py').read_bytes(); "
        "assert Path('origin/public-feedback.json').exists(); "
        "Path('work/main.py').write_text(Path('work/main.py').read_text()+"
        "('\\n# fixed\\n' if packet['memory'] else '\\n# control\\n')); tool('submit')")
    f,d,calls=production(tmp_path,monkeypatch,policy_version='agent-policy-v2',policy='trace_aware_hint_only',script=script)
    started,release=Event(),Event()
    execute=f.runner.runner_factory.execute
    def controlled_execute(self,request):
        if not started.is_set():
            started.set()
            assert release.wait(180),'first arm was not released'
        return execute(self,request)
    monkeypatch.setattr(f.runner.runner_factory,'execute',controlled_execute)
    state={}
    try:
        with synthetic_creation(f.native):
            monkeypatch.setattr(dev_disk,'status',lambda root:dict(dev_disk.thresholds(),free_gb=79*GIB/1e9,used_percent=1))
            worker.tick(state,d)
            assert 'paused by disk' in state['status'] and not state.get('current')
            assert f.note['memory_id'] in automatic_checks(d.root)['errors'][0]
            assert state['disk_gate']['required_bytes']==80*GIB
            monkeypatch.setattr(dev_disk,'status',lambda root:dict(dev_disk.thresholds(),free_gb=200*GIB/1e9,used_percent=1))
            until(state,d,lambda:bool((state.get('current') or {}).get('dev_mode')))
            assert state['current']['dev_mode']=='agent'
            assert started.wait(30),'first arm was not dispatched'
            assert d.runner.poll(state['current'])=='running'
            first=state['current']['arm_id']
            # Install the changed quota while control is running. Admission sees
            # 1%, then the fresh check immediately before treatment sees 60%.
            reads=[0]
            def quota():
                reads[0]+=1
                return dict(used_percent=1 if reads[0]==1 else 60,observed_at=time.time())
            d.quota=quota
            release.set()
            # Waiting on the arm itself cannot tick the worker into treatment.
            d.runner.futures[first].result(timeout=180)
            executions_before_stop=list(f.executions)
            futures_before_stop=set(d.runner.futures)
            assert len(executions_before_stop)==1
            until(state,d,lambda:len((state.get('current') or {}).get('trials',{}))==1)
            assert 'stop at 60%' in state['status'],state
            assert reads[0]==2
            assert f.executions==executions_before_stop
            assert state['current']['dispatch_pending'] and state['current']['arm']=='treatment'
            assert first in state['current']['arm_measurements'] and state['agent_arm_measured_max_bytes']>0
            for _ in range(3):
                worker.tick(state,d)
                assert 'stop at 60%' in state['status'],state
                assert len(state['current']['trials'])==1 and state['current']['dispatch_pending']
                assert state['current']['arm']=='treatment'
                assert f.executions==executions_before_stop
                assert set(d.runner.futures)==futures_before_stop
            # Restart from persisted state while a not-yet-dispatched arm is paused.
            state=json.loads((d.root/'state.json').read_bytes())
            d.quota=lambda:dict(used_percent=1,observed_at=time.time())
            monkeypatch.setattr(dev_disk,'status',lambda root:dict(dev_disk.thresholds(),free_gb=52*GIB/1e9,used_percent=1))
            for _ in range(3):
                worker.tick(state,d)
                assert 'paused by disk' in state['status'] and len(state['current']['trials'])==1
                assert state['current']['dispatch_pending'] and state['current']['arm']=='treatment'
                assert f.executions==executions_before_stop
                assert set(d.runner.futures)==futures_before_stop
            monkeypatch.setattr(dev_disk,'status',lambda root:dict(dev_disk.thresholds(),free_gb=200*GIB/1e9,used_percent=1))
            worker.tick(state,d)
            view=automatic_checks(d.root)
            assert view['current']['memory_id']==f.note['memory_id']
            assert view['current']['dev_model']=='gpt-6-astra' and view['current']['last_verdict']=='passed'
            assert view['current']['disk_used_bytes']>0 and view['current']['elapsed_seconds']>=0
            until(state,d,lambda:bool(state.get('results')))
        assert state['results'][0]['status']=='trusted',state
        assert len(f.executions)==10 and len(set(f.executions))==10
        assert len(f.service.list_pairs(state['results'][0]['memory_id']))==5
        assert len(state['counted'])==11  # One origin plus ten unique arms.
        assert any(path.endswith('/evaluate') for method,path in calls)
        assert all('18888' not in e for e in automatic_checks(d.root)['errors'])
    finally:
        release.set()
        d.runner.pool.shutdown()


@pytest.mark.parametrize('failure',['preflight','export'])
def test_origin_error_is_visible_once_and_does_not_block_next_note(tmp_path,monkeypatch,failure):
    from benchmarks.attention_harness import memory_origin_evidence
    f,d,_=production(tmp_path,monkeypatch)
    calls=[]
    def bad(*a,**kw):calls.append(1);raise ValueError('phase4 missing origin evidence')
    if failure=='preflight':monkeypatch.setattr(d.runner.origins,'needed',bad)
    else:monkeypatch.setattr(memory_origin_evidence,'locate',bad)
    state={}
    try:
        until(state,d,lambda:bool(state.get('note_errors')))
        for _ in range(4):worker.tick(state,d)
        assert len(calls)==1 and not state.get('current') and not state.get('pending')
        errors=automatic_checks(d.root)['errors']
        assert any(f.note['memory_id'] in e and 'phase4 missing origin evidence' in e for e in errors)
        assert not f.executions
        # Preserve queue ordering, skip only the permanently failed head.
        good=dict(f.note,memory_id='next-note',channel='dev_agent',version=1,uses=1)
        bad_note=d.client.validation_queue()[0]
        monkeypatch.setattr(d.client,'validation_queue',lambda:[bad_note,good])
        monkeypatch.setattr(d.runner.origins,'needed',lambda note:None)
        seen=[]
        monkeypatch.setattr(d.runner,'begin',lambda note,operation:seen.append(note['memory_id']) or (_ for _ in ()).throw(ValueError('stop after next selection')))
        worker.tick(state,d)
        assert seen==['next-note']
    finally:d.runner.pool.shutdown()


def test_estimates_use_larger_measured_arm_and_invalid_settings(tmp_path):
    from types import SimpleNamespace
    runner=SimpleNamespace(provision=SimpleNamespace(config={'agent_arm_estimated_bytes':2*GIB}))
    state=dict(agent_arm_measured_max_bytes=4*GIB)
    assert estimate(runner,state)==4*GIB
    runner.provision.config['agent_arm_estimated_bytes']=False
    with pytest.raises(ValueError,match='positive integer'):estimate(runner,state)


@pytest.mark.parametrize('policy',['reactive_help','trace_aware_hint_only'])
@pytest.mark.parametrize('fault',['manifest','config','cell','all'])
def test_harness_checks_visibility_against_sealed_origin(tmp_path,monkeypatch,policy,fault):
    from copy import deepcopy
    from benchmarks.attention_harness.dev_ledger import DevV2Store
    from benchmarks.attention_harness import dev_experiment
    from benchmarks.attention_harness.memory_agent_validation import validate_manifest
    f=fixture(tmp_path,monkeypatch,policy_version='agent-policy-v2',policy=policy)
    try:
        with synthetic_creation(f.native):
            batch=f.runner.begin(dict(f.note,created_at=f.service.get_memory(f.note['memory_id']).created_at),'visibility-operation')
            store=DevV2Store(next(iter(batch['cells'].values()))['ledger'])
            manifest=dev_experiment.verify_manifest(dev_experiment.snapshot(store,batch['experiment_id']))
            hidden=policy=='trace_aware_hint_only'
            rows=[manifest,*manifest['cells']]
            assert all('memory_visibility' not in row for row in manifest['configs'].values())
            assert all((r.get('memory_visibility')=='validation_override')==hidden for r in rows)
            changed=deepcopy(manifest)
            rows=([changed,*changed['configs'].values(),*changed['cells']] if fault=='all' else
                [dict(manifest=changed,config=next(iter(changed['configs'].values())),cell=changed['cells'][0])[fault]])
            for row in rows:
                if hidden and fault!='config':row.pop('memory_visibility',None)
                else:row['memory_visibility']='validation_override'
            with pytest.raises(ValueError,match='visibility differs from origin'):validate_manifest(changed)
    finally:f.runner.pool.shutdown()


def test_invalid_arm_error_reaches_memory_page(tmp_path,monkeypatch):
    f,d,_=production(tmp_path,monkeypatch,script="raise RuntimeError('phase4 synthetic model crash')")
    state={}
    try:
        with synthetic_creation(f.native):until(state,d,lambda:bool(state.get('results')))
        assert state['results'][0]['status']=='inconclusive'
        errors=automatic_checks(d.root)['errors']
        assert any(f.note['memory_id'] in e and 'fatal' in e for e in errors),errors
        assert not f.executions
    finally:d.runner.pool.shutdown()


def test_quota_pause_after_last_arm_shows_evaluation_not_pair_six(tmp_path):
    worker.set_enabled(tmp_path,True)
    worker.atomic(tmp_path/'state.json',dict(status='Automatic checks paused: quota stop',current=dict(
        dev_mode='agent',memory_id='candidate',note_id='original-note',title='Cup',pair_index=5,
        arm='control',active=False,dev_model={'model':'gpt-6.1-sol'},last_verdict='passed')))
    current=automatic_checks(tmp_path)['current']
    assert current['stage']=='evaluate' and current['paused'] and 'pair' not in current
    assert current['memory_id']=='original-note' and current['last_verdict']=='passed'
