import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import pytest
from benchmarks.attention_harness import memory_validation_runtime as runtime
from benchmarks.attention_harness.memory_validation_worker import main
from benchmarks.attention_harness.dev_v2_loop import immutable
from benchmarks.attention_harness.tests.autovalidate_fakes import FakeRunner,SIGNATURE


def test_cli_off_does_not_load_adapter_or_credentials(tmp_path,capsys):
    main(['--state-dir',str(tmp_path),'--once','--adapter','not_installed:do_not_load'])
    assert capsys.readouterr().out.strip()=='Off'


@pytest.mark.parametrize('external',[False,True,'nested'])
def test_begin_freezes_generated_plan_and_registers_repo_contract(tmp_path,monkeypatch,external):
    if external:
        from benchmarks.attention_harness.tests.test_memory_autovalidate_contracts import external_sources
        f,r,_,_,sources=external_sources(tmp_path/'external',nested=external=='nested');f.add_advice(r,SIGNATURE)
    else:
        fake=FakeRunner(tmp_path/'fake');f=fake.f;r=f.records['failed']
    source=r['record']['source'];config=r['descriptor']['config'];name=r['name']
    from openarm365_dev_v2 import scene_contract
    monkeypatch.setattr(scene_contract,'registered_contract_ref',lambda:f.contract)
    pairs=[dict(pair_id=str(i),control_cell=f'c{i}',treatment_cell=f't{i}') for i in range(5)]
    cells={p[k]:{'ledger':'synthetic'} for p in pairs for k in ('control_cell','treatment_cell')}
    batch=dict(dev_model={'provider':'codex','model':'gpt-6-astra'},origin=source,config=config,context=r['context'],source_catalogs=f.seal(),failed_execution={'import_id':name,'catalog_id':name,'descriptor':name+'/execution.json'},source_import={'import_id':'source','catalog_id':name,'descriptor':name+'/source.json'},memory_id='candidate',pairs=pairs,cells=cells,failure_signature=SIGNATURE,policy_id='full_trace_aware_attention_planner',budget={'assistance_credits':1})
    experiment=dict(artifact_dir=str(f.root),manifest=r['descriptor']['manifest'],selection={'model_source':'codex','budget_tier':'dev'})
    store=SimpleNamespace(cell=lambda key:dict(experiment_id='exp',task_id='pick',seed=101,run_notes=False,memory_family=False),tx=lambda:nullcontext(None),get=lambda *a:experiment,goal_for_cell=lambda key:dict(remaining={'executions':10}))
    monkeypatch.setattr(runtime,'DevV2Store',lambda path:store)
    from benchmarks.attention_harness import dev_experiment
    monkeypatch.setattr(dev_experiment,'verify_manifest',lambda e:dict(configs={'pick:101':r['config']}))
    calls=[]
    client=SimpleNamespace(provenance=lambda mid:dict(source_policy_sha256=source['sha256'],failure_signature=SIGNATURE),get_memory=lambda mid:SimpleNamespace(guidance='guidance'),register_dev_v2_catalog=lambda **kw:calls.append(('catalog',kw)),import_dev_v2_execution=lambda **kw:dict(cell=name,version=1,config=config,manifest=r['descriptor']['manifest']),import_dev_v2_source=lambda **kw:dict(source_policy_sha256=source['sha256'],guidance='guidance'),create_dev_v2_candidate=lambda **kw:calls.append(('candidate',kw)),record_plan=lambda mid,p:calls.append(('plan',p)))
    runner=runtime.LedgerRunner(client,lambda n,id:batch,experiments_root=f.root,validator_root=f.validator,state_root=tmp_path/'state')
    value=runner.begin(dict(memory_id='provisional',created_at=1,source_summary=dict(cell=name,version=1,experiment_id=name)),'operation')
    assert value['plan']['arm_generation']=='dev_from_origin' and value['plan']['origin_source_sha256']==source['sha256']
    if external=='nested':assert all(v['catalog']['role']!='contract' for k,v in calls if k=='catalog')
    else:assert calls[0][1]['catalog']['role']=='contract' and len(calls[0][1]['catalog']['files'])==1
    if external:assert {v['catalog_id']:v['catalog'] for k,v in calls if k=='catalog' and v['catalog']['role']=='source'}==sources
    candidate=next(value for kind,value in calls if kind=='candidate');assert candidate['created_at']==1 and candidate['from_provisional']=='provisional'
    count=len(calls);assert runner.begin({},'operation')==value and len(calls)==count
    runner.pool.shutdown()


