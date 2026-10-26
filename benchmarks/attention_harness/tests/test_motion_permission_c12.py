"""C12 bounded permission regressions and automatic fake acceptance wrapper."""
import copy
import json
import runpy
import sqlite3
import subprocess
from pathlib import Path

import pytest

from benchmarks.attention_harness.core.dev_store import DevConflict
from benchmarks.attention_harness.dev_planner import binding
from benchmarks.attention_harness.dev_v2_memory_import import prepare
from benchmarks.attention_harness.memory_motion_permission import ACTION, PURPOSE, validation_scope
from benchmarks.attention_harness.memory_origin_evidence import OriginalStore
from benchmarks.attention_harness.tests.autovalidate_fakes import AutomaticOriginFakeRunner
from benchmarks.attention_harness.tests.motion_c12_fakes import CODE, motion_fakes
from benchmarks.attention_harness.tests.r3_fixtures import r3_deployment
from benchmarks.attention_harness.tests.test_memory_origin_c9 import synthetic_export

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.usefixtures('r3_deployment')


def test_motion_fake_acceptance_wrapper():
    runpy.run_path(str(ROOT/'scripts/motion_c12_fake_e2e.py'), run_name='__main__')
    result = subprocess.run(['node','--test',str(Path(__file__).with_name('test_motion_permission_c12_ui.cjs'))],capture_output=True,text=True,timeout=30)
    assert result.returncode == 0, result.stdout+result.stderr


@pytest.fixture
def fake(tmp_path):
    with motion_fakes():
        yield AutomaticOriginFakeRunner(tmp_path/'fake',tmp_path/'worker')


def export(fake, output, **kwargs):
    with synthetic_export(fake):
        if 'source_catalogs' not in kwargs:
            from benchmarks.attention_harness.memory_source_catalogs import manifest_sources
            kwargs['source_catalogs']=manifest_sources(fake.failed['descriptor']['manifest'],fake.failed['context'],fake.f.root,contract=fake.f.contract)
        return prepare(OriginalStore(fake.failed['descriptor']['ledger']['uri']), 'failed', 1,
                       fake.failed['context']['namespace'], output, fake.f.root, **kwargs)


def test_prepare_opt_in_exact_binding_no_approval_and_read_only(fake):
    root=fake.f.root; before=Path(fake.failed['descriptor']['ledger']['uri']).read_bytes()
    export(fake,root/'c12-import',motion_permission=ACTION,import_purpose=PURPOSE)
    d=json.loads((root/'c12-import/descriptor.json').read_bytes())
    r=fake.failed
    expected=dict(binding('failed',1,Path(r['record']['source']['uri']).read_text(),r['config']),action=ACTION,actor=ACTION)
    assert d['permission_scope']==expected and 'approval_id' not in d
    assert d['import_purpose']==PURPOSE
    assert Path(r['descriptor']['ledger']['uri']).read_bytes()==before


def test_prepare_default_still_needs_human_approval(fake):
    with pytest.raises(DevConflict,match='exactly one consumed motion approval'):
        export(fake,fake.f.root/'ordinary')
    with pytest.raises(DevConflict,match='only for Memory validation'):
        export(fake,fake.f.root/'ordinary-opt-in',motion_permission=ACTION)


@pytest.mark.parametrize('field,value', [('budget_tier','legacy'),('budget_tier','formal'),('runner_boundary','formal'),('execution_target','real_robot')])
def test_validation_scope_rejects_other_boundaries(fake, field, value):
    r=fake.failed; config={**r['config'],field:value}
    with pytest.raises(ValueError):validation_scope('failed',1,CODE,config,r['bundle'],PURPOSE)


def test_service_inventory_matches_planner_for_all_capabilities(fake):
    from attention_memory_service.motion_permission import verify
    from attention_memory_service.dev_v2 import require
    from openarm365_dev.policy_validation import OPERATIONS
    source='from robot_sdk import '+', '.join(OPERATIONS)+'\n'+'\n'.join(f'x = {g}.{m}' for g,methods in OPERATIONS.items() for m in methods)+'\n'
    r=fake.failed; permission=validation_scope('failed',1,source,r['config'],r['bundle'],PURPOSE)
    verify(permission,dict(cell='failed',version=1,import_purpose=PURPOSE),r['config'],r['bundle'],source.encode(),require)
    assert permission['scope']['operations']==sorted(f'{g}.{m}' for g,methods in OPERATIONS.items() if g!='sensors' for m in methods)


