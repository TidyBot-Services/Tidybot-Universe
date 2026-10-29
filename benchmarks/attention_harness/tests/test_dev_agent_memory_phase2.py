import json
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import Future

import pytest

from benchmarks.attention_harness.tests.agent_origin_fakes import build, replay
from benchmarks.attention_harness import memory_origin_evidence as origin, memory_independent_validator as validator
from benchmarks.attention_harness.dev_v2_memory_import import prepare
from benchmarks.attention_harness.memory_validation_provision import read
from benchmarks.attention_harness.core.dev_store import DevConflict
from attention_memory_service.core.store import StateConflictError


@pytest.fixture
def agent_origin(tmp_path,monkeypatch):
    f=build(tmp_path,monkeypatch)
    monkeypatch.setattr(validator,'GpuMeter',lambda:SimpleNamespace(start=lambda:None,
        stop=lambda:dict(gpu_seconds=1.,wall_seconds=1.,samples=1)))
    return f


def run_origin(f):
    def submit(fn,*args):
        future=Future()
        try:future.set_result(fn(*args))
        except BaseException as exc:future.set_exception(exc)
        return future
    runner=SimpleNamespace(provision=f.provision,validator_root=f.native.validator,futures={},pool=SimpleNamespace(submit=submit),
        replay=lambda *a,**kw:replay(f,*a,**kw))
    preparation=origin.OriginPreparation(runner)
    current=preparation.needed(f.note);preparation.start(current)
    assert preparation.poll(current)=='completed'
    receipt=preparation.finish(current)
    assert receipt['status']=='passed',receipt
    return receipt


def register(f,receipt):
    resolved=f.provision.resolve(f.note)
    registered=receipt['registration']
    source=f.service.dev_v2.source(registered['source_import_id'])
    candidate=f.service.get_memory(registered['memory_id'])
    f.service.provenance(candidate.memory_id)
    return resolved,source,candidate


def source_id(resolved):return resolved['source_import']['import_id']


def test_agent_origin_fake_e2e(agent_origin):
    f=agent_origin
    from benchmarks.attention_harness.memory_auto_import import pending
    assert list(pending(f.store.path))==[]
    found=origin.locate(f.provision,f.note)
    assert found['request_id'] is None and found['version']['version']==1
    assert origin.check_origin(f.provision,f.note['memory_id'],validator_root=f.native.validator).startswith('PASS: Agent origin')
    assert not f.provision.state_root.exists()
    receipt=run_origin(f)
    assert f.service.get_memory(receipt['registration']['memory_id']).status.value=='candidate'
    resolved,source,candidate=register(f,receipt)
    assert source['channel']=='dev_agent' and source['advisor_evidence']=='agent_help_evidence'
    assert candidate.status.value=='candidate'
    assert f.service.provenance(candidate.memory_id)['channel']=='dev_agent'
    assert f.service.get_memory(f.note['memory_id']).status.value=='provisional'
    with pytest.raises(DevConflict,match='agent_memory_validation_unsupported'):f.provision(f.note,'phase2-refusal')
    assert origin.OriginPreparation(SimpleNamespace(provision=f.provision)).needed(f.note) is None
    result=read(receipt['validation'])
    assert len(read(result['descriptor'])['agent_policy_replays'])==2
    assert read(result['descriptor'])['permission_scope']['action']=='agent_dev_tier_motion'


@pytest.mark.parametrize('file',['v1/policy.py','v2/agent-help-evidence.json','v1/public-feedback.json'])
def test_agent_origin_tamper_refuses(agent_origin,file):
    f=agent_origin;path=f.root/file;path.chmod(0o644);path.write_bytes(path.read_bytes()+b'\n')
    with pytest.raises((DevConflict,StateConflictError,ValueError)):origin.locate(f.provision,f.note)


def test_agent_replay_receipt_tamper_refuses(agent_origin):
    f=agent_origin;receipt=run_origin(f)
    result=read(receipt['validation']);v=read(result['descriptor'])
    path=Path(v['agent_policy_replays'][0]['uri']);path.chmod(0o644);path.write_bytes(path.read_bytes()+b'\n')
    with pytest.raises((DevConflict,StateConflictError,ValueError)):register(f,receipt)


@pytest.mark.parametrize('marker',['validation_experiment','positive_control','validation_origin'])
def test_agent_control_cells_refused(agent_origin,marker):
    f=agent_origin;f.store.update_cell('cell',**{marker:True})
    with pytest.raises(DevConflict):prepare(f.store,'cell',2,f.context['namespace'],f.root/'bad',f.native.root,
        source=origin.source_prerequisites(origin.locate(f.provision,f.note)),import_purpose='memory_origin')


