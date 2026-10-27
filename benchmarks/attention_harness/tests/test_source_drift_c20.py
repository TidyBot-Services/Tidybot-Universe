import hashlib
import json
from pathlib import Path
from benchmarks.attention_harness.tests.source_drift_fakes import scenario,complete

def test_old_origin_validates_with_sealed_drift(tmp_path):
    with scenario(tmp_path) as (runner,path,old):
        decision=complete(runner)
        records=decision['source_drift']
        assert len(records)==1
        assert records[0]['files']==[dict(path=str(path),origin_sha256=hashlib.sha256(old).hexdigest(),current_sha256=hashlib.sha256(path.read_bytes()).hexdigest())]
        config=json.loads(Path(runner.batch['config']['uri']).read_bytes())
        assert config['dev_lock']['sha256']==records[0]['current_lock_sha256']
        assert config['origin_dev_lock']['sha256']==records[0]['origin_lock_sha256']
        rows=runner.service.list_dev_v2_memories()
        assert any(r.get('validation_summary',{}).get('source_drift')==records for r in rows if r.get('validation_summary'))


def test_current_lock_remains_byte_exact(tmp_path,monkeypatch):
    import copy
    import pytest
    from openarm365_dev_v2 import dev_config as dc
    from benchmarks.attention_harness.dev_v2_loop import immutable
    from benchmarks.attention_harness.memory_source_drift import current_identity
    from benchmarks.attention_harness.tests.test_source_policy_c20 import DEV
    sdk=tmp_path/'openarm365_dev_v2/sdk.py';sdk.parent.mkdir();sdk.write_text('# origin\n')
    planner=immutable(tmp_path/'planner.json',{})
    lock=dict(schema='attentionbench.dev-identity-lock.v2',source_root=str(tmp_path),formal_eligible=False,
              files={str(sdk):dc.sha(sdk),planner['uri']:planner['sha256']},source_sets={str(sdk.parent):[str(sdk)]},model_sources={},planner_lock=dict(path=planner['uri'],sha256=planner['sha256']))
    ref=immutable(tmp_path/'old-lock.json',lock);ref=dict(path=ref['uri'],sha256=ref['sha256'])
    monkeypatch.setattr(dc,'model_sources',lambda **kw:{})
    monkeypatch.setattr(dc,'LOCK',Path(planner['uri']));monkeypatch.setattr(dc,'LOCK_SHA',planner['sha256'])
    sdk.write_text('# current\n')
    current,record=current_identity(DEV,ref,tmp_path/'new')
    assert record['files']==[dict(path=str(sdk),origin_sha256=lock['files'][str(sdk)],current_sha256=dc.sha(sdk))]
    dc.verify_lock(current,expected_source_root=tmp_path)
    sdk.write_text('# changed after seal\n')
    with pytest.raises(ValueError,match='resource source drift'):dc.verify_lock(current,expected_source_root=tmp_path)


def test_service_rejects_code_changed_after_current_lock(tmp_path):
    import pytest
    from attention_memory_service.core.store import StateConflictError
    with scenario(tmp_path) as (runner,path,old):
        complete(runner)
        path.write_text('# changed again\n')
        with pytest.raises(StateConflictError,match='digest changed'):
            runner.service.dev_v2.execution(runner.starts[0])


def test_new_epoch_prepares_new_origin_without_rewriting_receipt(tmp_path):
    with scenario(tmp_path) as (runner,path,old):
        note=runner.service.validation_queue()[0]
        first=runner.origin_needed(note)['origin_key']
        complete(runner)
        receipt=runner.provision.state_root/'origins'/first/'receipt.json'
        frozen=receipt.read_bytes()
        assert runner.origin_needed(note) is None
        path.write_bytes(path.read_bytes()+b'# another epoch\n')
        next_origin=runner.origin_needed(note)
        assert next_origin['origin_key']!=first
        assert receipt.read_bytes()==frozen


def test_added_and_removed_files_have_exact_optional_digests(tmp_path):
    from benchmarks.attention_harness.memory_source_drift import current_source_lock
    from attention_memory_service.source_policy import drift_record
    from benchmarks.attention_harness.tests.test_source_policy_c20 import locks,DEV
    old,_,name=locks(tmp_path)
    folder=Path(name).parent;folder.mkdir()
    added=folder/'new.py';added.write_text('# added\n')
    current=current_source_lock(old)
    record=drift_record(DEV,old,current,'c'*64,'d'*64)
    assert record['files']==sorted([
        dict(path=name,origin_sha256='a'*64,current_sha256=None),
        dict(path=str(added),origin_sha256=None,current_sha256=hashlib.sha256(added.read_bytes()).hexdigest())],key=lambda r:r['path'])
