"""Offline retention for this gateway's finished runs. Nothing is removed without --delete."""
import os
from pathlib import Path
import re
import shutil
import stat

RUN = re.compile(r'devexp-[0-9a-f]{24}')


def directory(path, root):
    if path.is_symlink() or path.resolve().parent != root or not stat.S_ISDIR(path.lstat().st_mode):
        raise ValueError(f'refusing unsafe run directory: {path}')


def size_of(path):
    size = 0
    for parent, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            item = Path(parent) / name
            if item.is_symlink():raise ValueError(f'refusing symlink inside run: {item}')
            if name in files:
                info = item.stat(follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):raise ValueError(f'refusing special file inside run: {item}')
                size += info.st_size
    return size


def prune(store, runs_root, *, days, delete=False):
    if type(days) is not int or days < 1:raise ValueError('days must be a positive integer')
    supplied = Path(runs_root)
    if supplied.is_symlink():raise ValueError('runs-root must not be a symlink')
    root = supplied.resolve(strict=True)
    if not root.is_dir():raise ValueError('runs-root must be a directory')
    now = store.clock();finished = store.finished_before(now - days * 86400);candidates = []
    # Inspect the whole list before deletion: an unsafe entry must not leave a half-applied cleanup.
    for path in sorted(root.iterdir()):
        if not RUN.fullmatch(path.name):continue
        if path.is_symlink():raise ValueError(f'refusing symlink: {path}')
        if not path.is_dir():continue
        directory(path, root)
        if path.name not in finished:continue
        candidates.append((path, size_of(path), (now - finished[path.name]) / 86400, path.stat().st_ino))
    for path, size, age, inode in candidates:
        print(f'{path}\t{size} bytes\t{age:.2f} days')
    print(f'{len(candidates)} candidates, {sum(row[1] for row in candidates)} bytes total ({"delete" if delete else "dry run"})')
    if delete:
        for path, _, _, inode in candidates:
            directory(path, root)
            if path.stat().st_ino != inode:raise ValueError(f'run directory changed: {path}')
            # rmtree on this platform uses file descriptors to avoid following substituted symlinks.
            if not shutil.rmtree.avoids_symlink_attacks:raise ValueError('safe directory deletion is unavailable on this platform')
            shutil.rmtree(path)
            store.forget_run(path.name)
    return len(candidates)
