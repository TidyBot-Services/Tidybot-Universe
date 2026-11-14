"""Conservative, host-independent terminal-run frame cleanup.

Planning never mutates a run. Only enumerated RGB producers are disposable;
unknown producers, missing manifests and undecodable videos retain their frames.
"""
from collections import defaultdict
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import time
import threading
import uuid
from contextlib import contextmanager

VERSION = '1'
SCHEMA = 'attentionbench.frame-cleanup.v1'
CLEANED = 'cleaned up (video kept)'
TERMINAL = {'completed', 'failed', 'cancelled', 'needs_review', 'not_started', 'condition_invalid', 'stopped'}
IMAGES = {'.png', '.jpg', '.jpeg'}


class CleanedFrame(FileNotFoundError):
    """An explicitly removed resource; HTTP adapters return 410."""


def is_frame_image(name):
    """Same evidence boundary as attention_memory_service.dev_v2 (no service import)."""
    path = Path(name)
    if path.name in {'camera_initial.png', 'camera_final.png'}:
        return False
    parts = path.parts
    return (path.suffix.lower() in IMAGES and
            bool(set(parts) & {'frames', 'live', 'recording_frames', 'recording_views'}))


def safe(path):
    path = Path(os.path.abspath(path))
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('frame cleanup refuses symlink: '+str(path))
    return path


def load(path):
    return json.loads(safe(path).read_bytes())


def receipt_for(path):
    """Nearest cell receipt; never trust a receipt in an unrelated run."""
    path = safe(path)
    for parent in (path if path.is_dir() else path.parent, *path.parents):
        receipt = parent/'frame-cleanup.json'
        if receipt.is_file():
            value = load(receipt)
            if value.get('schema') == SCHEMA:
                return parent, value
        if parent.name == 'cells':
            break
    return None, None


def cleaned_files(root, receipt):
    if not receipt:return set()
    names = {n for group in receipt['views']
             for n in (*group.get('deleted_files', []), *group.get('already_missing', []))}
    if receipt.get('status') == 'in_progress':
        names.update(n for group in receipt['views'] for n in group['files'] if not (root/n).exists())
    return names


def was_cleaned(path):
    root, receipt = receipt_for(path)
    if receipt is None or not Path(path).is_relative_to(root):
        return False
    return str(Path(path).relative_to(root)) in cleaned_files(root, receipt)


def probe_count(path):
    safe(path)
    result = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-count_frames',
                             '-show_entries', 'stream=nb_read_frames', '-of', 'json', str(path)],
                            capture_output=True, text=True, check=True, timeout=120)
    if result.stderr.strip():
        raise ValueError('ffprobe decode error: '+result.stderr.strip()[-500:])
    return int(json.loads(result.stdout)['streams'][0]['nb_read_frames'])


def _scan(root):
    files = []
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in (*dirs, *names):
            p = Path(directory)/name
            if p.is_symlink():
                raise ValueError('frame cleanup refuses symlink: '+str(p))
        files.extend(Path(directory)/n for n in names if stat.S_ISREG((Path(directory)/n).lstat().st_mode))
    return sorted(files)


def _expected(episode, view):
    folder = episode/('recording_frames' if view == 'overview' else 'recording_views/'+view)
    journal = folder/'frames.jsonl'
    if view != 'overview' and (episode/'camera_views.json').is_file():
        entry = load(episode/'camera_views.json')['views'][view]
        if entry.get('status') != 'saved' or not entry.get('video') or int(entry['frame_count']) < 2:
            raise ValueError('no successful extra-view producer receipt')
        return int(entry['frame_count'])+1
    if journal.is_file():
        rows = [json.loads(line) for line in journal.read_text().splitlines() if line.strip()]
        if len(rows) < 2:
            raise ValueError('fewer than two producer frames')
        return len(rows)+1
    receipt = load(episode/('camera_recording.json' if view == 'overview' else 'camera_views.json'))
    if view != 'overview':
        receipt = receipt['views'][view]
    # Both StepRecorder.finalize_recording and AdvisorCameraRecorder.stop append
    # a final duplicate. Unknown receipts cannot authorize deletion.
    if not receipt.get('video_saved') or int(receipt['frame_count']) < 2:
        raise ValueError('no successful producer video receipt')
    return int(receipt['frame_count'])+1