def test_independent_validator_refuses_actor_scope_and_missing_purpose(fake):
    from benchmarks.attention_harness.memory_origin_evidence import locate, export_context, validate_origin
    with synthetic_export(fake):
        found=locate(fake.provision,dict(memory_id=fake.mid));ctx,sources=export_context(fake.provision,found)
        output=fake.f.root/'c12-seal'
        export(fake,output,source_catalogs=sources,motion_permission=ACTION,import_purpose=PURPOSE)
        catalog=json.loads((output/'catalog.json').read_bytes());original=json.loads((output/'descriptor.json').read_bytes())
        for change in ('actor','scope','purpose'):
            d=copy.deepcopy(original)
            if change=='actor':d['permission_scope']['actor']='authenticated_human'
            elif change=='scope':d['permission_scope']['scope']['sdk_calls']+=1
            else:d.pop('import_purpose')
            result=validate_origin(fake.provision,found,catalog,d,validator_root=fake.f.validator,check_only=True)
            assert result['status']=='refused',result
        assert fake.replays==0


@pytest.mark.parametrize('stage', ['same_scene','probe','treatment'])
def test_production_finish_seals_and_forwards_validation_permission(fake, monkeypatch, stage):
    """Exercise real LedgerRunner.finish preparation before its independent replay."""
    from types import SimpleNamespace
    from benchmarks.attention_harness import memory_validation_runtime as runtime
    from benchmarks.attention_harness.dev_v2_loop import immutable
    store=OriginalStore(fake.failed['descriptor']['ledger']['uri'])
    original_get=store.get
    def get(db, kind, key):
        value=original_get(db,kind,key)
        return {**value,'artifact_dir':str(fake.f.root)} if kind=='experiments' else value
    monkeypatch.setattr(store,'get',get)
    c=store.cell('failed'); d=fake.failed['descriptor']
    if stage!='probe':
        grant=immutable(fake.f.root/'grant.json',{'binding':{'cell':'failed','version':1}})
        immutable(Path(c['artifact_dir'])/'v1/memory-validation-envelope.json',{'grant':grant})
    else:
        Path(c['artifact_dir'],'v1/memory-validation-input.json').write_bytes(Path(d['validation_input']['uri']).read_bytes())
    runner=runtime.LedgerRunner.__new__(runtime.LedgerRunner)
    runner._cell=lambda current:(store,'failed',c)
    runner.experiments_root=fake.f.root;runner.validator_root=fake.f.validator;runner.state_root=fake.provision.state_root
    runner.client=SimpleNamespace(register_dev_v2_catalog=lambda **kw:None)
    runner.gpu_meter=lambda *a:0;runner.replay=lambda *a:pytest.fail('No replay in this wiring test')
    current=dict(config=d['config'],context=d['context'],failure_signature={},budget={},dev_model={'model':'fake'})
    if stage!='same_scene':
        current.update(kind='cross_seed',stage=stage,origin=fake.failed['record']['source'],target={'catalog':dict(role='contract',root=str(Path(fake.f.contract['uri']).parent),files={'scene.json':fake.f.contract['sha256']})})
    class Validated(Exception):pass
    def validate(**kwargs):
        assert kwargs['import_purpose']==PURPOSE
        assert kwargs['permission_scope']['actor']==ACTION
        assert kwargs['permission_scope']['scope']['operations']==['left_arm.move_delta','left_gripper.close']
        assert ('probe_input' in kwargs)==(stage=='probe')
        descriptor=json.loads((fake.f.root/'validation-import/failed/descriptor.json').read_bytes())
        assert descriptor['permission_scope']==kwargs['permission_scope'] and 'approval_id' not in descriptor
        raise Validated
    monkeypatch.setattr(runtime,'validate_arm',validate)
    with synthetic_export(fake),pytest.raises(Validated):runner.finish(current)


def test_prepare_preserves_consumed_human_approval_even_when_opted_in(fake):
    from benchmarks.attention_harness.dev_ledger import encoded
    r=fake.failed;scope=binding('failed',1,Path(r['record']['source']['uri']).read_text(),r['config'])
    approval=dict(request_type='approval',state='consumed',binding=scope,consumed_by='failed-execute',created_at=0,responded_at=.25,consumed_at=.5,deadline_epoch=2,response=dict(approved=True,actor='authenticated_human',response_id='response'))
    with sqlite3.connect(r['descriptor']['ledger']['uri']) as db:
        db.execute('INSERT INTO v2_planner_requests VALUES (?,?,?)',('human-approval','failed',encoded(approval)))
    out=fake.f.root/'c12-human'
    export(fake,out,motion_permission=ACTION,import_purpose=PURPOSE)
    d=json.loads((out/'descriptor.json').read_bytes())
    assert d['permission_scope']==scope and d['approval_id']=='human-approval'
    assert d['permission_scope']['action']=='execute_bounded_motion_policy'
    assert 'actor' not in d['permission_scope']
