"""Storage contracts on temporary synthetic runs; no host run/store access."""
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest
from benchmarks.attention_harness import run_storage as storage, frame_cleanup as frames
from benchmarks.attention_harness.dev_agent_workspace import Workspace, encode, read_beneath, digest_beneath, stat_beneath
from benchmarks.attention_harness.tests.test_frame_cleanup import bind

PHYSICS = b'{"robot0_eef_pos":[1.25,0,-2],"native_success":true,"message":"/tmp/private","nested":{"value":3,"secret":"x"},"token":"x","bad/key":4,"label":"left hand"}\n{"time":0.1,"observation":{"qpos":[0,1],"reward":3},"contacts":null}\n'
GOLDEN = b'[\n  {\n    "label": "left hand",\n    "message": "[withheld]",\n    "nested": {\n      "value": 3\n    },\n    "robot0_eef_pos": [\n      1.25,\n      0,\n      -2\n    ]\n  },\n  {\n    "contacts": null,\n    "observation": {\n      "qpos": [\n        0,\n        1\n      ]\n    },\n    "time": 0.1\n  }\n]\n'


def pack(path):
    target = Path(str(path)+'.zst')
    storage._compress(path, target)
    target.chmod(path.stat().st_mode & 0o777)
    path.unlink()
    return target


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else encode(value))
    return path


@pytest.fixture
def run(tmp_path):
    root = tmp_path/'devexp-synthetic/cells/cell'
    root.mkdir(parents=True)
    bind(root)
    ws = Workspace(root/'agent')
    physics = write(ws.private/'executions/e000001/openarm365-dev-test/native-physics.jsonl', PHYSICS*6500)
    state = storage.derive_state_log(physics.read_bytes())
    ws.publish('executions/e000001/state_log.json', state)
    return SimpleNamespace(root=root, ws=ws, physics=physics, state=state,
                           canonical=ws.canonical/'executions/e000001/state_log.json',
                           public=ws.ws/'executions/e000001/state_log.json')


def test_derivation_golden_locks_broker_projection_and_encoding():
    assert storage.derive_state_log(PHYSICS) == GOLDEN
    assert hashlib.sha256(GOLDEN).hexdigest() == '4ca0535c56dd50d4def176194b579a62b08208424603a3bff3428523ad3cd053'


@pytest.mark.parametrize('backend', ['zstandard', 'compression.zstd', 'cli'])
def test_readers_and_backend_precedence(tmp_path, monkeypatch, backend):
    path = write(tmp_path/'log.jsonl', b'{"n":1}\r\n{"n":2}\n')
    expected = path.read_bytes(); side = pack(path)
    original_import = storage.importlib.import_module
    calls = []
    def decoded(source):
        return io.BytesIO(subprocess.check_output(['zstd', '-dc'], input=source.read()))
    class Decoder:
        def stream_reader(self, source, closefd):
            assert closefd is False
            return decoded(source)
    def importer(name):
        calls.append(name)
        if name == backend == 'zstandard':
            return SimpleNamespace(ZstdDecompressor=Decoder)
        if name == backend == 'compression.zstd':
            return SimpleNamespace(ZstdFile=lambda source, mode: decoded(source))
        if name in ('zstandard', 'compression.zstd'):
            raise ImportError(name)
        return original_import(name)
    monkeypatch.setattr(storage.importlib, 'import_module', importer)
    assert storage.read_bytes(path) == expected
    assert storage.read_text(path) == expected.decode()
    assert list(storage.iter_lines(path)) == ['{"n":1}\r\n', '{"n":2}\n']
    assert storage.logical_size(path) == len(expected) and storage.exists(path)
    assert calls[:1 if backend == 'zstandard' else 2] == (['zstandard'] if backend == 'zstandard' else ['zstandard', 'compression.zstd'])
    # Raw wins even when the alternative is corrupt. Zstd wins over a bad stub.
    Path(str(path)+'.derived').write_text('invalid')
    assert storage.read_bytes(path) == expected
    path.write_bytes(b'raw'); side.write_bytes(b'corrupt')
    assert storage.read_bytes(path) == b'raw'
    path.unlink(); side.unlink(); Path(str(path)+'.derived').unlink()
    with pytest.raises(FileNotFoundError, match='log.jsonl'):
        storage.read_bytes(path)


