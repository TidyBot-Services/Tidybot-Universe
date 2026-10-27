"""Dev-only snapshots and pre-C1 serialization, using an isolated fake base."""
import ast
import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace as NS

import pytest
from curobo_v2 import dev_local as dl, execution_settings as es, identity as auth

BASELINE='68e7ecfd6f5f3a89f4ad71b25194f430b0a0c974'


def write(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():path.chmod(0o644)
    path.write_text(json.dumps(value,sort_keys=True,indent=2)+'\n')


def baseline(path):
    name='dev_local_baseline_'+path.replace('/','_').replace('.','_')
    module=ModuleType(name);module.__file__=str(Path(path).resolve())
    module.__package__=path.rsplit('/',1)[0].replace('/','.')
    sys.modules[name]=module
    exec(compile(subprocess.check_output(['git','show',BASELINE+':'+path],text=True),path,'exec'),module.__dict__)
    return module


def wire(value):return json.dumps(value,allow_nan=False).encode()


@pytest.fixture
def base(tmp_path,monkeypatch):
    repo=tmp_path/'source';repo.mkdir()
    subprocess.run(['git','init','-q',str(repo)],check=True)
    source=repo/'curobo_v2/example.py';source.parent.mkdir();source.write_text('value = 2\n')
    subprocess.run(['git','add','.'],cwd=repo,check=True)
    # No commit is needed to test the source inventory; supply a fixed source revision.
    command=dl.subprocess.check_output
    def output(args,**kw):
        if args==['git','rev-parse','HEAD'] and kw.get('cwd')==repo:return '1'*40+'\n'
        return command(args,**kw)
    monkeypatch.setattr(dl.subprocess,'check_output',output)
    project=tmp_path/'deployment-06/runtime/project';project.mkdir(parents=True)
    for name,raw in [('curobo_v2/example.py','value = 1\n'),('asset.dat','fixed asset'),
                     ('native-task-source/robocasa_tasks/__init__.py','# tasks\n'),
                     *[(n,str(project.parent.parent)+'/asset') for n in dl.RELOCATED_YAMLS]]:
        p=project/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(raw);p.chmod(0o444)
    outside=tmp_path/'official.py';outside.write_text('# official\n')
    files={str(p):auth.digest(p) for p in project.rglob('*') if p.is_file()}
    files[str(outside)]=auth.digest(outside)
    lock=dict(schema='attentionbench.curobo-v2.runtime.v1',generation=2,prefix='/isolated',
        python='/isolated/bin/python',library_root='/official',library_revision=auth.OFFICIAL_COMMIT,
        library_base=auth.OFFICIAL_COMMIT,official_only=True,module_version='0.8',distribution_version='0.8',
        service_root=str(project/'curobo_v2/service'),assets_root=str(project/'assets'),project_root=str(project),
        files=files,dependencies={'numpy':{'path':'/isolated/numpy.py','sha256':'a'*64}},
        distributions={'numpy':'1'},robot_configs={str(project/dl.RELOCATED_YAMLS[0]):files[str(project/dl.RELOCATED_YAMLS[0])]},
        task_sources={str(project/'native-task-source'):files[str(project/'native-task-source/robocasa_tasks/__init__.py')]},
        provenance=dict(parent_lock_sha256='official',parent_lock_path='official',official_provenance={},
                        adapter_revision='deployment-06',parent_adapter_lock_sha256=es.D05_SHA))
    parent=tmp_path/'official-lock.json';write(parent,lock);parent_sha=auth.digest(parent)
    lock['provenance'].update(parent_lock_path=str(parent),parent_lock_sha256=parent_sha)
    path=project.parent.parent/'runtime-lock.json';write(path,lock)
    registry=tmp_path/'trusted.json';write(registry,{auth.digest(path):dict(lock_path=str(path),
        official_commit=auth.OFFICIAL_COMMIT,parent_sha256=parent_sha,allowed_tiers=['dev'],
        profile=es.PROFILE,derived_from_sha256=es.D05_SHA)})
    monkeypatch.setattr(auth,'TRUSTED_LOCKS',{parent_sha:auth.OFFICIAL_COMMIT})
    monkeypatch.setattr(auth,'OFFICIAL_PARENT_LOCK',parent)
    monkeypatch.setattr(auth,'DEPLOYMENT_REGISTRY',registry)
    monkeypatch.setattr(auth,'_registry_digest',None)
    monkeypatch.setattr(auth,'_service_context',None)
    monkeypatch.setattr(es,'D06_LOCK',str(path))
    monkeypatch.setattr(dl,'DEV_LOCAL_ROOT',tmp_path/'snapshots')
    return NS(repo=repo,path=path,lock=lock,registry=registry,outside=outside)


def snapshot(base):
    result=dl.materialize(base.repo)
    lock,sha=auth.load_lock(result['lock_path'],tier='dev',boundary='dev_loop')
    return result,lock,sha


def test_determinism_isolation_and_hardlinks(base):
    result,lock,sha=snapshot(base)
    path=Path(result['lock_path']);raw=path.read_bytes()
    assert dl.materialize(base.repo)==result and path.read_bytes()==raw
    project=Path(lock['project_root'])
    assert (project/'asset.dat').stat().st_ino==(Path(base.lock['project_root'])/'asset.dat').stat().st_ino
    assert all(p.stat().st_mode & 0o222==0 for p in [path.parent,*path.parent.rglob('*')])
    (base.repo/'curobo_v2/example.py').write_text('value = 3\n')
    assert (project/'curobo_v2/example.py').read_text()=='value = 2\n'
    other=dl.materialize(base.repo)
    assert other['snapshot_key']!=result['snapshot_key'] and other['sha256']!=sha
    assert str(path.parent) in (project/dl.RELOCATED_YAMLS[0]).read_text()


def test_copy_fallback(base,monkeypatch):
    def failed(*a):raise OSError('cross-device link')
    monkeypatch.setattr(dl.os,'link',failed)
    _,lock,_=snapshot(base)
    assert (Path(lock['project_root'])/'asset.dat').stat().st_ino!=(Path(base.lock['project_root'])/'asset.dat').stat().st_ino


@pytest.mark.parametrize('tier,boundary',[('formal','formal'),('legacy','dev_loop'),(None,None),('dev','formal_eval'),('dev','dev'),('dev',None)])
def test_dev_only(base,tier,boundary):
    result,_,_=snapshot(base)
    with pytest.raises(auth.IdentityError,match='Unapproved'):auth.load_lock(result['lock_path'],tier=tier,boundary=boundary)


@pytest.mark.parametrize('change',['kind_missing','kind_fake','eligible','key','base_sha','base_local',
    'python','dependencies','distributions','library_revision','official_only','outside','extra_key','symlink','path','unregistered'])
def test_reject_forged_snapshot(base,monkeypatch,change,tmp_path):
    result,lock,_=snapshot(base);path=Path(result['lock_path'])
    p=lock['provenance']
    if change=='kind_missing':p.pop('deployment_kind')
    elif change=='kind_fake':p['deployment_kind']='registered'
    elif change=='eligible':p['formal_eligible']=True
    elif change=='key':p['snapshot_key']='0'*20
    elif change=='base_sha':p['base_deployment_lock_sha256']='0'*64
    elif change=='base_local':
        p['base_deployment_lock_path']=str(path);p['base_deployment_lock_sha256']=result['sha256']
    elif change in ('python','library_revision'):lock[change]='/bad'
    elif change in ('dependencies','distributions'):lock[change]={'fake':'bad'}
    elif change=='official_only':lock[change]=False
    elif change=='outside':lock['files'][str(base.outside)]='0'*64
    elif change=='extra_key':lock['extra']=True
    elif change=='symlink':
        project=Path(lock['project_root']);project.chmod(0o755);(project/'linked').symlink_to(base.outside)
    elif change=='path':path=tmp_path/'elsewhere/runtime-lock.json'
    elif change=='unregistered':
        write(base.registry,{});monkeypatch.setattr(auth,'_registry_digest',None)
    write(path,lock)
    with pytest.raises(auth.IdentityError,match='Unapproved'):auth.load_lock(path,tier='dev',boundary='dev_loop')


@pytest.mark.parametrize('change',['file','extra','registry','linked_path','receipt','manifest'])
def test_runtime_and_reuse_fail_closed(base,change,tmp_path):
    result,lock,_=snapshot(base);project=Path(lock['project_root'])
    if change=='file':
        p=project/'curobo_v2/example.py';p.chmod(0o644);p.write_text('drift')
    elif change=='extra':project.chmod(0o755);(project/'extra').write_text('unlisted')
    elif change=='registry':write(base.registry,{})
    elif change=='linked_path':
        link=tmp_path/'linked';link.symlink_to(Path(result['lock_path']).parent,target_is_directory=True)
        with pytest.raises(auth.IdentityError):auth.load_lock(link/'runtime-lock.json',tier='dev',boundary='dev_loop')
        return
    else:write(Path(result['lock_path']).with_name('dev-local.json' if change=='receipt' else 'source-manifest.json'),{})
    if change in ('file','extra','registry'):
        with pytest.raises(auth.IdentityError):auth.verify_files(lock)
    with pytest.raises((auth.IdentityError,ValueError)):dl.materialize(base.repo)


def test_base_inventory_and_source_paths(base):
    p=Path(base.lock['project_root'])/'unlisted';p.write_text('bad')
    with pytest.raises(ValueError,match='inventory'):dl.materialize(base.repo)
    p.unlink()
    (base.repo/'curobo_v2/example.py').unlink();(base.repo/'curobo_v2/example.py').symlink_to(base.outside)
    with pytest.raises(ValueError,match='Unsafe'):dl.materialize(base.repo)
    for name in ('../escape.py','/absolute.py'):
        with pytest.raises(ValueError,match='Unsafe'):dl.checked_source(base.repo,name)


def test_unrelated_tracked_symlink_materializes(base):
    name='protocol/evidence/linked.py'
    link=base.repo/name;link.parent.mkdir(parents=True)
    link.symlink_to(base.repo/'curobo_v2/example.py')
    subprocess.run(['git','add',name],cwd=base.repo,check=True)
    assert name in dl.source_names(base.repo)
    result,lock,_=snapshot(base)
    assert name not in lock['provenance']['source_manifest']
    assert not (Path(lock['project_root'])/name).exists()
    assert dl.materialize(base.repo)==result


def test_manifest_source_symlink_parent_rejected(base):
    source=base.repo/'curobo_v2'
    target=base.repo/'source-code';source.rename(target)
    source.symlink_to(target,target_is_directory=True)
    name='curobo_v2/example.py'
    assert name in dl.source_manifest(base.repo,Path(base.lock['project_root']))
    with pytest.raises(ValueError,match='Unsafe source manifest path: '+name):dl.materialize(base.repo)
    assert not dl.DEV_LOCAL_ROOT.exists()


@pytest.mark.parametrize('fault',['nonce','signature'])
def test_service_authentication(base,fault):
    result,lock,sha=snapshot(base);robot=next(iter(lock['robot_configs']))
    runtime=dict(generation=2,prefix=lock['prefix'],python=lock['python'],library_path='/official/curobo/__init__.py',
                 module_version=lock['module_version'],library_revision=lock['library_revision'],dependencies={'numpy':'/isolated/numpy.py'})
    value=dict(runtime=runtime,lock_sha256=sha,nonce='nonce',pid=123,service_root=lock['service_root'],
               robot_config=robot,robot_config_sha256=lock['robot_configs'][robot],kernel_backend='cuda_core',
               test_response_fault='none',test_gpu_activity=False)
    args=dict(lock=lock,lock_sha=sha,nonce='nonce',pid=123,token='token',challenge='challenge',robot_config=robot)
    payload=dict(identity=value,signature=auth.sign('token',value,'challenge'))
    assert auth.verify_service(payload,**args)==value
    if fault=='nonce':value['nonce']='stale';payload['signature']=auth.sign('token',value,'challenge')
    else:payload['signature']='0'*64
    with pytest.raises(auth.IdentityError,match='identity mismatch'):auth.verify_service(payload,**args)


def local_settings(result):
    return es.from_config(replace(es.R3_20261025,planner_deployment={k:result[k] for k in ('lock_path','sha256')}).to_dict())


def test_settings_service_pin_and_receipts(base,monkeypatch):
    result,lock,sha=snapshot(base);settings=local_settings(result)
    assert es.receipt_fields(settings)==dict(execution_profile=es.PROFILE,execution_settings_source='config',
        planner_lock_sha256=sha,deployment_kind='dev-local',formal_eligible=False)
    bad=settings.to_dict();bad['planner_deployment']['sha256']='0'*64
    with pytest.raises(ValueError,match='sha256'):es.from_config(bad)
    monkeypatch.setattr(auth,'_service_context',(lock,next(iter(lock['robot_configs']))))
    assert dict(es.R3_20261025.planner_deployment)==dict(settings.planner_deployment)


def config_environment(base,monkeypatch):
    from openarm365_dev_v2 import dev_config as d
    monkeypatch.setattr(d,'ROOT',base.repo)
    for name in ('SIM_PYTHON','ASSETS','LOCK'):monkeypatch.setattr(d,name,base.path)
    monkeypatch.setattr(d,'LOCK_SHA',auth.digest(base.path))
    monkeypatch.setattr(d,'ROBocasa',base.repo/'robocasa')
    monkeypatch.setattr(d,'ROBOSUITE',base.repo/'robosuite')
    monkeypatch.setattr(d,'model_sources',lambda **kw:{})
    original=d.sha
    monkeypatch.setattr(d,'sha',lambda p:original(p) if Path(p).is_file() else 'f'*64)
    return d


def test_config_and_formal_rejection(base,monkeypatch,tmp_path):
    from openarm365_dev_v2 import formal_config
    d=config_environment(base,monkeypatch)
    source=dict(kind='dev-local',source_root=str(base.repo))
    ref=d.make_lock(tmp_path/'dev-identity.json',planner_source=source)
    identity=d.verify_lock(ref)
    assert identity['planner_deployment_kind']=='dev-local'
    # Resource fixtures are intentionally synthetic; planner identity is checked by the real validator above.
    monkeypatch.setattr(d,'verify_lock',lambda *a,**kw:identity)
    config=d.make_dev_config('open_cabinet',102,ref,planner_source=source)
    assert d.validate_dev_config(config)==config
    (base.repo/'curobo_v2/example.py').write_text('later worktree change')
    assert d.validate_dev_config(config)==config
    for tier,boundary in [('legacy','dev_loop'),('dev','formal'),('legacy','formal')]:
        with pytest.raises(ValueError,match='requires dev_loop'):d.make_dev_config('open_cabinet',102,ref,planner_source=source,budget_tier=tier,budget_boundary=boundary)
    with pytest.raises(ValueError,match='rejects'):formal_config.reject_dev_local(config)
    with pytest.raises(ValueError,match='rejects'):formal_config.reject_dev_local({'dev_lock':ref})
    with pytest.raises(ValueError,match='requires dev_loop'):d.validate_dev_config(config,budget_boundary='formal')


@pytest.mark.parametrize('profile',[False,True])
def test_registered_serialization_unchanged(base,monkeypatch,tmp_path,profile):
    before=baseline('curobo_v2/execution_settings.py')
    before.D06_LOCK=es.D06_LOCK
    before._deployment_pin=es._deployment_pin
    old=before.from_config(before.R3_20261025.to_dict()) if profile else None
    new=es.from_config(es.R3_20261025.to_dict()) if profile else None
    for name in ('receipt_fields','request_fields','effective_sha'):
        assert wire(getattr(before,name)(old))==wire(getattr(es,name)(new))
    assert before.receipt_fields(old,include_legacy=True)==es.receipt_fields(new,include_legacy=True)
    if profile:assert old.sha256==new.sha256
    old_identity=baseline('curobo_v2/identity.py')
    for name in ('DEPLOYMENT_REGISTRY','OFFICIAL_PARENT_LOCK','TRUSTED_LOCKS'):setattr(old_identity,name,getattr(auth,name))
    assert wire(old_identity.load_lock(base.path,tier='dev',boundary='dev_loop'))==wire(auth.load_lock(base.path,tier='dev',boundary='dev_loop'))
    rows=[]
    for module,settings in [(baseline('curobo_v2/session.py'),old),(__import__('curobo_v2.session',fromlist=['V2Service']),new)]:
        source=subprocess.check_output(['git','show',BASELINE+':curobo_v2/session.py'],text=True) if module.__name__.startswith('dev_local_baseline') else Path('curobo_v2/session.py').read_text()
        tree=ast.parse(source)
        stmt=next(n for n in ast.walk(tree) if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Attribute) and n.value.func.attr=='write_text' and 'service_spawn.json' in ast.unparse(n))
        owner=NS(evidence=tmp_path,process=NS(pid=123),lock_sha=auth.digest(base.path),robot_config='robot.yml',tier='dev',boundary='dev_loop',execution_settings=settings)
        exec(compile(ast.Module(body=[stmt],type_ignores=[]),'spawn','exec'),dict(self=owner,json=json,trust_fields={},receipt_fields=before.receipt_fields if settings is old else es.receipt_fields))
        rows.append((tmp_path/'service_spawn.json').read_bytes())
    assert rows[0]==rows[1]
    d=config_environment(base,monkeypatch);prior=baseline('openarm365_dev_v2/dev_config.py')
    for name in ('ROOT','SIM_PYTHON','ASSETS','LOCK','LOCK_SHA','ROBocasa','ROBOSUITE','model_sources','sha'):setattr(prior,name,getattr(d,name))
    paths=[tmp_path/'before.json',tmp_path/'after.json']
    prior.make_lock(paths[0],execution_settings=old);d.make_lock(paths[1],execution_settings=new)
    assert paths[0].read_bytes()==paths[1].read_bytes()
    ref={'path':'fixture','sha256':'fixture'}
    prior.verify_lock=lambda *a,**kw:None;monkeypatch.setattr(d,'verify_lock',prior.verify_lock)
    kw={} if profile else {'budget_tier':'legacy'}
    assert wire(prior.make_dev_config('open_cabinet',102,ref,**kw))==wire(d._make_dev_config('open_cabinet',102,ref,execution_settings=new,**kw))


