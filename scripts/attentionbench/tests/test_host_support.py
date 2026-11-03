import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.attentionbench import installer, setup


def probe(code=1, stderr='setting up uid map: Permission denied'):
    return SimpleNamespace(returncode=code, stderr=stderr)


@pytest.mark.parametrize('result,policy,expected', [
    (probe(), '1', True), (probe(0), '1', False), (probe(stderr='other'), '1', False),
    (probe(), '0', False), (None, '1', False),
])
def test_restriction_requires_all_conditions(tmp_path, result, policy, expected):
    setting = tmp_path / 'userns'
    setting.write_text(policy)
    assert installer.apparmor_userns_restricted(result, setting) is expected


def test_restriction_reason_and_options(monkeypatch):
    monkeypatch.setattr(installer.shutil, 'which', lambda name: '/usr/bin/bwrap' if name == 'bwrap' else None)
    monkeypatch.setattr(installer.subprocess, 'run', lambda *a, **k: probe())
    monkeypatch.setattr(installer, 'apparmor_userns_restricted', lambda result: True)
    ok, reason, fix = installer.namespace_status()
    assert not ok and reason == installer.USERNS_REASON
    assert fix.index('C (recommended)') < fix.index('A:') < fix.index('B:')
    assert 'any local user' in fix and 'machine-wide' in fix


def test_fix_userns_dry_run(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, 'prerequisites', lambda *a, **k: True)
    monkeypatch.setattr(setup.installer, 'bootstrap', lambda args: None)
    monkeypatch.setattr(setup, 'fix_userns', lambda: pytest.fail('dry run applied fix'))
    assert setup.main(['--workspace', str(tmp_path), '--dry-run', '--fix-userns']) == 0


def test_fix_userns_no_restriction(monkeypatch, capsys):
    monkeypatch.setattr(installer, 'namespace_status', lambda: (True, 'available', ''))
    setup.fix_userns()
    assert 'not detected' in capsys.readouterr().out


@pytest.mark.parametrize('tty,bwrap,error', [(False, '/usr/bin/bwrap', 'TTY'), (True, '/other/bwrap', '/other/bwrap')])
def test_fix_userns_refuses(tmp_path, monkeypatch, tty, bwrap, error):
    monkeypatch.setattr(installer, 'namespace_status', lambda: (False, installer.USERNS_REASON, installer.USERNS_FIX))
    monkeypatch.setattr(setup.sys.stdin, 'isatty', lambda: tty)
    monkeypatch.setattr(setup.shutil, 'which', lambda name: bwrap)
    with pytest.raises(installer.StepError, match=error):
        setup.fix_userns()