def test_compact_hardlinks_derive_restore_and_measure(run):
    before = storage.measure(run.root)
    plan = storage.plan(run.root)
    assert sorted(a['action'] for a in plan['actions']) == ['derive', 'zstd']
    assert all(a['bytes'] > storage.MIN_BYTES for a in plan['actions'])
    result = storage.compact(run.root)
    assert result['status'] == 'complete'
    assert not run.canonical.exists() and not run.public.exists() and not run.physics.exists()
    assert storage.read_bytes(run.canonical) == run.state == storage.read_bytes(run.public)
    one, two = (Path(str(p)+'.derived') for p in (run.canonical, run.public))
    assert one.stat().st_ino == two.stat().st_ino
    assert storage.compact(run.root) == result
    assert storage.measure(run.root)['bytes'] < before['bytes']//5
    name = 'executions/e000001/state_log.json'
    assert read_beneath(run.ws.canonical, name, len(run.state), shared=True) == run.state
    assert digest_beneath(run.ws.canonical, name, shared=True) == hashlib.sha256(run.state).hexdigest()
    assert stat_beneath(run.ws.canonical, name, shared=True) == len(run.state)
    assert storage.restore(run.root)['status'] == 'restored'
    assert run.canonical.read_bytes() == run.state and run.public.stat().st_ino == run.canonical.stat().st_ino
    assert run.physics.read_bytes() == PHYSICS*6500
    assert not one.exists() and not Path(str(run.physics)+'.zst').exists()
    assert storage.restore(run.root)['status'] == 'restored'


def test_eval_binding_and_nonidentical_state_fallback(run):
    vid = run.ws.canonical/'versions/v0001'
    write(vid/'submission.json', dict(execution_id='e000001'))
    evaluation = write(run.ws.private/'eval/v0001/ws/state_log.json', run.state)
    different = write(run.ws.private/'eval/v0002/ws/state_log.json', run.state+b' ')
    actions = storage.plan(run.root)['actions']
    assert next(a for a in actions if str(evaluation.relative_to(run.root)) in a['paths'])['action'] == 'derive'
    assert next(a for a in actions if str(different.relative_to(run.root)) in a['paths'])['action'] == 'zstd'
    storage.compact(run.root)
    assert storage.read_bytes(evaluation) == run.state
    assert storage.read_bytes(different) == run.state+b' '


@pytest.mark.parametrize('tamper', ['state_sha', 'physics_sha', 'physics_bytes', 'traversal', 'derivation', 'execution'])
def test_derived_never_returns_unverified_bytes(run, tamper):
    storage.compact(run.root)
    side = Path(str(run.public)+'.derived')
    value = json.loads(side.read_bytes())
    if tamper == 'state_sha': value['sha256'] = '0'*64
    elif tamper == 'physics_sha': value['physics_sha256'] = '0'*64
    elif tamper == 'traversal': value['physics'] = '../elsewhere'
    elif tamper == 'derivation': value['derivation'] = 'state-log-v2'
    elif tamper == 'execution': value['physics'] = value['physics'].replace('e000001', 'e000002')
    else: pack_path = Path(str(run.physics)+'.zst'); pack_path.write_bytes(b'bad')
    side.chmod(0o644)
    side.write_bytes(encode(value))
    with pytest.raises((ValueError, OSError)):
        storage.read_bytes(run.public)


@pytest.mark.parametrize('state,reason,experiment', [('running', None, 'running'), ('paused',None,'paused'), ('failed','operator_paused','failed'), ('failed',None,'paused')])
def test_active_paused_refusal(run, state, reason, experiment):
    bind(run.root, state=state, reason=reason, experiment_state=experiment)
    with pytest.raises(ValueError, match='terminal, unpaused'):
        storage.compact(run.root)
    assert run.canonical.exists()


def test_live_owner_refused(run):
    bind(run.root, owner=frames._process_identity(os.getpid()))
    with pytest.raises(ValueError, match='no live'):
        storage.plan(run.root)


