import json
from pathlib import Path
import pytest

from benchmarks.attention_harness.dev_v2_memory_import import prepare, prepare_recoverable
from benchmarks.attention_harness.tests.family_import_evidence import layout, run


def test_family_template_probe_and_arm_real_prepare(tmp_path,monkeypatch):
    run(tmp_path,monkeypatch)


def test_old_template_layout_is_rejected_by_real_prepare(tmp_path,monkeypatch):
    evidence=layout(tmp_path,monkeypatch,legacy_template=True)
    for export in evidence.exports:
        with pytest.raises(ValueError,match='catalog root must contain only explicitly pinned evidence: .*family-templates'):
            prepare(*export.args,**export.options)
        assert (export.output/'descriptor.json').is_file()
        assert (export.output/'ledger.sqlite').is_file()
        assert not (export.output/'catalog.json').exists()


def test_interrupted_prepare_retries_without_changing_completed_import(tmp_path,monkeypatch):
    evidence=layout(tmp_path,monkeypatch)
    export=evidence.exports[0]
    with pytest.raises(ValueError,match='catalog root must contain'):
        prepare_recoverable(*export.args,**dict(export.options,source_catalogs={}))
    assert (export.output/'descriptor.json').is_file()
    assert (export.output/'ledger.sqlite').is_file()
    assert not (export.output/'catalog.json').exists()
    ref=prepare_recoverable(*export.args,**export.options)
    catalog=json.loads(Path(ref['uri']).read_bytes())
    assert catalog['root']==str(evidence.experiments)
    before={p:p.read_bytes() for p in export.output.iterdir()}
    monkeypatch.setattr('benchmarks.attention_harness.dev_v2_memory_import.prepare',
                        lambda *a,**kw:pytest.fail('completed export must not be prepared again'))
    assert prepare_recoverable(*export.args,**export.options)==ref
    assert all(p.read_bytes()==raw for p,raw in before.items())


def test_ledger_finish_recovers_partial_probe_import(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from benchmarks.attention_harness.memory_validation_runtime import LedgerRunner
    from benchmarks.attention_harness.dev_v2_loop import immutable
    evidence=layout(tmp_path,monkeypatch);export=evidence.exports[0]
    # The original failure leaves exactly these files, with no completion marker.
    with pytest.raises(ValueError,match='catalog root must contain'):
        prepare(*export.args,**dict(export.options,source_catalogs={}))
    validator=tmp_path/'validator';verdict=immutable(validator/'verdict.json',{})
    result=dict(status='passed',descriptor=verdict,catalog=dict(root=str(validator),
        role='validator',context=evidence.probe['context'],files={'verdict.json':verdict['sha256']}))
    immutable(evidence.state/'verdicts'/f'{export.key}.json',result)
    imports=[]
    client=SimpleNamespace(register_dev_v2_catalog=lambda **kw:None,
        import_dev_v2_execution=lambda **kw:imports.append(kw),
        cross_seed_write=lambda route,**kw:dict(route=route,**kw))
    runner=LedgerRunner(client,None,experiments_root=evidence.experiments,
        validator_root=validator,state_root=evidence.state)
    current=dict(evidence.probe,arm_id=export.key)
    try:
        first=runner.finish(current)
        before={p:p.read_bytes() for p in export.output.iterdir()}
        assert runner.finish(current)==first
    finally:runner.pool.shutdown()
    assert first['status']=='probe' and first['probe']['execution_import_id']==export.key
    assert len(imports)==2 and imports[0]==imports[1]
    assert 'catalog.json' in {p.name for p in before}
    assert all(p.read_bytes()==raw for p,raw in before.items())
