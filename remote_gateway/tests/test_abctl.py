"""abctl offline checks: templates, release worktrees, the live symlink, the deploy lock and the drop guard."""
import importlib.util
import json
from pathlib import Path
import re
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('abctl', ROOT/'remote_gateway/deploy/abctl.py')
abctl = importlib.util.module_from_spec(spec);spec.loader.exec_module(abctl)


def git(*args, cwd):
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A fake host: a central bare repo with two commits on main, a workspace and data folders."""
    ws, data = tmp_path/'ws', tmp_path/'data'
    (ws/'repos').mkdir(parents=True);data.mkdir()
    src = tmp_path/'src';src.mkdir()
    git('init', '-q', '-b', 'main', cwd=src)
    for n in (1, 2):
        (src/'f.txt').write_text(str(n))
        git('add', '-A', cwd=src);git('-c', 'user.name=t', '-c', 'user.email=t@x', 'commit', '-qm', f'c{n}', cwd=src)
    (ws/'git').mkdir()
    git('clone', '-q', '--bare', str(src), str(ws/'git/attentionbench.git'), cwd=tmp_path)
    monkeypatch.setattr(abctl, 'HOME', tmp_path)
    monkeypatch.setattr(abctl, 'DEFAULTS', dict(abctl.DEFAULTS, ws=str(ws), data=str(data), test_data=str(tmp_path/'test')))
    monkeypatch.setattr(abctl, '_processes', lambda: iter(()))
    return abctl.config(), src


def test_templates_render_every_placeholder(host):
    cfg, _ = host
    units = abctl.render_units(cfg, ROOT)
    assert set(units) == {'ab-gateway-campus.service', 'ab-harness-campus.service', 'ab-harness-test.service', 'ab-tunnel-campus.service', 'tunnel-run.sh', 'abctl'}
    for name, text in units.items():
        assert not re.search(r'@[A-Z_]+@', text), name
    assert '--key-source stdin' in units['ab-harness-campus.service'] and 'secrets/parcc.key' in units['ab-harness-campus.service']
    assert 'secrets' in units['ab-harness-campus.service'].split('ATTENTION_PREFLIGHT_HIDDEN_PATHS=')[1].splitlines()[0], 'agents never see the key folder'
    assert f'{cfg["ws"]}/repos/attentionbench/remote_gateway/deploy/abctl.py' in units['abctl'], 'abctl runs from the live release'


def test_release_is_a_worktree_and_live_switch_is_atomic(host):
    cfg, src = host
    commit, tree = abctl.release_for(cfg, 'main')
    assert (tree/'.git').is_file() and (tree/'f.txt').read_text() == '2'
    assert abctl.release_for(cfg, 'main') == (commit, tree), 'the same commit reuses its release'
    first, old = abctl.release_for(cfg, 'main~1')
    abctl.point_live(cfg, old);assert cfg['live'].resolve() == old
    abctl.point_live(cfg, tree);assert cfg['live'].resolve() == tree and abctl.live_commit(cfg) == commit
    assert not cfg['live'].with_name('.attentionbench.next').exists()


def test_deploy_refuses_a_ref_that_drops_live_changes(host, monkeypatch, capsys):
    cfg, src = host
    _, tree = abctl.release_for(cfg, 'main');abctl.point_live(cfg, tree)
    monkeypatch.setattr(abctl, 'test_tree', lambda *a:pytest.fail('must stop before testing'))
    args = abctl.argparse.Namespace(ref='main~1', allow_drop=False, now=False, wait_hours=None, by='t')
    assert abctl.cmd_deploy(cfg, args) == 1
    assert 'drops changes that are live now' in capsys.readouterr().out


def test_failed_tests_never_switch(host, monkeypatch):
    cfg, src = host
    _, live = abctl.release_for(cfg, 'main~1');abctl.point_live(cfg, live)
    monkeypatch.setattr(abctl, 'test_tree', lambda *a:'UI tests that pass on the live release fail here:\n  x')
    monkeypatch.setattr(abctl, 'switch', lambda *a:pytest.fail('must not switch'))
    assert abctl.cmd_deploy(cfg, abctl.argparse.Namespace(ref='main', allow_drop=False, now=False, wait_hours=None, by='t')) == 1
    assert cfg['live'].resolve() == live
    row = json.loads((cfg['state']/'deploy.log').read_text().splitlines()[-1])
    assert row['result'] == 'tests failed' and row['by'] == 't'


def test_one_deploy_at_a_time(host):
    cfg, _ = host
    args = abctl.argparse.Namespace(by='a')
    with abctl.Lock(cfg, args, 'deploy'):
        with pytest.raises(SystemExit, match='another deploy is running'):
            with abctl.Lock(cfg, abctl.argparse.Namespace(by='b'), 'deploy'):pass
    with abctl.Lock(cfg, args, 'deploy'):pass  # released


def test_prune_keeps_live_and_original(host):
    cfg, src = host
    cfg['keep_releases'] = 2
    original = cfg['releases']/'original-20261109';original.mkdir(parents=True)
    trees = [abctl.release_for(cfg, ref)[1] for ref in ('main~1', 'main')]
    abctl.point_live(cfg, trees[1]);abctl.prune(cfg)
    assert original.exists() and trees[1].exists()


def test_ui_failure_names_are_parsed(monkeypatch, tmp_path):
    (tmp_path/'benchmarks/attention_harness/tests').mkdir(parents=True)
    fake = subprocess.CompletedProcess([], 1, stdout=f'ok 1 - a\nnot ok 2 - b thing\n  not ok 3 - c\nnot ok 4 - {tmp_path.resolve()}/t/x.cjs\n', stderr='')
    monkeypatch.setattr(abctl, 'run', lambda *a, **k:fake)
    assert abctl.ui_failures(tmp_path) == ['b thing', 'c', 't/x.cjs'], 'absolute file names lose the release folder'


def test_ui_tests_run_with_paths_relative_to_the_release(monkeypatch, tmp_path):
    (tmp_path/'benchmarks/attention_harness/tests').mkdir(parents=True);(tmp_path/'benchmarks/attention_harness/tests/a.cjs').write_text('')
    seen = []
    monkeypatch.setattr(abctl, 'run', lambda cmd, **k:seen.append(cmd) or subprocess.CompletedProcess(cmd, 0, stdout='', stderr=''))
    abctl.ui_failures(tmp_path)
    assert seen[0][2:] == ['benchmarks/attention_harness/tests/a.cjs'], 'names must not depend on the release folder'


def test_prune_never_removes_the_rollback_target(host):
    cfg, src = host
    cfg['keep_releases'] = 2
    prev = abctl.release_for(cfg, 'main~1')[1]
    git('-C', str(src), 'commit', '--allow-empty', '-qm', 'c3', '-c', cwd=src) if False else None
    live = abctl.release_for(cfg, 'main')[1]
    abctl.point_live(cfg, live)
    (cfg['state']).mkdir(parents=True, exist_ok=True)
    (cfg['state']/'history.json').write_text(json.dumps([dict(release=str(live), previous=str(prev), commit='x', at='t')]))
    extra = cfg['releases']/'zz-test';git('--git-dir', str(cfg['bare']), 'worktree', 'add', '--detach', str(extra), 'main', cwd=cfg['ws'])
    abctl.prune(cfg)
    assert prev.exists() and live.exists() and not extra.exists(), 'a newer test-only release goes first'


@pytest.mark.parametrize('store', ['campus', 'test', 'daily-experiments'])
@pytest.mark.parametrize('layout', [
    'frame-cleanup.json',
    'cells/'+'b'*64+'/frame-cleanup.json',
    'artifacts/devexp-one/frame-cleanup.json',
    'artifacts/devexp-one/cells/'+'b'*64+'/frame-cleanup.json',
])
def test_cleanup_receipts_and_processes_count_as_busy(host, monkeypatch, store, layout):
    cfg, _ = host
    if store == 'daily-experiments':
        runs_root = cfg['ws']/'daily-experiments'
        cfg['runs_roots'] = [runs_root]
    else:
        runs_root = cfg['data']/'harness/runs' if store == 'campus' else cfg['test_data']/'runs'
    receipt = runs_root/'devexp-one'/layout;receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps(dict(status='in_progress')))
    assert abctl.cleanup_workers(cfg) == (1,['devexp-one'])
    monkeypatch.setattr(abctl, '_processes', lambda: iter([(Path('/proc/123'),
        ['python', '-m', 'benchmarks.attention_harness.frame_cleanup', str(receipt.parent), '0'])]))
    assert abctl.cleanup_workers(cfg) == (1,['devexp-one'])
    receipt.write_text(json.dumps(dict(status='completed')))
    assert abctl.cleanup_workers(cfg) == (1,['devexp-one'])
    monkeypatch.setattr(abctl, '_processes', lambda: iter(()))
    assert abctl.cleanup_workers(cfg) == (0,[])


def test_wait_idle_waits_for_receipt_without_process(host, monkeypatch, capsys):
    cfg, _ = host
    receipt=cfg['test_data']/('runs/devexp-interrupted/artifacts/devexp-interrupted/cells/'+'b'*64+'/frame-cleanup.json');receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps(dict(status='in_progress')))
    monkeypatch.setattr(abctl, 'workers', lambda:(0,[]))
    monkeypatch.setattr(abctl, 'run', lambda *a,**kw:subprocess.CompletedProcess([],1,stdout='',stderr=''))
    sleeps=[]
    def tick(seconds):
        sleeps.append(seconds);receipt.write_text(json.dumps(dict(status='completed')))
    monkeypatch.setattr(abctl.time,'sleep',tick)
    abctl.wait_idle(cfg,abctl.argparse.Namespace(now=False,wait_hours=1))
    assert sleeps==[120] and 'cleaning frames 1 (devexp-interrupted)' in capsys.readouterr().out


def test_now_mentions_cleanup_and_requires_confirmation(host, monkeypatch):
    cfg, _ = host
    monkeypatch.setattr(abctl, 'workers', lambda:(0,[]))
    monkeypatch.setattr(abctl, 'cleanup_workers', lambda cfg:(1,['devexp-cleaning']))
    prompts=[];monkeypatch.setattr(abctl,'confirm',lambda question:prompts.append(question) or False)
    with pytest.raises(SystemExit,match='not deployed'):
        abctl.wait_idle(cfg,abctl.argparse.Namespace(now=True,wait_hours=None))
    assert 'cleaning frames 1 (devexp-cleaning)' in prompts[0] and 'interrupts frame cleanup' in prompts[0]


@pytest.mark.parametrize('contents', ['{', '[]'])
def test_cleanup_scan_does_not_ignore_bad_receipt(host,monkeypatch,contents):
    cfg,_=host
    receipt=cfg['test_data']/('runs/devexp-bad/artifacts/devexp-bad/cells/'+'b'*64+'/frame-cleanup.json')
    receipt.parent.mkdir(parents=True);receipt.write_text(contents)
    monkeypatch.setattr(abctl,'run',lambda *a,**kw:subprocess.CompletedProcess([],1,stdout='',stderr=''))
    assert abctl.cleanup_workers(cfg)==(1,['devexp-bad:unreadable-cleanup'])


def test_cleanup_scan_stays_out_of_cell_contents(host,monkeypatch):
    cfg,_=host
    cell=cfg['data']/('harness/runs/devexp-one/artifacts/devexp-one/cells/'+'b'*64)
    receipt=cell/'v0001/frame-cleanup.json';receipt.parent.mkdir(parents=True);receipt.write_text('{')
    monkeypatch.setattr(abctl,'run',lambda *a,**kw:subprocess.CompletedProcess([],1,stdout='',stderr=''))
    assert abctl.cleanup_workers(cfg)==(0,[])


def test_status_prints_cleanup_run_ids(host,monkeypatch,capsys):
    cfg,_=host
    cfg['gateway_json'].write_text(json.dumps(dict(min_free_gb=10)))
    monkeypatch.setattr(abctl,'live_commit',lambda cfg:None)
    monkeypatch.setattr(abctl,'systemctl',lambda *a,**kw:'active')
    monkeypatch.setattr(abctl,'listening',lambda port:True)
    monkeypatch.setattr(abctl,'key_loaded',lambda cfg:False)
    monkeypatch.setattr(abctl,'workers',lambda:(0,[]))
    monkeypatch.setattr(abctl,'run',lambda *a,**kw:subprocess.CompletedProcess([],1,stdout='',stderr=''))
    for run_id in ('devexp-a','devexp-b'):
        receipt=cfg['data']/'harness/runs'/run_id/'artifacts'/run_id/'cells'/('b'*64)/'frame-cleanup.json'
        receipt.parent.mkdir(parents=True);receipt.write_text(json.dumps(dict(status='in_progress')))
    abctl.cmd_status(cfg,abctl.argparse.Namespace())
    assert 'cleaning frames 2 (devexp-a devexp-b)' in capsys.readouterr().out


@pytest.mark.parametrize('argv,busy', [
    (['bash', '-c', 'pytest benchmarks/attention_harness/tests/test_frame_cleanup.py'], False),
    (['bash', '-c', 'grep benchmarks.attention_harness.frame_cleanup devexp-false'], False),
    (['pytest', 'benchmarks/attention_harness/tests/test_frame_cleanup.py'], False),
    (['python', '-m', 'pytest', 'benchmarks/attention_harness/tests/test_frame_cleanup.py'], False),
    (['python', '-c', 'pass', 'benchmarks.attention_harness.frame_cleanup'], False),
    (['python', '-m', 'benchmarks.attention_harness.frame_cleanup_extra'], False),
    (['python', 'frame_cleanup.py'], False),
    (['bash', 'frame_cleanup_backfill.py'], False),
    (['python', '-m', 'benchmarks.attention_harness.frame_cleanup', '/runs/devexp-one', '123'], True),
    (['/venv/bin/python3.11', '/tools/frame_cleanup_backfill.py', '--runs-root', '/runs/devexp-one'], True),
    (['python', 'state_log_dedupe.py', '--runs-root=/runs/devexp-one'], True),
])
def test_cleanup_process_argv(tmp_path, monkeypatch, argv, busy):
    proc = tmp_path/'proc'
    (proc/'123').mkdir(parents=True)
    (proc/'123/cmdline').write_bytes(b'\0'.join(os_arg.encode() for os_arg in argv)+b'\0')
    # A live cleanup command belonging to this very process must be ignored.
    own = proc/str(abctl.os.getpid());own.mkdir()
    (own/'cmdline').write_bytes(b'python\0-m\0benchmarks.attention_harness.frame_cleanup\0/runs/devexp-self\0')
    (proc/'456').mkdir()  # A process that exited before cmdline could be read.
    (proc/'789').mkdir();(proc/'789/cmdline').write_bytes(b'')
    processes = abctl._processes
    monkeypatch.setattr(abctl, '_processes', lambda: processes(proc))
    cfg = dict(data=tmp_path/'data', test_data=tmp_path/'test')
    expected = ['devexp-one' if any('devexp-one' in arg for arg in argv) else 'pid:123'] if busy else []
    assert abctl.cleanup_workers(cfg) == (len(expected), expected)


def test_cleanup_process_without_run_id_reports_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(abctl, '_processes', lambda: iter([(Path('/proc/123'), ['python', 'frame_cleanup_backfill.py'])]))
    assert abctl.cleanup_workers(dict(data=tmp_path, test_data=tmp_path)) == (1, ['pid:123'])


def _run(cfg, name, release, state, *, test=False):
    import sqlite3
    run = (cfg['test_data'] if test else cfg['data'])/'harness/runs'/name
    (run/'artifacts'/name).mkdir(parents=True)
    (run/'artifacts'/name/'identity-lock.json').write_text(json.dumps(dict(source_root=str(release))))
    db = sqlite3.connect(run/'ledger.sqlite3');db.execute('CREATE TABLE v2_experiments (id TEXT, payload TEXT)')
    db.execute('INSERT INTO v2_experiments VALUES (?, ?)', (name, json.dumps(dict(state=state))));db.commit();db.close()


def test_prune_keeps_the_release_a_paused_run_needs(host):
    """11-18: three deploys pruned the release a paused run was frozen on, and Resume failed."""
    cfg, src = host
    cfg['keep_releases'] = 1
    paused = abctl.release_for(cfg, 'main~1')[1]
    git('--git-dir', str(cfg['bare']), 'worktree', 'add', '--detach', str(cfg['releases']/'done-run'), 'main~1', cwd=cfg['ws'])
    finished = cfg['releases']/'done-run'
    live = abctl.release_for(cfg, 'main')[1];abctl.point_live(cfg, live)
    _run(cfg, 'devexp-' + 'a' * 24, paused, 'paused')
    _run(cfg, 'devexp-' + 'b' * 24, finished, 'completed', test=True)
    abctl.prune(cfg)
    assert live.exists() and paused.exists(), 'the paused run can still resume'
    assert not finished.exists(), 'a finished run does not hold its release'


def test_an_unreadable_run_keeps_its_release(host):
    cfg, src = host
    cfg['keep_releases'] = 1
    old = abctl.release_for(cfg, 'main~1')[1]
    live = abctl.release_for(cfg, 'main')[1];abctl.point_live(cfg, live)
    run = cfg['data']/'harness/runs'/('devexp-' + 'c' * 24)
    (run/'artifacts'/('devexp-' + 'c' * 24)).mkdir(parents=True)
    (run/'artifacts'/('devexp-' + 'c' * 24)/'identity-lock.json').write_text(json.dumps(dict(source_root=str(old))))
    abctl.prune(cfg)
    assert old.exists()
