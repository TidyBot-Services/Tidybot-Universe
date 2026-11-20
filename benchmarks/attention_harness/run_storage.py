"""Verified terminal-run log storage. Logical paths always name original bytes.

No required dependencies: python-zstandard, Python 3.14 compression.zstd, or the
zstd executable provide streaming decompression, in that order. Run mutation is
serialized with frame cleanup. Unknown path classes are never compacted.
"""
from contextlib import contextmanager
from collections import defaultdict
import argparse
import fnmatch
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import stat
import subprocess
import tempfile
import uuid

SCHEMA = 'attentionbench.run-compact.v1'
DERIVED_SCHEMA = 'attentionbench.derived-state-log.v1'
DERIVATION = 'state-log-v1'
MIN_BYTES = 1024 * 1024


def _backend():
    for name in ('zstandard', 'compression.zstd'):
        try:
            return name, importlib.import_module(name)
        except ImportError:
            pass
    return 'cli', None


@contextmanager
def _decompress(source):
    kind, module = _backend()
    if kind == 'zstandard':
        with module.ZstdDecompressor().stream_reader(source, closefd=False) as stream:
            with io.BufferedReader(stream) as buffered:
                yield buffered
    elif kind == 'compression.zstd':
        with module.ZstdFile(source, 'rb') as stream:
            yield stream
    else:
        # A real descriptor also works for dirfd-opened/no-follow inputs. Never
        # hand the subprocess a pathname which it could reopen through a link.
        with tempfile.TemporaryFile() as errors:
            proc = subprocess.Popen(['zstd', '-dc'], stdin=source, stdout=subprocess.PIPE, stderr=errors)
            try:
                yield proc.stdout
                proc.stdout.close()
                # Closing a valid stream early is a normal file operation;
                # its producer may be blocked writing the unread tail.
                if proc.wait() not in (0, -signal.SIGPIPE):
                    errors.seek(0)
                    raise ValueError('zstd decompression failed: '+errors.read(1024).decode(errors='replace'))
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
                proc.stdout.close()


def _side(path, suffix):
    return Path(str(path)+suffix)


@contextmanager
def open_binary(path):
    """Stream original bytes, preferring raw, then zstd, then verified derivation."""
    path = Path(path)
    try:
        raw = path.open('rb')
    except FileNotFoundError:
        raw = None
    if raw is not None:
        with raw:
            yield raw
        return
    try:
        compressed = _side(path, '.zst').open('rb')
    except FileNotFoundError:
        compressed = None
    if compressed is not None:
        with compressed, _decompress(compressed) as stream:
            yield stream
        return
    try:
        stub = _side(path, '.derived').read_bytes()
    except FileNotFoundError:
        raise FileNotFoundError(str(path)) from None
    with io.BytesIO(_derive(path, json.loads(stub))) as stream:
        yield stream


def read_bytes(path):
    with open_binary(path) as stream:
        return stream.read()


def read_text(path, encoding='utf-8', errors='strict'):
    return read_bytes(path).decode(encoding, errors)


def iter_lines(path, encoding='utf-8', errors='strict'):
    with open_binary(path) as binary:
        with io.TextIOWrapper(binary, encoding=encoding, errors=errors, newline='') as stream:
            yield from stream


def exists(path):
    path = Path(path)
    return any(p.is_file() for p in (path, _side(path, '.zst'), _side(path, '.derived')))


def logical_size(path):
    path = Path(path)
    if path.is_file():
        return path.stat().st_size
    with open_binary(path) as stream:
        return sum(len(block) for block in iter(lambda: stream.read(65536), b''))


def logical_paths(root, pattern='*'):
    """Enumerate logical names once; include sidecars without exposing them as evidence."""
    root = Path(root)
    paths = set()
    for path in root.rglob('*'):
        if not path.is_file():
            continue
        if path.name.endswith(('.zst', '.derived')):
            original = Path(str(path).rsplit('.', 1)[0])
            if original.suffix in ('.json', '.jsonl'):
                path = original
        relative = path.relative_to(root)
        if '/' in pattern and '**' not in pattern:
            pieces = pattern.split('/')
            matches = len(relative.parts) == len(pieces) and all(
                fnmatch.fnmatchcase(part, match) for part, match in zip(relative.parts, pieces))
        else:
            matches = relative.match(pattern)
        if matches:
            paths.add(path)
    return sorted(paths)