def test_memory_catalog_excludes_whole_inode_group_and_fail_closed(run, tmp_path, monkeypatch):
    dbpath = tmp_path/'memory.sqlite'
    with sqlite3.connect(dbpath) as db:
        db.execute('CREATE TABLE dev_v2_catalogs(payload TEXT)')
        db.execute('INSERT INTO dev_v2_catalogs VALUES (?)', (json.dumps(dict(root=str(run.root), files={str(run.public.relative_to(run.root)):'a'*64})),))
    result = storage.compact(run.root, memory_stores=[dbpath])
    assert len(result['actions']) == 1 and result['actions'][0]['action'] == 'zstd'
    assert run.canonical.exists() and run.public.exists()
    monkeypatch.setenv('ATTENTIONBENCH_MEMORY_STORES', str(tmp_path/'missing.sqlite'))
    assert storage.plan(run.root)['blocked'].startswith('Memory store unreadable')
    # Explicitly unreadable store produces no receipt nor deletion in a fresh run.
    (run.root/'run-compact.json').unlink()
    result = storage.compact(run.root)
    assert result['actions'] == [] and not (run.root/'run-compact.json').exists()


def test_protected_snapshot_external_alias_and_nonallowlist(run, tmp_path):
    obj = run.ws.private/'snapshots/objects'/hashlib.sha256(run.state).hexdigest()
    obj.parent.mkdir(parents=True)
    os.link(run.canonical, obj)
    write(run.root/'config.json', b' '* (storage.MIN_BYTES+1))
    os.link(run.physics, tmp_path/'outside.jsonl')
    assert storage.plan(run.root)['actions'] == []
    assert storage.compact(run.root)['status'] == 'complete'
    assert run.public.exists() and obj.exists()


@pytest.mark.parametrize('stage', ['representation', 'unlink', 'receipt'])
def test_crash_recovery(run, monkeypatch, stage):
    original_link, original_unlink, original_atomic = storage._publish_link, Path.unlink, frames.atomic
    crashed = False
    def link(source, target):
        nonlocal crashed
        original_link(source, target)
        if stage == 'representation' and not crashed:
            crashed = True; raise RuntimeError('simulated crash')
    def unlink(path, *a, **kw):
        nonlocal crashed
        original_unlink(path,*a,**kw)
        if stage == 'unlink' and path == run.canonical and not crashed:
            crashed = True; raise RuntimeError('simulated crash')
    def atomic(path, value):
        nonlocal crashed
        original_atomic(path,value)
        if stage == 'receipt' and path.name == 'run-compact.json' and any(a['status']=='complete' for a in value['actions']) and not crashed:
            crashed = True; raise RuntimeError('simulated crash')
    with monkeypatch.context() as m:
        m.setattr(storage, '_publish_link', link); m.setattr(Path, 'unlink', unlink); m.setattr(frames,'atomic',atomic)
        with pytest.raises(RuntimeError, match='simulated crash'):
            storage.compact(run.root)
    assert storage.read_bytes(run.public) == run.state
    assert storage.read_bytes(run.canonical) == run.state
    assert storage.compact(run.root)['status'] == 'complete'
    assert storage.restore(run.root)['status'] == 'restored'


def test_crash_bad_alternate_rebuilt_only_from_verified_raw(run, monkeypatch):
    original = storage._publish_link
    def crash(*args):
        original(*args); raise RuntimeError('crash')
    with monkeypatch.context() as m:
        m.setattr(storage, '_publish_link', crash)
        with pytest.raises(RuntimeError):storage.compact(run.root)
    side = Path(str(run.canonical)+'.derived')
    side.chmod(0o644); side.write_bytes(b'{}')
    assert storage.compact(run.root)['status'] == 'complete'
    assert storage.read_bytes(run.public) == run.state


def test_trash_keeps_originals_and_restore(run, tmp_path):
    result = storage.compact(run.root, trash_dir=tmp_path/'trash')
    trash = Path(result['trash_run'])
    assert (trash/run.public.relative_to(run.root)).read_bytes() == run.state
    assert storage.restore(run.root)['status'] == 'restored'


@pytest.mark.parametrize('kind', ['symlink', 'parent_symlink', 'writable_hardlink', 'fifo', 'directory'])
def test_secure_sidecar_refusal(tmp_path, kind):
    root = tmp_path/'root'; root.mkdir()
    raw = write(root/'file.json', b'[]\n'); side = pack(raw)
    if kind == 'symlink':
        side.rename(tmp_path/'data'); side.symlink_to(tmp_path/'data')
    elif kind == 'parent_symlink':
        root.rename(tmp_path/'real'); root.symlink_to(tmp_path/'real', target_is_directory=True)
    elif kind == 'writable_hardlink':
        os.link(side, root/'extra')
    elif kind == 'fifo':
        side.unlink();os.mkfifo(side)
    else:
        side.unlink();side.mkdir()
    with pytest.raises((ValueError, OSError)):
        read_beneath(root, 'file.json', shared=True)


