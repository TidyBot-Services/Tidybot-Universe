"""Byte comparisons against the immutable phase-2 merge bases, at identical paths."""
import json,shutil,subprocess,types
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from benchmarks.attention_harness import dev_experiment,dev_agent_config,memory_validation_runtime
from benchmarks.attention_harness.dev_ledger import DevV2Store
from benchmarks.attention_harness.dev_v2_loop import immutable
from benchmarks.attention_harness.tests.c21_fixtures import base,r4_deployment,offline_dev_identity
from benchmarks.attention_harness.tests.r3_fixtures import r3_deployment
from benchmarks.attention_harness.tests.test_dev_agent_configuration import SELECTION
from benchmarks.attention_harness.tests.autovalidate_fakes import FakeRunner


def baseline(module,revision):
    repo=Path(module.__file__).resolve()
    while not (repo/'.git').exists():repo=repo.parent
    name=Path(module.__file__).relative_to(repo)
    source=subprocess.check_output(['git','show',revision+':'+str(name)],cwd=repo,text=True)
    value=types.ModuleType(module.__package__+'._phase3_golden_'+module.__name__.split('.')[-1]);value.__package__=module.__package__
    exec(compile(source,str(name),'exec'),value.__dict__)
    return value


def snapshot(root):return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_normal_agent_and_onecall_manifest_bytes(tmp_path,offline_dev_identity):
    old=baseline(dev_experiment,'f4025b3');options=baseline(dev_agent_config,'f4025b3')
    assert options.normalize(SELECTION)==dev_agent_config.normalize(SELECTION)
    assert options.configuration(SELECTION)==dev_agent_config.configuration(SELECTION)
    for agent in (False,True):
        selection=dict(SELECTION,request_key='golden-phase3-'+str(agent))
        if not agent:
            selection.pop('dev_mode');selection.update(target_depth=1,target_valid_executions=1,max_versions=1)
        db=tmp_path/('ledger-'+str(agent));store=DevV2Store(db)
        before=old.create(store,tmp_path/'runs',selection)
        raw=Path(before['manifest']['uri']).read_bytes();db.unlink()
        # The pre-v2 agent oracle has a v1 epoch. One-call keeps the exact call.
        after=dev_experiment.create(DevV2Store(db),tmp_path/'runs',selection,
            **({'agent_policy_version':'agent-policy-v1'} if agent else {}))
        assert Path(after['manifest']['uri']).read_bytes()==raw
        assert old.verify_manifest(after)==dev_experiment.verify_manifest(after)


def test_onecall_runtime_config_envelope_and_receipt_bytes(tmp_path,monkeypatch):
    from benchmarks.attention_harness import dev_v2_loop
    old=baseline(memory_validation_runtime,'f4025b3')
    root=tmp_path/'executor';root.mkdir();directory=root/'v1';directory.mkdir()
    config=dict(seed=101);config_ref=immutable(root/'config.json',config)
    source=immutable(root/'policy.py','def policy(sdk):\n    pass\n')
    record=dict(version=1,source=source,config_sha256=config_ref['sha256'])
    origin=immutable(root/'origin-feedback.json',{})
    current=dict(arm_id='cell',arm='control',memory_id='candidate',context={'namespace':'offline'},config=config_ref,
        origin=source,origin_feedback=origin,cells={'cell':dict(ledger='unused')})
    grant=dict(grant_id='golden-grant',binding=dict(memory_version=1))
    client=SimpleNamespace(authorize_dev_v2_validation=lambda **kw:dict(grant,request=kw),
        get_memory=lambda mid:SimpleNamespace(status='candidate',version=1,guidance='Only this note'),get_plan=lambda mid:None)
    runs=[]
    for module in (old,memory_validation_runtime):
        cell=dict(experiment_id='experiment',artifact_dir=str(root),versions=[record],jobs=[],state='running')
        experiment=dict(experiment_id='experiment',state='running',owner=None)
        updates=[]
        def update(key,**kw):cell.update(kw);updates.append(kw)
        store=SimpleNamespace(cell=lambda key:cell,tx=lambda:nullcontext(None),get=lambda db,table,key:dict(experiment if table=='experiments' else cell),
            update_cell=update,start_cell_clock=lambda key:None,put=lambda *a:None,event=lambda *a:None)
        class Rows:
            def execute(self,*a):return []
        store.tx=lambda:nullcontext(Rows())
        monkeypatch.setattr(module,'DevV2Store',lambda path:store)
        def execute(*args,**kwargs):
            immutable(directory/'runtime-receipt.json',dict(source=args[3]['source'],config=args[4],envelope=kwargs['memory_validation']))
        monkeypatch.setattr(dev_v2_loop,'execute',execute)
        runner=module.LedgerRunner(client,None,experiments_root=root,validator_root=tmp_path/'validator',state_root=tmp_path/'worker')
        try:runner._run(current,{'pid':1})
        finally:runner.pool.shutdown()
        runs.append((snapshot(directory),updates))
        shutil.rmtree(directory);directory.mkdir()
    assert runs[0]==runs[1]


