"""Differential byte goldens against the unmodified phase-1 Git baseline."""
import json
import shutil
import subprocess
import types
from pathlib import Path
from types import SimpleNamespace

from benchmarks.attention_harness.tests.autovalidate_fakes import AutomaticOriginFakeRunner, synthetic_creation
from benchmarks.attention_harness.tests.r3_fixtures import r3_deployment
from benchmarks.attention_harness import memory_origin_evidence as current
from benchmarks.attention_harness import dev_planner_memory as memory
from benchmarks.attention_harness import memory_independent_validator as validator
from benchmarks.attention_harness import dev_v2_memory_import as exporter


def baseline(module,repo):
    path=Path(module.__file__).relative_to(repo)
    revision='12e2df9' if module.__package__.startswith('attention_memory_service') else '1c61eda'
    code=subprocess.check_output(['git','show',revision+':'+str(path)],cwd=repo,text=True)
    old=types.ModuleType(module.__package__+'._phase2_baseline_'+module.__name__.split('.')[-1])
    old.__package__=module.__package__;exec(compile(code,str(path),'exec'),old.__dict__)
    return old


def snapshot(root):return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_onecall_origin_export_validator_runner_and_service_goldens(tmp_path,monkeypatch,r3_deployment):
    from attention_memory_service import dev_v2 as service_module, memory_v2, motion_permission
    repo=Path(__file__).resolve().parents[3]
    originals={m.__name__:baseline(m,repo) for m in (current,validator,exporter)}
    svc_repo=Path(service_module.__file__).resolve().parents[1]
    old_service=baseline(service_module,svc_repo);old_memory=baseline(memory_v2,svc_repo)
    old_permission=baseline(motion_permission,svc_repo)
    for purpose in (None,'memory_validation'):
        descriptor=dict(import_purpose=purpose,permission_scope=dict(action='sensor_only'))
        def outcome(module):
            try:return module.require_result_permission(descriptor,service_module.require)
            except service_module.StateConflictError as exc:return str(exc)
        assert outcome(old_permission)==outcome(motion_permission)
    f=AutomaticOriginFakeRunner(tmp_path/'fake',tmp_path/'worker')
    monkeypatch.setattr(memory,'context',lambda *a,**kw:dict(f.failed['context']))
    monkeypatch.setattr(memory,'sdk_identity',lambda *a:f.f.sdk)
    monkeypatch.setattr(validator,'GpuMeter',f.origin_meter)
    monkeypatch.setattr(originals[validator.__name__],'GpuMeter',f.origin_meter)
    import tempfile,uuid
    monkeypatch.setattr(uuid,'uuid4',lambda:SimpleNamespace(hex='a'*32))
    def directory(suffix=None,prefix=None,dir=None):
        path=Path(dir or tmp_path)/((prefix or 'tmp')+'golden'+(suffix or ''));path.mkdir();return str(path)
    monkeypatch.setattr(tempfile,'mkdtemp',directory)
    note=f.service.validation_queue()[0]
    with synthetic_creation(f.f):
        old=originals[current.__name__]
        assert old.origin_note(f.provision,note)==current.origin_note(f.provision,note)
        assert old.identity(f.provision,note)==current.identity(f.provision,note)
        found=current.locate(f.provision,note);before=old.locate(f.provision,note)
        assert {k:v for k,v in found.items() if k!='store'}=={k:v for k,v in before.items() if k!='store'}
        assert old.export_context(f.provision,before)==current.export_context(f.provision,found)
        assert old.source_prerequisites(before)==current.source_prerequisites(found)
        ctx,sources=current.export_context(f.provision,found)
        out=f.f.root/'golden-export'
        kwargs=dict(source_catalogs=sources,motion_permission='dev_tier_sim_motion',import_purpose='memory_validation')
        exporter.prepare(found['store'],'failed',1,ctx['namespace'],out,f.f.root,**kwargs)
        new=snapshot(out);shutil.rmtree(out)
        originals[exporter.__name__].prepare(before['store'],'failed',1,ctx['namespace'],out,f.f.root,**kwargs)
        assert snapshot(out)==new
        catalog=json.loads((out/'catalog.json').read_bytes());descriptor=json.loads((out/'descriptor.json').read_bytes())
        assert current.validate_origin(f.provision,found,catalog,descriptor,validator_root=f.f.validator,check_only=True)==old.validate_origin(f.provision,before,catalog,descriptor,validator_root=f.f.validator,check_only=True)
        shutil.rmtree(out)
        # Same absolute files and names on both passes make every SHA comparable.
        assert old.check_origin(f.provision,note['memory_id'],validator_root=f.f.validator)==current.check_origin(f.provision,note['memory_id'],validator_root=f.f.validator)
        runs=[]
        current_validate=validator.validate_arm;current_prepare=exporter.prepare
        for module in (old,current):
            monkeypatch.setattr(validator,'validate_arm',originals[validator.__name__].validate_arm if module is old else current_validate)
            monkeypatch.setattr(exporter,'prepare',originals[exporter.__name__].prepare if module is old else current_prepare)
            f.origin_runner.origins=module.OriginPreparation(f.origin_runner)
            preparation=f.origin_runner.origins;current_job=preparation.needed(note)
            preparation.start(current_job);assert preparation.poll(current_job)=='completed'
            receipt=preparation.finish(current_job);assert receipt['status']=='passed',receipt
            runs.append((receipt,snapshot(f.provision.state_root),snapshot(f.f.root/'memory-origin-imports'),snapshot(f.f.validator/'arm-golden')))
            for path in (f.provision.state_root,f.f.root/'memory-origin-imports',f.f.validator/'arm-golden'):shutil.rmtree(path)
            f.origin_runner.futures.clear();f.replays=0;f.meter_events=[]
        assert runs[0]==runs[1]
        # Restore immutable runner files for the service differential.
        for root,files in ((f.provision.state_root,runs[1][1]),(f.f.root/'memory-origin-imports',runs[1][2]),(f.f.validator/'arm-golden',runs[1][3])):
            for name,data in files.items():p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
        receipt=runs[1][0]
        f.service.dev_v2.register('golden-contract',dict(role='contract',root=str(Path(f.f.contract['uri']).parent),context=ctx,files={'scene.json':f.f.contract['sha256']}))
        for cid,ref in receipt['catalogs'].items():f.service.dev_v2.register(cid,json.loads(Path(ref['uri']).read_bytes()))
        resolved=f.provision.resolve(note)
        old_provision=baseline(__import__('benchmarks.attention_harness.memory_validation_provision',fromlist=['Provisioner']),repo)
        assert old_provision.Provisioner.resolve(f.provision,note)==resolved
        f.service.import_dev_v2_execution(**resolved['failed_execution'])
        evidence=f.service.dev_v2
        prior=old_service.DevV2Evidence(f.service.store)
        args=resolved['failed_execution']
        assert evidence.verify_execution(args['catalog_id'],args['descriptor'])==prior.verify_execution(args['catalog_id'],args['descriptor'])
        source_args=resolved['source_import'];sd=evidence.read(source_args['catalog_id'],source_args['descriptor'])
        assert evidence.verdict(args['import_id'],sd['validator_id'],sd['validation_descriptor'])==prior.verdict(args['import_id'],sd['validator_id'],sd['validation_descriptor'])
        assert f.service.import_dev_v2_source(**source_args)==prior.import_source(**source_args)
        candidate_args=dict(memory_id='golden-candidate',source_import_id=source_args['import_id'],created_at=1.,from_provisional=note['memory_id'])
        assert evidence.create_candidate(**candidate_args).artifact()==prior.create_candidate(**candidate_args).artifact()
        assert f.service.v2.provenance('golden-candidate')==old_memory.MemoryV2Manager(f.service.store).provenance('golden-candidate')
        assert old_provision.Provisioner.__call__(f.provision,note,'golden-onecall-operation')==f.provision(note,'golden-onecall-operation')
