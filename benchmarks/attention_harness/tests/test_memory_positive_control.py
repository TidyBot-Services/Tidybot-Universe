import json
import sqlite3
from pathlib import Path

import pytest

from benchmarks.attention_harness.tests.positive_control_fakes import run,PositiveFake
from benchmarks.attention_harness.memory_validation_worker import tick,atomic,main
from benchmarks.attention_harness.memory_positive_control import queue


@pytest.mark.parametrize('treatments,expected,cross', [((False,)*5,'would_pass',True),((True,)*5,'would_fail',True)])
def test_positive_control_fake_e2e(tmp_path,treatments,expected,cross):
    fake,state,record=run(tmp_path,treatments=treatments,cross=cross)
    assert record['verdict']['decision']==expected
    assert state.get('results',[])==[]
    assert fake.replays==2 and fake.meter_events==['start','replay','stop']*2
    assert fake.authority.get_memory(fake.mid).status.value=='provisional'
    assert not any(r['memory_id']==fake.spec['control_id'] for r in fake.authority.list_memories())
    with sqlite3.connect(fake.authority.store.path) as db:
        assert not db.execute("SELECT 1 FROM dev_v2_imports WHERE kind='source'").fetchone()
    if expected=='would_pass':
        assert state['runs_today']==29 # origin + ten same-scene + two probes + ten transfer + six neutral
        assert record['cross_seed']['task_trust']['verdict']=='would be trusted for this task'
        assert {s['verdict'] for s in record['cross_seed']['seeds']}=={'transfer_passed','neutral_no_harm'}
    else:
        assert state['runs_today']==11
        assert not fake.cross_batches


def test_queue_does_not_enable_or_run(tmp_path):
    fake=PositiveFake(tmp_path/'fake',tmp_path/'worker')
    path=tmp_path/'worker/positive-controls/run8.json'
    assert main(['--state-dir',str(tmp_path/'worker'),'--positive-control-check',str(path)])==0
    state={};tick(state,fake.deps(tmp_path/'worker'))
    assert state['status']=='Off' and fake.starts==[] and fake.replays==0
    changed=json.loads(path.read_bytes());changed['guidance']='changed';atomic(path,changed)
    with pytest.raises(Exception):queue(tmp_path/'worker',path)


def test_positive_control_real_runtime_replacement(tmp_path):
    from benchmarks.attention_harness.tests.invalid_arm_fakes import scenario
    from benchmarks.attention_harness.memory_monitor import automatic_checks
    with scenario(tmp_path,fake_factory=PositiveFake) as case:
        for _ in range(60):
            tick(case.state,case.deps)
            record=case.state.get('positive_controls',{}).get(case.fake.spec['control_id'],{})
            if record.get('same_done'):break
            if 'paused:' in case.state.get('status',''):
                for f in case.runner.futures.values():f.result()
                pytest.fail(case.state['status'])
        assert record['verdict']['decision']=='would_pass',case.state['status']
        discarded=record['worker']['results'][0]['discarded_pairs']
        assert len(discarded)==1 and discarded[0]['arm']=='treatment'
        assert len(case.generated)==12 and len(case.executed)==11
        assert case.state['runs_today']==13 # original replay + invalid pair + replacement
        assert len(record['pairs'])==5
        assert any(p['control']['run_id'] and p['control_import_id'].endswith('-v2') for p in record['pairs'])
        view=automatic_checks(case.deps.root)
        assert view['results']==[] and view['positive_controls'][0]['decision']=='would_pass'
        assert len(view['positive_controls'][0]['pairs'])==5


def test_positive_controls_excluded_from_frozen_notebook(tmp_path):
    from benchmarks.attention_harness.formal_memory_contract import FrozenMemoryGateway,validate_memory_contract,ACCOUNTING
    import hashlib
    fake,state,record=run(tmp_path,cross=False)
    ctx=fake.spec['context'];mid=fake.spec['control_id']
    contract=dict(schema_version='attentionbench.formal-memory-contract.v1',suite=ctx['suite'],task_id=ctx['task_id'],
        condition='full_trace_aware_attention_planner',visibility='trusted_exact_versions',reset_each_run=True,
        automatic_promotion=False,accounting=ACCOUNTING,evidence_root=str(tmp_path),context=ctx,
        initial_state=dict(kind='sqlite_snapshot',path=str(fake.authority.store.path),
                           sha256=hashlib.sha256(fake.authority.store.path.read_bytes()).hexdigest(),versions=[dict(memory_id=mid,version=1)]))
    with pytest.raises(ValueError,match='not the frozen trusted version'):
        validate_memory_contract(contract,suite=ctx['suite'],task_id=ctx['task_id'],policy_id=contract['condition'],config=ctx)
    assert FrozenMemoryGateway(fake.authority,contract).retrieve(ctx,now=0)==[]


