"""Synthetic source-link checks; real interpreter is hashed, never executed."""
import inspect,json
from pathlib import Path
import pytest
from benchmarks.attention_harness.core.dev_store import DevConflict
from benchmarks.attention_harness.dev_v2_loop import immutable
from benchmarks.attention_harness.memory_source_catalogs import source_catalogs,registration_order,verify_source_link,file_digest
from benchmarks.attention_harness.memory_validation_provision import Provisioner
from benchmarks.attention_harness.tests.autovalidate_fakes import context,SceneFixture,sync_request,API,IMPORTER

REAL=Path('/home/truares/Documents/Codex/2026-10-09/new-chat/work/service_env/bin/python')


def two_hops(tmp_path):
    first=tmp_path/'service/bin';second=tmp_path/'runtime/bin'
    first.mkdir(parents=True);second.mkdir(parents=True)
    final=second/'python3.11';final.write_bytes(b'controlled interpreter bytes')
    (second/'python').symlink_to('python3.11');(first/'python').symlink_to(second/'python')
    (second/'unrelated').write_text('must not seal')
    return first/'python',second/'python',final


def lock_for(tmp_path,interpreter):
    executor=tmp_path/'executor';sdk=executor/'sdk';sdk.mkdir(parents=True)
    source=sdk/'api.py';source.write_text('public = True\n')
    lock=immutable(executor/'lock.json',dict(schema='attentionbench.dev-identity-lock.v2',formal_eligible=False,
        source_sets={str(sdk):[str(source)]},files={str(source):file_digest(source),str(interpreter):file_digest(interpreter)}))
    return executor,lock


def test_two_directories_exact_files_dependency_order_and_provision(tmp_path):
    first,second,final=two_hops(tmp_path);executor,lock=lock_for(tmp_path,first)
    before=Path(lock['uri']).read_bytes();catalogs=source_catalogs(lock,context(),executor)
    assert source_catalogs(lock,context(),executor)==catalogs
    assert [c['root'] for c in catalogs.values()]==[str(second.parent),str(first.parent)]
    target,alias=list(catalogs.values());target_id,alias_id=list(catalogs)
    sha=file_digest(final)
    assert target['files']=={'python':sha,'python3.11':sha} and alias['files']=={'python':sha}
    assert target['links']['python']==dict(target=str(final),target_catalog=target_id,target_name='python3.11')
    assert alias['links']['python']==dict(target=str(second),target_catalog=target_id,target_name='python')
    reversed_catalogs=dict(reversed(list(catalogs.items())))
    assert list(registration_order(reversed_catalogs))==list(catalogs)
    refs={cid:immutable(tmp_path/(cid+'.json'),c) for cid,c in reversed_catalogs.items()}
    provision=Provisioner(dict(experiments_root=str(executor),state_root=str(tmp_path/'worker'),evidence_catalogs=refs))
    assert list(provision.catalogs())==list(catalogs)
    assert Path(lock['uri']).read_bytes()==before
    first.unlink();first.symlink_to(final)  # Same bytes, different one-hop target.
    with pytest.raises(DevConflict):provision.catalogs()


def test_real_historical_interpreter_builds_two_source_catalogs(tmp_path):
    assert REAL.is_symlink(), 'historical interpreter alias unavailable'
    executor,lock=lock_for(tmp_path,REAL);before=Path(lock['uri']).read_bytes()
    catalogs=source_catalogs(lock,context(),executor)
    target=Path('/home/truares/miniconda3/envs/maniskill/bin')
    assert {c['root'] for c in catalogs.values()}=={str(REAL.parent),str(target)}
    by_root={c['root']:(cid,c) for cid,c in catalogs.items()};tid,tc=by_root[str(target)]
    assert set(tc['files'])=={'python','python3.11'}
    assert tc['links']['python']==dict(target=str(target/'python3.11'),target_catalog=tid,target_name='python3.11')
    assert by_root[str(REAL.parent)][1]['links']['python']['target_catalog']==tid
    assert Path(lock['uri']).read_bytes()==before