def test_real_d06_read_only_unchanged():
    path=Path(es.D06_LOCK)
    if not path.is_file():pytest.skip('deployment-06 absent')
    prior=baseline('curobo_v2/identity.py')
    assert wire(prior.load_lock(path,tier='dev',boundary='dev_loop'))==wire(auth.load_lock(path,tier='dev',boundary='dev_loop'))
    before=baseline('curobo_v2/execution_settings.py')
    assert es.R3_20261025.sha256==before.R3_20261025.sha256


def test_build_helpers_unchanged(base):
    old=baseline('scripts/build_d06.py')
    assert dl.source_names(base.repo)==old.source_names(base.repo)
    assert dl.source_manifest(base.repo,Path(base.lock['project_root']))==old.source_manifest(base.repo,Path(base.lock['project_root']))


def test_service_evidence_without_gpu(base,monkeypatch,tmp_path):
    import curobo_v2.session as session
    result,lock,sha=snapshot(base)
    evidence=tmp_path/'evidence';evidence.mkdir()
    owner=session.V2Service(lock_path=result['lock_path'],robot_config=next(iter(lock['robot_configs'])),
                            evidence=evidence,deadline=1e15,tier='dev',boundary='dev_loop')
    assert es.receipt_fields(owner.execution_settings)['deployment_kind']=='dev-local'
    from curobo_v2.evidence import preserve_runtime
    preserve_runtime(owner.lock_path,owner.lock,evidence)
    # Exercise the exact evidence statements without spawning native Python/GPU.
    tree=ast.parse(Path(session.__file__).read_text())
    method=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='__enter__')
    receipt=next(n for n in method.body if isinstance(n,ast.If) and 'is_dev_local(self.lock)'==ast.unparse(n.test))
    owner.process=NS(pid=123)
    spawn=next(n for n in ast.walk(method) if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Attribute) and n.value.func.attr=='write_text' and 'service_spawn.json' in ast.unparse(n))
    exec(compile(ast.Module(body=[receipt,spawn],type_ignores=[]),'evidence','exec'),dict(self=owner,
        is_dev_local=dl.is_dev_local,Path=Path,json=json,IdentityError=auth.IdentityError,receipt_fields=es.receipt_fields,trust_fields={}))
    value=json.loads((evidence/'planner_deployment.json').read_bytes())
    assert value==dict(lock['provenance'],lock_sha256=sha,file_count=len(lock['files']),dependency_count=len(lock['dependencies']))
    assert (evidence/'runtime-lock.json').read_bytes()==Path(result['lock_path']).read_bytes()
    assert json.loads((evidence/'service_spawn.json').read_bytes())['deployment_kind']=='dev-local'