def test_expanded_limit_and_shared_inode_mode(tmp_path):
    raw = write(tmp_path/'file.json', b' '*200000)
    side = pack(raw); side.chmod(0o444);os.link(side, tmp_path/'alias.zst')
    with pytest.raises(ValueError, match='oversize'):
        read_beneath(tmp_path, raw.name, 10000, shared=True)
    with pytest.raises(ValueError, match='oversize'):
        digest_beneath(tmp_path, raw.name, 10000, shared=True)
    with pytest.raises(ValueError, match='unsafe'):
        read_beneath(tmp_path, raw.name, 200000)
    assert len(read_beneath(tmp_path, raw.name, 200000, shared=True)) == 200000


def test_migrated_view_projection_diagnose_eval_dedupe_cleanup(run):
    from benchmarks.attention_harness import dev_agent_view, dev_agent_ui_projection, dev_agent_diagnose, dev_agent_eval
    from scripts.attentionbench import state_log_dedupe
    for name,value in [('trace.json',[]),('result.json',dict(status='completed')),('frames.json',[]),('diagnostics/planner.json',[])]:
        run.ws.publish('executions/e000001/'+name, value)
    run.ws.publish('executions/e000001/stdout.txt', b'')
    run.ws.publish('executions/e000001/stderr.txt', b'')
    run.ws.publish('task.json',{})
    run.ws.publish('versions/v0001/bundle.py',b'pass\n')
    storage.compact(run.root)
    for name in ('trace.json', 'diagnostics/planner.json', 'frames.json'):
        for base in (run.ws.canonical,run.ws.ws):pack(base/'executions/e000001'/name)
    cell = dict(dev_mode='agent', artifact_dir=str(run.root))
    public = dev_agent_view.artifact(cell, 'executions/e000001/state_log.json')[0]
    assert public == json.loads(run.state)
    assert dev_agent_ui_projection.public(cell,'executions/e000001/state_log.json') == public
    notes = []
    assert dev_agent_diagnose.read_json(run.public,[run.ws.ws],notes) == public and not notes
    eval_ws = dev_agent_eval.build_workspace(run.ws,dict(version_id='v0001', execution_id='e000001'))
    assert (eval_ws/'state_log.json').read_bytes() == run.state
    assert dev_agent_ui_projection.public(cell,'executions/e000001/trace.json') == []
    assert list(state_log_dedupe.groups(run.root)) == []
    assert frames.plan(run.root)['frames_deleted'] == 0
    # A restarted Workspace may link/stat canonical evidence during regenerate.
    run.ws.regenerate()
    assert run.canonical.read_bytes() == run.state


def test_migrated_free_view_sources(tmp_path, monkeypatch):
    from benchmarks.attention_harness import free_view, dev_agent_view
    from benchmarks.attention_harness.tests.test_free_view import make_cell, exporter, EID
    cell, episode = make_cell(tmp_path)
    for name in ('native-physics.jsonl', 'rpc-receipts.jsonl'):pack(episode/name)
    made = []
    monkeypatch.setattr(free_view, '_export', exporter(made));monkeypatch.setattr(free_view,'MIN_FREE_BYTES',0)
    path, _ = dev_agent_view.artifact(cell, f'free-view/{EID}/scene.json')
    assert path.is_file() and made == [episode]


def test_export_bundle_contains_original_names_and_bytes(tmp_path):
    from scripts.attentionbench.tests.test_evidence_bundle import fixture
    from scripts.attentionbench.export_evidence_bundle import export
    import tarfile
    run, memory, execution = fixture(tmp_path)
    pack(execution/'state_log.json'); pack(execution/'trace.json')
    result = export(run, memory, tmp_path/'out', load_local_secrets=False)
    with tarfile.open(result['asset']) as archive:
        names = archive.getnames()
        state = next(n for n in names if n.endswith('/state_log.json'))
        assert archive.extractfile(state).read() == b'{}'
        assert not any(n.endswith(('.zst','.derived')) for n in names)


