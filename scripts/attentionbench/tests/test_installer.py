import hashlib
import io
import tarfile
import zipfile
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
    monkeypatch.setattr(installer.routes, 'available', lambda: [])
    monkeypatch.setattr(installer.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=1, stdout='codex-cli 0.1', stderr=''))
    assert installer.doctor(SimpleNamespace(workspace=str(ws), json=True)) == 1
    rows = json.loads(capsys.readouterr().out)
    by_name = {row['check']: row for row in rows}
    for name in ('gpu', 'codex-version', 'assets', 'path:memory.m3_keys'):
        assert by_name[name]['status'] == 'FAIL'
        assert by_name[name]['fix']
    # A busy port or a missing model route does not make a workspace broken.
    assert by_name['port-8769']['status'] == 'WARN' and 'next free port' in by_name['port-8769']['reason']
    assert by_name['model-routes']['status'] == 'WARN' and 'run.sh' in by_name['model-routes']['reason']


def test_keys_created_private_and_not_printed(tmp_path, monkeypatch, capsys):
    # Exercise the bootstrap through its last local initialization stage with
    # network and installer commands replaced by inert fixtures.
    ws = tmp_path / 'workspace'
    (ws / 'repos/attentionbench').mkdir(parents=True)
    monkeypatch.setattr(installer, 'REPO', (ws / 'repos/attentionbench').resolve())
    monkeypatch.setattr(installer.tools, 'ensure', lambda ws, pins: None)
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
    monkeypatch.setattr(installer.tools, 'ensure', lambda ws, pins: None)
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


def zip_with(path, members):
    with zipfile.ZipFile(path, 'w') as bundle:
        for name, data in members.items():
            bundle.writestr(name, data)
    return path


def sha(data):
    return hashlib.sha256(data).hexdigest()


def test_unpack_writes_only_files_the_manifest_lists_with_matching_bytes(tmp_path):
    archive = zip_with(tmp_path / 'pack.zip', {'lightwheel/a.obj': b'good', 'lightwheel/b.obj': b'changed', 'README.md': b'not listed'})
    wanted = {'objects/lightwheel/a.obj': sha(b'good'), 'objects/lightwheel/b.obj': sha(b'expected')}
    root = tmp_path / 'assets'
    assert installer.unpack_pack(archive, root, wanted, 'objects/') == 1
    assert (root / 'objects/lightwheel/a.obj').read_bytes() == b'good'
    assert not (root / 'objects/lightwheel/b.obj').exists() and not (root / 'README.md').exists()


@pytest.mark.parametrize('name', ['../escape', '/absolute'])
def test_unsafe_archive_member_is_refused(tmp_path, name):
    archive = tmp_path / 'pack.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        info = zipfile.ZipInfo(name)
        bundle.writestr(info, b'x')
    with pytest.raises(installer.StepError, match='unsafe'):
        installer.unpack_pack(archive, tmp_path, {}, '')


def asset_workspace(tmp_path, monkeypatch, packs):
    ws = tmp_path / 'ws'
    baseline = ws / 'sim/robocasa/robocasa/models/assets'
    baseline.mkdir(parents=True)
    (baseline / 'tracked.xml').write_bytes(b'tracked')
    manifest = ws / 'manifest.json'
    manifest.write_text(json.dumps({'tracked.xml': sha(b'tracked'), 'objects/a': sha(b'a'), 'objects/b': sha(b'b')}))
    fetched = []
    def fetch_pack(pack, downloads):
        fetched.append(pack['name'])
        downloads.mkdir(parents=True, exist_ok=True)
        return zip_with(downloads / (pack['name'] + '.zip'), packs[pack['name']])
    monkeypatch.setattr(installer, 'fetch_pack', fetch_pack)
    monkeypatch.setattr(installer, 'asset_sources', lambda lock: {'robocasa': [
        {'name': name, 'prefix': 'objects/'} for name in packs]})
    return ws, {'assets': {'installed_manifest': 'manifest.json'}}, fetched


def test_assets_combine_checkout_and_archives_and_drop_each_archive(tmp_path, monkeypatch):
    ws, lock, fetched = asset_workspace(tmp_path, monkeypatch, {'one': {'a': b'a'}, 'two': {'b': b'b'}})
    source = installer.download_assets(ws, lock)
    assert {p.name for p in source.rglob('*') if p.is_file()} == {'tracked.xml', 'a', 'b'}
    assert fetched == ['one', 'two'] and not list((ws / 'data/robocasa365/downloads').iterdir())


def test_rerun_continues_after_the_last_finished_archive(tmp_path, monkeypatch):
    ws, lock, fetched = asset_workspace(tmp_path, monkeypatch, {'one': {'a': b'a'}, 'two': {'b': b'b'}})
    real = installer.unpack_pack
    def interrupted(archive, root, wanted, prefix):
        if archive.stem == 'two':
            raise KeyboardInterrupt
        return real(archive, root, wanted, prefix)
    monkeypatch.setattr(installer, 'unpack_pack', interrupted)
    with pytest.raises(KeyboardInterrupt):
        installer.download_assets(ws, lock)
    monkeypatch.setattr(installer, 'unpack_pack', real)
    fetched.clear()
    source = installer.download_assets(ws, lock)
    assert fetched == ['two'] and (source / 'objects/a').is_file() and (source / 'objects/b').is_file()