def test_memory_context_and_metadata(base,monkeypatch,tmp_path):
    from benchmarks.attention_harness import dev_planner_memory as memory, execution_profile as labels
    from openarm365_dev_v2 import scene_contract
    result,_,sha=snapshot(base)
    d=config_environment(base,monkeypatch)
    monkeypatch.setattr(d,'verify_lock',lambda *a,**kw:None)
    config=d._make_dev_config('open_cabinet',102,dict(path='test',sha256='test'),
        execution_settings=es.from_config(es.R3_20261025.to_dict()))
    ref=dict(uri=str(tmp_path/'contract.json'),sha256='c'*64)
    config['scene_contract']=ref
    monkeypatch.setattr(scene_contract,'registered_contract_ref',lambda:ref)
    sdk=dict(contract='fixed',prompt='fixed',implementation={},planner_lock='fixed')
    monkeypatch.setattr(memory,'sdk_identity',lambda c:sdk)
    old=baseline('benchmarks/attention_harness/dev_planner_memory.py');old.sdk_identity=lambda c:sdk
    for profiled in (False,True):
        c=copy.deepcopy(config)
        if not profiled:c.pop('execution_settings')
        assert wire(memory.context(c,'test'))==wire(old.context(c,'test'))
    config['execution_settings']=local_settings(result).to_dict()
    prior=memory.context(config,'test')
    config['planner_source']=dict(kind='dev-local',source_root=str(base.repo))
    assert memory.context(config,'test')==dict(prior,planner_dev_local_sha256=sha)
    assert labels.config_metadata(config)==dict(execution_profile=es.PROFILE,planner_lock_sha256=sha,
                                                deployment_kind='dev-local',formal_eligible=False)