def test_memory_prepare_rehydrates_and_registers_original_bytes(tmp_path, monkeypatch):
    from benchmarks.attention_harness.tests.agent_origin_fakes import build
    from benchmarks.attention_harness.tests.test_dev_v2_memory_import_evidence import attach, export
    f = build(tmp_path,monkeypatch)
    name = 'executions/e000001/state_log.json'
    ref = f.ws.publish(name, [])
    attach(f,[ref]); pack(f.ws.canonical/name); pack(f.ws.ws/name)
    result = export(f, f.root/'compacted-export')
    catalog = json.loads(Path(result['uri']).read_bytes())
    assert (f.ws.canonical/name).read_bytes() == encode([])
    assert catalog['files'][str((f.ws.canonical/name).relative_to(f.native.root))] == ref['sha256']
    f.service.register_dev_v2_catalog(catalog_id='rehydrated',catalog=catalog)


def test_cli_measure_and_cleanup_process_recognition(run, capsys):
    assert storage.main(['measure',str(run.root),'--json']) == 0
    assert json.loads(capsys.readouterr().out)[0]['bytes'] > 0
    assert frames._is_cleanup_process(['python','-m','benchmarks.attention_harness.run_storage','compact',str(run.root)])
    assert not frames._is_cleanup_process(['python','-c','benchmarks.attention_harness.run_storage'])


def test_free_view_export_jsonl_compacted_and_partial_line(tmp_path):
    from scripts.attentionbench.free_view_export import jsonl
    path=write(tmp_path/'rpc-receipts.jsonl',b'{"sequence":1}\n{"sequence":2}\n{"partial":')
    pack(path)
    assert jsonl(path)==[dict(sequence=1),dict(sequence=2)]


def test_resume_freeze_retains_snapshot_safety_and_logical_names(run):
    from benchmarks.attention_harness.dev_agent_replay import freeze_origin, provision_replay
    storage.compact(run.root)
    cut=freeze_origin(run.ws,'pre-submit-v1')
    manifest=json.loads(Path(cut['path']).read_bytes())
    assert 'executions/e000001/state_log.json' in manifest['files']
    assert not any(n.endswith(('.zst','.derived')) for n in manifest['files'])
    restored=provision_replay(cut,run.root/'replay')
    assert (restored.canonical/'executions/e000001/state_log.json').read_bytes()==run.state
    (run.ws.ws/'scratch').mkdir(exist_ok=True)
    os.mkfifo(run.ws.ws/'scratch/fifo')
    with pytest.raises(ValueError,match='unsafe_snapshot_file'):
        freeze_origin(run.ws,'pre-submit-v2')


def test_detached_cleanup_chains_compaction_and_sweep_recovers_pending(run, monkeypatch):
    monkeypatch.setattr(storage, '_backend', lambda:('cli',None))
    monkeypatch.setattr(frames.sys,'argv',['frame_cleanup',str(run.root),'0'])
    frames._main()
    assert json.loads((run.root/'run-compact.json').read_bytes())['status']=='complete'
    assert storage.read_bytes(run.public)==run.state
    assert not (run.root/'run-compact-pending.json').exists()
    # Recovery only queues explicit pending/in-progress work; completed legacy
    # frame receipts alone do not enroll old runs for automatic compaction.
    write(run.root/'run-compact-pending.json',dict(status='in_progress'))
    queued=[]
    monkeypatch.setattr(frames,'schedule',lambda p,**kw:queued.append(p))
    thread=frames.startup_sweep(run.root);thread.join(10)
    assert run.root in queued


def test_failed_compression_verification_keeps_all_raw_names(run, monkeypatch):
    def corrupt(source,target):target.write_bytes(b'not zstd')
    monkeypatch.setattr(storage,'_compress',corrupt)
    with pytest.raises(ValueError,match='decompression failed'):
        storage.compact(run.root)
    assert run.physics.exists() and run.public.read_bytes()==run.state
    assert json.loads((run.root/'run-compact.json').read_bytes())['status']=='in_progress'


def test_compaction_receipts_block_idle_and_abctl(run, monkeypatch):
    from remote_gateway.deploy import abctl
    write(run.root/'run-compact.json',dict(status='in_progress'))
    monkeypatch.setattr(abctl,'_processes',lambda:iter(()))
    cfg=dict(data=run.root,test_data=run.root,runs_roots=[run.root.parent.parent.parent])
    count,ids=abctl.cleanup_workers(cfg)
    assert count==1 and ids==['devexp-synthetic']
    script=Path(__file__).resolve().parents[3]/'scripts/attentionbench/idle-check.sh'
    done=subprocess.run(['bash',str(script),str(run.root.parent.parent.parent)],capture_output=True,text=True)
    assert done.returncode==1 and 'run-compact.json' in done.stdout


