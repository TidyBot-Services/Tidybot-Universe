"""One physical copy per agent frame (10-29): canonical, ws, Eval and snapshots share a 0444 inode."""
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest
from benchmarks.attention_harness import dev_agent_isolation as isolation
from benchmarks.attention_harness import dev_agent_workspace as workspace_module
from benchmarks.attention_harness.dev_agent_workspace import Workspace, read_beneath, shared_name
from benchmarks.attention_harness.dev_agent_eval import build_workspace
from benchmarks.attention_harness.dev_agent_replay import freeze_origin, provision_replay

CONFIG = dict(budget_tier='dev', runner_boundary='dev_loop', sdk_call_limit=320, horizon=9600, attempt_wall_seconds=1920)
FRAME = 'executions/e000001/frames/overview-000000-sim-0.000000s.png'
PNG = b'\x89PNG\r\n\x1a\n' + bytes(range(256)) * 64


@pytest.fixture
def run(tmp_path):
    w = Workspace(tmp_path/'agent').prepare({'task_id': 'fixture', 'task_prompt': 'Move safely.'}, CONFIG)
    receipt = w.publish(FRAME, PNG)
    w.publish('executions/e000001/frames.json', [dict(filename=FRAME.split('/', 2)[2], sha256=receipt['sha256'])])
    for name in ('stdout.txt', 'stderr.txt'): w.publish('executions/e000001/'+name, b'')
    for name in ('state_log.json', 'trace.json', 'diagnostics/planner.json', 'final_observation.json'):
        w.publish('executions/e000001/'+name, [])
    w.publish('versions/v0001/bundle.py', b'pass\n')
    return w, receipt


def inode(path):
    info = os.lstat(path); return info.st_dev, info.st_ino


def test_frame_is_one_read_only_inode_and_hash_is_unchanged(run):
    w, receipt = run
    canonical, ws = w.canonical/FRAME, w.ws/FRAME
    assert inode(canonical) == inode(ws) and os.lstat(canonical).st_nlink == 2
    assert os.lstat(canonical).st_mode & 0o777 == 0o444
    assert receipt['sha256'] == hashlib.sha256(PNG).hexdigest() == hashlib.sha256(ws.read_bytes()).hexdigest()
    # Only frames are shared; every other public file keeps its own private 0600 copy.
    other = 'executions/e000001/stdout.txt'
    assert os.lstat(w.canonical/other).st_nlink == 1 and os.lstat(w.canonical/other).st_mode & 0o777 == 0o600
    assert shared_name(FRAME) and not shared_name('executions/e000001/frames.json') and not shared_name('work/x.png')
    # Republishing identical bytes keeps the single inode; the reader accepts it only with shared=True.
    w.publish(FRAME, PNG); w.regenerate()
    assert inode(canonical) == inode(ws)
    assert read_beneath(w.ws, FRAME, len(PNG), shared=True) == PNG
    with pytest.raises(ValueError, match='unsafe_file'): read_beneath(w.ws, FRAME, len(PNG))


def test_writable_hardlink_is_still_rejected(run, tmp_path):
    w, _ = run
    secret = tmp_path/'secret'; secret.write_text('secret')
    os.link(secret, w.ws/'work/hard.png')
    with pytest.raises(ValueError, match='unsafe_file'): read_beneath(w.ws, 'work/hard.png', shared=True)


def test_dev_replacing_its_name_never_alters_private_evidence(run):
    w, receipt = run
    ws = w.ws/FRAME
    ws.unlink(); ws.write_bytes(b'forged')
    assert hashlib.sha256((w.canonical/FRAME).read_bytes()).hexdigest() == receipt['sha256']
    w.regenerate()
    assert inode(w.canonical/FRAME) == inode(ws) and ws.read_bytes() == PNG


def test_eval_and_snapshot_share_the_same_inode(run, tmp_path):
    w, receipt = run
    ev = build_workspace(w, dict(version_id='v0001', execution_id='e000001'))
    frame = ev/'frames/overview-000000-sim-0.000000s.png'
    assert inode(frame) == inode(w.canonical/FRAME) and os.lstat(frame).st_nlink == 3
    assert json.loads((ev/'frames.json').read_bytes())[0]['sha256'] == hashlib.sha256(frame.read_bytes()).hexdigest()
    cut = freeze_origin(w, 'post-exec-v1')
    obj = w.private/'snapshots/objects'/receipt['sha256']
    assert inode(obj) == inode(w.canonical/FRAME) and os.lstat(obj).st_nlink == 4
    manifest = json.loads(Path(cut['path']).read_bytes())
    assert manifest['files'][FRAME]['sha256'] == receipt['sha256']
    replay = provision_replay(cut, tmp_path/'replay')
    assert (replay.ws/FRAME).read_bytes() == PNG and inode(replay.ws/FRAME) == inode(replay.canonical/FRAME)


