"""Try speed-up item 7: dev-tier stat-based locked-file check (formal path untouched)."""
import hashlib
import os
from pathlib import Path

import pytest

import curobo_v2.identity as auth


def make(tmp_path):
    project = tmp_path / 'project'; project.mkdir()
    lib = tmp_path / 'lib.py'; lib.write_text('x = 1\n')
    src = project / 'a.py'; src.write_text('a = 1\n')
    files = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (lib, src)}
    return dict(project_root=str(project), files=files), lib, src


class Clock:
    def __init__(self): self.t = 0.
    def __call__(self): return self.t


def test_first_check_hashes_then_unchanged_files_are_not_rehashed(tmp_path, monkeypatch):
    lock, lib, src = make(tmp_path); clock = Clock()
    monkeypatch.setattr(auth.DevFileWatch, 'RACY_NS', -10 ** 18)  # pretend every file is old
    calls = []; real = auth.verify_files
    monkeypatch.setattr(auth, 'verify_files', lambda l: (calls.append(1), real(l)))
    watch = auth.DevFileWatch(lock, clock)
    assert watch.check() == 'hashed' and watch.check() == 'unchanged' and len(calls) == 1
    clock.t = auth.DevFileWatch.FULL_SECONDS
    assert watch.check() == 'hashed' and len(calls) == 2


@pytest.mark.parametrize('change', ['content_same_size', 'touch_back', 'new_file', 'symlink', 'removed', 'mode'])
def test_any_change_forces_a_full_check(tmp_path, change):
    lock, lib, src = make(tmp_path)
    watch = auth.DevFileWatch(lock, Clock()); watch.check()
    if change == 'content_same_size':
        lib.write_text('x = 2\n')
    elif change == 'touch_back':
        st = lib.stat(); lib.write_text('x = 3\n'); os.utime(lib, ns=(st.st_atime_ns, st.st_mtime_ns))
    elif change == 'new_file':
        (Path(lock['project_root']) / 'b.py').write_text('')
    elif change == 'symlink':
        (Path(lock['project_root']) / 'link').symlink_to(lib)
    elif change == 'removed':
        src.unlink()
    else:
        lib.chmod(0o600)
        assert watch.check() == 'hashed'  # metadata only: full re-hash, contents still match
        return
    with pytest.raises((auth.IdentityError, OSError)):
        watch.check()


def test_registry_change_is_refused_on_the_fast_path(tmp_path, monkeypatch):
    lock, lib, src = make(tmp_path)
    registry = tmp_path / 'trusted.json'; registry.write_text('{}')
    monkeypatch.setattr(auth, 'DEPLOYMENT_REGISTRY', registry)
    monkeypatch.setattr(auth, '_registry_digest', auth.digest(registry))
    watch = auth.DevFileWatch(lock, Clock()); watch.check()
    registry.write_text('{"x": 1}')
    with pytest.raises(auth.IdentityError, match='trust policy changed'):
        watch.check()


def test_worker_uses_the_watch_only_for_the_dev_loop():
    text = (Path(auth.__file__).with_name('worker.py')).read_text()
    assert "DevFileWatch(lock) if (args.tier, args.boundary) == ('dev', 'dev_loop') else None" in text
    assert 'if file_watch is None:verify_files(lock)' in text


def test_recently_changed_files_are_hashed_on_every_check(tmp_path, monkeypatch):
    """Racy-clean rule: a write in the same timestamp tick as the stat pass must still be caught."""
    lock, lib, src = make(tmp_path)
    watch = auth.DevFileWatch(lock, Clock())
    assert watch.check() == 'hashed' and watch.check() == 'hashed'
    lib.write_text('x = 9\n')  # same size, likely same coarse timestamp
    with pytest.raises(auth.IdentityError):
        watch.check()