def test_onecall_validation_grant_payload_bytes(tmp_path):
    from attention_memory_service import dev_v2
    fake=FakeRunner(tmp_path/'fake');batch=fake.begin(fake.service.validation_queue()[0],'golden-operation')
    current=dict(batch,arm_id='c0',arm='control')
    record=fake.f.records['c0'];plan=batch['plan']
    from benchmarks.attention_harness.tests.autovalidate_fakes import grant_args
    payload=grant_args(record,plan)
    old=baseline(dev_v2,'5bec4a8').DevV2Evidence(fake.service.store)
    before=old.authorize_validation(**payload)
    after=fake.service.dev_v2.authorize_validation(**payload)
    assert json.dumps(before,sort_keys=True).encode()==json.dumps(after,sort_keys=True).encode()


def test_onecall_finish_receipt_bytes(tmp_path,monkeypatch):
    import sqlite3
    fake=FakeRunner(tmp_path/'fake',failed_check=('t0','safety'))
    current=fake.begin(fake.service.validation_queue()[0],'operation');current.update(arm_id='t0',arm='treatment')
    failed=fake.finish(current);record=fake.f.records['t0'];validation=failed['validation']
    with sqlite3.connect(record['descriptor']['ledger']['uri']) as db:jobs=[json.loads(p) for p, in db.execute('SELECT payload FROM v2_jobs')]
    cell=dict(record['cell'],versions=[record['record']],jobs=jobs)
    experiment=dict(artifact_dir=str(fake.f.root),manifest=record['descriptor']['manifest'])
    store=SimpleNamespace(cell=lambda key:cell,tx=lambda:nullcontext(None),get=lambda *a:experiment)
    current.update(context=record['context'],config=record['descriptor']['config'],cells={'t0':{'ledger':'synthetic'}})
    output=fake.f.root/'validation-import/t0';output.mkdir(parents=True)
    immutable(output/'catalog.json',fake.f.seal()['t0']);immutable(output/'descriptor.json',record['descriptor'])
    state_root=tmp_path/'state';(state_root/'verdicts').mkdir(parents=True);immutable(state_root/'verdicts/t0.json',validation)
    results=[]
    for module in (baseline(memory_validation_runtime,'f4025b3'),memory_validation_runtime):
        monkeypatch.setattr(module,'DevV2Store',lambda path:store)
        calls=[]
        client=SimpleNamespace(register_dev_v2_catalog=lambda **kw:calls.append(('catalog',kw)),
            import_dev_v2_execution=lambda **kw:calls.append(('execution',kw)))
        runner=module.LedgerRunner(client,None,experiments_root=fake.f.root,validator_root=fake.f.validator,state_root=state_root)
        try:result=runner.finish(current)
        finally:runner.pool.shutdown()
        results.append(json.dumps(dict(receipt=result,calls=calls),sort_keys=True).encode())
    assert results[0]==results[1]


def test_onecall_replacement_authorization_bytes(tmp_path,r3_deployment,monkeypatch):
    from benchmarks.attention_harness import memory_validation_replacements as replacements
    from benchmarks.attention_harness.tests.invalid_arm_fakes import scenario
    from benchmarks.attention_harness.memory_validation_worker import tick
    with scenario(tmp_path,((0,'treatment',1),)) as case:
        for _ in range(10):
            tick(case.state,case.deps)
            current=case.state['current']
            if current.get('discarded_pairs'):break
        assert current['pair_version']==2 and len(current['discarded_pairs'])==1
        store=DevV2Store(current['cells'][current['arm_id']]['ledger'])
        before=snapshot(case.fake.f.root)
        import time
        monkeypatch.setattr(time,'time',lambda:2000000000.)
        runs=[]
        for module in (baseline(replacements,'f4025b3'),replacements):
            module.authorize_replacement(store,current)
            runs.append(snapshot(case.fake.f.root))
            for path in case.fake.f.root.rglob('*'):
                if not path.is_file():continue
                name=str(path.relative_to(case.fake.f.root))
                if name not in before:path.unlink()
                elif path.read_bytes()!=before[name]:path.write_bytes(before[name])
        assert runs[0]==runs[1]


def test_onecall_begin_and_poll_receipt_bytes(tmp_path,r3_deployment):
    from benchmarks.attention_harness.tests.autovalidate_fakes import ProvisionedFakeRunner,synthetic_creation
    fake=ProvisionedFakeRunner(tmp_path/'fake',tmp_path/'worker')
    class Client:
        def __getattr__(self,name):return getattr(fake.service,name)
        def register_dev_v2_catalog(self,**kw):fake.register(kw['catalog_id'],kw['catalog'])
    outputs=[]
    note=fake.service.validation_queue()[0]
    with synthetic_creation(fake.f):
        for module in (baseline(memory_validation_runtime,'f4025b3'),memory_validation_runtime):
            runner=module.LedgerRunner(Client(),fake.provision,experiments_root=fake.f.root,validator_root=fake.f.validator,state_root=fake.provision.state_root)
            try:
                batch=runner.begin(note,'golden-begin-operation')
                receipt=fake.provision.state_root/'golden-begin-operation/batch.json'
                current=dict(batch,arm_id=batch['pairs'][0]['control_cell'],arm='control')
                outputs.append((receipt.read_bytes(),runner.poll(current)))
                receipt.unlink()
            finally:runner.pool.shutdown()
    assert outputs[0]==outputs[1]
