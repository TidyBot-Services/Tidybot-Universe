"""Workspace copies of uv, conda and the Codex CLI, pinned in attentionbench.lock.json.

Nothing the user installed is used or changed. The planner chain compares installer output byte for
byte, and the agent sandbox depends on the Codex package layout, so a host's own versions cannot stand
in for these. The Codex CLI runs the agents for every model route; a Codex login is a separate matter.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

from scripts.attentionbench import fetch


class ToolError(RuntimeError):
    pass


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _stamp(ws: Path, name: str) -> Path:
    return ws / 'tools/.stamps' / f'{name}.json'


def _ready(ws: Path, name: str, pin: dict, executable: Path) -> bool:
    try:
        return json.loads(_stamp(ws, name).read_text()).get('sha256') == pin['sha256'] and os.access(executable, os.X_OK)
    except (OSError, ValueError):
        return False


def _mark(ws: Path, name: str, pin: dict) -> None:
    stamp = _stamp(ws, name)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(json.dumps({'sha256': pin['sha256']}) + '\n')


def _fetch(ws: Path, name: str, pin: dict) -> Path:
    file = fetch.download(pin['url'], ws / 'cache/tools' / Path(pin['url']).name, size=pin['bytes'], label=name)
    actual = digest(file)
    if actual != pin['sha256']:
        file.unlink()
        raise ToolError(f'{name} SHA256 {actual} differs from the lock; the bad download was removed, rerun setup')
    return file


def _unpack(archive: Path, target: Path, strip: int = 0) -> None:
    """Unpack into a fresh directory, then move it into place, so an interrupted run leaves nothing half done."""
    staging = target.with_name(target.name + '.partial')
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    with tarfile.open(archive) as bundle:
        members = []
        for member in bundle.getmembers():
            parts = Path(member.name).parts
            if Path(member.name).is_absolute() or '..' in parts or member.issym() or member.islnk():
                raise ToolError(f'unsafe entry {member.name} in {archive.name}')
            if len(parts) > strip:
                member.name = '/'.join(parts[strip:])
                members.append(member)
        bundle.extractall(staging, members=members, **({'filter': 'data'} if hasattr(tarfile, 'data_filter') else {}))
    shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)


def ensure_uv(ws: Path, pin: dict) -> None:
    uv = ws / 'tools/bin/uv'
    if _ready(ws, 'uv', pin, uv):
        return
    staging = ws / 'tools/uv-package'
    _unpack(_fetch(ws, 'uv', pin), staging, strip=1)
    uv.parent.mkdir(parents=True, exist_ok=True)
    for name in ('uv', 'uvx'):
        (staging / name).replace(uv.parent / name)
    shutil.rmtree(staging)
    _mark(ws, 'uv', pin)


def ensure_conda(ws: Path, pin: dict) -> None:
    prefix = ws / 'tools/conda'
    if _ready(ws, 'conda', pin, prefix / 'condabin/conda'):
        return
    installer = _fetch(ws, 'conda', pin)
    shutil.rmtree(prefix, ignore_errors=True)
    print(f'[tools] installing conda {pin["version"]} in {prefix}', flush=True)
    result = subprocess.run(['bash', str(installer), '-b', '-p', str(prefix)], env=conda_env(ws),
                            text=True, capture_output=True)
    if result.returncode:
        raise ToolError(f'conda installer exited {result.returncode}: {(result.stderr or result.stdout)[-800:]}')
    _mark(ws, 'conda', pin)


def ensure_codex(ws: Path, pin: dict) -> None:
    codex = ws / 'tools/codex/bin/codex'
    if _ready(ws, 'codex', pin, codex):
        return
    _unpack(_fetch(ws, 'codex', pin), ws / 'tools/codex')
    _mark(ws, 'codex', pin)


def ensure_ffmpeg(ws: Path, pin: dict) -> None:
    """Fallback for a host without ffmpeg and without sudo: recording needs both ffmpeg and ffprobe."""
    if shutil.which('ffmpeg') and shutil.which('ffprobe'):
        return
    prefix = ws / 'tools/media'
    print('[tools] ffmpeg missing on this host; installing a workspace copy', flush=True)
    shutil.rmtree(prefix, ignore_errors=True)
    result = subprocess.run(['conda', 'create', '--yes', '--prefix', str(prefix), '--override-channels',
                             '--channel', 'conda-forge', pin['spec']], env=conda_env(ws), text=True, capture_output=True)
    if result.returncode:
        raise ToolError(f'ffmpeg install exited {result.returncode}: {(result.stderr or result.stdout)[-800:]}')
    for name in ('ffmpeg', 'ffprobe'):
        link = ws / 'tools/bin' / name
        link.unlink(missing_ok=True)
        link.symlink_to(prefix / 'bin' / name)


def conda_env(ws: Path) -> dict:
    """Conda reads ~/.condarc and writes ~/.conda; a private home keeps both out of the install."""
    home = ws / 'tools/home'
    home.mkdir(parents=True, exist_ok=True)
    return {**os.environ, 'HOME': str(home)}


def ensure(ws: Path, pins: dict) -> None:
    """Every step checks its own stamp, so a rerun picks up where an interrupted one stopped."""
    ensure_uv(ws, pins['uv'])
    ensure_conda(ws, pins['conda'])
    ensure_codex(ws, pins['codex'])
    ensure_ffmpeg(ws, pins['ffmpeg'])