@pytest.mark.parametrize('fault',['digest','cycle','too_long','directory_link'])
def test_builder_refuses_invalid_original_chain(tmp_path,fault):
    first,second,final=two_hops(tmp_path);executor,lock=lock_for(tmp_path,first)
    if fault=='digest':final.write_text('changed')
    elif fault=='cycle':second.unlink();second.symlink_to(first)
    elif fault=='too_long':
        second.unlink()
        previous=second
        for i in range(8):
            following=second.parent/('hop'+str(i));previous.symlink_to(following);previous=following
        previous.symlink_to(final)
    else:
        directory=tmp_path/'alias';directory.symlink_to(second.parent,target_is_directory=True)
        first.unlink();first.symlink_to(directory/'python')
    with pytest.raises(DevConflict):source_catalogs(lock,context(),executor)


@pytest.mark.parametrize('fault',['missing_pin','wrong_catalog','wrong_name','digest','regular'])
def test_supplied_pins_checked_before_export(tmp_path,fault):
    first,second,final=two_hops(tmp_path);executor,lock=lock_for(tmp_path,first)
    catalogs=source_catalogs(lock,context(),executor);cid=list(catalogs)[-1];alias=catalogs[cid];sha=alias['files']['python']
    if fault=='missing_pin':alias.pop('links')
    elif fault=='wrong_catalog':alias['links']['python']['target_catalog']='missing'
    elif fault=='wrong_name':alias['links']['python']['target_name']='../bin/python'
    elif fault=='digest':final.write_text('changed')
    else:first.unlink();first.write_bytes(final.read_bytes())
    with pytest.raises(DevConflict):verify_source_link(catalogs,cid,'python',sha)


def test_two_hop_lock_imported_by_inprocess_service(tmp_path):
    from attention_memory_service.dev_v2 import DevV2Evidence
    if 'hops' not in inspect.signature(DevV2Evidence._path).parameters:
        pytest.skip('needs the pinned-source-link Memory service')
    from attention_memory_service.memory_service_api import create_app
    from attention_memory_service.memory_service import MemoryService
    from attention_memory_service.core.store import StateConflictError
    first,second,final=two_hops(tmp_path)
    (tmp_path/'fixture').mkdir()
    f=SceneFixture(tmp_path/'fixture');lock=json.loads(Path(f.lock['uri']).read_bytes())
    lock['files'][str(first)]=file_digest(final);f.lock=f.write(Path(f.lock['uri']),lock)
    frozen=Path(f.lock['uri']).read_bytes();r=f.make('linked-origin',failure=True)
    sources=source_catalogs(f.lock,r['context'],f.root,contract=f.contract)
    cats=f.seal();cats['contract']=dict(role='contract',root=str(Path(f.contract['uri']).parent),context=r['context'],files={'scene.json':f.contract['sha256']})
    roots=dict(executor=[str(f.root)],validator=[str(f.validator)],contract=[str(Path(f.contract['uri']).parent)],source=[str(first.parent),str(second.parent)])
    app=create_app(tmp_path/'service.sqlite',api_key=API,importer_key=IMPORTER,dev_v2_registration_roots=roots)
    for cid,catalog in {**sources,**cats}.items():
        status,result=sync_request(app,'/dev-v2/catalogs',method='POST',key=API,importer_key=IMPORTER,payload=dict(catalog_id=cid,catalog=catalog))
        assert status==200,result
    service=MemoryService(tmp_path/'service.sqlite')
    assert service.import_dev_v2_execution(import_id='linked-origin',catalog_id='linked-origin',descriptor='linked-origin/execution.json')['cell']=='linked-origin'
    assert Path(f.lock['uri']).read_bytes()==frozen
    first.unlink();first.symlink_to(final)
    with pytest.raises(StateConflictError):service.import_dev_v2_execution(import_id='retargeted',catalog_id='linked-origin',descriptor='linked-origin/execution.json')