def test_runtime_refuses_overlapping_roots(tmp_path):
    with pytest.raises(ValueError):runtime.LedgerRunner(None,None,experiments_root=tmp_path,validator_root=tmp_path/'inside',state_root=tmp_path)


def test_probe_requests_gpu_measurement_without_validation_envelope(tmp_path,monkeypatch):
    from benchmarks.attention_harness import dev_v2_loop
    from benchmarks.attention_harness.tests.family_import_evidence import layout
    evidence=layout(tmp_path,monkeypatch)
    current=dict(evidence.probe,arm_id=evidence.probe['probe_cell'])
    calls=[]
    monkeypatch.setattr(dev_v2_loop,'execute',lambda *a,**kw:calls.append(kw))
    client=SimpleNamespace(get_memory=lambda mid:SimpleNamespace(
        record_kind='family_candidate',status='candidate',version=1))
    runner=runtime.LedgerRunner(client,None,experiments_root=evidence.experiments,
        validator_root=tmp_path/'validator',state_root=evidence.state)
    try:runner._run(current,None)
    finally:runner.pool.shutdown()
    assert calls==[dict(gpu_measurement=True)]
    cell=evidence.exports[0].store.cell(current['arm_id'])
    directory=Path(cell['artifact_dir'])/'v1'
    clean=json.loads((directory/'memory-validation-input.json').read_bytes())
    assert clean['memory_ids']==[] and clean['exposure']=='none'
    assert not (directory/'memory-validation-envelope.json').exists()


def test_start_acquires_ledger_before_background_work(tmp_path,monkeypatch):
    from benchmarks.attention_harness import dev_experiment
    c=dict(experiment_id='exp',jobs=[],owner=None);calls=[]
    store=SimpleNamespace(cell=lambda key:c,update_cell=lambda key,**changes:c.update(changes))
    monkeypatch.setattr(runtime,'DevV2Store',lambda path:store)
    monkeypatch.setattr(dev_experiment,'acquire',lambda *a:calls.append('acquire') or {'state':'running'})
    monkeypatch.setattr(dev_experiment,'request_pause',lambda *a:calls.append('pause'))
    runner=runtime.LedgerRunner(None,None,experiments_root=tmp_path/'executor',validator_root=tmp_path/'validator',state_root=tmp_path/'state')
    runner.pool.shutdown()
    def submit(*a):
        assert c['owner'] and c['state']=='running';calls.append('submitted');return SimpleNamespace(done=lambda:False)
    runner.pool=SimpleNamespace(submit=submit)
    current=dict(arm_id='arm',cells={'arm':{'ledger':'fake'}})
    runner.start(current,'arm');runner.start(current,'arm');runner.pause(current)
    assert calls==['acquire','submitted','pause']


def test_bad_adapter_is_visible_without_starting_work(tmp_path,capsys):
    from benchmarks.attention_harness.memory_validation_worker import set_enabled
    set_enabled(tmp_path,True);main(['--state-dir',str(tmp_path),'--once','--adapter','not_installed:factory'])
    assert 'adapter unavailable' in capsys.readouterr().out
    state=json.loads((tmp_path/'state.json').read_bytes());assert state['notices'] and 'paused' in state['status']