def plan(run_dir):
    root = safe(run_dir)
    if not root.is_dir():
        raise ValueError('run directory missing')
    files = _scan(root)
    file_set = set(files)
    siblings = defaultdict(list)
    for p in files:
        if p.suffix.lower() in IMAGES:siblings[p.parent].append(p)
    groups = {}
    kept = set()
    frame_shas = defaultdict(set)
    protected_shas = set()
    def group(episode, view):
        key = (episode, view)
        if key not in groups:
            groups[key] = dict(execution=str(episode.relative_to(root)), view=view,
                               paths=set(), kept=set(), already_missing=set())
        return groups[key]
    def add(g, path, first=False):
        if first:
            kept.add(path); g['kept'].add(path)
        else:
            g['paths'].add(path)
    # Agent frames: canonical and ws use the same manifest; Eval names are
    # mapped through its frames.json. Snapshot object names have no suffix.
    manifests = [p for p in files if p.name == 'frames.json' and
                 ('agent' in p.parts) and ('public' in p.parts or 'ws' in p.parts)]
    for manifest in manifests:
        rows = load(manifest)
        if not isinstance(rows, list):
            continue
        base = manifest.parent
        agent = next((p for p in base.parents if p.name == 'agent'), None)
        if agent is None:
            continue
        eid = base.name if re.fullmatch(r'e\d+', base.name) else None
        if eid is None:
            # Eval ws is associated by matching frame hashes below.
            continue
        episodes = sorted((agent/'private/executions'/eid).glob('*/camera_recording.json'))
        episode = episodes[0].parent if len(episodes) == 1 else agent/'private/executions'/eid/'unknown-episode'
        first_by_view = {}
        for row in rows:
            view = row.get('view') or Path(row['filename']).name.split('-')[0]
            first_by_view.setdefault(view, row['filename'])
        for row in rows:
            view = row.get('view') or Path(row['filename']).name.split('-')[0]
            name = Path(row['filename'])
            if name.is_absolute() or '..' in name.parts or name.parts[:1] != ('frames',):
                raise ValueError('unsafe agent frame manifest')
            if not is_frame_image(name):
                continue
            g = group(episode, view)
            first = row['filename'] == first_by_view[view] or '-000000-' in name.name
            for copy in (agent/'ws/executions'/eid, agent/'private/public/executions'/eid):
                path = copy/name
                if path in file_set:
                    add(g, path, first)
                else:
                    g['already_missing'].add(path)
            sha = row.get('sha256')
            if sha:
                frame_shas[(agent, sha)].add((episode, view))
                if first:
                    protected_shas.add((agent, sha))
    # Never remove a snapshot object also referenced as non-frame content.
    for path in files:
        if 'snapshots' in path.parts and path.suffix == '.json':
            agent = next((p for p in path.parents if p.name == 'agent'), None)
            for name, entry in load(path).get('files', {}).items():
                if not is_frame_image(name):
                    protected_shas.add((agent, entry['sha256']))
                else:
                    obj = path.parent/'objects'/entry['sha256']
                    # Both the cut pointer and manifests/<sha>.json describe
                    # objects in snapshots/objects, never manifests/objects.
                    if path.parent.name == 'manifests':
                        obj = path.parent.parent/'objects'/entry['sha256']
                    safe(obj)
                    if obj not in file_set:
                        for owner in frame_shas.get((agent, entry['sha256']), ()):
                            group(*owner)['already_missing'].add(obj)
    # Journals remain after a prior prune even if every image is gone.
    for journal in (p for p in files if p.name == 'frames.jsonl'):
        folder = journal.parent
        if folder.name == 'recording_frames':
            episode, view = folder.parent, 'overview'
        elif folder.parent.name == 'recording_views':
            episode, view = folder.parent.parent, folder.name
        else:
            continue
        for line in journal.read_text().splitlines():
            if not line.strip(): continue
            name = Path(json.loads(line).get('file', ''))
            if name.is_absolute() or '..' in name.parts:
                raise ValueError('unsafe recording frame manifest')
            path = folder/name
            if is_frame_image(path.relative_to(root)) and path not in file_set:
                group(episode, view)['already_missing'].add(path)
    for path in files:
        rel = path.relative_to(root)
        if path.name in {'camera_initial.png', 'camera_final.png'} or set(rel.parts) & {'replay_trim', 'free_view_v1'}:
            continue
        if path.parent.name == 'objects' and path.parent.parent.name == 'snapshots':
            agent = path.parent.parent.parent.parent
            key = (agent, path.name)
            if key not in protected_shas:
                for owner in frame_shas.get(key, ()):
                    group(*owner)['paths'].add(path)
        elif 'eval' in rel.parts and path.parent.name == 'frames' and path.suffix.lower() in IMAGES:
            agent = next((p for p in path.parents if p.name == 'agent'), None)
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            for owner in frame_shas.get((agent, sha), ()):
                add(group(*owner), path, '-000000-' in path.name)
        elif path.suffix.lower() in IMAGES:
            # Raw episode RGB (including live exports), never arbitrary images.
            parents = path.parents
            marker = next((p for p in parents if p.name in {'recording_frames', 'recording_views', 'live'}), None)
            if marker is None:
                continue
            episode = marker.parent
            view = 'overview'
            if marker.name == 'recording_views':
                view = path.relative_to(marker).parts[0]
            elif marker.name == 'live':
                # Agent policy replay live files have no trustworthy per-view
                # producer mapping. Retain unless a local receipt identifies it.
                match = re.match(r'(?:views/)?(overview|external|side|top|left_wrist|right_wrist)[/-]', str(path.relative_to(marker)))
                if match: view = 'overview' if match[1] == 'external' else match[1]
            g = group(episode, view)
            same = siblings[path.parent]
            add(g, path, path == min(same) or path.name == '000000.jpg')
    result = []
    eligible = set()
    blocked = set()
    for (episode, view), g in sorted(groups.items()):
        video = episode/('advisor_replay.mp4' if view == 'overview' else f'advisor_replay_{view}.mp4')
        paths = g['paths']-kept
        nothing_to_delete = not paths and not g['already_missing']
        reason = None; expected = actual = None
        # Probe recordings often contain only a kept first frame and no video.
        # Gate only groups with deletable or previously missing frame evidence.
        if not nothing_to_delete:
            try:
                expected = _expected(episode, view)
                actual = probe_count(video)
                if actual != expected:
                    raise ValueError(f'mp4 count mismatch: expected {expected}, decoded {actual}')
            except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
                reason = f'{type(exc).__name__}: {exc}'
        if reason: blocked.update(paths)
        else: eligible.update(paths)
        result.append(dict(execution=g['execution'], view=view, mp4=str(video.relative_to(root)),
                           expected_count=expected, decoded_count=actual, skipped=reason,
                           files=sorted(str(p.relative_to(root)) for p in paths),
                           already_missing=sorted(str(p.relative_to(root)) for p in g['already_missing']),
                           kept_first_frames=sorted(str(p.relative_to(root)) for p in g['kept'])))
        if nothing_to_delete:
            result[-1]['nothing_to_delete'] = True
    eligible -= blocked
    inodes = defaultdict(list)
    for p in files:
        st = p.stat(); inodes[(st.st_dev, st.st_ino)].append(p)
    freed = {}; now = 0
    for paths in inodes.values():
        st = paths[0].stat(); now += st.st_size
        if len(paths) == st.st_nlink and all(p in eligible for p in paths):
            freed[min(paths)] = st.st_size
    for g in result:
        g['files'] = [n for n in g['files'] if root/n in eligible] if not g['skipped'] else []
        g['bytes_freed'] = sum(freed.pop(root/n, 0) for n in g['files'])
        g['delete_count'] = len(g['files'])
    return dict(schema=SCHEMA, tool_version=VERSION, run=str(root), views=result,
                bytes_now=now, bytes_freed=sum(g['bytes_freed'] for g in result),
                frames_deleted=len(eligible), kept_first_frames=sorted(str(p.relative_to(root)) for p in kept))