def test_cross_filesystem_falls_back_to_a_read_only_copy(tmp_path, monkeypatch):
    def refuse(*args, **kwargs): raise OSError(errno.EXDEV, 'Invalid cross-device link')
    monkeypatch.setattr(workspace_module.os, 'link', refuse)
    w = Workspace(tmp_path/'agent').prepare({'task_id': 'fixture', 'task_prompt': 'Move safely.'}, CONFIG)
    w.publish(FRAME, PNG)
    canonical, ws = os.lstat(w.canonical/FRAME), os.lstat(w.ws/FRAME)
    assert (canonical.st_ino != ws.st_ino) and canonical.st_nlink == ws.st_nlink == 1
    assert ws.st_mode & 0o777 == 0o444 and (w.ws/FRAME).read_bytes() == PNG


def test_profile_mounts_shared_folders_read_only(run, tmp_path, monkeypatch):
    w, _ = run
    monkeypatch.setattr(isolation, 'real_binary', lambda: Path('/usr/bin/codex'))
    assert '"executions" = "read"' in isolation.build_profile(w.ws)[1]
    ev = build_workspace(w, dict(version_id='v0001', execution_id='e000001'))
    profile = isolation.build_profile(ev)[1]
    assert '"frames" = "read"' in profile and '"executions"' not in profile
    bare = tmp_path/'bare'; bare.mkdir()
    assert '"read" }, ' not in isolation.build_profile(bare)[1].split('":project_roots"')[1].split('}')[0] + '}, '


@pytest.mark.skipif(os.environ.get('D1_REAL_SANDBOX') != '1', reason='real codex sandbox (no inference); opt in with D1_REAL_SANDBOX=1')
def test_real_sandbox_dev_cannot_alter_shared_frame(run):
    w, receipt = run
    script = '''import json,os
p="'''+FRAME+'''"
r={}
for k,f in (("chmod",lambda:os.chmod(p,0o644)),("append",lambda:open(p,"ab").write(b"X")),("unlink",lambda:os.unlink(p)),
            ("rename",lambda:os.rename(p,"work/moved.png")),("link",lambda:os.link(p,"work/copy.png")),("work_write",lambda:open("work/ok","w").write("x"))):
    try:f();r[k]="allowed"
    except OSError as e:r[k]=errno_name=e.errno
print(json.dumps(r))
'''
    command = ['codex', 'sandbox', '-P', 'ws', *isolation.build_profile(w.ws), '-C', str(w.ws), '--', '/usr/bin/python3', '-I', '-c', script]
    done = subprocess.run(command, cwd=w.ws, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    assert result.pop('work_write') == 'allowed'
    assert all(v != 'allowed' for v in result.values()), result
    assert hashlib.sha256((w.canonical/FRAME).read_bytes()).hexdigest() == receipt['sha256']
    assert os.lstat(w.canonical/FRAME).st_mode & 0o777 == 0o444 and os.lstat(w.canonical/FRAME).st_nlink == 2
    assert not (w.ws/'work/copy.png').exists()


@pytest.mark.skipif(os.environ.get('D1_REAL_SANDBOX') != '1', reason='real codex sandbox (no inference); opt in with D1_REAL_SANDBOX=1')
def test_real_preflight_reports_shared_readonly(run):
    import socket
    w, _ = run
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0)); listener.listen()
        receipt = isolation.verify_runtime(w.ws, w.private, planner_port=listener.getsockname()[1])
    assert json.loads(receipt['stdout'])['shared_readonly'] is True
    assert not list((w.ws/'executions').glob('.ro-*'))


@pytest.mark.skipif(os.environ.get('D1_REAL_SANDBOX') != '1', reason='real codex sandbox (no inference); opt in with D1_REAL_SANDBOX=1')
def test_real_preflight_fails_closed_without_read_only_mount(run, monkeypatch):
    import socket
    w, _ = run
    original = isolation.build_profile
    # Profile without the read-only mount; the canary is still placed in executions/.
    monkeypatch.setattr(isolation, 'build_profile',
                        lambda workspace: [a.replace(', "executions" = "read"', '') for a in original(workspace)])
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0)); listener.listen()
        with pytest.raises(isolation.IsolationUnavailable, match='boundary_check_failed'):
            isolation.verify_runtime(w.ws, w.private, planner_port=listener.getsockname()[1])
    assert hashlib.sha256((w.canonical/FRAME).read_bytes()).hexdigest() == hashlib.sha256(PNG).hexdigest()
