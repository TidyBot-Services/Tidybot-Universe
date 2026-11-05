"""Workspace tools: pinned, checked, and reused on a rerun."""
import hashlib
import io
import tarfile

import pytest

from scripts.attentionbench import tools


def tarball(path, members, *, link=False):
    with tarfile.open(path, 'w:gz') as bundle:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            bundle.addfile(info, io.BytesIO(data))
        if link:
            info = tarfile.TarInfo('escape')
            info.type = tarfile.SYMTYPE
            info.linkname = '/etc/passwd'
            bundle.addfile(info)
    return path


def pin_for(path):
    data = path.read_bytes()
    return {'url': 'https://x/' + path.name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


@pytest.fixture
def served(monkeypatch):
    calls = []
    def download(url, target, *, size=None, label=None):
        calls.append(label)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(served.files[url.rsplit('/', 1)[1]])
        return target
    served.files = {}
    monkeypatch.setattr(tools.fetch, 'download', download)
    return calls


def test_uv_is_placed_in_the_workspace_once(tmp_path, served):
    archive = tarball(tmp_path / 'uv.tar.gz', {'uv-x/uv': b'#!/bin/sh\n', 'uv-x/uvx': b'#!/bin/sh\n'})
    served_files = archive.read_bytes()
    pin = pin_for(archive)
    globals()['served'].files[archive.name] = served_files
    ws = tmp_path / 'ws'
    tools.ensure_uv(ws, pin)
    tools.ensure_uv(ws, pin)
    assert served == ['uv'] and (ws / 'tools/bin/uv').is_file()


def test_codex_package_keeps_its_layout(tmp_path, served):
    archive = tarball(tmp_path / 'codex.tar.gz', {'bin/codex': b'x', 'codex-resources/bwrap': b'y', 'codex-package.json': b'{}'})
    globals()['served'].files[archive.name] = archive.read_bytes()
    ws = tmp_path / 'ws'
    tools.ensure_codex(ws, pin_for(archive))
    assert (ws / 'tools/codex/bin/codex').is_file() and (ws / 'tools/codex/codex-resources/bwrap').is_file()


def test_wrong_digest_is_removed_and_refused(tmp_path, served):
    archive = tarball(tmp_path / 'uv.tar.gz', {'uv-x/uv': b'x'})
    globals()['served'].files[archive.name] = archive.read_bytes()
    ws = tmp_path / 'ws'
    with pytest.raises(tools.ToolError, match='differs from the lock'):
        tools.ensure_uv(ws, dict(pin_for(archive), sha256='0' * 64))
    assert not (ws / 'cache/tools/uv.tar.gz').exists() and not (ws / 'tools/bin/uv').exists()


def test_unpack_refuses_links(tmp_path):
    archive = tarball(tmp_path / 'bad.tar.gz', {'a': b'x'}, link=True)
    with pytest.raises(tools.ToolError, match='unsafe'):
        tools._unpack(archive, tmp_path / 'out')


def test_interrupted_unpack_leaves_no_target(tmp_path, monkeypatch):
    archive = tarball(tmp_path / 'a.tar.gz', {'a': b'x'})
    monkeypatch.setattr(tarfile.TarFile, 'extractall', lambda self, *a, **k: (_ for _ in ()).throw(KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        tools._unpack(archive, tmp_path / 'out')
    assert not (tmp_path / 'out').exists()


def test_host_ffmpeg_is_used_when_present(tmp_path, monkeypatch):
    monkeypatch.setattr(tools.shutil, 'which', lambda name: '/usr/bin/' + name)
    monkeypatch.setattr(tools.subprocess, 'run', lambda *a, **k: pytest.fail('installed ffmpeg anyway'))
    tools.ensure_ffmpeg(tmp_path, {'spec': 'ffmpeg'})


def test_conda_gets_a_private_home(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', '/home/someone')
    assert tools.conda_env(tmp_path)['HOME'] == str(tmp_path / 'tools/home')