def test_nonidentical_state_uses_one_zstd_inode_for_all_hardlinks(run):
    run.canonical.chmod(0o644)
    run.canonical.write_bytes(run.state+b' ')
    run.canonical.chmod(0o444)
    result=storage.compact(run.root)
    assert all(a['action']=='zstd' for a in result['actions'])
    canonical=Path(str(run.canonical)+'.zst');public=Path(str(run.public)+'.zst')
    assert canonical.stat().st_ino==public.stat().st_ino
    assert storage.read_bytes(run.public)==run.state+b' '
    storage.restore(run.root)
    assert run.canonical.stat().st_ino==run.public.stat().st_ino


def test_secure_compressed_file_mutation_is_detected(tmp_path,monkeypatch):
    from contextlib import contextmanager
    raw=write(tmp_path/'file.json',b' '*200000);side=pack(raw)
    original=storage._decompress
    @contextmanager
    def changed(source):
        with original(source) as stream:
            class Reader:
                def read(self,n=-1):
                    data=stream.read(n)
                    side.chmod(0o400)
                    return data
            yield Reader()
    monkeypatch.setattr(storage,'_decompress',changed)
    with pytest.raises(ValueError,match='file_changed'):
        read_beneath(tmp_path,raw.name,200000)


def test_abrupt_process_exit_recovers_partial_compression_temp(run):
    import sys
    code='''import os, sys
from benchmarks.attention_harness import run_storage as storage
from pathlib import Path
def die(source,target):
    target.write_bytes(b"partial compressed frame")
    os._exit(23)
storage._compress=die
storage.compact(Path(sys.argv[1]))
'''
    done=subprocess.run([sys.executable,'-c',code,str(run.root)])
    assert done.returncode==23
    receipt=json.loads((run.root/'run-compact.json').read_bytes())
    pending=next(a for a in receipt['actions'] if a.get('temporary'))
    orphan=run.root/pending['temporary']
    assert orphan.exists() and run.physics.exists()
    assert storage.compact(run.root)['status']=='complete'
    assert not orphan.exists()
    assert storage.read_bytes(run.public)==run.state


def test_restore_raw_run_is_a_read_only_noop(run):
    before={str(p.relative_to(run.root)):(p.stat().st_ino,p.stat().st_size) for p in run.root.rglob('*')}
    assert storage.restore(run.root) is None
    assert {str(p.relative_to(run.root)):(p.stat().st_ino,p.stat().st_size) for p in run.root.rglob('*')}==before


def test_diagnose_and_export_reject_symlink_sidecars_and_raw_wins(tmp_path):
    from benchmarks.attention_harness.dev_agent_diagnose import read_json
    from scripts.attentionbench.export_evidence_bundle import safe_file
    raw=write(tmp_path/'state_log.json',b'[]\n');side=pack(raw)
    target=tmp_path/'actual.zst';side.rename(target);side.symlink_to(target)
    notes=[]
    assert read_json(raw,[tmp_path],notes) is None and notes
    assert not safe_file(raw)
    raw.write_bytes(b'[]\n');notes=[]
    assert read_json(raw,[tmp_path],notes)==[] and not notes
    assert safe_file(raw)


def test_cli_stream_may_be_closed_before_eof(tmp_path,monkeypatch):
    monkeypatch.setattr(storage,'_backend',lambda:('cli',None))
    raw=write(tmp_path/'log.jsonl',b'x'*2000000);pack(raw)
    with storage.open_binary(raw) as stream:
        assert stream.read(10)==b'x'*10
    lines=storage.iter_lines(raw)
    assert next(lines)== 'x'*2000000
    lines.close()



def test_v1_derivation_components_are_pinned_until_legacy_reader_is_preserved():
    import inspect
    from benchmarks.attention_harness.dev_agent_diagnostics import public_observation,safe_text,project,FORBIDDEN,FIELDS
    from benchmarks.attention_harness.dev_agent_eval import feedback
    material='\n'.join(inspect.getsource(f) for f in
                       (public_observation,safe_text,project,feedback,encode,storage.derive_state_log))
    material+=json.dumps(dict(forbidden=FORBIDDEN.pattern,flags=FORBIDDEN.flags,fields=sorted(FIELDS)),sort_keys=True)
    assert hashlib.sha256(material.encode()).hexdigest()=='34d8e528e234c3e5b706006549d4a06e2b8cffbef587e8fbd1dac6dc39bb47a0', (
        'state-log-v1 components changed: preserve the legacy derivation before adding a new version')
