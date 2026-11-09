#!/usr/bin/env python3
"""abctl: run the Remote service on its host (qifei) — status, invites, deploy, rollback.

Everything runs as the service account. The live code is the symlink WS/repos/attentionbench, which points
at one release under WS/releases/<commit>; releases are git worktrees of the central repo WS/git/attentionbench.git.
A deploy tests the new release, waits until no run is executing, switches the symlink, restarts the harnesses
and the gateway (never the tunnel, so the public URL stays), checks health and switches back on failure.

Paths come from ~/.config/abctl.json when present; the defaults are the qifei layout. Stdlib only.
Read README-abctl.md next to this file for the walkthrough.
"""
import argparse
import datetime
import fcntl
import getpass
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request

HOME = Path.home()
DEFAULTS = dict(
    ws=str(HOME/'ab-remote-20261107'),            # installed workspace: envs, tools, sim, deployments
    data=str(HOME/'ab-campus-data'),              # campus gateway state, harness store, secrets
    test_data=str(HOME/'ab-remote-data'),         # test harness (8860) store; holds the shared sim.lock
    keep_releases=3,
    ports=dict(test=8860, campus=8870, gateway=8871),
    units=['ab-harness-test', 'ab-harness-campus', 'ab-gateway-campus'],  # restarted by deploy, in this order
    tunnel_unit='ab-tunnel-campus',
    idle_wait_hours=6,
)


def config():
    cfg = dict(DEFAULTS)
    path = HOME/'.config/abctl.json'
    if path.exists():cfg.update(json.loads(path.read_text()))
    ws, data = Path(cfg['ws']), Path(cfg['data'])
    cfg.update(ws=ws, data=data, test_data=Path(cfg['test_data']), bare=ws/'git/attentionbench.git', releases=ws/'releases',
               live=ws/'repos/attentionbench', state=ws/'abctl', gateway_json=data/'gateway.json', stop_file=data/'stop-new-runs',
               url_file=data/'public-url.txt', key_file=data/'secrets/parcc.key', secret=data/'gateway-state/gateway.secret',
               gateway_db=data/'gateway-state/gateway.sqlite3')
    return cfg


# ---------- small helpers ----------
def run(cmd, **kw):
    kw.setdefault('text', True)
    return subprocess.run(cmd, **kw)


def out(cmd, **kw):
    done = run(cmd, capture_output=True, **kw)
    if done.returncode:raise SystemExit(f'failed: {" ".join(map(str, cmd))}\n{done.stderr.strip()}')
    return done.stdout.strip()


def git(cfg, *args, cwd=None):
    return out(['git', *(['--git-dir', str(cfg['bare'])] if cwd is None else []), *args], cwd=cwd)


def systemctl(*args, check=True):
    env = dict(os.environ, XDG_RUNTIME_DIR=f'/run/user/{os.getuid()}')
    done = run(['systemctl', '--user', *args], capture_output=True, env=env)
    if check and done.returncode:raise SystemExit(f'systemctl {" ".join(args)} failed: {done.stderr.strip()}')
    return done.stdout.strip()


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec='seconds')


def say(message):
    print(message, flush=True)


def confirm(question):
    if not sys.stdin.isatty():raise SystemExit(f'{question} — needs a person at the terminal (type yes).')
    return input(f'{question} Type yes to go on: ').strip() == 'yes'


def workers():
    """Run workers executing on this host (both harnesses). A run between executions has none."""
    done = run(['pgrep', '-af', '[d]aily_dev_v2 --worker'], capture_output=True)
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    ids = sorted({part for line in lines for part in line.split() if part.startswith('devexp-')})
    return len(lines), ids


def listening(port):
    return f':{port} ' in run(['ss', '-ltn'], capture_output=True).stdout


def http_ok(url):
    try:
        with urllib.request.urlopen(url, timeout=10) as response:return response.status == 200
    except Exception:
        return False