def test_bad_archive_is_removed_for_the_next_run(tmp_path, monkeypatch):
    ws, lock, _ = asset_workspace(tmp_path, monkeypatch, {'one': {'a': b'a'}})
    def corrupt(pack, downloads):
        downloads.mkdir(parents=True, exist_ok=True)
        file = downloads / 'one.zip'
        file.write_bytes(b'not a zip')
        return file
    monkeypatch.setattr(installer, 'fetch_pack', corrupt)
    with pytest.raises(installer.StepError, match='removed'):
        installer.download_assets(ws, lock)
    assert not (ws / 'data/robocasa365/downloads/one.zip').exists()


def test_native_tree_is_public_archives_plus_the_overlay(tmp_path, monkeypatch):
    ws = tmp_path / 'ws'
    dest = ws / 'sim/curobo-native-assets'
    (dest / 'data/old').mkdir(parents=True)  # An interrupted attempt.
    overlay = tmp_path / 'overlay.tar.gz'
    with tarfile.open(overlay, 'w:gz') as bundle:
        info = tarfile.TarInfo('scene/plate.obj')
        info.size = 5
        bundle.addfile(info, io.BytesIO(b'fixed'))
    packs = {'scene': {'robocasa_dataset/plate.obj': b'public', 'robocasa_dataset/other': b'o'}, 'robot': {'top-1/r.urdf': b'u'}}
    def fetch_pack(pack, downloads):
        downloads.mkdir(parents=True, exist_ok=True)
        return zip_with(downloads / (pack['name'] + '.zip'), packs[pack['name']])
    monkeypatch.setattr(installer, 'fetch_pack', fetch_pack)
    monkeypatch.setattr(installer, 'REPO', tmp_path)
    monkeypatch.setattr(installer, 'asset_sources', lambda lock: {'native': {
        'archives': [{'name': 'scene', 'strip': 'robocasa_dataset/', 'target': 'data/scene/'},
                     {'name': 'robot', 'strip': 'top-1/', 'target': 'data/robots/'}],
        'overlay': {'path': 'overlay.tar.gz', 'sha256': installer.digest(overlay), 'backups': ['scene/plate.obj']}}})
    installer.download_native_assets(ws, {}, dest)
    tree = {str(p.relative_to(dest / 'data')): p.read_bytes() for p in (dest / 'data').rglob('*') if p.is_file()}
    assert tree == {'scene/plate.obj': b'fixed', 'scene/plate.obj.bak': b'public', 'scene/other': b'o', 'robots/r.urdf': b'u'}


def test_lock_lists_every_public_source_and_tool():
    lock = installer.data()
    sources = installer.asset_sources(lock)
    assert {pack['name'] for pack in sources['robocasa']} == {
        'textures', 'generative_textures', 'fixtures_lightwheel', 'fixtures', 'objects_lightwheel', 'objaverse', 'aigen_objs'}
    overlay = sources['native']['overlay']
    assert installer.digest(ROOT / overlay['path']) == overlay['sha256'] and len(overlay['backups']) == 33
    for name in ('codex', 'conda', 'uv'):
        assert len(lock['tools'][name]['sha256']) == 64 and lock['tools'][name]['url'].startswith('https://')


def test_changed_planner_inputs_rebuild_only_with_update(tmp_path, monkeypatch):
    calls = []
    def command(step, argv, **kw):
        calls.append(step)
        if len(calls) == 1:
            raise installer.StepError('planner', installer.STALE_PLANNER, 'x')
    monkeypatch.setattr(installer, 'command', command)
    rebuilt = []
    monkeypatch.setattr(installer, 'rebuild_planner', lambda ws, args: rebuilt.append(ws))
    with pytest.raises(installer.StepError):
        installer.run_planner(tmp_path, SimpleNamespace(update=False), ['x'])
    assert not rebuilt
    calls.clear()
    installer.run_planner(tmp_path, SimpleNamespace(update=True), ['x'])
    assert rebuilt == [tmp_path] and calls == ['planner', 'planner']


def test_a_slow_command_reports_progress(monkeypatch, capsys):
    monkeypatch.setattr(installer, 'HEARTBEAT_SECONDS', .05)
    installer.command('slow', [sys.executable, '-c', 'import time; time.sleep(.2)'])
    assert 'still running' in capsys.readouterr().out


def test_failed_command_reports_its_output():
    with pytest.raises(installer.StepError, match='boom'):
        installer.command('bad', [sys.executable, '-c', 'import sys; print("boom"); sys.exit(3)'])