def _cell(path):
    for parent in Path(path).parents:
        if parent.name == 'agent':
            return parent.parent
    raise ValueError('state log is outside an agent cell')


def derive_state_log(physics):
    """Pinned v1 broker bytes. The golden fixture locks all three projections."""
    from .dev_agent_diagnostics import public_observation
    from .dev_agent_eval import feedback
    from .dev_agent_workspace import encode
    return encode(feedback([public_observation(json.loads(line)) for line in physics.splitlines()]))


def _physics_path(path, stub):
    if path.name != 'state_log.json' or stub.get('schema') != DERIVED_SCHEMA or stub.get('derivation') != DERIVATION:
        raise ValueError('unsupported state-log derivation')
    root = _cell(path)
    name = stub.get('physics', '')
    # Only the same cell's native physics journal can be a derivation source.
    if not re.fullmatch(r'agent/private/executions/e\d+/[^/]+/native-physics\.jsonl', name):
        raise ValueError('unsafe derived physics path')
    execution = re.search(r'/executions/(e\d+)/', path.as_posix())
    if execution:
        eid = execution[1]
    else:
        evaluation = re.search(r'/eval/(v\d+)/ws/state_log\.json$', path.as_posix())
        if evaluation is None:
            raise ValueError('derived state log has no execution binding')
        from .dev_agent_workspace import read_beneath as read_public
        record = json.loads(read_public(root/'agent/private/public',
                                        'versions/'+evaluation[1]+'/submission.json'))
        eid = record['execution_id']
    if name.split('/')[3] != eid:
        raise ValueError('derived execution mismatch')
    return root, name


def _verify(data, sha, size=None):
    if (size is not None and len(data) != size) or hashlib.sha256(data).hexdigest() != sha:
        raise ValueError('run storage sha256/length mismatch')
    return data


def _derive(path, stub, *, secure=False, limit=None):
    root, name = _physics_path(path, stub)
    if secure:
        # The source is private but still must pass the exact no-follow/inode
        # checks. Bound work as well as the final expanded public file.
        from .dev_agent_workspace import PUBLIC_EVIDENCE_LIMIT
        physics = read_beneath(root, name, PUBLIC_EVIDENCE_LIMIT, shared=True)
    else:
        from .frame_cleanup import safe
        physics = read_bytes(safe(root/name))
    _verify(physics, stub['physics_sha256'])
    data = derive_state_log(physics)
    if limit is not None and len(data) > limit:
        raise ValueError('file_changed_or_oversize')
    return _verify(data, stub['sha256'], stub['bytes'])