def key_loaded(cfg):
    """True when the campus harness process carries the PARCC key (counted, never shown)."""
    done = run(['pgrep', '-f', f'[u]i_server --store {cfg["data"]}/harness'], capture_output=True)
    for pid in done.stdout.split():
        try:
            if any(item.startswith(b'PARCC_API_KEY=') and len(item) > 14 for item in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0')):return True
        except OSError:
            pass
    return False


def live_commit(cfg):
    target = cfg['live'].resolve()
    try:return out(['git', 'rev-parse', 'HEAD'], cwd=target)
    except SystemExit:return None


def public_url(cfg):
    try:return cfg['url_file'].read_text().split()[0]
    except (OSError, IndexError):return None


def log(cfg, **row):
    cfg['state'].mkdir(parents=True, exist_ok=True)
    with open(cfg['state']/'deploy.log', 'a') as f:f.write(json.dumps(dict(at=now(), **row), ensure_ascii=False) + '\n')


def who(args):
    ssh = os.environ.get('SSH_CONNECTION', '').split()
    return dict(by=getattr(args, 'by', None) or os.environ.get('ABCTL_USER') or getpass.getuser(), from_ip=ssh[0] if ssh else None)


# ---------- read-only commands ----------
def cmd_status(cfg, args):
    say(f'live      {cfg["live"]} -> {cfg["live"].resolve()}')
    commit = live_commit(cfg)
    if commit:say(f'commit    {out(["git", "log", "-1", "--format=%h %s (%an, %ar)"], cwd=cfg["live"].resolve())}')
    say(f'url       {public_url(cfg) or "(none yet)"}')
    for unit in [*cfg['units'], cfg['tunnel_unit']]:
        say(f'{unit:20s} {systemctl("is-active", unit, check=False)}')
    for name, port in cfg['ports'].items():say(f'port {port} ({name}) {"listening" if listening(port) else "DOWN"}')
    say(f'PARCC key in campus harness: {"yes" if key_loaded(cfg) else "NO"}')
    count, ids = workers()
    say(f'executing {count} worker(s) {" ".join(ids)}')
    say(f'new runs  {"PAUSED (" + str(cfg["stop_file"]) + ")" if cfg["stop_file"].exists() else "open"}')
    free = shutil.disk_usage(cfg['data']).free / 1e9
    say(f'disk free {free:.0f} GB (gateway refuses new runs under {json.loads(cfg["gateway_json"].read_text()).get("min_free_gb")} GB)')
    path = cfg['state']/'deploy.log'
    if path.exists():
        say('recent deploys:')
        for line in path.read_text().splitlines()[-5:]:
            row = json.loads(line);say(f'  {row["at"]} {row.get("action")} {row.get("commit", "")[:12]} {row.get("result")} by {row.get("by")}')


def cmd_doctor(cfg, args):
    problems = []
    def check(ok, text):
        say(f'{"ok " if ok else "!! "} {text}')
        if not ok:problems.append(text)
    check(cfg['ws'].is_dir(), f'workspace {cfg["ws"]}')
    check((cfg['ws']/'envs/ui/bin/python').exists(), 'harness python envs/ui/bin/python')
    check(cfg['bare'].is_dir(), f'central repo {cfg["bare"]}')
    check(cfg['live'].is_symlink(), f'{cfg["live"]} is a release symlink (run "abctl adopt" once if not)')
    cloudflared = cfg['ws']/'tools/bin/cloudflared'
    check(cloudflared.exists() and os.access(cloudflared, os.X_OK), f'cloudflared at {cloudflared}')
    for path, mode in ((cfg['key_file'], 0o600), (cfg['secret'], 0o600)):
        ok = path.exists() and (path.stat().st_mode & 0o077) == 0
        check(ok, f'{path.name} exists and is owner-only ({oct(mode)})')
    check(cfg['gateway_json'].exists(), f'gateway config {cfg["gateway_json"]}')
    linger = run(['loginctl', 'show-user', getpass.getuser(), '-p', 'Linger'], capture_output=True).stdout.strip()
    check(linger == 'Linger=yes', 'systemd linger on (services survive logout)')
    for unit in [*cfg['units'], cfg['tunnel_unit']]:check(systemctl('is-enabled', unit, check=False) == 'enabled', f'unit {unit} enabled')
    check(shutil.which('node') is not None, 'node (UI tests)')
    free = shutil.disk_usage(cfg['data']).free / 1e9
    check(free > 100, f'disk free {free:.0f} GB > 100 GB')
    say('all good' if not problems else f'{len(problems)} problem(s)')
    return 1 if problems else 0


def cmd_url(cfg, args):
    say(public_url(cfg) or 'no public URL yet (is the tunnel running? abctl logs tunnel)')


def cmd_invite(cfg, args):
    say(out([sys.executable, '-B', '-m', 'remote_gateway', '--config', str(cfg['gateway_json']), 'invite', '--days', str(args.days)], cwd=cfg['live'].resolve()))
    say(f'site: {public_url(cfg)}')


def cmd_pause(cfg, args):
    cfg['stop_file'].touch();say('new runs paused; runs already going continue')


def cmd_resume(cfg, args):
    cfg['stop_file'].unlink(missing_ok=True);say('new runs open')


def cmd_logs(cfg, args):
    unit = {'test': 'ab-harness-test', 'campus': 'ab-harness-campus', 'gateway': 'ab-gateway-campus', 'tunnel': cfg['tunnel_unit']}.get(args.service, args.service)
    env = dict(os.environ, XDG_RUNTIME_DIR=f'/run/user/{os.getuid()}')
    return run(['journalctl', '--user', '-u', unit, '-n', str(args.lines), '--no-pager'], env=env).returncode


def cmd_releases(cfg, args):
    live = cfg['live'].resolve()
    for path in sorted(cfg['releases'].iterdir(), key=lambda p:p.stat().st_mtime):
        try:line = out(['git', 'log', '-1', '--format=%h %s (%ar)'], cwd=path)
        except SystemExit:line = '(not a git tree)'
        say(f'{"*" if path == live else " "} {path.name:16s} {line}')


# ---------- tests ----------
def ui_failures(tree):
    # Relative paths, so a failure has the same name in every release folder.
    done = run(['node', '--test', *sorted(str(p.relative_to(tree)) for p in (tree/'benchmarks/attention_harness/tests').glob('*.cjs'))], capture_output=True, cwd=tree)
    prefix = f'{Path(tree).resolve()}/'  # node prints whole-file failures with absolute paths
    return sorted({line.split(' - ', 1)[1].strip().replace(prefix, '') for line in done.stdout.splitlines() if line.lstrip().startswith('not ok') and ' - ' in line})


def baseline(cfg, commit, tree):
    """UI test failures of the live release, measured once on this host and cached (node versions differ by host)."""
    path = cfg['state']/f'ui-failures-v3-{commit}.json'
    if path.exists():return json.loads(path.read_text())
    say(f'measuring the UI test baseline of the live release {commit[:12]} …')
    value = ui_failures(tree);cfg['state'].mkdir(parents=True, exist_ok=True);path.write_text(json.dumps(value))
    return value


def test_tree(cfg, tree):
    """Gateway tests must all pass; UI tests may not fail anywhere the live release passes."""
    say('gateway tests …')
    # The harness-link tests resolve paths.json through the installed workspace, as the services do.
    env = dict(os.environ, ATTENTIONBENCH_WORKSPACE=str(cfg['ws']), PYTHONDONTWRITEBYTECODE='1')
    done = run([str(cfg['ws']/'envs/ui/bin/python'), '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'remote_gateway/tests'], capture_output=True, cwd=tree, env=env)
    say(done.stdout.strip().splitlines()[-1] if done.stdout.strip() else done.stderr.strip()[-400:])
    if done.returncode:return f'gateway tests failed:\n{done.stdout[-3000:]}'
    say('UI tests …')
    commit = live_commit(cfg)
    known = set(baseline(cfg, commit, cfg['live'].resolve())) if commit else set()
    new = [name for name in ui_failures(tree) if name not in known]
    if new:return 'UI tests that pass on the live release fail here:\n  ' + '\n  '.join(new)
    say('tests ok (no new UI failures)')
    return None


def release_for(cfg, ref):
    commit = git(cfg, 'rev-parse', '--verify', f'{ref}^{{commit}}')
    tree = cfg['releases']/commit[:12]
    if not tree.exists():
        cfg['releases'].mkdir(parents=True, exist_ok=True)
        git(cfg, 'worktree', 'add', '--detach', str(tree), commit)
    return commit, tree


def cmd_test(cfg, args):
    commit, tree = release_for(cfg, args.ref)
    say(f'testing {commit[:12]} in {tree}')
    problem = test_tree(cfg, tree)
    if problem:say(problem);return 1
    return 0


# ---------- deploy / rollback ----------
class Lock:
    def __init__(self, cfg, args, action):
        cfg['state'].mkdir(parents=True, exist_ok=True)
        self.path, self.info = cfg['state']/'deploy.lock', dict(action=action, at=now(), pid=os.getpid(), **who(args))
    def __enter__(self):
        self.f = open(self.path, 'a+')
        try:fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.f.seek(0);raise SystemExit(f'another deploy is running: {self.f.read().strip()}')
        self.f.seek(0);self.f.truncate();self.f.write(json.dumps(self.info));self.f.flush()
        return self
    def __exit__(self, *exc):
        self.f.seek(0);self.f.truncate();fcntl.flock(self.f, fcntl.LOCK_UN);self.f.close()


def wait_idle(cfg, args):
    count, ids = workers()
    if not count:return
    if args.now:
        if not confirm(f'{count} run worker(s) are executing ({" ".join(ids)}); restarting now stops them.'):raise SystemExit('not deployed')
        return
    deadline = time.time() + float(args.wait_hours or cfg['idle_wait_hours']) * 3600
    while count:
        if time.time() > deadline:raise SystemExit(f'still busy after {args.wait_hours or cfg["idle_wait_hours"]} h: {" ".join(ids)} — not deployed')
        say(f'{datetime.datetime.now():%H:%M} waiting: {count} run worker(s) executing ({" ".join(ids)}); checking again in 2 min')
        time.sleep(120);count, ids = workers()


def backup_db(cfg):
    folder = cfg['data']/'gateway-backups';folder.mkdir(exist_ok=True)
    target = folder/f'gateway-{datetime.datetime.now():%Y%m%d-%H%M%S}.sqlite3'
    with sqlite3.connect(cfg['gateway_db']) as source, sqlite3.connect(target) as copy:source.backup(copy)
    for old in sorted(folder.glob('gateway-*.sqlite3'))[:-10]:old.unlink()
    return target


def point_live(cfg, tree):
    link = cfg['live'].with_name('.attentionbench.next')
    link.unlink(missing_ok=True);link.symlink_to(tree);os.replace(link, cfg['live'])


def restart_and_check(cfg):
    for unit in cfg['units']:systemctl('restart', unit)
    deadline = time.time() + 120
    while time.time() < deadline:
        time.sleep(5)
        missing = [f'port {p}' for p in cfg['ports'].values() if not listening(p)]
        if not missing and not key_loaded(cfg):missing.append('PARCC key in campus harness')
        gw = cfg['ports']['gateway']
        if not missing:
            missing = [path for path in ('/ui/', '/api/lobby') if not http_ok(f'http://127.0.0.1:{gw}{path}')]
        if not missing:return None
    return 'not healthy after 120 s: ' + ', '.join(missing)


def switch(cfg, args, commit, tree, action):
    was_paused = cfg['stop_file'].exists()
    cfg['stop_file'].touch()
    previous = cfg['live'].resolve()
    try:
        backup = backup_db(cfg);say(f'gateway database backed up: {backup}')
        point_live(cfg, tree);say(f'live -> {tree}')
        problem = restart_and_check(cfg)
        if problem:
            say(f'!! {problem}; switching back to {previous}')
            point_live(cfg, previous);again = restart_and_check(cfg)
            log(cfg, action=action, commit=commit, result='rolled back', error=problem, after_rollback=again or 'healthy', **who(args))
            raise SystemExit(f'deploy failed and was rolled back ({"healthy" if not again else again}): {problem}')
        history = cfg['state']/'history.json'
        rows = json.loads(history.read_text()) if history.exists() else []
        rows.append(dict(at=now(), release=str(tree), commit=commit, previous=str(previous)));history.write_text(json.dumps(rows[-50:], indent=1))
        log(cfg, action=action, commit=commit, result='ok', release=str(tree), **who(args))
        say(f'{action} done: {commit[:12]} is live at {public_url(cfg)}')
    finally:
        if not was_paused:cfg['stop_file'].unlink(missing_ok=True)
    prune(cfg)


def prune(cfg):
    # Never removed: the live release and the one before it (the rollback target). Others: the newest are kept.
    live = cfg['live'].resolve()
    history = cfg['state']/'history.json'
    rows = json.loads(history.read_text()) if history.exists() else []
    keep = {live, *(Path(rows[-1]['previous']).resolve() for _ in [0] if rows)}
    trees = sorted((p for p in cfg['releases'].iterdir() if p.is_dir() and p.resolve() not in keep), key=lambda p:p.stat().st_mtime)
    for old in trees[:max(0, len(trees) - max(0, cfg['keep_releases'] - len(keep)))]:
        if (old/'.git').is_file():git(cfg, 'worktree', 'remove', '--force', str(old))
        # A non-worktree release (the adopted original) is kept: it is the oldest fallback and is removed only by hand.


def cmd_deploy(cfg, args):
    with Lock(cfg, args, 'deploy'):
        commit, tree = release_for(cfg, args.ref)
        live = live_commit(cfg)
        if live == commit:say(f'{commit[:12]} is already live');return 0
        if live and run(['git', '--git-dir', str(cfg['bare']), 'merge-base', '--is-ancestor', live, commit]).returncode:
            say(f'!! {args.ref} ({commit[:12]}) does not contain the live commit {live[:12]}: deploying it drops changes that are live now.')
            say('   Merge or rebase onto the live commit first (git merge main), or pass --allow-drop if that is intended.')
            if not args.allow_drop or not confirm('Deploy anyway and drop the live changes?'):return 1
        say(f'deploying {args.ref} = {commit[:12]} ({out(["git", "log", "-1", "--format=%s", commit], cwd=tree)})')
        problem = test_tree(cfg, tree)
        if problem:
            log(cfg, action='deploy', commit=commit, result='tests failed', error=problem[:2000], **who(args))
            say(problem);say('not deployed');return 1
        wait_idle(cfg, args)
        switch(cfg, args, commit, tree, 'deploy')
    return 0


def cmd_rollback(cfg, args):
    with Lock(cfg, args, 'rollback'):
        history = cfg['state']/'history.json'
        rows = json.loads(history.read_text()) if history.exists() else []
        live = cfg['live'].resolve()
        target = Path(args.release) if args.release else Path(rows[-1]['previous']) if rows and Path(rows[-1]['release']) == live else None
        if target and not target.is_absolute():target = cfg['releases']/target
        if not target or not target.exists():raise SystemExit('no previous release to roll back to (see abctl releases; abctl rollback <name>)')
        commit = out(['git', 'rev-parse', 'HEAD'], cwd=target) if (target/'.git').exists() else target.name
        say(f'rolling back {live.name} -> {target.name}')
        wait_idle(cfg, args)
        switch(cfg, args, commit, target, 'rollback')
    return 0


# ---------- setup ----------
def cmd_adopt(cfg, args):
    """One time: turn the plain checkout at repos/attentionbench into a release and the symlink."""
    live = cfg['live']
    if live.is_symlink():say(f'{live} is already a symlink');return 0
    commit = git(cfg, 'rev-parse', '--verify', f'{args.ref}^{{commit}}')
    expected = git(cfg, 'rev-parse', f'{commit}^{{tree}}')
    actual = out(['git', 'rev-parse', 'HEAD^{tree}'], cwd=live)
    if expected != actual and not confirm(f'the live checkout tree {actual[:12]} differs from {args.ref} {expected[:12]}.'):return 1
    with Lock(cfg, args, 'adopt'):
        wait_idle(cfg, args)
        cfg['releases'].mkdir(parents=True, exist_ok=True)
        original = cfg['releases']/f'original-{datetime.date.today():%Y%m%d}'
        os.rename(live, original)
        _, tree = release_for(cfg, commit)
        live.symlink_to(original)  # keep serving the exact original until the switch below
        switch(cfg, args, commit, tree, 'adopt')
    return 0


def cmd_workspace(cfg, args):
    """A personal worktree of the central repo for editing; commits land in the central repo directly."""
    path = HOME/'ab-dev'/args.name
    if path.exists():say(f'{path} exists');return 0
    branches = git(cfg, 'branch', '--list', args.name)
    git(cfg, 'worktree', 'add', str(path), *([args.name] if branches else ['-b', args.name, args.base]))
    say(f'workspace ready: {path} (branch {args.name}, from {args.base if not branches else "existing branch"})')
    say(f'  cd {path}; edit; git add -A; git -c user.name="Your Name" -c user.email=you@upenn.edu commit -m "..."')
    say(f'  abctl test {args.name}      # same checks as a deploy, nothing goes live')
    say(f'  abctl deploy {args.name}    # goes live when no run is executing')
    return 0


def render_units(cfg, source):
    values = dict(WS=str(cfg['ws']), DATA=str(cfg['data']), TEST_DATA=str(cfg['test_data']), **{f'PORT_{k.upper()}': str(v) for k, v in cfg['ports'].items()})
    units = {}
    for path in sorted((source/'remote_gateway/deploy/host').glob('*.in')):
        text = path.read_text()
        for key, value in values.items():text = text.replace(f'@{key}@', value)
        units[path.name[:-3]] = text
    return units


def cmd_install(cfg, args):
    """New host or repaired host: write the systemd units and tunnel script from the templates, check secrets."""
    source = Path(__file__).resolve().parents[2]
    units = render_units(cfg, source)
    targets = {name: (cfg['ws']/'tools/bin'/name if name == 'abctl' else cfg['data']/name if name.endswith('.sh') else HOME/'.config/systemd/user'/name) for name in units}
    changed = [name for name in units if not targets[name].exists() or targets[name].read_text() != units[name]]
    for name in units:say(f'{"changed" if name in changed else "same   "} {targets[name]}')
    if args.dry_run:
        for name in changed:say(f'--- {name}\n{units[name]}')
        return 0
    if not cfg['key_file'].exists():
        cfg['key_file'].parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        key = getpass.getpass('PARCC key (not shown; Enter to skip, GLM then unavailable): ').strip()
        if key:
            fd = os.open(cfg['key_file'], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as f:f.write(key + '\n')
    for name in changed:
        targets[name].parent.mkdir(parents=True, exist_ok=True);targets[name].write_text(units[name])
        if name.endswith('.sh') or name == 'abctl':targets[name].chmod(0o755)
    systemctl('daemon-reload')
    for unit in [*cfg['units'], cfg['tunnel_unit']]:systemctl('enable', unit)
    say('units installed and enabled. Start them with: abctl restart-all (or systemctl --user start <unit>)')
    return cmd_doctor(cfg, args)


def cmd_restart_all(cfg, args):
    with Lock(cfg, args, 'restart'):
        wait_idle(cfg, args)
        for unit in [*cfg['units'], cfg['tunnel_unit']]:systemctl('restart', unit)
        say('restarted; the tunnel restart gives a NEW public URL in a few seconds (abctl url)')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog='abctl', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--by', help='your name for the deploy log (default: $ABCTL_USER or the login name)')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('status', 'doctor', 'url', 'pause', 'resume', 'releases'):sub.add_parser(name)
    sub.add_parser('invite').add_argument('--days', type=int, default=1)
    logs = sub.add_parser('logs');logs.add_argument('service', help='test | campus | gateway | tunnel');logs.add_argument('-n', '--lines', type=int, default=80)
    sub.add_parser('test').add_argument('ref')
    deploy = sub.add_parser('deploy');deploy.add_argument('ref')
    rollback = sub.add_parser('rollback');rollback.add_argument('release', nargs='?')
    adopt = sub.add_parser('adopt');adopt.add_argument('ref', nargs='?', default='main')
    restart = sub.add_parser('restart-all')
    for p in (deploy, rollback, adopt, restart):
        p.add_argument('--now', action='store_true', help='do not wait for running runs (asks you to type yes)')
        p.add_argument('--wait-hours', type=float)
    deploy.add_argument('--allow-drop', action='store_true')
    workspace = sub.add_parser('workspace');workspace.add_argument('name');workspace.add_argument('--base', default='main')
    sub.add_parser('install').add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    cfg = config()
    os.environ.setdefault('PYTHONDONTWRITEBYTECODE', '1')
    return globals()[f'cmd_{args.command.replace("-", "_")}'](cfg, args) or 0


if __name__ == '__main__':
    sys.exit(main())
