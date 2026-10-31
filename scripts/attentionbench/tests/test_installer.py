import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / 'scripts/attentionbench/installer.py'
spec = importlib.util.spec_from_file_location('attentionbench_installer', SCRIPT)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def test_lock_schema_and_inputs():
    lock = installer.data()
    assert lock['schema'] == 'attentionbench.installer.v1'
    assert set(lock['repos']) == {'attentionbench', 'robocasa365_sim', 'attention_memory_service',
                                  'robocasa', 'robosuite', 'curobo', 'robocasa_tasks'}
    assert lock['repos']['robocasa_tasks'] == {
        'commit': 'b18bbf1585c42e370ae45ababdddad700cc2c71d',
        'url': 'https://github.com/TidyBot-Services/maniskill-robocasa-tasks.git'}
    assert all(len(v['commit']) == 40 for v in lock['repos'].values())
    assert lock['python'] == {'ui': '3.10', 'sim': '3.11', 'curobo': '3.11'}
    assert lock['codex'] == {'version': 'codex-cli 0.165.0', 'model': 'gpt-6.1-sol'}
    for name in ('conda_explicit', 'pip_requirements'):
        assert installer.digest(ROOT / lock['curobo_env'][name]) == lock['curobo_env'][name + '_sha256']


def test_relative_paths_cover_template(tmp_path):
    installer.write_paths(tmp_path)
    paths = json.loads((tmp_path / 'paths.json').read_text())['paths']
    expected = json.loads(installer.TEMPLATE.read_text())['paths']
    assert set(paths) == set(expected)
    assert all(not Path(value).is_absolute() and '..' not in Path(value).parts for value in paths.values())


def test_dry_run_touches_nothing(tmp_path):
    ws = tmp_path / 'new workspace'
    result = subprocess.run([sys.executable, str(SCRIPT), 'bootstrap', '--workspace', str(ws), '--dry-run'], capture_output=True, text=True)
    assert result.returncode == 0
    assert not ws.exists()
    for step in ('repos:', 'ui-venv:', 'assets:', 'sim-env:', 'curobo-env:', 'planner:', 'paths:', 'stores:'):
        assert step in result.stdout


def test_asset_mismatch_lists_every_file(tmp_path):
    source = tmp_path / 'assets'; source.mkdir()
    (source / 'bad').write_bytes(b'wrong')
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'bad': '0' * 64, 'missing': '1' * 64}))
    assert installer.verify_assets(source, manifest, installer.digest(manifest)) == ['bad', 'missing']


def test_doctor_failure_kinds_and_fix(tmp_path, monkeypatch, capsys):
    ws = tmp_path
    lock = installer.data()
    manifest = ws / 'manifest.json'; manifest.write_text(json.dumps({'bad': '0' * 64}))
    source = ws / 'assets'; source.mkdir(); (source / 'bad').write_text('wrong')
    stamp = ws / 'data/robocasa365/assets-verified.json'; stamp.parent.mkdir(parents=True)
    stamp.write_text(json.dumps({'source': str(source)}))
    lock['assets']['installed_manifest'] = 'manifest.json'
    lock['assets']['installed_manifest_sha256'] = installer.digest(manifest)
    monkeypatch.setattr(installer, 'data', lambda: lock)
    monkeypatch.setattr(installer.shutil, 'which', lambda name: '/bin/true' if name == 'bwrap' else '/bin/false')
    class Busy:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def connect_ex(self, *a): return 0
    monkeypatch.setattr(installer.socket, 'socket', lambda: Busy())
    monkeypatch.setattr(installer.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=1, stdout='codex-cli 0.1', stderr=''))
    assert installer.doctor(SimpleNamespace(workspace=str(ws), json=True)) == 1
    rows = json.loads(capsys.readouterr().out)
    by_name = {row['check']: row for row in rows}
    for name in ('gpu', 'codex-version', 'assets', 'port-8769', 'path:memory.m3_keys'):
        assert by_name[name]['status'] == 'FAIL'
        assert by_name[name]['fix']


def test_keys_created_private_and_not_printed(tmp_path, monkeypatch, capsys):
    # Exercise the bootstrap through its last local initialization stage with
    # network and installer commands replaced by inert fixtures.
    ws = tmp_path / 'workspace'
    monkeypatch.setattr(installer, 'stamped', lambda ws, step, inputs, update, action: None)
    monkeypatch.setattr(installer, 'digest', lambda *a: installer.data()['assets']['runtime_manifest_sha256'])
    monkeypatch.setattr(installer, 'download_assets', lambda ws, lock: ws / 'assets')
    monkeypatch.setattr(installer, 'verify_assets', lambda *a: [])
    monkeypatch.setattr(installer, 'command', lambda *a, **kw: None)
    args = SimpleNamespace(workspace=str(ws), assets_from=None, update=False, skip_planner=True, dry_run=False)
    assert installer.bootstrap(args) == 0
    keys = ws / 'repos/memory-m3/keys.env'
    assert keys.stat().st_mode & 0o777 == 0o600
    values = [line.split('=', 1)[1] for line in keys.read_text().splitlines()]
    assert len(values) == len(set(values)) == 3
    output = capsys.readouterr().out
    assert not any(value in output for value in values)


def test_bootstrap_invokes_planner_chain(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, 'stamped', lambda *a: None)
    monkeypatch.setattr(installer, 'digest', lambda *a: installer.data()['assets']['runtime_manifest_sha256'])
    monkeypatch.setattr(installer, 'download_assets', lambda ws, lock: ws / 'assets')
    monkeypatch.setattr(installer, 'verify_assets', lambda *a: [])
    monkeypatch.setattr(installer, 'command', lambda *a, **kw: None)
    from scripts.attentionbench import build_planner_chain
    called = []
    monkeypatch.setattr(build_planner_chain, 'build', lambda args: called.append(args))
    args = SimpleNamespace(workspace=str(tmp_path / 'workspace'), assets_from=None, update=False, skip_planner=False, dry_run=False)
    assert installer.bootstrap(args) == 0
    assert len(called) == 1
    assert called[0].workspace == str(tmp_path / 'workspace')
