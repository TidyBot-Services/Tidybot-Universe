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
    fake = subprocess.CompletedProcess([], 1, stdout='ok 1 - a\nnot ok 2 - b thing\n  not ok 3 - c\n', stderr='')
    monkeypatch.setattr(abctl, 'run', lambda *a, **k:fake)
    assert abctl.ui_failures(tmp_path) == ['b thing', 'c']


def test_ui_tests_run_with_paths_relative_to_the_release(monkeypatch, tmp_path):
    (tmp_path/'benchmarks/attention_harness/tests').mkdir(parents=True);(tmp_path/'benchmarks/attention_harness/tests/a.cjs').write_text('')
    seen = []
    monkeypatch.setattr(abctl, 'run', lambda cmd, **k:seen.append(cmd) or subprocess.CompletedProcess(cmd, 0, stdout='', stderr=''))
    abctl.ui_failures(tmp_path)
    assert seen[0][2:] == ['benchmarks/attention_harness/tests/a.cjs'], 'names must not depend on the release folder'