@contextmanager
def _safe_source(root, name, *, shared=False):
    from .dev_agent_workspace import parts, directory
    components = parts(name)
    parent = directory(root, components[:-1])
    try:
        fd = os.open(components[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or (before.st_nlink != 1 and not (shared and not before.st_mode & 0o222)):
            raise ValueError('unsafe_file')
        yield stream, before
        after = os.fstat(stream.fileno())
        if (_signature(before) != _signature(after) or
                (after.st_nlink != 1 and not (shared and not after.st_mode & 0o222))):
            raise ValueError('file_changed_or_oversize')


@contextmanager
def open_beneath(root, name, limit, *, shared=False):
    """Fallback for missing raw files; same no-follow/shared rules for sidecars.

    Caller retains its existing raw path implementation. A missing *parent* is
    also an error; no resolve(), symlink traversal or unchecked subprocess path.
    Every expanded byte is bounded, including digest-only reads.
    """
    try:
        with _safe_source(root, name+'.zst', shared=shared) as (source, before):
            with _decompress(source) as stream:
                yield _Limited(stream, limit, name)
            return
    except FileNotFoundError:
        pass
    try:
        with _safe_source(root, name+'.derived', shared=shared) as (source, before):
            if before.st_size > 16384:
                raise ValueError('unsafe_file')
            data = source.read(16385)
            if len(data) != before.st_size:
                raise ValueError('file_changed_or_oversize')
            stub = json.loads(data)
            if type(stub.get('bytes')) is not int or not 0 <= stub['bytes'] <= limit:
                raise ValueError('file_changed_or_oversize')
            data = _derive(Path(root)/name, stub, secure=True, limit=limit)
    except FileNotFoundError:
        raise FileNotFoundError(str(Path(root)/name)) from None
    with io.BytesIO(data) as stream:
        yield stream


class _Limited:
    def __init__(self, stream, limit, name):
        self.stream, self.limit, self.name, self.size = stream, limit, name, 0

    def read(self, size=-1):
        remaining = self.limit + 1 - self.size
        data = self.stream.read(remaining if size < 0 else min(size, remaining))
        self.size += len(data)
        if self.size > self.limit:
            raise ValueError(f'oversize_file:{self.name}:{self.size}')
        return data


def read_beneath(root, name, limit, *, shared=False):
    # This entry point also supports raw sources (not just fallbacks).
    try:
        with _safe_source(root, name, shared=shared) as (stream, before):
            if before.st_size > limit:
                raise ValueError('unsafe_file')
            data = stream.read(limit+1)
            if len(data) != before.st_size or len(data) > limit:
                raise ValueError('file_changed_or_oversize')
            return data
    except FileNotFoundError:
        with open_beneath(root, name, limit, shared=shared) as stream:
            return stream.read()


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode)


def _allowed(rel):
    text = rel.as_posix()
    if any(p.startswith('.') for p in rel.parts) or 'snapshots' in rel.parts:
        return False
    if rel.name in {'manifest.json', 'identity-lock.json', 'config.json', 'run-compact.json'} or 'lock' in rel.name or rel.name.startswith('frame-cleanup'):
        return False
    public = r'agent/(?:ws|private/public)/executions/e\d+/'
    evaluation = r'agent/private/eval/v\d+/ws/'
    if re.search(r'(?:^|/)(?:'+public+'|'+evaluation+r')(?:state_log\.json|trace\.json|frames\.json|diagnostics/planner\.json)$', text):
        return True
    # Both one-call and agent episode paths end in executions/<episode>/... .
    return bool(re.search(r'(?:^|/)executions/(?:e\d+/)?[^/]+/(?:native-(?:physics|actions)\.jsonl|rpc-receipts\.jsonl|planner/(?:planner_http\.jsonl|v2_client\.jsonl|world-\d+\.json))$', text))


def _memory_files(stores=None):
    if stores is None:
        stores = [p for p in os.environ.get('ATTENTIONBENCH_MEMORY_STORES', '').split(os.pathsep) if p]
    protected = set()
    for path in stores:
        path = Path(path).absolute()
        # mode=ro also reads committed WAL contents; immutable=1 would not.
        with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as db:
            for payload, in db.execute('SELECT payload FROM dev_v2_catalogs'):
                value = json.loads(payload)
                root = Path(value['root'])
                if not root.is_absolute() or not isinstance(value['files'], dict):
                    raise ValueError('invalid Memory catalog')
                for name in value['files']:
                    if Path(name).is_absolute() or '..' in Path(name).parts:
                        raise ValueError('invalid Memory catalog file')
                    protected.add(Path(os.path.abspath(root/name)))
                    protected.add((root/name).resolve())
    return protected


def _hash(path):
    h = hashlib.sha256(); size = 0
    with open_binary(path) as stream:
        for data in iter(lambda: stream.read(1024*1024), b''):
            h.update(data); size += len(data)
    return h.hexdigest(), size


def _derivation(path, sha, size):
    if path.name != 'state_log.json':
        return None
    try:
        cell = _cell(path)
        match = re.search(r'/executions/(e\d+)/', path.as_posix())
        if match:
            eid = match[1]
        else:
            # Eval workspace is a copy, not an execution directory. Its public
            # version evidence is the broker's explicit execution binding.
            vid = path.parent.parent.name
            record = json.loads(read_bytes(cell/'agent/private/public/versions'/vid/'submission.json'))
            eid = record['execution_id']
            if not re.fullmatch(r'e\d+', eid):
                return None
        candidates = [p/'native-physics.jsonl' for p in (cell/'agent/private/executions'/eid).iterdir()
                      if p.is_dir() and exists(p/'native-physics.jsonl')]
        if len(candidates) != 1:
            return None
        physics = candidates[0]
        raw = read_bytes(physics)
        if _hash_bytes(derive_state_log(raw)) != (sha, size):
            return None
        return dict(schema=DERIVED_SCHEMA, derivation=DERIVATION, sha256=sha, bytes=size,
                    physics=str(physics.relative_to(cell)), physics_sha256=hashlib.sha256(raw).hexdigest())
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return None


def _hash_bytes(data):
    return hashlib.sha256(data).hexdigest(), len(data)


def plan(run_dir, *, memory_stores=None):
    from . import frame_cleanup as frames
    root = frames.safe(run_dir)
    frames._guard(root)
    try:
        protected = _memory_files(memory_stores)
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        return dict(schema=SCHEMA, run=str(root), actions=[], blocked='Memory store unreadable: '+str(exc), bytes=0)
    groups = defaultdict(list)
    for path in frames._scan(root):
        info = path.stat()
        groups[(info.st_dev, info.st_ino)].append(path)
    actions = []
    for paths in groups.values():
        info = paths[0].stat()
        if (info.st_size <= MIN_BYTES or len(paths) != info.st_nlink or
                any(not _allowed(p.relative_to(root)) or p in protected for p in paths)):
            continue
        sha, size = _hash(paths[0])
        stub = _derivation(paths[0], sha, size)
        if stub and any(_cell(p) != _cell(paths[0]) or _derivation(p, sha, size) != stub for p in paths):
            stub = None
        actions.append(dict(paths=[str(p.relative_to(root)) for p in sorted(paths)], bytes=size,
                            sha256=sha, mode=stat.S_IMODE(info.st_mode),
                            action='derive' if stub else 'zstd', derived=stub,
                            status='pending'))
    return dict(schema=SCHEMA, run=str(root), actions=actions, bytes=sum(a['bytes'] for a in actions))


def _sync(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _compress(source, target):
    kind, module = _backend()
    with source.open('rb') as raw, target.open('xb') as out:
        if kind == 'zstandard':
            module.ZstdCompressor(level=3).copy_stream(raw, out)
        elif kind == 'compression.zstd':
            with module.ZstdFile(out, 'wb', level=3) as stream:
                shutil.copyfileobj(raw, stream, 1024*1024)
        else:
            subprocess.run(['zstd', '-3', '-c'], stdin=raw, stdout=out, stderr=subprocess.PIPE, check=True)
        out.flush(); os.fsync(out.fileno())


def _verified_representation(path, action):
    if action['action'] == 'derive':
        data = _derive(Path(str(path)[:-8]), json.loads(path.read_bytes()))
        result = _hash_bytes(data)
    else:
        h = hashlib.sha256(); size = 0
        with path.open('rb') as source, _decompress(source) as stream:
            for data in iter(lambda: stream.read(1024*1024), b''):
                h.update(data); size += len(data)
        result = h.hexdigest(), size
    if result != (action['sha256'], action['bytes']):
        raise ValueError('compaction verification mismatch: '+str(path))


def _publish_link(source, target):
    from .frame_cleanup import safe
    # Deterministic per destination, so a crash before replace cannot leave an
    # unaccounted extra hardlink. Only an identical orphan may be removed.
    temporary = safe(target.with_name('.compact-link-'+hashlib.sha256(target.name.encode()).hexdigest()))
    if temporary.exists():
        if _hash(temporary) != _hash(source):
            raise ValueError('stale compaction link differs from source')
        temporary.unlink()
    try:
        os.link(source, temporary, follow_symlinks=False)
        os.replace(temporary, target)
        _sync(target.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _discard_temporary(root, action):
    from .frame_cleanup import _beneath
    name = action.get('temporary')
    if name is None:
        return
    path = _beneath(root, name)
    parents = {(root/n).parent for n in action['paths']}
    if path.parent not in parents or not re.fullmatch(r'\.compact-[0-9a-f]{32}\.zst', path.name):
        raise ValueError('invalid compaction temporary path')
    path.unlink(missing_ok=True)
    action.pop('temporary', None)


def compact(run_dir, *, trash_dir=None, memory_stores=None):
    from . import frame_cleanup as frames
    root = frames.safe(run_dir)
    with frames.run_lock(root):
        frames._guard(root)
        receipt = root/'run-compact.json'
        value = frames.load(receipt) if receipt.exists() else None
        if value and (value.get('schema') != SCHEMA or value.get('run') != str(root)):
            raise ValueError('invalid compaction receipt')
        if value and value['status'] == 'complete':
            return value
        if value is None or value.get('status') == 'restored':
            value = plan(root, memory_stores=memory_stores)
            if value.get('blocked'):
                return value
            value['status'] = 'in_progress'
            if trash_dir is not None:
                trash = frames.safe(Path(trash_dir)/root.name)
                if trash.is_relative_to(root) or root.is_relative_to(trash):
                    raise ValueError('trash and run must be disjoint')
                trash.mkdir(parents=True, exist_ok=True)
                if trash.stat().st_dev != root.stat().st_dev:
                    raise ValueError('trash must be on same filesystem')
                value['trash_run'] = str(trash)
            frames.atomic(receipt, value)
        # A newly registered catalog must protect a pending/recovered operation,
        # too. Failure to read any configured store blocks *all* mutation.
        try:
            protected = _memory_files(memory_stores)
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
            return dict(value, blocked='Memory store unreadable: '+str(exc))
        for action in value['actions']:
            if action['status'] == 'complete':
                continue
            paths = [frames._beneath(root, name) for name in action['paths']]
            if any(not _allowed(p.relative_to(root)) for p in paths):
                raise ValueError('receipt contains non-allowlisted path')
            if any(p in protected for p in paths):
                # Catalogs always require raw bytes, even if registration raced
                # an interrupted compaction. Rehydrate before skipping.
                _restore_action(root, action)
                action['status'] = 'protected'
                frames.atomic(receipt, value)
                continue
            suffix = '.derived' if action['action'] == 'derive' else '.zst'
            representations = [frames.safe(_side(p, suffix)) for p in paths]
            source = next((p for p in representations if p.is_file()), None)
            if source is not None:
                try:
                    _verified_representation(source, action)
                except (OSError, ValueError):
                    # Rebuild from verified raw without removing any published
                    # alternate first: some aliases may already have no raw.
                    raw = next((p for p in paths if p.is_file()), None)
                    if raw is None or _hash(raw) != (action['sha256'], action['bytes']):
                        raise
                    source = None
                else:
                    _discard_temporary(root, action)
            if source is None:
                raw = next((p for p in paths if p.is_file()), None)
                if raw is None:
                    raise ValueError('missing raw and compacted evidence')
                if _hash(raw) != (action['sha256'], action['bytes']):
                    raise ValueError('raw evidence changed before compaction')
                _discard_temporary(root, action)
                source = representations[paths.index(raw)]
                temporary = source.with_name('.compact-'+uuid.uuid4().hex+'.zst')
                action['temporary'] = str(temporary.relative_to(root))
                frames.atomic(receipt, value)
                try:
                    if suffix == '.derived':
                        frames.atomic(temporary, action['derived'])
                    else:
                        _compress(raw, temporary)
                    # Derivations need their original logical path, rather than
                    # the random temporary name used for durable publication.
                    if suffix == '.derived':
                        _verify(_derive(raw, action['derived']), action['sha256'], action['bytes'])
                    else:
                        _verified_representation(temporary, action)
                    os.chmod(temporary, action['mode'])
                    os.replace(temporary, source); _sync(source.parent)
                finally:
                    _discard_temporary(root, action)
            # Publish *all* alternate names durably before unlinking any raw
            # alias. A crash at any point leaves a readable logical path.
            for target in representations:
                if target != source:
                    _publish_link(source, target)
            frames._guard(root)
            for path in paths:
                if not path.exists():
                    continue
                if _hash(path) != (action['sha256'], action['bytes']):
                    raise ValueError('raw evidence changed before removal')
                if value.get('trash_run'):
                    target = frames._beneath(Path(value['trash_run']), str(path.relative_to(root)))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.exists():
                        raise ValueError('trash target already exists')
                    os.replace(path, target); _sync(target.parent)
                else:
                    path.unlink()
                _sync(path.parent)
            action['status'] = 'complete'
            frames.atomic(receipt, value)
        value['status'] = 'complete'
        frames.atomic(receipt, value)
        return value


def _restore_action(root, action):
    from . import frame_cleanup as frames
    paths = [frames._beneath(root, n) for n in action['paths']]
    if any(not _allowed(p.relative_to(root)) for p in paths):
        raise ValueError('receipt contains non-allowlisted path')
    source = next((p for p in paths if p.is_file()), None)
    if source is None:
        source = next((p for p in paths if exists(p)), None)
        if source is None:
            raise FileNotFoundError(str(paths[0]))
        data = read_bytes(source)
        _verify(data, action['sha256'], action['bytes'])
        from .dev_agent_workspace import atomic_write
        atomic_write(root, str(source.relative_to(root)), data, action['mode'])
    if _hash(source) != (action['sha256'], action['bytes']):
        raise ValueError('restore sha256 mismatch')
    for path in paths:
        if path != source:
            if path.exists() and _hash(path) != (action['sha256'], action['bytes']):
                raise ValueError('restore raw evidence drift')
            _publish_link(source, path)
    for path in paths:
        for suffix in ('.zst', '.derived'):
            side = frames.safe(_side(path, suffix))
            side.unlink(missing_ok=True)
        _sync(path.parent)
    _discard_temporary(root, action)


def restore(run_dir):
    """Rehydrate originals, including interrupted transactions, without a terminal gate.

    Needed before resuming a run or registering raw-byte Memory evidence. The
    same run lock prevents a cleanup/compaction transaction from interleaving.
    """
    from . import frame_cleanup as frames
    root = frames.safe(run_dir)
    receipt = frames.safe(root/'run-compact.json')
    if not receipt.is_file():
        return None
    with frames.run_lock(root):
        value = frames.load(receipt)
        if value:
            if value.get('schema') != SCHEMA or value.get('run') != str(root):
                raise ValueError('invalid compaction receipt')
            # Derived logs must be expanded before physics sidecars are removed.
            for action in sorted(value['actions'], key=lambda a: a['action'] != 'derive'):
                _restore_action(root, action)
            value['status'] = 'restored'
            frames.atomic(receipt, value)
        return value


def rehydrate(path):
    """Materialize one logical file for an existing raw-path consumer (e.g. Memory)."""
    from .frame_cleanup import safe
    from .dev_agent_workspace import atomic_write
    path = safe(path)
    if path.exists():
        return path
    for suffix in ('.zst', '.derived'):
        side = safe(_side(path, suffix))
        if side.is_file():
            data = read_bytes(path)
            atomic_write(path.parent, path.name, data, stat.S_IMODE(side.stat().st_mode))
            return path
    raise FileNotFoundError(str(path))


def measure(run_dir):
    from .frame_cleanup import safe, _scan
    root = safe(run_dir); seen = set()
    classes = dict.fromkeys(('state_log*.json', 'other .json', 'images', 'native-physics.jsonl',
                             'other .jsonl', 'video', 'archives', 'snapshot objects', 'misc'), 0)
    for path in _scan(root):
        info = path.stat(); inode = info.st_dev, info.st_ino
        if inode in seen:
            continue
        seen.add(inode)
        name = path.name
        if name.endswith(('.zst', '.derived')):
            name = name.rsplit('.', 1)[0]
        suffix = Path(name).suffix.lower()
        if 'snapshots' in path.parts and path.parent.name == 'objects': key = 'snapshot objects'
        elif name.startswith('state_log') and suffix == '.json': key = 'state_log*.json'
        elif name == 'native-physics.jsonl': key = 'native-physics.jsonl'
        elif suffix == '.json': key = 'other .json'
        elif suffix == '.jsonl': key = 'other .jsonl'
        elif suffix in ('.jpg', '.jpeg', '.png'): key = 'images'
        elif suffix in ('.mp4', '.webm'): key = 'video'
        elif suffix in ('.zip', '.gz', '.tar', '.xz', '.bz2', '.zst'): key = 'archives'
        else: key = 'misc'
        classes[key] += info.st_blocks * 512
    return dict(run=str(root), classes=classes, bytes=sum(classes.values()))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('plan', 'compact', 'restore', 'measure'))
    parser.add_argument('runs', nargs='+', type=Path)
    parser.add_argument('--trash', type=Path)
    parser.add_argument('--memory-store', action='append', type=Path)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    rows = []
    for root in args.runs:
        try:
            if args.command == 'compact': value = compact(root, trash_dir=args.trash, memory_stores=args.memory_store)
            elif args.command == 'plan': value = plan(root, memory_stores=args.memory_store)
            elif args.command == 'restore': value = restore(root)
            else: value = measure(root)
            rows.append(value)
        except Exception as exc:
            rows.append(dict(run=str(root), error=f'{type(exc).__name__}: {exc}'))
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for row in rows:
            print(json.dumps(row, indent=2))
    return 1 if any(row and ('error' in row or 'blocked' in row) for row in rows) else 0


if __name__ == '__main__':
    raise SystemExit(main())
