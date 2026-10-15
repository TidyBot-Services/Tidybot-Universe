"""Controlled never-dispatched recovery; no real goal or private files opened."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from benchmarks.attention_harness import dev_integration as host
from benchmarks.attention_harness import dev_experiment as experiments
from benchmarks.attention_harness.dev_ledger import DevV2Store, RESOURCES, encoded
from benchmarks.attention_harness.dev_loop import process_identity, owner_alive, sha
from benchmarks.attention_harness.core.dev_store import DevConflict, DevBudgetExhausted
from openarm365_dev_v2 import dev_config


def raw(store):
    with store.tx() as db:return store.get(db,'goals',host.GOAL_ID)


@pytest.fixture
def prepared(tmp_path, monkeypatch, request):
    ledger=tmp_path/'controlled.sqlite';state=tmp_path/'host'
    monkeypatch.setattr(host,'LEDGER',ledger);monkeypatch.setattr(host,'STATE',state)
    store=DevV2Store(ledger)
    limits=dict(dev_calls=16,advisor_calls=8,executions=32,provider_tokens=400000,
        input_tokens=400000,output_tokens=400000,sdk_calls=2560,control_steps=76800,
        help_count=24,help_credits=24,help_wait_seconds=7200,model_seconds=2880,execution_seconds=7200)
    if getattr(request,'param',False):
        from benchmarks.attention_harness.dev_configuration import derive_cell_budget,normalize_configuration
        from benchmarks.attention_harness.dev_planner import POLICY
        plan=normalize_configuration(dict(max_versions=2,target_depth=2,target_valid_executions=2,help_budget=1,
            policies=[POLICY],planner_features={'human_wait_seconds':600},coverage='budget_limited'))
        limits={k:2*v for k,v in derive_cell_budget(plan).items()}
    store.ensure_goal(host.GOAL_ID,limits,7200)
    # Simulate creation against the immediately previous entry bytes without
    # changing any shared source. Restore the actual hash functions afterwards.
    real_epoch=host.epoch;real_sha=dev_config.sha
    prior=copy.deepcopy(real_epoch())
    reviewed=json.loads((host.OUT/'main-integration/source-readiness.json').read_bytes())['prepared_lifecycle_revision']['files']
    previous_entry=reviewed['benchmarks/attention_harness/dev_integration.py']['before_sha256']
    prior['main_files'][str(host.ROOT/'benchmarks/attention_harness/dev_integration.py')]=previous_entry
    with monkeypatch.context() as m:
        m.setattr(host,'epoch',lambda:prior)
        m.setattr(dev_config,'sha',lambda p:previous_entry if str(p)==str(host.ROOT/'benchmarks/attention_harness/dev_integration.py') else real_sha(p))
        result=host.prepare(argparse.Namespace(memory_url='http://127.0.0.1:1',memory_store_id='controlled-test',
            namespace='controlled-test',port=8876,seed=101,wait_seconds=600))
    eid=result['experiment_id'];e=experiments.snapshot(store,eid)
    (state/'ui.log').write_text("Error while finding module specification for '__main__' (ValueError: __main__.__spec__ is None)\n")
    (state/'ui-process.json').write_text(json.dumps(dict(pid=99999999,start_ticks='0',boot_id='controlled-dead')))
    def forbidden(*a,**k):raise AssertionError('offline recovery attempted dispatch/network/clock')
    monkeypatch.setattr(DevV2Store,'claim',forbidden)
    monkeypatch.setattr(DevV2Store,'start_cell_clock',forbidden)
    from benchmarks.attention_harness import dev_v2_loop, memory_service_client
    monkeypatch.setattr(dev_v2_loop,'ParccClient',forbidden)
    monkeypatch.setattr(dev_v2_loop,'OpenArm365DevRunnerV2',forbidden)
    monkeypatch.setattr(memory_service_client,'MemoryServiceClient',forbidden)
    return store,state,e


def binding(store,e):
    with store.tx() as db:return store.get(db,'experiments',e['experiment_id'])


@pytest.mark.parametrize('prepared',[False,True],indirect=True)
def test_rebind_preserves_allocation_evidence_deadline_and_prepare_retry(prepared):
    store,state,e=prepared;before=raw(store)
    with store.tx() as db:
        before['authorized_until']=time.time()+7000
        store.put(db,'goals',host.GOAL_ID,before)
    cells_before=[{k:v for k,v in c.items() if k not in {'planner','jobs','versions'}} for c in e['cells']]
    files={p:p.read_bytes() for p in state.rglob('*') if p.is_file()}
    with pytest.raises(ValueError,match='source epoch changed'):host.verify_epoch()
    result=host.rebind_prepared();assert result['changed'] and not result['dispatch']
    host.verify_epoch()
    active=binding(store,e);m=experiments.verify_manifest(active)
    assert active['manifest']!=e['manifest'] and m['selection']==e['selection']
    assert m['identity_lock']['path']!=str(Path(e['artifact_dir'])/'identity-lock.json')
    assert all(c['dev_lock']==m['identity_lock'] for c in m['configs'].values())
    assert raw(store)==before
    assert all(p.read_bytes()==data for p,data in files.items())
    audit=host.checked_json(result['audit'])
    assert audit['old_manifest']==e['manifest']
    assert {Path(p['uri']).name for p in audit['preserved']} >= {'epoch.json','manifest.json','identity-lock.json','ui-selection.json','ui.log','ui-process.json'}
    for p in audit['preserved']:
        assert sha(p['uri'])==p['sha256']==sha(p['preserved']['uri'])
    assert not host.rebind_prepared()['changed']
    host.prepare(argparse.Namespace(memory_url='http://127.0.0.1:1',memory_store_id='controlled-test',namespace='controlled-test',port=8876,seed=101,wait_seconds=600))
    assert raw(store)==before
    with store.tx() as db:
        assert db.execute('SELECT COUNT(*) FROM v2_experiments').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM v2_cells').fetchone()[0]==2
        assert db.execute('SELECT COUNT(*) FROM v2_jobs').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM v2_versions').fetchone()[0]==0
        cells=[json.loads(p) for p, in db.execute('SELECT payload FROM v2_cells ORDER BY rowid')]
        assert cells==cells_before
        assert db.execute("SELECT COUNT(*) FROM v2_events WHERE kind='preparation_source_rebound'").fetchone()[0]==1
    # No double allocation: with limits exactly equal to the existing plan, a
    # repeated prepare still succeeds; reserve_check can use an unspent slot.
    for c in cells:store.reserve_check(c['key'],{'dev_calls':1,'executions':1})


@pytest.mark.parametrize('case',[
    'reserved','claimed','needs_review','released','settled','version','clock','finished','opening','used','cell_used',
    'cell_running','cell_owner','cell_created','mixed_policy','exp_running','exp_owner','cancelled',
    'request_waiting','request_approved','request_rejected','work','memory_event','unknown_event',
    'scheduler_dead','scheduler_live','ui_live','ui_unknown','unknown_artifact','deadline','short_deadline','orphan_job','orphan_version','orphan_request','orphan_work'])
def test_rebind_refuses_every_execution_or_unknown_boundary_and_audits(prepared,case):
    store,state,e=prepared;key=e['cells'][0]['key']
    with store.tx() as db:
        goal=store.get(db,'goals',host.GOAL_ID);cell=store.get(db,'cells',key);exp=store.get(db,'experiments',e['experiment_id'])
        if case in {'reserved','claimed','needs_review','released','settled'}:
            db.execute('INSERT INTO v2_jobs VALUES (?,?,?,?,?,?)',('test-job',key,1,'model',case,encoded(dict(cell=key,reservation={'dev_calls':1},actual={'dev_calls':1} if case=='settled' else {}))))
        elif case.startswith('orphan_'):
            kind=case.split('_')[1]
            if kind=='job':db.execute('INSERT INTO v2_jobs VALUES (?,?,?,?,?,?)',('orphan','missing',1,'execute','needs_review',encoded(dict(cell='missing',reservation={'executions':1}))))
            elif kind=='version':db.execute('INSERT INTO v2_versions VALUES (?,?,?)',('missing',1,'{}'))
            else:
                table='v2_planner_requests' if kind=='request' else 'v2_planner_work'
                db.execute('CREATE TABLE IF NOT EXISTS '+table+'(id TEXT PRIMARY KEY,cell TEXT,payload TEXT)')
                db.execute('INSERT INTO '+table+' VALUES (?,?,?)',('orphan','missing','{}'))
        elif case=='version':db.execute('INSERT INTO v2_versions VALUES (?,?,?)',(key,1,'{}'))
        elif case=='clock':goal['real_started_at']=time.time()
        elif case=='finished':goal['real_finished_at']=time.time()
        elif case=='opening':goal['opening']=[{'test':'prior work'}]
        elif case=='used':goal['used']['provider_tokens']=1
        elif case=='cell_used':cell['used']['help_wait_seconds']=.1
        elif case=='cell_running':cell['state']='running'
        elif case=='cell_owner':cell['owner']=dict(pid=99999999,start_ticks='0',boot_id='dead')
        elif case=='cell_created':cell['created_at']=time.time()
        elif case=='mixed_policy':cell['mixed_policy']=True
        elif case=='exp_running':exp['state']='running'
        elif case=='exp_owner':exp['owner']=process_identity()
        elif case=='cancelled':exp['cancelled']=True
        elif case.startswith('request_'):
            db.execute('CREATE TABLE IF NOT EXISTS v2_planner_requests(id TEXT PRIMARY KEY,cell TEXT,payload TEXT)')
            db.execute('INSERT INTO v2_planner_requests VALUES (?,?,?)',('test-request',key,encoded(dict(state=case.split('_')[1]))))
        elif case=='work':
            db.execute('CREATE TABLE IF NOT EXISTS v2_planner_work(id TEXT PRIMARY KEY,cell TEXT,payload TEXT)')
            db.execute('INSERT INTO v2_planner_work VALUES (?,?,?)',('test-work',key,'{}'))
        elif case in {'memory_event','unknown_event'}:store.event(db,'planner_memory_used' if case=='memory_event' else 'unknown_test_event',dict(cell=key))
        elif case.startswith('scheduler_'):(Path(e['artifact_dir'])/'scheduler-process-test.json').write_text(json.dumps(process_identity() if case=='scheduler_live' else dict(pid=99999999,start_ticks='0',boot_id='dead')))
        elif case=='ui_live':(state/'ui-process.json').write_text(json.dumps(process_identity()))
        elif case=='ui_unknown':(state/'ui-process.json').write_text('null')
        elif case=='unknown_artifact':(Path(e['artifact_dir'])/'scheduler.log').write_text('unknown')
        elif case in {'deadline','short_deadline'}:goal['authorized_until']=time.time()+(-1 if case=='deadline' else 2)
        store.put(db,'goals',host.GOAL_ID,goal);store.put(db,'cells',key,cell);store.put(db,'experiments',e['experiment_id'],exp)
    before=binding(store,e);goal_before=raw(store)
    with pytest.raises((DevConflict,DevBudgetExhausted)):host.rebind_prepared()
    assert binding(store,e)==before and raw(store)==goal_before
    with store.tx() as db:
        assert db.execute("SELECT COUNT(*) FROM v2_events WHERE kind='preparation_source_rebind_refused'").fetchone()[0]==1
        assert not db.execute("SELECT 1 FROM v2_events WHERE kind='preparation_source_rebound'").fetchone()


@pytest.mark.parametrize('kind',['ui','service','new_sdk','deleted_source','model','source_set'])
def test_revision_scope_cannot_absorb_unrelated_changes(prepared,kind):
    store,state,e=prepared;old=host.active_epoch();new=host.epoch()
    lock=host.checked_json(host.checked_json(e['manifest'])['identity_lock']);current=copy.deepcopy(lock)
    current['files'][str(host.ROOT/'benchmarks/attention_harness/dev_integration.py')]=sha(host.ROOT/'benchmarks/attention_harness/dev_integration.py')
    if kind=='ui':new['main_files'][str(host.ROOT/'benchmarks/attention_harness/ui/live.js')]='f'*64
    elif kind=='service':new['service_head']='unrelated'
    elif kind=='new_sdk':current['files']['/tmp/unrelated-sdk.py']='f'*64
    elif kind=='deleted_source':current['files'].pop(next(iter(current['files'])))
    elif kind=='model':current['model_sources']={}
    elif kind=='source_set':current['source_sets'][next(iter(current['source_sets']))].append('/tmp/unrelated.py')
    with pytest.raises(DevConflict):host.validate_source_revision(old,new,lock,current)


def test_reference_tampering_refused(prepared):
    store,state,e=prepared;p=Path(e['manifest']['uri']);p.chmod(0o644);p.write_text('{}')
    with pytest.raises(DevConflict,match='reference drift'):host.rebind_prepared()
    assert binding(store,e)['manifest']==e['manifest']


def test_crash_before_commit_is_inert_and_retry_uses_same_revision(prepared,monkeypatch):
    store,state,e=prepared;real=host.immutable
    def fail(path,value):
        ref=real(path,value)
        if Path(path).name=='manifest.json':raise OSError('controlled crash after file write')
        return ref
    with monkeypatch.context() as m:
        m.setattr(host,'immutable',fail)
        with pytest.raises(OSError,match='controlled crash'):host.rebind_prepared()
    assert binding(store,e)['manifest']==e['manifest']
    assert host.rebind_prepared()['changed'];host.verify_epoch()
    assert len(list((Path(e['artifact_dir'])/'source-revisions').iterdir()))==1


def test_source_race_rolls_back_binding(prepared,monkeypatch):
    store,state,e=prepared;real=host.epoch;calls=[]
    def racing():
        value=real();calls.append(1)
        if len(calls)>1:value['main_head']='raced'
        return value
    monkeypatch.setattr(host,'epoch',racing)
    with pytest.raises(DevConflict,match='source changed during rebind'):host.rebind_prepared()
    assert binding(store,e)['manifest']==e['manifest']


def test_real_minus_m_start_reaches_real_serve_subprocess(prepared,tmp_path):
    store,state,e=prepared;host.rebind_prepared()
    # sitecustomize changes only temporary goal locations and the parent-only
    # availability probe. The actual -m entry and Popen command remain intact;
    # the serve child uses the real socket implementation and actual UI server.
    hooks=tmp_path/'hooks';hooks.mkdir()
    (hooks/'sitecustomize.py').write_text(f'''
import sys, os, json
from pathlib import Path
ROOT={str(host.ROOT)!r}
STATE={str(state)!r}
LEDGER={str(store.path)!r}
def trace(frame,event,arg):
    if event=='call' and frame.f_code.co_name=='main' and frame.f_code.co_filename==str(Path(ROOT)/'benchmarks/attention_harness/dev_integration.py'):
        g=frame.f_globals;g['STATE']=Path(STATE);g['LEDGER']=Path(LEDGER)
        original=g['execution_environment']
        def environment(*a,**k):
            env=original(*a,**k);env['PYTHONPATH']={str(hooks)!r}+os.pathsep+ROOT;return env
        g['execution_environment']=environment
        if sys.argv[1]=='start':
            import socket
            real=socket.socket
            class ParentProbe:
                def __enter__(self):return self
                def bind(self,*a):pass
                def __exit__(self,*a):socket.socket=real
            socket.socket=ParentProbe
        sys.settrace(None)
    return trace
sys.settrace(trace)
''')
    env=dict(os.environ,PYTHONPATH=str(hooks)+os.pathsep+str(host.ROOT),PYTHONDONTWRITEBYTECODE='1',
        ATTENTION_MEMORY_API_KEY='controlled-test-only-key',ATTENTION_HUMAN_APPROVAL_TOKEN='controlled-test-only-human-capability')
    # No caller/provider/importer secrets are inherited by this test subprocess.
    for name in list(env):
        if name in {'PARCC_API_KEY','LITELLM_KEY'} or any(w in name.upper() for w in ('IMPORTER','OPERATOR')):env.pop(name)
    try:
        result=subprocess.run([sys.executable,'-B','-m',host.CLI_MODULE,'start','--port','8876'],cwd=host.ROOT,env=env,capture_output=True,text=True,timeout=30)
        log=(state/'ui.log').read_text().splitlines()[1:];log='\n'.join(log)
        assert "__main__.__spec__ is None" not in log
        assert "No module named" not in log
        if result.returncode:
            assert 'UI startup failed' in result.stderr
            assert 'serve' in log and ('socket.__init__' in log or 'self.socket = socket.socket' in log)
            assert 'PermissionError: [Errno 1] Operation not permitted' in log
            print(log)
            print(result.stderr)
            print('REAL_SUBPROCESS: entered serve/create_server; socket EPERM; HTTP NOT VERIFIED')
        else:
            assert json.loads(result.stdout)['model_dispatch'] is False
            print('REAL_SUBPROCESS: isolated temporary UI HTTP startup passed; zero model/native')
    finally:
        receipt=json.loads((state/'ui-process.json').read_bytes())
        if receipt and owner_alive(receipt):
            import signal
            os.kill(receipt['pid'],signal.SIGTERM)
            for _ in range(50):
                if not owner_alive(receipt):break
                time.sleep(.05)
            assert not owner_alive(receipt)
    assert raw(store)['real_started_at'] is None
    with store.tx() as db:assert db.execute('SELECT COUNT(*) FROM v2_jobs').fetchone()[0]==0


@pytest.mark.parametrize('name',['seed','wait_seconds','goal_id','selection','memory'])
def test_original_preparation_condition_drift_refused(prepared,name):
    store,state,e=prepared
    path=Path(e['artifact_dir'])/'ui-selection.json' if name=='selection' else state/('memory.json' if name=='memory' else 'prepared.json')
    data=json.loads(path.read_bytes())
    if name=='selection':data['seeds']=[102]
    elif name=='memory':data['namespace']='unrelated'
    else:data[name]='changed'
    path.chmod(0o644);path.write_text(json.dumps(data))
    with pytest.raises(DevConflict,match='conditions changed|binding changed'):host.rebind_prepared()
    assert binding(store,e)['manifest']==e['manifest']


@pytest.mark.parametrize('name',[
    'benchmarks/attention_harness/dev_main_bridge.py',
    'benchmarks/attention_harness/ui/live.js',
    'openarm365_dev_v2/tests/test_integration_epoch.py',
    'openarm365_dev_v2/tests/test_prepared_lifecycle.py',
])
def test_only_reviewed_before_after_ui_and_test_pairs_are_accepted(prepared,name):
    _,_,e=prepared
    ready=json.loads((host.OUT/'main-integration/source-readiness.json').read_bytes())
    pairs=ready['prepared_lifecycle_revision']['files'];pair=pairs[name];path=str(host.ROOT/name)
    new=host.epoch();old=copy.deepcopy(new)
    from openarm365_dev_v2.dev_config import make_lock
    current=host.checked_json(make_lock(Path(e['artifact_dir'])/'controlled-final-lock.json'))
    prior=copy.deepcopy(current)
    for mapping in (old['main_files'],prior['files']):
        if path not in mapping:continue
        if pair['before_sha256'] is None:mapping.pop(path)
        else:mapping[path]=pair['before_sha256']
    if pair['before_sha256'] is None:
        for names in prior['source_sets'].values():
            if path in names:names.remove(path)
    host.validate_source_revision(old,new,prior,current)
    altered=copy.deepcopy(old);lock=copy.deepcopy(prior)
    (altered['main_files'] if path in new['main_files'] else lock['files'])[path]='f'*64
    with pytest.raises(DevConflict,match='before/after'):host.validate_source_revision(altered,new,lock,current)


def test_source_readiness_cannot_widen_the_explicit_file_set(prepared,monkeypatch,tmp_path):
    _,_,e=prepared
    ready=json.loads((host.OUT/'main-integration/source-readiness.json').read_bytes())
    ready['prepared_lifecycle_revision']['files']['curobo_v2/sdk.py']={'before_sha256':None,'after_sha256':'f'*64}
    target=tmp_path/'review/main-integration';target.mkdir(parents=True)
    (target/'source-readiness.json').write_text(json.dumps(ready))
    monkeypatch.setattr(host,'OUT',target.parent)
    lock=host.checked_json(host.checked_json(e['manifest'])['identity_lock'])
    with pytest.raises(DevConflict,match='exact reviewed'):host.validate_source_revision(host.active_epoch(),host.epoch(),lock,lock)
