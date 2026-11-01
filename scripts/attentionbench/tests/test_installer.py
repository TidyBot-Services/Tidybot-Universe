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
    assert lock['repos']['attentionbench']['commit'] == 'self'
    assert all(len(v['commit']) == 40 for k, v in lock['repos'].items() if k != 'attentionbench')
    assert lock['python'] == {'ui': '3.10', 'sim': '3.11', 'curobo': '3.11'}
    assert lock['codex'] == {'version': 'codex-cli 0.165.0', 'model': 'gpt-6.1-sol'}
    for name in ('conda_explicit', 'pip_requirements'):
        assert installer.digest(ROOT / lock['curobo_env'][name]) == lock['curobo_env'][name + '_sha256']
    assert installer.digest(ROOT / lock['curobo_env']['toppra_wheel']) == lock['curobo_env']['toppra_wheel_sha256']


def test_relative_paths_cover_template(tmp_path):
    installer.write_paths(tmp_path)
    paths = json.loads((tmp_path / 'paths.json').read_text())['paths']
    expected = json.loads(installer.TEMPLATE.read_text())['paths']
    assert set(paths) == set(expected)
    assert all(not Path(value).is_absolute() and '..' not in Path(value).parts for value in paths.values())
    config = tmp_path / paths['sim.configs_dir'] / 'pick_place_counter_to_sink_seed101.json'
    assert config.relative_to(tmp_path).parts[:2] == ('repos', 'attentionbench')
    assert (ROOT / config.relative_to(tmp_path).relative_to('repos/attentionbench')).is_file()


def test_doctor_path_policy_covers_template():
    policy = json.loads(installer.DOCTOR_PATHS.read_text())['paths']
    template = json.loads(installer.TEMPLATE.read_text())['paths']
    assert set(policy) == set(template)
    assert all(type(row['required']) is bool and (row['required'] or row['reason']) for row in policy.values())


def test_template_matches_collaborator_layout():
    from scripts.attentionbench.write_author_paths import PATHS
    template = json.loads(installer.TEMPLATE.read_text())['paths']
    assert {key: row[2] for key, row in PATHS.items()} == {
        key: value for key, value in template.items() if key != 'planner.local_dev_trust'}


def test_doctor_required_missing_fails_optional_missing_skips(tmp_path, monkeypatch, capsys):
    installer.write_paths(tmp_path)
    paths_file = tmp_path / 'paths.json'
    paths = json.loads(paths_file.read_text())
    paths['paths']['sim.configs_dir'] = 'repos/attentionbench/missing-configs'
    paths['paths'].pop('ui.serve_daily')
    paths_file.write_text(json.dumps(paths))
    original_check = installer._check
    def pass_non_path(results, name, ok, reason, fix):
        original_check(results, name, ok if name.startswith('path:') else True, reason, fix)
    monkeypatch.setattr(installer, '_check', pass_non_path)
    monkeypatch.setattr(installer.shutil, 'which', lambda name: None)
    monkeypatch.setattr(installer, 'verify_assets', lambda *a: [])
    monkeypatch.setattr(installer, 'digest', lambda *a: '')
    class FreeSocket:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def connect_ex(self, *a): return 1
    monkeypatch.setattr(installer.socket, 'socket', lambda: FreeSocket())
    assert installer.doctor(SimpleNamespace(workspace=str(tmp_path), json=True)) == 1
    rows = {row['check']: row for row in json.loads(capsys.readouterr().out)}
    assert rows['path:sim.configs_dir']['status'] == 'FAIL'
    assert rows['path:ui.serve_daily']['status'] == 'SKIP'
    assert rows['path:ui.serve_daily']['reason'].startswith('optional (')
    for key, value in paths['paths'].items():
        if key == 'sim.configs_dir':
            continue
        target = tmp_path / value
        target.mkdir(parents=True, exist_ok=True)
    assert installer.doctor(SimpleNamespace(workspace=str(tmp_path), json=True)) == 1
    # The missing required key remains a failure even if every other path exists.
    capsys.readouterr()
    required = tmp_path / paths['paths']['sim.configs_dir']
    required.mkdir(parents=True)
    assert installer.doctor(SimpleNamespace(workspace=str(tmp_path), json=True)) == 0
    rows = {row['check']: row for row in json.loads(capsys.readouterr().out)}
    assert rows['path:sim.configs_dir']['status'] == 'PASS'
    assert rows['path:ui.serve_daily']['status'] == 'SKIP'
    assert installer.doctor(SimpleNamespace(workspace=str(tmp_path), json=False)) == 0
    assert 'SKIP path:ui.serve_daily: optional (' in capsys.readouterr().out


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
    (ws / 'repos/attentionbench').mkdir(parents=True)
    monkeypatch.setattr(installer, 'REPO', (ws / 'repos/attentionbench').resolve())
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
    roots = json.loads((ws / 'repos/memory-m3/registration-roots.json').read_text())
    assert roots['executor'] == [str((ws / 'data/daily-experiments').resolve())]
    assert roots['validator'] == [str((ws / 'data/memory-validator').resolve())]
    assert str((ws / 'repos/attentionbench').resolve()) in roots['source']
    # The Memory service refuses roots of different roles that contain one another.
    flat = [(role, Path(p)) for role, entries in roots.items() for p in entries]
    assert not any(a.is_relative_to(b) for ra, a in flat for rb, b in flat if ra != rb)


def test_registration_roots_replace_old_empty_default(tmp_path):
    memory = tmp_path / 'repos/memory-m3'
    memory.mkdir(parents=True)
    (tmp_path / 'data/daily-experiments').mkdir(parents=True)
    (tmp_path / 'data/memory-validator').mkdir()
    (memory / 'registration-roots.json').write_text('{}\n')
    installer.write_registration_roots(tmp_path)
    roots = json.loads((memory / 'registration-roots.json').read_text())
    assert set(roots) == {'executor', 'validator', 'contract', 'source'} and roots['executor']
    custom = {'executor': ['/x'], 'validator': ['/y']}
    (memory / 'registration-roots.json').write_text(json.dumps(custom))
    installer.write_registration_roots(tmp_path)
    assert json.loads((memory / 'registration-roots.json').read_text()) == custom


def test_bootstrap_invokes_planner_chain(tmp_path, monkeypatch):
    (tmp_path / 'workspace/repos/attentionbench').mkdir(parents=True)
    monkeypatch.setattr(installer, 'REPO', (tmp_path / 'workspace/repos/attentionbench').resolve())
    monkeypatch.setattr(installer, 'stamped', lambda *a: None)
    monkeypatch.setattr(installer, 'digest', lambda *a: installer.data()['assets']['runtime_manifest_sha256'])
    monkeypatch.setattr(installer, 'download_assets', lambda ws, lock: ws / 'assets')
    monkeypatch.setattr(installer, 'verify_assets', lambda *a: [])
    called = []
    monkeypatch.setattr(installer, 'command', lambda step, argv, **kw: called.append((step, argv)))
    args = SimpleNamespace(workspace=str(tmp_path / 'workspace'), assets_from=None, update=False, skip_planner=False, dry_run=False)
    assert installer.bootstrap(args) == 0
    planner = [argv for step, argv in called if step == 'planner']
    assert len(planner) == 1
    assert planner[0][0] == str(tmp_path / 'workspace/envs/ui/bin/python')
    assert planner[0][1].endswith('scripts/attentionbench/build_planner_chain.py')
    assert planner[0][2:4] == ['--workspace', str(tmp_path / 'workspace')]