def test_summary_separates_deployments(base,monkeypatch):
    from benchmarks.attention_harness import summarize_experiment as summary
    monkeypatch.setattr(summary,'_summarize_one',lambda exp:dict(count=len(exp['cells'])))
    config=dict(execution_settings=es.R3_20261025.to_dict())
    def exp(name,c):return dict(experiment_id=name,manifest={'configs':{'task:1':c}},cells=[dict(task_id='task',seed=1)])
    registered=exp('registered',config)
    old=baseline('benchmarks/attention_harness/summarize_experiment.py');old._summarize_one=summary._summarize_one
    assert wire(summary.summarize(registered))==wire(old.summarize(registered))
    local=exp('local',dict(config,planner_source=dict(kind='dev-local',source_root=str(base.repo))))
    rows=summary.summarize([registered,local])['profiles']
    assert len(rows)==2 and {r.get('deployment_kind','registered') for r in rows}=={'registered','dev-local'}


def test_guard_functions_unchanged():
    source=subprocess.check_output(['git','show',BASELINE+':curobo_v2/identity.py'],text=True)
    old=ast.parse(source);now=ast.parse(Path(auth.__file__).read_text())
    for name in ('verify_files','verify_runtime','verify_service'):
        get=lambda tree:ast.dump(next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name))
        assert get(old)==get(now)
    path='curobo_v2/native_auth.py'
    assert Path(path).read_bytes()==subprocess.check_output(['git','show',BASELINE+':'+path])