def test_positive_evaluation_timeout_isolated(monkeypatch):
    import io
    from attention_memory_service import memory_service_client as transport
    from benchmarks.attention_harness.memory_service_client import MemoryServiceClient
    client=MemoryServiceClient('http://localhost:18000',api_key='fake',importer_key='fake-importer',timeout=7,evaluate_timeout_seconds=2400)
    client._importer_file=True;calls=[]
    def urlopen(request,*,timeout):
        calls.append(timeout);return io.BytesIO(b'null')
    monkeypatch.setattr(transport,'urlopen',urlopen)
    client.positive_control_write('evaluate',memory_id='pc')
    client.positive_control_read('pc','decision')
    client.positive_control_read('pc','pairs')
    assert calls==[2400,2400,7] and client.timeout==7


@pytest.mark.parametrize('fault,expected',[('safety','would_fail'),('scene','inconclusive')])
def test_positive_failed_independent_check(tmp_path,fault,expected):
    fake,state,record=run(tmp_path,failed_check=('first-treatment',fault))
    assert record['verdict']['decision']==expected
    assert record['verdict']['failed_check']['validation_grant_id']
    assert record['pairs']==[] and state['runs_today']==3
    assert fake.authority.get_memory(fake.mid).status.value=='provisional'


@pytest.mark.parametrize('gate',['idle','weekly_quota'])
def test_positive_controls_share_dispatch_gates(tmp_path,gate):
    import time
    fake=PositiveFake(tmp_path/'fake',tmp_path/'worker');deps=fake.deps(tmp_path/'worker')
    atomic(tmp_path/'worker/autovalidate.json',dict(enabled=True))
    if gate=='idle':deps.activity=lambda current:'Another run is active'
    else:deps.quota=lambda:dict(used_percent=75,observed_at=time.time())
    state={};tick(state,deps)
    assert state['runs_today']==0 and fake.replays==0 and not fake.starts
    assert 'Another run' in state['status'] if gate=='idle' else '75%' in state['status']


def test_scripted_reference_origin_store_sqlite_without_advisor(tmp_path,monkeypatch):
    from unittest.mock import patch
    from benchmarks.attention_harness.tests.autovalidate_fakes import synthetic_creation
    from benchmarks.attention_harness.memory_positive_control import local_origin
    from benchmarks.attention_harness.memory_validation_generation import parse_generation
    # The synthetic fixture uses a stub SDK source; native validation is tested separately.
    monkeypatch.setattr('openarm365_dev.policy_validation.validate_generated_policy',lambda source,**kwargs:None)
    fake=PositiveFake(tmp_path/'fake',tmp_path/'worker');r=fake.failed;d=r['descriptor']
    old=Path(d['ledger']['uri']);ledger=old.with_name('store.sqlite');old.rename(ledger)
    manifest=json.loads(Path(d['manifest']['uri']).read_bytes());manifest['ledger_path']=str(ledger)
    mref=fake.f.write(Path(d['manifest']['uri']),manifest)
    code=Path(r['record']['source']['uri']).read_text();content=json.dumps(dict(source=code,reason='scripted reference'))
    assert parse_generation(content)[2] is None
    usage=dict(total_tokens=100,prompt_tokens=40,completion_tokens=60)
    req=fake.f.write(old.parent/'script-request.json',dict(messages=[dict(role='user',content='Reference policy')]))
    resp=fake.f.write(old.parent/'script-response.json',dict(content=content,model='scripted/replay',usage=usage))
    wire=fake.f.write(old.parent/'script-wire.json',dict(http_status=200,request_id=None,body=dict(model='scripted/replay',
        choices=[dict(message=dict(content=content))],usage=usage)))
    v=dict(r['record'],manifest=mref,model_input=req,model_output=resp)
    fake.f.write(old.parent/'v1/version.json',v)
    with sqlite3.connect(ledger) as db:
        e=json.loads(db.execute('SELECT payload FROM v2_experiments').fetchone()[0]);e['manifest']=mref
        db.execute('UPDATE v2_experiments SET payload=?',(json.dumps(e),))
        db.execute('UPDATE v2_versions SET payload=? WHERE version=1',(json.dumps(v),))
        db.execute('DELETE FROM v2_versions WHERE version>1')
        db.execute('DELETE FROM v2_jobs WHERE version>1')
        db.execute('DELETE FROM v2_planner_requests')
        job=dict(job_id='script-model',cell='failed',version=1,kind='model',claimed_at=0,settled_at=.5,
            actual=dict(dev_calls=1,provider_tokens=100,input_tokens=40,output_tokens=60),
            receipt=dict(request=req,response=resp,provider_response=wire,usage=usage))
        db.execute('INSERT INTO v2_jobs VALUES (?,?,?,?,?,?)',('script-model','failed',1,'model','settled',json.dumps(job)))
    atomic(tmp_path/'worker/autovalidate.json',dict(enabled=True))
    state={};deps=fake.deps(tmp_path/'worker')
    with synthetic_creation(fake.f),patch('benchmarks.attention_harness.dev_planner_memory.context',lambda *a,**kw:fake.failed['context']):
        found=local_origin(fake.provision,fake.spec)
        assert found['store'].path.name=='store.sqlite'
        assert not any(j['kind']=='advisor' for j in found['cell']['jobs'])
        for _ in range(3):tick(state,deps)
    assert fake.replays==2 and state['runs_today']==2,state['status']
    assert fake.authority.positive_controls.get(fake.spec['control_id'])['record_kind']=='positive_control'