def state(run_dir):
    """Read the very same SQLite cell/experiment payloads used by the UI."""
    root = safe(run_dir)
    manifests = [p/'manifest.json' for p in (root, *root.parents) if (p/'manifest.json').is_file()]
    manifests += list(root.rglob('manifest.json'))
    for manifest in manifests:
        m = load(manifest)
        ledger = m.get('ledger_path')
        if not ledger:
            continue
        safe(ledger)
        with sqlite3.connect(Path(ledger).as_uri()+'?mode=ro', uri=True) as db:
            rows = [json.loads(p) for p, in db.execute('SELECT payload FROM v2_cells')]
            exps = {json.loads(p)['experiment_id']: json.loads(p) for p, in db.execute('SELECT payload FROM v2_experiments')}
        cells = [c for c in rows if c.get('artifact_dir') and
                 (Path(c['artifact_dir']) == root or Path(c['artifact_dir']).is_relative_to(root))]
        if not cells:
            continue
        paused = any(c.get('reason') == 'operator_paused' or c['state'] == 'paused' or
                     exps[c['experiment_id']].get('state') == 'paused' or
                     exps[c['experiment_id']].get('pause_requested_at') for c in cells)
        owners = [c.get('owner') for c in cells]+[exps[c['experiment_id']].get('owner') for c in cells]
        active = any(_owner_alive(o) for o in owners if o) or _cleanup_alive(root)
        tokens = {str(root), root.name, *(c['key'] for c in cells), *(c['experiment_id'] for c in cells)}
        for proc in Path('/proc').glob('[0-9]*/cmdline'):
            if int(proc.parent.name) == os.getpid(): continue
            try: command = proc.read_bytes().replace(b'\0', b' ').decode(errors='replace')
            except (FileNotFoundError, PermissionError, ProcessLookupError): continue
            if 'frame_cleanup' in command: continue
            if any(w in command for w in ('worker', 'scheduler', 'dev_experiment', 'dev_agent_loop')) and any(t in command for t in tokens):
                active = True
        return dict(terminal=all(c['state'] in TERMINAL for c in cells), paused=bool(paused), active=active,
                    mode='agent' if any((Path(c['artifact_dir'])/'agent').is_dir() for c in cells) else 'one-call')
    raise ValueError('no UI ledger state bound to this run')


