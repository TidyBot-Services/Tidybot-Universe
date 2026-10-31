"""Bounded public pinning; all exports and registrations are offline copies."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time

import pytest

from benchmarks.attention_harness import dev_agent_workspace as workspace
from benchmarks.attention_harness import dev_v2_memory_import as importer
from benchmarks.attention_harness.core.dev_store import DevConflict
from benchmarks.attention_harness.dev_ledger import encoded
from benchmarks.attention_harness.tests.agent_origin_fakes import build


@pytest.mark.parametrize('mib', [40, 200])
def test_large_digest_is_chunked(tmp_path, monkeypatch, mib):
    path = tmp_path/'large.bin'
    size = mib*1024*1024
    with path.open('wb') as stream:
        stream.truncate(size)
    expected = hashlib.sha256()
    for _ in range(size//65536):
        expected.update(bytes(65536))
    original = os.read
    calls = []
    def read(fd, count):
        assert 0 < count <= 65536
        calls.append(count)
        return original(fd, count)
    monkeypatch.setattr(os, 'read', read)
    assert workspace.digest_beneath(tmp_path, path.name) == expected.hexdigest()
    assert len(calls) >= size//65536


def test_oversize_is_diagnosable_without_reading(tmp_path, monkeypatch):
    path = tmp_path/'large.bin'
    size = workspace.PUBLIC_EVIDENCE_LIMIT+1
    with path.open('wb') as stream:
        stream.truncate(size)
    monkeypatch.setattr(os, 'read', lambda *a: pytest.fail('oversize file was read'))
    with pytest.raises(ValueError, match=f'^oversize_file:large.bin:{size}$'):
        workspace.digest_beneath(tmp_path, path.name)


def test_exact_bound_and_old_reader_unchanged(tmp_path):
    (tmp_path/'file').write_bytes(b'x'*65536)
    assert workspace.digest_beneath(tmp_path, 'file', 65536) == hashlib.sha256(b'x'*65536).hexdigest()
    with pytest.raises(ValueError, match='^unsafe_file$'):
        workspace.read_beneath(tmp_path, 'file', 65535)


@pytest.mark.parametrize('kind', ['symlink', 'parent_symlink', 'hardlink', 'directory', 'fifo'])
def test_unsafe_files_refused(tmp_path, kind):
    (tmp_path/'source').write_bytes(b'evidence')
    name = 'file'
    if kind == 'symlink':
        (tmp_path/name).symlink_to('source')
    elif kind == 'parent_symlink':
        (tmp_path/'real').mkdir()
        (tmp_path/'real/file').write_bytes(b'evidence')
        (tmp_path/'link').symlink_to('real', target_is_directory=True)
        name = 'link/file'
    elif kind == 'hardlink':
        os.link(tmp_path/'source', tmp_path/name)
    elif kind == 'directory':
        (tmp_path/name).mkdir()
    else:
        os.mkfifo(tmp_path/name)
    with pytest.raises((OSError, ValueError)):
        workspace.digest_beneath(tmp_path, name, shared=True)


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'a//b', 'a/./b'])
def test_invalid_paths_refused(tmp_path, name):
    with pytest.raises(ValueError, match='invalid_path'):
        workspace.digest_beneath(tmp_path, name)


def test_shared_readonly_frame_allowed(tmp_path):
    (tmp_path/'frame.png').write_bytes(b'frame')
    (tmp_path/'frame.png').chmod(0o444)
    os.link(tmp_path/'frame.png', tmp_path/'copy.png')
    assert workspace.digest_beneath(tmp_path, 'copy.png', shared=True) == hashlib.sha256(b'frame').hexdigest()
    with pytest.raises(ValueError, match='unsafe_file'):
        workspace.digest_beneath(tmp_path, 'copy.png')


@pytest.mark.parametrize('change', ['overwrite', 'grow', 'shrink', 'link', 'chmod'])
def test_changes_during_hashing_refused(tmp_path, monkeypatch, change):
    path = tmp_path/'file'
    path.write_bytes(b'x'*131072)
    if change == 'chmod':
        path.chmod(0o444)
        os.link(path, tmp_path/'linked')
    before = path.stat()
    original = os.read
    changed = False
    def read(fd, count):
        nonlocal changed
        chunk = original(fd, count)
        if not changed:
            changed = True
            # Filesystem timestamp resolution must distinguish this mutation
            # from the initial write even when mtime is restored below.
            time.sleep(.02)
            if change in ('overwrite', 'grow', 'shrink'):
                with path.open('r+b') as stream:
                    if change == 'overwrite':stream.write(b'y')
                    else:stream.truncate(before.st_size + (1 if change == 'grow' else -1))
                # A restored mtime must still fail the ctime/size checks.
                os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            elif change == 'link':os.link(path, tmp_path/'linked')
            else:path.chmod(0o644)
        return chunk
    monkeypatch.setattr(os, 'read', read)
    with pytest.raises(ValueError, match='file_changed_or_oversize'):
        workspace.digest_beneath(tmp_path, 'file', shared=True)


def attach(f, refs):
    """Add sealed public refs to a settled receipt, as exported version metadata does."""
    with f.store.tx() as db:
        jid, raw = db.execute('SELECT id,payload FROM v2_jobs WHERE version=1').fetchone()
        job = json.loads(raw)
        job['receipt']['public_evidence'] = refs
        db.execute('UPDATE v2_jobs SET payload=? WHERE id=?', (encoded(job), jid))


def export(f, output, prepare=importer.prepare):
    return prepare(f.store, 'cell', 1, f.context['namespace'], output, f.native.root,
                   import_purpose='memory_origin', motion_permission='dev_tier_sim_motion')


@pytest.mark.parametrize('mib', [40, 200])
def test_pin_does_not_buffer_state_and_keeps_metadata_refs(tmp_path, monkeypatch, mib):
    f = build(tmp_path, monkeypatch)
    name = 'executions/e000001/state_log.json'
    path = f.ws.canonical/name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb') as stream:
        stream.write(b'[]')
        for _ in range(mib):stream.write(b' '*(1024*1024))
    digest = workspace.digest_beneath(f.ws.canonical, name)
    ref = f.ws.publish('executions/e000001/evidence.json', dict(state=dict(path=name, sha256=digest)))
    attach(f, [ref])
    original = Path.read_bytes
    def read(path):
        if path == f.ws.canonical/name:pytest.fail('state log was buffered')
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', read)
    result = export(f, f.root/'export')
    catalog = json.loads(Path(result['uri']).read_bytes())
    relative = str(path.relative_to(f.native.root))
    assert catalog['files'][relative] == digest
    assert str((f.ws.canonical/ref['path']).relative_to(f.native.root)) in catalog['files']
    f.service.register_dev_v2_catalog(catalog_id='large-public', catalog=catalog)


def test_pin_digest_drift_refused(tmp_path, monkeypatch):
    f = build(tmp_path, monkeypatch)
    ref = f.ws.publish('executions/e000001/state_log.json', [])
    attach(f, [dict(ref, sha256='0'*64)])
    with pytest.raises(DevConflict, match='agent public evidence digest drift'):
        export(f, f.root/'bad')


def test_pin_oversize_diagnostic(tmp_path, monkeypatch):
    f = build(tmp_path, monkeypatch)
    name = 'executions/e000001/state_log.json'
    path = f.ws.canonical/name
    path.parent.mkdir(parents=True, exist_ok=True)
    size = workspace.PUBLIC_EVIDENCE_LIMIT+1
    with path.open('wb') as stream:stream.truncate(size)
    attach(f, [dict(path=name, sha256='0'*64)])
    with pytest.raises(ValueError, match=f'^oversize_file:{name}:{size}$'):
        export(f, f.root/'oversize')
    assert not (f.root/'oversize/catalog.json').exists()


def test_real_origin_public_evidence_before_after(tmp_path, monkeypatch):
    original = Path('/home/truares/Documents/Codex/2026-10-14/goal-attentionbench-robocasa365-dev-v2-home/outputs/daily-experiments/devexp-dc15f5ae1ddfba76727ce318')
    if not (original/'ledger.sqlite3').exists():pytest.skip('reported origin run unavailable')
    with sqlite3.connect((original/'ledger.sqlite3').as_uri()+'?mode=ro', uri=True) as db:
        cell = json.loads(db.execute('SELECT payload FROM v2_cells').fetchone()[0])
    base = Path(cell['artifact_dir'])
    f = build(tmp_path, monkeypatch)
    refs = {}; visited = set()
    def walk(value):
        if isinstance(value, dict):
            if 'sha256' in value and ('uri' in value or 'path' in value):
                name = value.get('uri', value.get('path'))
                path = Path(name)
                if not path.is_absolute():
                    refs[name] = value['sha256']
                    if path.suffix == '.json' and path.name != 'state_log.json' and path not in visited:
                        visited.add(path)
                        walk(json.loads((base/'agent/private/public'/path).read_bytes()))
                elif path.is_relative_to(base) and path.suffix == '.json' and path not in visited:
                    visited.add(path);walk(json.loads(path.read_bytes()))
            for item in value.values():walk(item)
        elif isinstance(value, list):
            for item in value:walk(item)
    for name in ('v2/version.json', 'v2/agent-help-evidence.json'):
        walk(json.loads((base/name).read_bytes()))
    assert 'executions/e000007/state_log.json' in refs
    for name, digest in refs.items():
        target = f.ws.canonical/name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(base/'agent/private/public'/name, target)
        target.chmod(0o444)
        assert workspace.digest_beneath(f.ws.canonical, name, shared=True) == digest
    attach(f, [dict(path=name, sha256=digest) for name, digest in refs.items()])
    code = subprocess.check_output(['git', 'show', '4fa2368:benchmarks/attention_harness/dev_v2_memory_import.py'], text=True)
    old = dict(__name__='benchmarks.attention_harness.old_import', __package__='benchmarks.attention_harness')
    exec(compile(code, '<base-import>', 'exec'), old)
    with pytest.raises(ValueError, match='^unsafe_file$'):
        export(f, f.root/'before', old['prepare'])
    result = export(f, f.root/'after')
    catalog = json.loads(Path(result['uri']).read_bytes())
    for name, digest in refs.items():
        assert catalog['files'][str((f.ws.canonical/name).relative_to(f.native.root))] == digest
    f.service.register_dev_v2_catalog(catalog_id='copied-real-public', catalog=catalog)
    print(f'REAL_PUBLIC_COPY: {len(refs)} exact-digest public files; base unsafe_file; fixed export and offline catalog registration passed')