def test_concurrent_materialize_and_nested_base(base,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(lambda _:dl.materialize(base.repo),range(3)))
    assert results[0]==results[1]==results[2]
    assert not list(dl.DEV_LOCAL_ROOT.glob('.tmp-*'))
    monkeypatch.setattr(es,'D06_LOCK',results[0]['lock_path'])
    with pytest.raises(ValueError,match='Base must be a registered'):dl.materialize(base.repo)


def formal_request(tmp_path,suite,raw):
    from benchmarks.attention_harness.formal_runner_boundary import FormalRunRequest
    policy=tmp_path/'policy.py';policy.write_text('pass\n')
    path=tmp_path/'config';path.write_text(raw)
    entry=dict(suite=suite,task_id='task',seed=1,approved_config_sha256=auth.digest(path),
               approved_policy_sha256=auth.digest(policy),overall_deadline_seconds=300)
    entry_sha=hashlib.sha256(json.dumps(entry,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    return FormalRunRequest(suite=suite,task_id='task',seed=1,policy_code_path=policy,
        policy_sha256=auth.digest(policy),config_sha256=auth.digest(path),config_path=path,
        artifact_root=tmp_path/'evidence',overall_deadline_seconds=300,entry_lock=dict(entry,sha256=entry_sha),entry_sha256=entry_sha)


def test_formal_entry_rejects_source_and_pin(base,tmp_path):
    from openarm365_dev_v2.formal_config import SCHEMA,validate_formal_config
    result,_,_=snapshot(base)
    for body in [dict(planner_source=dict(kind='dev-local',source_root=str(base.repo))),
                 dict(execution_settings=local_settings(result).to_dict())]:
        with pytest.raises(ValueError,match='rejects'):validate_formal_config(dict(body,schema_version=SCHEMA))
        req=formal_request(tmp_path,'robocasa365',json.dumps(body))
        with pytest.raises(ValueError,match='rejects'):req.validate()


@pytest.mark.parametrize('suite,raw',[('robocasa','config: non-JSON\n'),
                                    ('robosuite','config: non-JSON\n'),('robocasa365','{}')])
def test_formal_boundary_rejects_dev_local_result(tmp_path,suite,raw):
    from benchmarks.attention_harness.formal_runner_boundary import run_with_formal_boundary
    req=formal_request(tmp_path,suite,raw)
    req.validate()
    runner=NS(suite=suite,execute=lambda request:dict(deployment_kind='dev-local'))
    with pytest.raises(RuntimeError,match='dev-local execution cannot enter formal comparison'):
        run_with_formal_boundary(req,runner=runner)


def test_registry_approval_body_unchanged():
    old=ast.parse(subprocess.check_output(['git','show',BASELINE+':curobo_v2/identity.py'],text=True))
    now=ast.parse(Path(auth.__file__).read_text())
    prior=next(n for n in old.body if isinstance(n,ast.FunctionDef) and n.name=='approved_deployment')
    current=next(n for n in now.body if isinstance(n,ast.FunctionDef) and n.name=='_registered_deployment')
    assert [ast.dump(n) for n in current.body]==[ast.dump(n) for n in prior.body]