def _cleanup_alive(root):
    for proc in Path('/proc').glob('[0-9]*/cmdline'):
        if int(proc.parent.name) == os.getpid(): continue
        try: args = proc.read_bytes().decode(errors='replace').split('\0')
        except (OSError, ProcessLookupError): continue
        for index, arg in enumerate(args[:-1]):
            if arg != 'benchmarks.attention_harness.frame_cleanup' and Path(arg).name != 'frame_cleanup.py':continue
            try:
                owned = Path(args[index+1])
                if not owned.is_absolute():owned = Path(os.readlink(proc.parent/'cwd'))/owned
                owned = Path(os.path.abspath(owned))
                if owned == root or owned.is_relative_to(root) or root.is_relative_to(owned):return True
            except OSError:continue
    return False


def _process_identity(pid):
    # Same Linux identity tuple used by dev_loop.process_identity; no runner or
    # service imports in this standalone tool.
    try:
        fields = Path(f'/proc/{int(pid)}/stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] == 'Z':return None
        return dict(pid=int(pid), start_ticks=fields[19],
                    boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    except (OSError, ValueError, IndexError):return None


def _owner_alive(owner):
    return _process_identity(owner['pid']) == owner if isinstance(owner, dict) and 'pid' in owner else True


def atomic(path, value):
    safe(path)
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    safe(temporary)
    try:
        with temporary.open('x') as stream:
            json.dump(value, stream, sort_keys=True, indent=2); stream.write('\n')
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:
        if temporary.exists():temporary.unlink()


@contextmanager
def run_lock(run_dir, name='frame-cleanup.lock'):
    root = safe(run_dir)
    path = safe(root/name)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('another frame_cleanup process owns this run: '+str(root)) from None
        yield
    finally:
        os.close(fd)


def _guard(root):
    status = state(root)
    if not status['terminal'] or status['paused'] or status['active']:
        raise ValueError('frame cleanup requires a terminal, unpaused run with no live worker/scheduler')


def _beneath(root, name):
    path = Path(name)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('unsafe cleanup path')
    return safe(root/path)


def apply(run_dir, *, trash_dir=None):
    root = safe(run_dir)
    with run_lock(root):
        return _apply(root, trash_dir=trash_dir)


def _apply(root, *, trash_dir=None):
    _guard(root)
    receipt_path = root/'frame-cleanup.json'
    result = load(receipt_path) if receipt_path.exists() else None
    resuming = result is not None
    if resuming and result.get('status') != 'in_progress':
        return result
    if not resuming:
        cells = sorted(p for p in root.rglob('cells/*') if p.is_dir() and p.parent.name == 'cells')
        if cells:
            nested_trash = Path(trash_dir)/root.name/'cells' if trash_dir is not None else None
            rows = []
            for cell in cells:
                try: rows.append(apply(cell, trash_dir=nested_trash))
                except Exception as exc: rows.append(dict(run=str(cell), status='refused: '+str(exc)))
            return dict(schema=SCHEMA, cells=rows)
        result = plan(root)
    destination = None
    if resuming:
        # Older trash receipts require the operator's original --trash-dir;
        # new receipts always carry the exact destination across restarts.
        saved = result.get('trash_run')
        if 'bytes_reclaimable' in result and not saved and trash_dir is None:
            raise ValueError('legacy trash receipt requires its original --trash-dir; refusing to unlink')
        if saved:
            destination = safe(saved)
        elif 'bytes_reclaimable' in result and trash_dir is not None:
            destination = safe(Path(trash_dir)/root.name)
        if destination and not (destination.is_dir() and (destination/'restore.json').is_file()):
            raise ValueError('cleanup trash run/restore.json is missing; refusing to unlink: '+str(destination))
        for g in result['views']:
            if g.get('skipped'): continue
            try:
                video = _beneath(root, g['mp4'])
                if not video.is_file(): raise ValueError('mp4 missing')
                expected = _expected(_beneath(root, g['execution']), g['view'])
                actual = probe_count(video)
                if actual != expected:
                    raise ValueError(f'mp4 count mismatch: expected {expected}, decoded {actual}')
                g.update(expected_count=expected, decoded_count=actual)
            except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
                g['skipped'] = f'{type(exc).__name__}: {exc}'
        result.setdefault('resumed_at', []).append(datetime.now(timezone.utc).isoformat())
    elif trash_dir is not None:
        destination = safe(trash_dir)
        if destination.is_relative_to(root) or root.is_relative_to(destination):
            raise ValueError('trash and run directories must be disjoint')
        destination.mkdir(parents=True, exist_ok=True)
        if destination.stat().st_dev != root.stat().st_dev:
            raise ValueError('trash must be on the same filesystem')
        destination = destination/root.name
        if destination.exists(): raise ValueError('trash run already exists')
        result['bytes_reclaimable'] = result['bytes_freed']
        for g in result['views']: g['bytes_reclaimable'] = g['bytes_freed']
    if destination:
        result['trash_run'] = str(destination)
        result['bytes_freed'] = 0
        for g in result['views']: g['bytes_freed'] = 0
    consumers = defaultdict(list)
    for g in result['views']:
        if not g.get('skipped'):
            for name in g['files']: consumers[name].append(g)
    names = sorted(consumers)
    blocked = {n for g in result['views'] if g.get('skipped') for n in g['files']}
    names = [n for n in names if n not in blocked]
    previous = {r['name']: r for r in result.get('file_records', [])}
    restore_value = load(destination/'restore.json') if destination and (destination/'restore.json').exists() else dict(schema=SCHEMA, run=str(root), files=[])
    if destination and restore_value['run'] != str(root): raise ValueError('trash belongs to another run')
    restore_records = {r['name']: r for r in restore_value['files']}
    records = []
    digests = {}
    for name in names:
        path = _beneath(root, name)
        if not path.exists():
            for g in consumers[name]:
                g.setdefault('already_missing', []).append(name)
            continue
        st = path.stat()
        if not stat.S_ISREG(st.st_mode): raise ValueError('frame is not a regular file')
        key = (st.st_dev, st.st_ino)
        if destination:
            _beneath(destination, name)
            if key not in digests:digests[key] = hashlib.sha256(path.read_bytes()).hexdigest()
        record = dict(name=name, bytes=st.st_size, device=st.st_dev, inode=st.st_ino,
                      mtime_ns=st.st_mtime_ns, nlink=st.st_nlink, sha256=digests.get(key))
        old = previous.get(name) or restore_records.get(name)
        if old and any(record[k] != old[k] for k in ('device','inode','bytes','mtime_ns')):
            raise ValueError('frame changed after planning')
        records.append(record)
        previous.setdefault(name, record)
        if destination:restore_records[name] = record
    _guard(root)
    if destination:
        destination.mkdir(parents=True, exist_ok=True)
        restore_value['files'] = list(restore_records.values())
        atomic(destination/'restore.json', restore_value)
    result.update(time=result.get('time', datetime.now(timezone.utc).isoformat()), status='in_progress', file_records=list(previous.values()))
    for g in result['views']:
        g.setdefault('deleted_files', [])
        g['already_missing'] = sorted(set(g.get('already_missing', [])))
    atomic(receipt_path, result)
    try:
        progress_at = time.monotonic()
        for index, record in enumerate(records):
            name = record['name']; path = _beneath(root, name); st = path.stat()
            if (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns) != (record['device'], record['inode'], record['bytes'], record['mtime_ns']):
                raise ValueError('frame changed after planning')
            if destination:
                target = _beneath(destination, name)
                if target.exists(): raise ValueError('trash would overwrite a file')
                target.parent.mkdir(parents=True, exist_ok=True)
                os.rename(path, target)
            else: path.unlink()
            for g in consumers[name]:g['deleted_files'].append(name)
            # Missing files are recoverable from intent after an abrupt exit.
            # Bound checkpoint frequency for receipts listing hundreds of thousands of files.
            if index == 0 or time.monotonic() - progress_at >= 1:
                atomic(receipt_path, result)
                progress_at = time.monotonic()
        result['status'] = 'completed'
    except Exception as exc:
        result.update(status='error', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        for g in result['views']:g['deleted_files'] = sorted(set(g['deleted_files']))
        removed = {n for g in result['views'] for n in g['deleted_files']}
        removed_inodes = defaultdict(list)
        for row in previous.values():
            if row['name'] in removed:removed_inodes[(row['device'], row['inode'])].append(row)
        freed = {}
        if not destination:
            for rows in removed_inodes.values():
                if len(rows) == rows[0]['nlink']:freed[min(r['name'] for r in rows)] = rows[0]['bytes']
        sizes = {r['name']: r['bytes'] for r in previous.values()}
        for g in result['views']:
            g['delete_count'] = len(g['deleted_files'])
            g['deleted_bytes'] = sum(sizes.get(n, 0) for n in g['deleted_files'])
            g['bytes_freed'] = sum(freed.pop(n, 0) for n in g['deleted_files'])
        result['frames_deleted'] = len(removed)
        result['bytes_freed'] = sum(g['bytes_freed'] for g in result['views'])
        atomic(receipt_path, result)
    return result


def restore(trash_dir):
    trash = safe(trash_dir)
    _scan(trash)  # Reject symlinked directories before recursive discovery.
    manifests = [trash/'restore.json'] if (trash/'restore.json').is_file() else sorted(trash.rglob('restore.json'))
    for manifest in manifests:
        value = load(manifest); root = safe(value['run'])
        present = []
        for row in value['files']:
            name = Path(row['name'])
            if name.is_absolute() or '..' in name.parts: raise ValueError('unsafe restore path')
            source = safe(manifest.parent/name); target = safe(root/name)
            if not source.exists():
                if not target.exists():raise ValueError('trash file missing')
                st = target.stat()
                if (st.st_dev, st.st_ino, st.st_size) != (row['device'], row['inode'], row['bytes']) or hashlib.sha256(target.read_bytes()).hexdigest() != row['sha256']:
                    raise ValueError('unmoved file byte/inode check failed')
                continue
            if target.exists(): raise ValueError('restore would overwrite a file')
            st = source.stat()
            if (st.st_dev, st.st_ino, st.st_size) != (row['device'], row['inode'], row['bytes']) or hashlib.sha256(source.read_bytes()).hexdigest() != row['sha256']:
                raise ValueError('trash byte/inode check failed')
            present.append((source, target))
        for source, target in present:
            target.parent.mkdir(parents=True, exist_ok=True); os.rename(source, target)
        receipt = root/'frame-cleanup.json'
        if receipt.exists(): receipt.unlink()
        manifest.rename(manifest.with_name('restored.json'))
    return len(manifests)


def notices(root):
    root = Path(root)
    rows = []
    for path in (root/'frame-cleanup-error.json', root/'frame-cleanup.json'):
        if not path.is_file():continue
        try:
            value = load(path)
            if not isinstance(value, dict):raise ValueError('cleanup receipt must be an object')
            if value.get('error'):rows.append(value['error'])
            rows.extend(g['execution']+'/'+g['view']+': '+g['skipped'] for g in value.get('views', []) if g.get('skipped'))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            rows.append(f'{path.name}: {type(exc).__name__}: {exc}')
    return rows


def _schedule(run_dir, *, wait_parent=True):
    """Detach cleanup from the run outcome; wait until the worker has exited."""
    root = safe(run_dir)
    if not root.is_dir(): return
    status = state(root)
    if not status['terminal'] or status['paused'] or _cleanup_alive(root): return
    receipt = root/'frame-cleanup.json'
    if receipt.exists() and load(receipt).get('status') != 'in_progress': return
    log = safe(root/'frame-cleanup.log')
    repo = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    env['PYTHONPATH'] = str(repo)+(os.pathsep+env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    with log.open('ab') as stream:
        subprocess.Popen([sys.executable, '-B', '-m', __name__, str(root), str(os.getpid() if wait_parent else 0)],
                         stdout=stream, stderr=stream, start_new_session=True,
                         cwd=repo, env=env)


def schedule(run_dir, *, wait_parent=True):
    try:
        _schedule(run_dir, wait_parent=wait_parent)
    except Exception as exc:
        _notice_error(run_dir, exc)


def startup_sweep(*roots):
    """Scan in a daemon thread; detached children must not wait for the server to exit."""
    def sweep():
        for directory in roots:
            if directory is None: continue
            root = Path(directory)
            try:
                for receipt in root.rglob('frame-cleanup.json'):
                    try:
                        if load(receipt).get('status') == 'in_progress':schedule(receipt.parent, wait_parent=False)
                    except Exception as exc: _notice_error(receipt.parent, exc)
            except Exception as exc: _notice_error(root, exc)
    try:
        thread = threading.Thread(target=sweep, name='frame-cleanup-startup', daemon=True)
        thread.start()
        return thread
    except Exception as exc:
        for root in roots:
            if root is not None:_notice_error(root, exc)


def _notice_error(root, exc):
    message = f'{type(exc).__name__}: {exc}'
    print('frame cleanup: '+message, file=sys.stderr, flush=True)
    try: atomic(Path(root)/'frame-cleanup-error.json', dict(error=message))
    except Exception: pass


def _main():
    root = Path(sys.argv[1]); parent = int(sys.argv[2])
    identity = _process_identity(parent) if parent else None
    while identity and _process_identity(parent) == identity: time.sleep(1)
    try:
        status = state(root)
        if status['terminal'] and not status['paused']:
            apply(root)
    except Exception as exc:
        message = f'{type(exc).__name__}: {exc}'
        print(message, file=sys.stderr, flush=True)
        atomic(root/'frame-cleanup-error.json', dict(error=message))


if __name__ == '__main__': _main()