def test_agent_source_requires_origin_purpose(agent_origin):
    f=agent_origin;found=origin.locate(f.provision,f.note)
    with pytest.raises(DevConflict,match='unsupported'):prepare(f.store,'cell',2,f.context['namespace'],f.root/'bad',f.native.root,
        source=origin.source_prerequisites(found))


def test_real_data_offline_origin_copy(tmp_path,monkeypatch):
    import hashlib
    import shutil
    import sqlite3
    from benchmarks.attention_harness.dev_ledger import DevV2Store, encoded
    from benchmarks.attention_harness.dev_agent_ledger import AgentLedger
    from benchmarks.attention_harness import dev_agent_memory, memory_auto_import
    from benchmarks.attention_harness.memory_validation_provision import Provisioner
    from attention_memory_service.memory_service import MemoryService
    original=Path('/home/truares/Documents/Codex/2026-10-14/goal-attentionbench-robocasa365-dev-v2-home/outputs/daily-experiments/devexp-2656176e6fa79c9bbee569bf')
    if not (original/'ledger.sqlite3').exists():pytest.skip('historical run unavailable')
    root=tmp_path/'experiments'/'historical';root.mkdir(parents=True)
    ledger=root/'ledger.sqlite3'
    with sqlite3.connect((original/'ledger.sqlite3').as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(ledger) as dst:src.backup(dst)
    copied={}
    with sqlite3.connect(ledger) as db:
        original_cell=json.loads(db.execute('SELECT payload FROM v2_cells').fetchone()[0])
    original_public=Path(original_cell['artifact_dir'])/'agent/private/public'
    def remap(value):
        if isinstance(value,dict):
            if value.get('schema') in ('attentionbench.dev-identity-lock.v2','attentionbench.curobo-v2.runtime.v1') or value.get('schema_version')=='attentionbench.openarm365-dual-dev-config.v2':return value
            if 'sha256' in value and ('uri' in value or 'path' in value):
                key='uri' if 'uri' in value else 'path';path=Path(value[key])
                if not path.is_absolute():
                    copy(original_public/path)
                    return value
                if path.is_relative_to(original):
                    ref=copy(path)
                    return dict(value,**{key:ref['uri'],'sha256':ref['sha256']})
            return {k:(v if k=='dev_lock' else remap(v)) for k,v in value.items()}
        if isinstance(value,list):return [remap(v) for v in value]
        if isinstance(value,str) and value.startswith(str(original)):return str(root)+value[len(str(original)):]
        return value
    def copy(path):
        path=Path(path)
        if path in copied:return copied[path]
        target=root/path.relative_to(original);target.parent.mkdir(parents=True,exist_ok=True)
        if path.suffix=='.json':
            raw=path.read_bytes();value=json.loads(raw);changed=remap(value)
            target.write_bytes(raw if changed==value else encoded(changed).encode())
        else:shutil.copyfile(path,target)
        ref=dict(uri=str(target),sha256=hashlib.sha256(target.read_bytes()).hexdigest());copied[path]=ref
        return ref
    store=DevV2Store(ledger)
    # Materialize all referenced evidence before rewriting only the copied ledger.
    with store.tx() as db:
        tables={table:db.execute('SELECT rowid,payload FROM '+table).fetchall() for table in
                ('v2_experiments','v2_cells','v2_versions','v2_jobs','v2_agent_jobs')}
    for table,rows in tables.items():
        transformed=[(encoded(remap(json.loads(raw))),rid) for rid,raw in rows]
        with store.tx() as db:db.executemany('UPDATE '+table+' SET payload=? WHERE rowid=?',transformed)
    with store.tx() as db:
        db.execute('DELETE FROM v2_events')
        key,raw=db.execute('SELECT id,payload FROM v2_cells').fetchone()
    cell=json.loads(raw);base=Path(cell['artifact_dir']);base.mkdir(parents=True,exist_ok=True)
    old_base=original/str(base.relative_to(root))
    config=json.loads((old_base/'config.json').read_bytes())
    # Config bytes are frozen; only their absolute reference is relocated.
    shutil.copyfile(old_base/'config.json',base/'config.json')
    from openarm365_dev_v2 import scene_contract
    monkeypatch.setattr(scene_contract,'registered_contract_ref',lambda:config['scene_contract'])
    from curobo_v2 import dev_local
    monkeypatch.setattr(dev_local,'DEV_LOCAL_ROOT',Path(config['execution_settings']['planner_deployment']['lock_path']).parent.parent)
    ws=SimpleNamespace(private=base/'agent/private',canonical=base/'agent/private/public')
    for n in (1,2,3):
        record=next(v for v in store.cell(key)['versions'] if v['version']==n)
        directory=base/f'v{n}';directory.mkdir(parents=True,exist_ok=True)
        (directory/'version.json').write_text(encoded(record))
        shutil.copyfile(record['source']['uri'],directory/'policy.py')
        job=next(j for j in store.cell(key)['jobs'] if j['version']==n and j['kind']=='execute')
        assert Path(job['receipt']['public_feedback']['uri'])==directory/'public-feedback.json'
        bundle=read(job['receipt']['bundle']);episode=Path(bundle['artifact_dir']);episode.mkdir(parents=True,exist_ok=True)
        for name in ('scene-condition.json','initial_public_observation.json','final_public_observation.json'):
            copy(Path(str(episode).replace(str(root),str(original),1))/name)
        answer=dict(status='not_requested')
        if n>1:
            answer=json.loads((old_base/f'agent/ws/versions/v{n-1:04d}/help.json').read_bytes());answer['provider']='advisor'
        (directory/'help.json').write_text(encoded(answer))
    for n in (1,2):
        for name in ('advisor.json','request-view.json'):copy(old_base/f'agent/private/help/v{n:04d}'/name)
    responses=ws.canonical/'rpc/responses';responses.mkdir(parents=True,exist_ok=True)
    for path in (old_base/'agent/ws/rpc/responses').glob('*.json'):
        if json.loads(path.read_bytes()).get('op')=='ask_help':shutil.copyfile(path,responses/path.name)
    agent_ledger=AgentLedger(ledger,key,help_limit=10)
    proposal=dev_agent_memory.stage_provisional(store,key,2,base/'v2',ws,agent_ledger)
    assert proposal
    value=read(proposal);payload=memory_auto_import.provisional_payload(value,'offline-real-origin')
    service=MemoryService(tmp_path/'service.sqlite')
    counts=memory_auto_import.run([ledger],client=service,namespace='offline-real-origin',out=lambda value:None)
    assert counts['created']==1 and not counts['refused'] and not counts['deferred']
    provision=Provisioner(dict(experiments_root=str(root),state_root=str(tmp_path/'worker')),service)
    found=origin.locate(provision,dict(memory_id=payload['memory_id']))
    ctx,sources=origin.export_context(provision,found)
    lock=config['dev_lock'];lock_path=Path(lock['path'])
    sources['historical-config-lock']=dict(role='source',root=str(lock_path.parent),context=ctx,files={lock_path.name:lock['sha256']})
    output=root/'imports'
    prepare(found['store'],key,1,ctx['namespace'],output/'execution',root,source_catalogs=sources,
        import_purpose='memory_origin',motion_permission='dev_tier_sim_motion')
    catalog=json.loads((output/'execution/catalog.json').read_bytes());descriptor=json.loads((output/'execution/descriptor.json').read_bytes())
    result=origin.validate_origin(provision,found,catalog,descriptor,validator_root=tmp_path/'validator',check_only=True)
    assert result==dict(status='checked'),result
    prepare(found['store'],key,2,ctx['namespace'],output/'source',root,source_catalogs=sources,
        import_purpose='memory_origin',source=origin.source_prerequisites(found),failure_signature=found['provenance']['failure_signature'])
    artifacts=dict(sources=sources,execution_catalog=catalog,execution_descriptor=descriptor,
        source_catalog=json.loads((output/'source/catalog.json').read_bytes()),source_descriptor=json.loads((output/'source/descriptor.json').read_bytes()))
    (tmp_path/'real-origin-artifacts.json').write_text(json.dumps(artifacts,ensure_ascii=False,indent=2)+'\n')
    print('REAL_ORIGIN_ARTIFACTS '+str(tmp_path/'real-origin-artifacts.json'))
    print('REAL_ORIGIN_DESCRIPTORS '+json.dumps({k:v for k,v in artifacts.items() if k.endswith('descriptor')},ensure_ascii=False,sort_keys=True))


def test_agent_origin_native_runner_wiring(agent_origin):
    f=agent_origin
    from benchmarks.attention_harness.memory_agent_replay import replay_policy
    called=[]
    class Runner:
        def __init__(self,**kwargs):
            assert kwargs['agent_mode'] is True and kwargs['independent_evaluation'] is True
            self.observe=kwargs['episode_observer']
        def execute(self,request):
            called.append(request)
            source=Path(request.policy_code_path).read_text()
            assert source==(f.root/'v1/policy.py').read_text()
            episode=Path(request.artifact_root)/'episode'
            obs,state,ref=replay(f,request.task_id,request.seed,f.config,source=source,directory=episode,cell='cell',version=1)
            f.native.write(episode/'initial_public_observation.json',obs)
            f.native.write(episode/'scene-condition.json',dict(state=state))
            self.observe(episode)
            return read(ref)
    obs,state,receipt=replay_policy(f.config['task_id'],f.config['seed'],f.config,
        source=(f.root/'v1/policy.py').read_text(),directory=f.native.validator/'native-runner',cell='cell',version=1,runner_factory=Runner)
    assert len(called)==1 and Path(receipt['uri']).is_relative_to(f.native.validator)