def test_finish_routes_failed_descriptor_and_grant_without_trial(tmp_path,monkeypatch):
    import sqlite3
    fake=FakeRunner(tmp_path/'fake',failed_check=('t0','safety'))
    current=fake.begin(fake.service.validation_queue()[0],'operation');current.update(arm_id='t0',arm='treatment')
    failed=fake.finish(current);r=fake.f.records['t0'];result=failed['validation']
    with sqlite3.connect(r['descriptor']['ledger']['uri']) as db:jobs=[json.loads(p) for p, in db.execute('SELECT payload FROM v2_jobs')]
    cell=dict(r['cell'],versions=[r['record']],jobs=jobs)
    experiment=dict(artifact_dir=str(fake.f.root),manifest=r['descriptor']['manifest'])
    store=SimpleNamespace(cell=lambda key:cell,tx=lambda:nullcontext(None),get=lambda *a:experiment)
    monkeypatch.setattr(runtime,'DevV2Store',lambda path:store)
    current.update(context=r['context'],config=r['descriptor']['config'],cells={'t0':{'ledger':'synthetic'}})
    output=fake.f.root/'validation-import/t0';output.mkdir(parents=True)
    immutable(output/'catalog.json',fake.f.seal()['t0']);immutable(output/'descriptor.json',r['descriptor'])
    state_root=tmp_path/'state';(state_root/'verdicts').mkdir(parents=True);immutable(state_root/'verdicts/t0.json',result)
    calls=[]
    client=SimpleNamespace(register_dev_v2_catalog=lambda **kw:fake.register(kw['catalog_id'],kw['catalog']),import_dev_v2_execution=lambda **kw:calls.append(kw),import_dev_v2_trial=lambda **kw:pytest.fail('failed arm imported as trial'))
    runner=runtime.LedgerRunner(client,None,experiments_root=fake.f.root,validator_root=fake.f.validator,state_root=state_root)
    actual=runner.finish(current);runner.pool.shutdown()
    assert actual['status']=='failed' and not actual['checks']['safety']
    assert actual['failed_check']==dict(failed['failed_check'],catalog_id='validator-t0')
    assert fake.service.record_dev_v2_failed_check(**actual['failed_check'])['decision']=='rejected'
    assert len(calls)==1


def test_default_factory_and_own_experiment_before_first_arm(tmp_path,monkeypatch):
    from benchmarks.attention_harness.memory_validation_provision import Provisioner
    from benchmarks.attention_harness.memory_service_client import MemoryServiceClient
    from benchmarks.attention_harness import memory_validation_worker as worker
    root=tmp_path/'state';root.mkdir();experiments=tmp_path/'experiments';experiments.mkdir()
    (root/'runtime.json').write_text(json.dumps(dict(enabled=False,base_url='http://unused',key_file='unused',experiments_root=str(experiments),validator_root=str(tmp_path/'validator'),activity_ledger_roots=[str(experiments)],activity_ledgers=[])))
    assert worker.settings(root)=={'enabled':False}
    client=object();monkeypatch.setattr(MemoryServiceClient,'offline',lambda *a,**kw:client)
    observed=[];monkeypatch.setattr(worker,'run_activity',lambda ledgers,**kw:observed.append(kw['own_experiment']))
    quota_calls=[];snapshot=dict(used_percent=12,observed_at=1700000000)
    monkeypatch.setattr(worker,'weekly_quota_snapshot',lambda home:quota_calls.append(('read',home)) or snapshot)
    monkeypatch.setattr(worker,'refresh_quota',lambda **kw:quota_calls.append(('refresh',kw)))
    deps=runtime.build_runtime(root,quota_probe_command=['fake-quota-probe'])
    assert isinstance(deps.runner.provision,Provisioner) and deps.runner.provision.client is client
    deps.activity(dict(experiment_id='validation-owned'))
    assert observed==['validation-owned']
    assert deps.quota()==snapshot
    deps.refresh_quota()
    assert quota_calls==[('read',None),('refresh',dict(home=None,command=['fake-quota-probe']))]
    deps.runner.pool.shutdown()