@pytest.mark.parametrize('answer,count', [('n', 0), ('y', 2)])
def test_fix_userns_confirmation(tmp_path, monkeypatch, capsys, answer, count):
    calls = []
    monkeypatch.setattr(installer, 'namespace_status', lambda: (False, installer.USERNS_REASON, installer.USERNS_FIX))
    monkeypatch.setattr(setup.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(setup.shutil, 'which', lambda name: '/usr/bin/bwrap')
    monkeypatch.setattr('builtins.input', lambda prompt: answer)
    monkeypatch.setattr(setup.subprocess, 'run', lambda argv, **kw: calls.append(argv) or SimpleNamespace(returncode=0))
    setup.fix_userns()
    assert len(calls) == count
    assert all(argv[0] == 'sudo' and 'sysctl' not in argv for argv in calls)
    if answer == 'y':
        assert calls == [
            ['sudo', 'install', '-m', '0644', str(installer.USERNS_PROFILE), installer.USERNS_TARGET],
            ['sudo', 'apparmor_parser', '-r', installer.USERNS_TARGET],
        ]
    assert 'profile attentionbench-bwrap-userns' in capsys.readouterr().out


@pytest.mark.parametrize('found,downloads,offline,ok', [
    ({'3.10', '3.11'}, 'never', 'true', True),
    ({'3.10'}, '', '', True), ({'3.10'}, 'never', '', False),
    ({'3.10'}, 'manual', '', False), ({'3.10'}, '', 'yes', False),
    ({'3.10'}, '', 'enabled', False),
])
def test_uv_python_row(monkeypatch, found, downloads, offline, ok):
    monkeypatch.setattr(setup.shutil, 'which', lambda name: '/usr/bin/uv' if name == 'uv' else None)
    monkeypatch.setattr(setup, '_run', lambda argv: SimpleNamespace(returncode=0 if argv[-1] in found else 1))
    monkeypatch.setenv('UV_PYTHON_DOWNLOADS', downloads)
    monkeypatch.setenv('UV_OFFLINE', offline)
    assert setup.uv_python_status({'python': {'ui': '3.10', 'sim': '3.11', 'curobo': '3.11'}})[0] is ok


def test_fingerprint_fields_without_nvidia(tmp_path, monkeypatch):
    monkeypatch.setattr(installer.shutil, 'which', lambda name: None)
    monkeypatch.setattr(installer, 'host_libraries', lambda: {'libcuda.so.1': None, 'libc.so.6': {'path': '/lib/libc.so.6', 'sha256': 'a'}})
    record = installer.host_fingerprint()
    assert set(record) == {'os', 'kernel', 'glibc', 'driver', 'gpu', 'libraries', 'created_at'}
    assert set(record['os']) == {'VERSION_ID', 'PRETTY_NAME'}
    assert record['driver'] is None and record['gpu'] is None
    assert record['libraries']['libcuda.so.1'] is None
    assert record['created_at'].endswith('+00:00')


def test_host_libraries_rows(tmp_path, monkeypatch):
    rows = []
    installer.check_host_libraries(rows, tmp_path)
    assert rows[-1]['status'] == 'SKIP'
    lib = tmp_path / 'libc.so.6'
    lib.write_text('old')
    lock = tmp_path / 'deployments/official-parent/runtime-lock.json'
    lock.parent.mkdir(parents=True)
    lock.write_text(json.dumps({'native_host_dependencies': {str(lib): installer.digest(lib)}}))
    monkeypatch.setattr(installer, 'host_libraries', lambda: {'libcuda.so.1': None, 'libc.so.6': {'path': '/new/libc.so.6', 'sha256': 'new'}})
    monkeypatch.setattr(installer, '_latest_host_upgrade', lambda: 'Upgrade: libc6')
    installer.check_host_libraries(rows, tmp_path)
    assert rows[-1]['status'] == 'PASS'
    lib.write_text('new')
    installer.check_host_libraries(rows, tmp_path)
    assert rows[-1]['status'] == 'FAIL' and 'changed' in rows[-1]['reason'] and '/new/libc.so.6' in rows[-1]['reason']
    assert 'Upgrade: libc6' in rows[-1]['fix'] and '--rebuild-planner' in rows[-1]['fix']
    lib.unlink()
    installer.check_host_libraries(rows, tmp_path)
    assert 'missing' in rows[-1]['reason']


def test_host_libraries_versioned_cuda_path(tmp_path, monkeypatch):
    cuda = tmp_path / 'libcuda.so.580'
    cuda.write_text('old')
    lock = tmp_path / 'deployments/official-parent/runtime-lock.json'
    lock.parent.mkdir(parents=True)
    lock.write_text(json.dumps({'native_host_dependencies': {str(cuda): installer.digest(cuda)}}))
    monkeypatch.setattr(installer, 'host_libraries', lambda: {
        'libcuda.so.1': {'path': '/new/libcuda.so.581', 'sha256': 'new'}, 'libc.so.6': None})
    cuda.write_text('new')
    rows = []
    installer.check_host_libraries(rows, tmp_path)
    assert 'changed' in rows[-1]['reason'] and '/new/libcuda.so.581' in rows[-1]['reason']


def test_rebuild_moves_only_deployment_stamps(tmp_path, monkeypatch):
    ws = tmp_path
    (ws / 'paths.json').write_text('{}')
    (ws / 'deployments/official-parent').mkdir(parents=True)
    (ws / 'deployments/official-parent/runtime-lock.json').write_text('{}')
    stamps = ws / 'data/installer/stamps'
    stamps.mkdir(parents=True)
    for step in (*installer.DEPLOYMENT_STEPS, 'env', 'native-assets'):
        (stamps / f'planner-{step}.json').write_text(step)
    monkeypatch.setattr(installer, '_port_free', lambda port: True)
    from scripts.attentionbench import control
    monkeypatch.setattr(control, 'live_workers', lambda ws: [])
    installer.rebuild_planner(ws, SimpleNamespace(skip_planner=False))
    superseded = next(ws.glob('deployments.superseded-*'))
    assert (superseded / 'official-parent/runtime-lock.json').is_file()
    assert not (ws / 'deployments').exists()
    for step in installer.DEPLOYMENT_STEPS:
        assert (superseded / 'stamps' / f'planner-{step}.json').is_file()
        assert not (stamps / f'planner-{step}.json').exists()
    for step in ('env', 'native-assets'):
        assert (stamps / f'planner-{step}.json').is_file()


@pytest.mark.parametrize('skip,workers', [(True, False), (False, True)])
def test_rebuild_refuses_before_move(tmp_path, monkeypatch, skip, workers):
    (tmp_path / 'paths.json').write_text('{}')
    (tmp_path / 'deployments').mkdir()
    monkeypatch.setattr(installer, '_port_free', lambda port: True)
    from scripts.attentionbench import control
    monkeypatch.setattr(control, 'live_workers', lambda ws: [('worker',)] if workers else [])
    with pytest.raises(installer.StepError):
        installer.rebuild_planner(tmp_path, SimpleNamespace(skip_planner=skip))
    assert (tmp_path / 'deployments').is_dir()


def test_rebuild_refuses_busy_port_and_missing_paths(tmp_path, monkeypatch):
    (tmp_path / 'deployments').mkdir()
    args = SimpleNamespace(skip_planner=False, ui_port=8769, memory_port=8878)
    with pytest.raises(installer.StepError, match='paths.json'):
        installer.validate_rebuild(tmp_path, args)
    (tmp_path / 'paths.json').write_text('{}')
    monkeypatch.setattr(installer, '_port_free', lambda port: port != 8769)
    with pytest.raises(installer.StepError, match='stop.sh'):
        installer.validate_rebuild(tmp_path, args)
    assert (tmp_path / 'deployments').is_dir()


def test_2404_docker_profile_only_adds_abi_and_userns():
    docker = Path(__file__).resolve().parents[3] / 'config/attentionbench/docker'
    base = (docker / 'attentionbench-bwrap.apparmor').read_text().splitlines()[1:]
    variant = (docker / 'attentionbench-bwrap-2404.apparmor').read_text().splitlines()[1:]
    assert [line for line in variant if line not in ('abi <abi/4.0>,', '  userns,')] == base
