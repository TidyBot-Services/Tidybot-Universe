import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.attentionbench import installer, setup
from curobo_v2.host_fingerprint import gate, fingerprint_sha256


def probe(code=1, stderr='setting up uid map: Permission denied'):
    return SimpleNamespace(returncode=code, stderr=stderr)


@pytest.mark.parametrize('result,policy,expected', [
    (probe(), '1', True), (probe(0), '1', False), (probe(stderr='loopback: Failed RTM_NEWADDR'), '1', True),
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


def host(monkeypatch, *, packages=(), restricted=True, tty=True):
    monkeypatch.setattr(setup, 'system_packages', lambda: list(packages))
    monkeypatch.setattr(installer, 'apparmor_policy_on', lambda: restricted)
    monkeypatch.setattr(installer, 'apparmor_on_this_host', lambda: True)
    monkeypatch.setattr(installer, 'namespace_status', lambda: (not restricted, installer.USERNS_REASON, installer.USERNS_FIX))
    monkeypatch.setattr(setup.sys.stdin, 'isatty', lambda: tty)


def test_dry_run_changes_nothing_on_the_host(tmp_path, monkeypatch, capsys):
    host(monkeypatch, packages=['ffmpeg'])
    monkeypatch.setattr(setup.subprocess, 'run', lambda *a, **k: pytest.fail('dry run ran a command'))
    monkeypatch.setattr('builtins.input', lambda prompt: pytest.fail('dry run asked'))
    setup.prepare_host(True)
    output = capsys.readouterr().out
    assert 'sudo apt-get install -y ffmpeg' in output and 'apparmor_parser' in output


def test_no_changes_needed_asks_nothing(monkeypatch, capsys):
    host(monkeypatch, restricted=False)
    monkeypatch.setattr('builtins.input', lambda prompt: pytest.fail('nothing to ask'))
    setup.prepare_host(False)
    assert capsys.readouterr().out == ''


def test_no_terminal_prints_the_commands_and_changes_nothing(monkeypatch, capsys):
    host(monkeypatch, tty=False)
    monkeypatch.setattr(setup.subprocess, 'run', lambda *a, **k: pytest.fail('ran sudo without a terminal'))
    setup.prepare_host(False)
    output = capsys.readouterr().out
    assert 'apparmor_parser' in output and 'nothing was changed' in output


@pytest.mark.parametrize('answer,count', [('n', 0), ('y', 3)])
def test_one_question_covers_packages_and_apparmor(monkeypatch, capsys, answer, count):
    calls, asked = [], []
    host(monkeypatch, packages=['bubblewrap', 'ffmpeg'])
    monkeypatch.setattr('builtins.input', lambda prompt: asked.append(prompt) or answer)
    monkeypatch.setattr(setup.subprocess, 'run', lambda argv, **kw: calls.append(argv) or SimpleNamespace(returncode=0))
    setup.prepare_host(False)
    assert len(asked) == 1 and len(calls) == count
    assert all(argv[0] == 'sudo' and 'sysctl' not in argv for argv in calls)
    if answer == 'y':
        assert calls == [
            ['sudo', 'apt-get', 'install', '-y', 'bubblewrap', 'ffmpeg'],
            ['sudo', 'install', '-m', '0644', str(installer.USERNS_PROFILE), installer.USERNS_TARGET],
            ['sudo', 'apparmor_parser', '-r', installer.USERNS_TARGET],
        ]
    assert 'profile attentionbench-bwrap-userns' in capsys.readouterr().out


def test_a_container_is_not_asked_for_an_apparmor_change(monkeypatch):
    host(monkeypatch, packages=['bubblewrap'])
    monkeypatch.setattr(installer, 'apparmor_on_this_host', lambda: False)
    assert setup.system_commands() == ([['sudo', 'apt-get', 'install', '-y', 'bubblewrap']], False)


def test_a_failed_host_command_stops_setup(monkeypatch):
    host(monkeypatch)
    monkeypatch.setattr('builtins.input', lambda prompt: 'y')
    monkeypatch.setattr(setup.subprocess, 'run', lambda argv, **kw: SimpleNamespace(returncode=1))
    with pytest.raises(installer.StepError, match='apparmor|install'):
        setup.prepare_host(False)


def test_system_packages_lists_only_what_is_missing(monkeypatch):
    found = {'git', 'cc', 'g++', 'ffmpeg', 'ffprobe'}
    monkeypatch.setattr(setup.shutil, 'which', lambda name: '/usr/bin/' + name if name in found else None)
    monkeypatch.setattr(setup.Path, 'is_file', lambda self: str(self) == '/usr/bin/bwrap')
    monkeypatch.setattr(installer, 'missing_libraries', lambda: [])
    assert setup.system_packages() == []
    found -= {'g++', 'ffprobe'}
    monkeypatch.setattr(installer, 'missing_libraries', lambda: ['libEGL.so.1'])
    assert setup.system_packages() == ['build-essential', 'ffmpeg', 'libegl1']


@pytest.mark.parametrize('downloads,offline,ok', [
    ('', '', True), ('automatic', '0', True), ('never', '', False), ('manual', '', False),
    ('', 'yes', False), ('', 'enabled', False),
])
def test_uv_python_row(monkeypatch, downloads, offline, ok):
    monkeypatch.setenv('UV_PYTHON_DOWNLOADS', downloads)
    monkeypatch.setenv('UV_OFFLINE', offline)
    assert setup.uv_python_status({'python': {'ui': '3.10', 'sim': '3.11', 'curobo': '3.11'}})[0] is ok


def test_fingerprint_fields_without_nvidia(tmp_path, monkeypatch):
    from curobo_v2 import host_fingerprint as host
    def no_driver(*args, **kwargs):
        raise OSError('no nvidia-smi')
    real_read = Path.read_text
    monkeypatch.setattr(host.subprocess, 'run', no_driver)
    monkeypatch.setattr(host.Path, 'read_text', lambda self, *a, **k: no_driver() if 'nvidia' in str(self) else real_read(self, *a, **k))
    monkeypatch.setattr('curobo_v2.host_fingerprint.host_libraries', lambda: {'libcuda.so.1': None, 'libc.so.6': {'path': '/lib/libc.so.6', 'sha256': 'a'}})
    record = installer.host_fingerprint()
    assert set(record) == {'os', 'kernel', 'glibc', 'driver', 'gpu', 'libraries', 'created_at'}
    assert set(record['os']) == {'VERSION_ID', 'PRETTY_NAME'}
    assert record['driver'] is None and record['gpu'] is None
    assert record['libraries']['libcuda.so.1'] is None
    assert record['created_at'].endswith('+00:00')


def test_an_old_driver_warns_and_the_proof_decides():
    failures, warnings = gate({'driver': '535.183.01', 'glibc': 'glibc 2.35', 'os': {'VERSION_ID': '22.04'}})
    assert not failures and 'planner proof' in warnings[0]


@pytest.mark.parametrize('driver,glibc,fails', [
    ('535.183.01', '2.35', False), ('570.124.05', '2.35', False), ('570.124.06', '2.35', False),
    ('580.126.09', '2.39', False), ('580.126.09', '2.34', True),
    (None, '2.39', True),
])
def test_host_gate_version_boundaries(driver, glibc, fails):
    value = {'driver': driver, 'glibc': 'glibc ' + glibc,
             'os': {'VERSION_ID': '24.04'}, 'created_at': 'one'}
    failures, warnings = gate(value)
    assert bool(failures) is fails
    value['created_at'] = 'two'
    assert fingerprint_sha256(value) == fingerprint_sha256(dict(value, created_at='one'))
    value['os']['VERSION_ID'] = '23.10'
    assert gate(value)[1]


def test_optional_nvidia_libraries_are_best_effort(tmp_path, monkeypatch):
    from curobo_v2 import host_fingerprint as host
    ml = tmp_path / 'libnvidia-ml.so.1'
    ml.write_bytes(b'library')
    output = f'libnvidia-ml.so.1 (libc6,x86-64) => {ml}\n'
    monkeypatch.setattr(host.subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(stdout=output))
    libraries = host.host_libraries()
    assert libraries['libnvidia-ml.so.1']['sha256'] == installer.digest(ml)
    assert libraries['libnvidia-ptxjitcompiler.so.1'] is None


def test_reprove_does_not_change_registry_or_stamps(tmp_path, monkeypatch):
    ws = tmp_path / 'workspace'
    lock = ws / 'deployments/dev06/runtime-lock.json'
    lock.parent.mkdir(parents=True)
    lock.write_bytes(b'existing lock')
    registry = ws / 'deployments/local-dev-trust.json'
    registry.write_bytes(b'existing registry')
    stamp = ws / 'data/installer/stamps/planner-proof.json'
    stamp.parent.mkdir(parents=True)
    stamp.write_bytes(b'existing stamp')
    before = (lock.read_bytes(), registry.read_bytes(), stamp.read_bytes())
    def fake_proof(step, argv, **kwargs):
        assert str(lock) in argv and '--record-host' in argv
        Path(argv[argv.index('--output') + 1]).write_text('{"passed": true}')
    monkeypatch.setattr(installer, 'command', fake_proof)
    assert setup.main(['--workspace', str(ws), '--reprove']) == 0
    assert (lock.read_bytes(), registry.read_bytes(), stamp.read_bytes()) == before
    assert list((ws / 'data/run').glob('host-reproof-*.json'))
    def failed_proof(*args, **kwargs):
        raise installer.StepError('reprove', 'proof failed', 'Inspect it.')
    monkeypatch.setattr(installer, 'command', failed_proof)
    assert setup.main(['--workspace', str(ws), '--reprove']) == 1
    assert (lock.read_bytes(), registry.read_bytes(), stamp.read_bytes()) == before


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
    assert rows[-1]['status'] == 'WARN' and 'changed' in rows[-1]['reason'] and '/new/libc.so.6' in rows[-1]['reason']
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


def test_fingerprint_without_path_still_reads_the_driver(monkeypatch):
    from curobo_v2 import host_fingerprint as host
    monkeypatch.delenv('PATH', raising=False)
    value = host.host_fingerprint()
    assert host.gate(value)[0] == [] or value['driver'] is None
    assert value['libraries']['libc.so.6']


def test_driver_falls_back_to_the_kernel_module(monkeypatch):
    from curobo_v2 import host_fingerprint as host
    def no_smi(*args, **kwargs):
        raise OSError('no nvidia-smi')
    real_read = Path.read_text
    line = 'NVRM version: NVIDIA UNIX Open Kernel Module for x86_64  580.126.09  Release Build\n'
    monkeypatch.setattr(host.subprocess, 'run', no_smi)
    monkeypatch.setattr(host.Path, 'read_text', lambda self, *a, **k: line if 'nvidia' in str(self) else real_read(self, *a, **k))
    assert host.host_fingerprint()['driver'] == '580.126.09'
