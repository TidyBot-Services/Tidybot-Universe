"""Start a simulated crowd against the real gateway.

  offline: this file starts its own fake harness, a new gateway and the monitor. Nothing real is touched.
  real:    needs a gateway that already runs, and a release: see PLAN.md. Not part of the offline check.

Everything is written under --dir. Limits live only in the gateway.json written there, never in the defaults.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import secrets as tokens
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request

from . import sim_users

ROOT = Path(__file__).resolve().parents[2]
CAST = [('U1', '阿青', '认真回复'), ('U2', '小白', '排队时取消'), ('U3', '老周', '跳过求助'), ('U4', '小林', '不回复'), ('U5', '大海', '中途取消'),
        ('U6', '阿杰', '断线重连'), ('U7', '小雨', '关页再回来'), ('U8', '黑客', '越权尝试'), ('G1', '路人', '访客')]
# The time-share test (TESTPLAN-remote-sim-timeshare-20261108): three slots, one robot, six people.
TIMESHARE_CAST = [('U1', '阿青', '正常跑完'), ('U2', '小白', '写代码慢'), ('U3', '老周', '连续提交'), ('U4', '小林', '排队时取消'), ('U5', '大海', '无进展'), ('U6', '阿杰', '第N+1个')]
TIMESHARE_REAL_CAST = [('R1', '阿青', '共用机器人'), ('R2', '小白', '共用机器人'), ('R3', '老周', '共用机器人'), ('R4', '小林', '等运行名额'), ('G1', '路人', '访客')]
# Everything a limit can open, at what the harness can honour. One run on the robot and the disk lines are not limits here: see PLAN.md.
NO_NEW_RUNS = Path.home() / '.config/disk-hygiene/no-new-runs'
OPEN_LIMITS = dict(versions=10, help_requests=10, runs_per_day=100000, wall_seconds=21600, active_runs=1000, requests_per_second=100000, request_burst=1000000,
                   live_requests_per_second=100000, live_request_burst=1000000, logins_per_hour=100000)


def read_quota(url):
    """Codex weekly quota: from a harness URL, or with 'local' straight from this machine's Codex session logs, as the harness's QuotaMonitor reads it."""
    if url == 'local':
        from benchmarks.attention_harness.dev_agent_quota import read_weekly_quota
        value = read_weekly_quota()
        if value is None:raise OSError('Codex quota unreadable from ~/.codex/sessions')
        return dict(value, source='local ~/.codex/sessions')
    with urllib.request.urlopen(url, timeout=8) as response:return json.load(response)


def wait_up(url, seconds=20):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):return
        except OSError:time.sleep(.2)
    raise SystemExit(f'没有起来：{url}')


def gateway_config(directory, *, port, upstream, sim_lock=None, stop_files=(), slots=3):
    # Run slots are not a limit to open: they follow the host's GPU memory and disk (the time-share plan's P0).
    value = dict(state_dir=str(directory / 'gateway-state'), upstream=upstream, ui_dir=str(ROOT / 'benchmarks/attention_harness/ui'), listen_port=port, cookie_secure=False, stop_files=list(stop_files),
                 limits=dict(OPEN_LIMITS, run_slots=slots), sim_lock=str(sim_lock) if sim_lock else None)
    path = directory / 'gateway.json';path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')
    return path


def gateway_cli(config, *args):
    return subprocess.run([sys.executable, '-B', '-m', 'remote_gateway', '--config', str(config), *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def invites(config, count, path):
    """Codes go to one owner-only file, and to the simulated people. Nothing prints them."""
    codes = [gateway_cli(config, 'invite', '--days', '2').split()[1] for _ in range(count)]
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as stream:json.dump(codes, stream)
    return codes


def spawn(arguments, log):
    return subprocess.Popen([sys.executable, '-B', *arguments], cwd=ROOT, stdout=open(log, 'ab'), stderr=subprocess.STDOUT, env={**os.environ, 'PYTHONPATH': str(ROOT)})


def send_fault(port, value):
    request = urllib.request.Request(f'http://127.0.0.1:{port}/sim/fault', data=value.encode(), method='POST')
    urllib.request.urlopen(request, timeout=5).close()


def outcome_checks(port, events, cast, codes, model_source='codex'):
    """What the person saw is one thing; this asks the fake host what really reached it."""
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/sim/state', timeout=5) as response:world = json.load(response)
    rows = [json.loads(line) for line in events.open(encoding='utf-8')]
    log = sim_users.Log(events, codes)
    for run_id, run in world['runs'].items():
        model, provider = ('gpt-6.1-sol', 'openai') if model_source == 'codex' else ('parcc/GLM', 'parcc')
        if run['dev_model'] != model or run.get('dev_reasoning_effort') != 'high' or run['quota_stop_percent'] != 77 or run['eval_provider'] != provider or run.get('advisor_provider') != provider:
            log.write(lane='-', kind='guard', message=f'run {run_id[-8:]} 的配置不对：{run}')
    wanted = {'认真回复': 'human', '关页再回来': 'human', '断线重连': 'human', '跳过求助': 'skip', '不回复': 'advisor'}
    for lane, name, story in cast:
        run_id = next((row['run_id'] for row in reversed(rows) if row.get('lane') == lane and row.get('run_id') and story != '访客'), None)
        run = world['runs'].get(run_id)
        if story not in wanted or run is None:continue
        got = [item['provider'] for item in run['helped']]
        log.write(lane=lane, name=name, kind='check', check=f'后端收到的求助结果是 {wanted[story]}', ok=wanted[story] in got, detail=f'后端记录 {got}')
    # Time-share: the runs really overlapped, and the robot still had one Try at a time (the fake records any breach as a violation).
    log.write(lane='-', kind='check', check=f'同时开着的 run 最多 {world["slots"]} 个，且确实有多个同时开着', ok=2 <= world.get('peak_open', 0) <= world['slots'],
              detail=f'峰值 {world.get("peak_open")} 个，名额 {world["slots"]}')
    waited = {run_id[-8:]:run['waited_seconds'] for run_id, run in world['runs'].items() if run.get('waited_seconds')}
    log.write(lane='-', kind='check', check='有 Try 排过机器人（等待不计入 run 时间）', ok=bool(waited), detail=f'等过的 run 与秒数：{waited}')
    if any(story in sim_users.TIMESHARE_STORIES for _, _, story in cast):timeshare_checks(world, rows, cast, log)
    log.write(lane='-', kind='check-end')
    # The verdict is read after these lines are in, so a wrong outcome cannot pass.
    time.sleep(1.5)


def timeshare_checks(world, rows, cast, log):
    """What the fake host saw of the robot, story by story. Violations (overlap, more open than slots, two turns in a row) are alarms already."""
    lanes = {}
    for row in rows:
        if row.get('run_id') and row.get('lane') not in lanes and row.get('lane') != '-':lanes[row['lane']] = row['run_id']
    runs, turns = world['runs'], world.get('turns', [])
    def check(lane, name, ok, detail):log.write(lane=lane, name=dict((c[0], c[1]) for c in cast).get(lane, lane), kind='check', check=name, ok=bool(ok), detail=detail)
    spans = sorted((t for t in turns if t.get('released')), key=lambda t:t['granted'])
    check('-', '机器人上同一时间只有一个 Try（按时间线核对）', all(a['released'] <= b['granted'] + 1e-3 for a, b in zip(spans, spans[1:])), f'{len(spans)} 次执行')
    check('-', '没有违反分时规则', not world.get('violations'), world.get('violations'))
    check('-', f'同时开着的 run 达到名额 {world["slots"]}', world.get('peak_open') == world['slots'], f'峰值 {world.get("peak_open")}')
    for lane, name, story in cast:
        run = runs.get(lanes.get(lane));mine = [t for t in turns if t['run'] == lanes.get(lane)]
        if run is None:
            check(lane, f'{story}：后端有这个 run', False, '没有找到');continue
        if story == '正常跑完':
            check(lane, '拿到机器人、执行、交回，每次都交回了', run['tries'] == world.get('tries', 3) and all(t.get('released') for t in mine) and run['reason'] == 'agent_idle', f'{run["tries"]} 次 Try，结束原因 {run["reason"]}')
        if story == '写代码慢':
            between = [t for t in turns if mine and t['run'] != run and mine[0]['released'] and mine[0]['released'] <= t['granted'] <= (mine[1]['granted'] if len(mine) > 1 else 1e18)]
            check(lane, '它写代码时别人在用机器人', between, f'两次 Try 之间别人执行了 {len(between)} 次')
            check(lane, '没有因为别人占机器人被判无进展', run['reason'] == 'agent_idle', f'结束原因 {run["reason"]}')
        if story == '连续提交':
            check(lane, '排过机器人（等待不计入时长）', run['waited_seconds'] > 0, f'等了 {run["waited_seconds"]} 秒')
        if story == '排队时取消':
            check(lane, '离开队列，从没拿过机器人', run['reason'] == 'operator_cancel' and not mine, f'{run["reason"]}，拿过 {len(mine)} 次')
        if story == '无进展':
            check(lane, '被“无进展”停下，不是取消', run['reason'] == 'no_progress' and bool(run.get('stop_detail')), f'{run["reason"]}：{run.get("stop_detail")}')
        if story == '第N+1个':
            began = run.get('started_at') or 0
            freed = [r for r in runs.values() if r.get('ended_at') and r['ended_at'] <= began + 5 and r is not run]
            check(lane, '有人结束后自动补上', run['tries'] > 0 and freed, f'开始于有 {len(freed)} 个 run 结束之后，执行了 {run["tries"]} 次')
    if 'kill_holder' in json.dumps(world.get('fault_log', [])) or any(t.get('reclaimed') for t in turns):
        dead = [t for t in turns if t.get('reclaimed')]
        later = [t for t in turns if dead and t['granted'] > dead[0]['released']]
        check('-', '持有者被杀后机器人被回收，别人接着执行', dead and later, f'回收 {len(dead)} 次，之后执行 {len(later)} 次')


def port_free(*ports):
    """An old process on one of these ports would answer in place of the new one, and the results would look real."""
    for port in ports:
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1', port)) == 0:raise SystemExit(f'端口 {port} 已被占用，先停掉那个进程')


def offline(args):
    port_free(args.gateway_port, args.fake_port, args.monitor_port)
    directory = args.dir.resolve();directory.mkdir(parents=True, exist_ok=True)
    (directory / 'gateway-state').mkdir(exist_ok=True, mode=0o700)
    for name in ('events.jsonl', 'report.json'):(directory / name).unlink(missing_ok=True)
    shutil.rmtree(directory / 'shots', ignore_errors=True)
    config = gateway_config(directory, port=args.gateway_port, upstream=f'http://127.0.0.1:{args.fake_port}', slots=args.slots)
    state = directory / 'gateway-state'
    # A pause left by the last run in this folder (the sweep pauses the gateway) would refuse every new run.
    for name in ('gateway.sqlite3', 'audit.jsonl', 'gateway.secret', 'public-pause'):(state / name).unlink(missing_ok=True)
    gateway_cli(config, 'secret')
    processes = [spawn(['-m', 'remote_gateway.simtest.fake_harness', '--port', str(args.fake_port), '--secret-file', str(state / 'gateway.secret'), '--speed', str(args.speed), '--try-seconds', str(args.try_seconds), '--think-seconds', str(args.think_seconds), '--slots', str(args.slots), '--no-progress-seconds', str(args.no_progress_seconds)], directory / 'fake.log')]
    try:
        wait_up(f'http://127.0.0.1:{args.fake_port}/sim/state')
        processes.append(spawn(['-m', 'remote_gateway.simtest.codex_gateway', '--config', str(config)], directory / 'gateway.log'))
        wait_up(f'http://127.0.0.1:{args.gateway_port}/login')
        processes.append(spawn(['-m', 'remote_gateway.simtest.monitor', '--port', str(args.monitor_port), '--dir', str(directory), '--gateway', f'http://127.0.0.1:{args.gateway_port}', '--fake', f'http://127.0.0.1:{args.fake_port}', '--mode', 'offline'], directory / 'monitor.log'))
        wait_up(f'http://127.0.0.1:{args.monitor_port}/api/state')
        whole = TIMESHARE_CAST if args.cast == 'timeshare' else CAST
        cast = [c for c in whole if c[0] in args.only] if args.only else whole
        codes = invites(config, len(cast), directory / 'invites.json')
        people = [(lane, name, story, code) for (lane, name, story), code in zip(cast, codes)]
        print(f'监控页 http://127.0.0.1:{args.monitor_port}/', flush=True)
        settings = sim_users.Settings(base=f'http://127.0.0.1:{args.gateway_port}', out=directory, scale=args.scale, drop_seconds=args.drop_seconds, reply_command=(sys.executable, '-B', '-m', 'remote_gateway.simtest.reply_writer', '--canned'), reply_source='canned', model_source=args.model_source,
                                      fake=f'http://127.0.0.1:{args.fake_port}')
        for at, value in args.fault:threading.Timer(at, send_fault, (args.fake_port, value)).start()
        asyncio.run(sim_users.run_all(settings, people, directory / 'events.jsonl', codes))
        sweep(config, directory / 'events.jsonl', codes)
        outcome_checks(args.fake_port, directory / 'events.jsonl', cast, codes, args.model_source)
        with urllib.request.urlopen(f'http://127.0.0.1:{args.monitor_port}/api/state', timeout=5) as response:final = json.load(response)
        (directory / 'report.json').write_text(json.dumps(final, ensure_ascii=False, indent=1), encoding='utf-8')
        print(json.dumps(final['totals'], ensure_ascii=False), flush=True)
        if args.hold:
            print('监控页保持打开，按 Ctrl-C 结束', flush=True)
            signal.pause()
        return 0 if final['totals']['verdict'] == 'pass' else 1
    finally:
        for process in reversed(processes):process.terminate()
        for process in processes:process.wait(timeout=10)


def sweep(config, events, codes, *, settle_seconds=5):
    """Once its people have gone, nothing of this test may wait in the line or use the robot: each such run is an error, and is stopped."""
    from remote_gateway import policy
    from remote_gateway.config import load
    from remote_gateway.server import Gateway
    gateway_cli(config, 'pause');time.sleep(settle_seconds)  # No start from here on; one already sent has time to show as running.
    gateway = Gateway(load(config));owners = gateway.store.owners();log = sim_users.Log(events, codes)
    left = [(row['experiment_id'], '在排队') for row in gateway.store.queue()]
    left += [(row['experiment_id'], '在运行') for row in gateway.listing().get('dev_sessions', []) if policy.running(row) and row['experiment_id'] in owners]
    for run, where in left:
        gateway.store.dequeue(run)
        status, _ = gateway.upstream.json(owners[run], 'POST', f'/api/dev-v2/experiments/{run}/cancel', {})
        log.write(lane='-', kind='guard', name='收尾检查', source='遗留 run', message=f'测试结束时 run {run[-8:]} 还{where}，已取消（HTTP {status}）')
    return left


def help_checks(run_root, events, codes):
    """Real runs: what each help request became on the host. The page only shows what reached it, so a request the robot made
    that never reached the person or the Advisor is written here as an error, with the host's own reason."""
    rows = [json.loads(line) for line in events.open(encoding='utf-8')]
    runs = {}
    for row in rows:  # The person who made a run names it first; a guest who watches it later does not own it.
        if row.get('run_id'):runs.setdefault(row['run_id'], row.get('lane'))
    log = sim_users.Log(events, codes);found = 0
    for run_id, lane in runs.items():
        for history in Path(run_root).glob(f'{run_id}/artifacts/{run_id}/cells/*/agent/ws/history/help.jsonl'):
            for line in history.read_text(encoding='utf-8').splitlines():
                row = json.loads(line) if line.strip() else {}
                if (row.get('decision') or {}).get('action') != 'request' or row.get('delivered'):continue
                error = history.parents[2] / 'private' / 'help' / str(row.get('execution_id')) / 'error.json'
                why = json.loads(error.read_text(encoding='utf-8')).get('error') if error.is_file() else row.get('reason')
                found += 1
                log.write(lane=lane or '-', kind='problem', source='求助没送到', level='error',
                          message=f'run {run_id[-8:]} 在 {row.get("execution_id")} 后求助，但没有送到人或 Advisor（{row.get("status")}）：{why}')
    return found


def host_run(prefix, command):
    """One read-only shell command on the run host: here, or through --host-prefix (for example an ssh command)."""
    return subprocess.run(f'{prefix} {command}' if prefix else command, shell=True, check=True, capture_output=True, text=True, timeout=120).stdout


def host_disk(prefix, root):
    """Free bytes on the run host, and the bytes already under its run root."""
    free = int(host_run(prefix, f'df -B1 --output=avail {root}').split()[-1])
    # A running run deletes its temporary files while du walks them, and du then exits 1 after printing the total. A total is still required.
    used = int(host_run(prefix, f"sh -c 'du -sb {root} 2>/dev/null; true'").split()[0])
    return free, used


class DiskGuard(threading.Thread):
    """Stops the test before the disk does: refuses new runs, then tells every person to give their run back."""
    def __init__(self, args, config, shared, log):
        super().__init__(daemon=True)
        self.args, self.config, self.shared, self.log = args, config, shared, log
        self.done = threading.Event();self.start_free, self.start_used = host_disk(args.host_prefix, args.run_root);self.run_used, self.current = self.start_used, None

    def quota(self):
        return read_quota(self.args.quota_url)['used_percent']

    def stop(self, reason):
        if self.shared.get('abort'):return
        self.shared['abort'] = reason
        self.log.write(lane='-', kind='guard', message=reason)
        gateway_cli(self.config, 'pause')

    def check(self):
        args = self.args;free, used = host_disk(args.host_prefix, args.run_root);gib = 2**30
        with urllib.request.urlopen(f'http://127.0.0.1:{args.gateway_port}/api/lobby', timeout=5) as response:
            running = (json.load(response)['robot']['running'] or {}).get('experiment_id')
        if running != self.current:self.current, self.run_used = running, used
        used_quota = self.quota()
        if used_quota >= args.quota_stop_percent:self.stop(f'Codex 额度 {used_quota:g}%，到了停线 {args.quota_stop_percent:g}%')
        elif free < args.disk_floor_gb * gib:self.stop(f'磁盘剩余 {free / gib:.1f}G，低于底线 {args.disk_floor_gb:g}G')
        elif used - self.start_used > args.test_budget_gb * gib:self.stop(f'本次测试已写入 {(used - self.start_used) / gib:.1f}G，超过总预算 {args.test_budget_gb:g}G')
        elif running and used - self.run_used > args.run_budget_gb * gib:self.stop(f'run {running[-8:]} 已写入 {(used - self.run_used) / gib:.1f}G，超过单 run 预算 {args.run_budget_gb:g}G')

    def run(self):
        while not self.done.wait(30):
            try:self.check()
            except Exception as error:self.stop(f'磁盘检查失败，按超限处理：{type(error).__name__}: {error}')


def real(args):
    """Real runs, through a gateway that already runs. It starts no service, and it refuses to begin unless every condition holds."""
    directory = args.dir.resolve();config = directory / 'gateway.json'
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
    if not args.release_commit or not head.startswith(args.release_commit):raise SystemExit(f'没有放行：--release-commit 必须是中控台给的 commit，当前代码是 {head[:10]}')
    if not args.run_root:raise SystemExit('真实测试必须给 --run-root，否则无法看磁盘')
    if not config.exists():raise SystemExit(f'{config} 不存在：先按 PLAN.md 写好测试实例配置')
    value = json.loads(config.read_text(encoding='utf-8'))
    blocked = [path for path in value.get('stop_files', []) if Path(path).exists()]
    if blocked:raise SystemExit(f'停止标志还在，不开始：{blocked}')
    if NO_NEW_RUNS.exists():print(f'注意：{NO_NEW_RUNS} 存在，测试实例按用户决定不读它；磁盘底线 {args.disk_floor_gb:g}G 由本脚本把守。报告要写明。', flush=True)
    free, _ = host_disk(args.host_prefix, args.run_root)
    if free < args.disk_floor_gb * 2**30:raise SystemExit(f'磁盘剩余 {free / 2**30:.1f}G，低于底线 {args.disk_floor_gb:g}G，不开始')
    wait_up(f'http://127.0.0.1:{args.gateway_port}/login')
    port_free(args.monitor_port)
    print('开始前 df：', host_run(args.host_prefix, f'df -h {args.run_root}').splitlines()[-1], flush=True)
    quota = read_quota(args.quota_url)
    print('开始前 Codex 额度：', quota, flush=True)
    if quota['used_percent'] >= args.quota_stop_percent:raise SystemExit(f'Codex 额度 {quota["used_percent"]}%，到了停线，不开始')
    monitor = spawn(['-m', 'remote_gateway.simtest.monitor', '--port', str(args.monitor_port), '--dir', str(directory), '--gateway', f'http://127.0.0.1:{args.gateway_port}', '--mode', 'real',
                     *(['--sim-lock', value['sim_lock']] if value.get('sim_lock') else []), '--disk-floor-gb', str(args.disk_floor_gb)], directory / 'monitor.log')
    try:
        wait_up(f'http://127.0.0.1:{args.monitor_port}/api/state')
        whole = TIMESHARE_REAL_CAST if args.cast == 'timeshare' else CAST
        cast = [c for c in whole if c[0] in args.only] if args.only else whole
        codes = invites(config, len(cast), directory / 'invites.json')
        shared = {};log = sim_users.Log(directory / 'events.jsonl', codes)
        guard = DiskGuard(args, config, shared, log);guard.start()
        settings = sim_users.Settings(base=f'http://127.0.0.1:{args.gateway_port}', out=directory, scale=args.scale, drop_seconds=args.drop_seconds, seed=args.seed, run_cap_seconds=args.run_cap_seconds, model_source=args.model_source,
                                      reply_command=(sys.executable, '-B', '-m', 'remote_gateway.simtest.reply_writer'), reply_source='codex')
        print(f'监控页 http://127.0.0.1:{args.monitor_port}/', flush=True)
        asyncio.run(sim_users.run_all(settings, [(lane, name, story, code) for (lane, name, story), code in zip(cast, codes)], directory / 'events.jsonl', codes, shared))
        guard.done.set()
        sweep(config, directory / 'events.jsonl', codes)
        if args.host_prefix:log.write(lane='-', kind='problem', source='求助没送到', level='notice', message='run 在别的机器上，这里读不到求助记录，没有核对')
        else:help_checks(args.run_root, directory / 'events.jsonl', codes)
        print('结束后 df：', host_run(args.host_prefix, f'df -h {args.run_root}').splitlines()[-1], flush=True)
        print('结束后 Codex 额度：', guard.quota(), flush=True)
        with urllib.request.urlopen(f'http://127.0.0.1:{args.monitor_port}/api/state', timeout=5) as response:final = json.load(response)
        (directory / 'report.json').write_text(json.dumps(final, ensure_ascii=False, indent=1), encoding='utf-8')
        print(json.dumps(final['totals'], ensure_ascii=False), flush=True)
        if args.hold:signal.pause()
        return 0 if final['totals']['verdict'] == 'pass' else 1
    finally:
        monitor.terminate();monitor.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dir', type=Path, required=True)
    parser.add_argument('--gateway-port', type=int, default=8871)
    parser.add_argument('--fake-port', type=int, default=8872)
    parser.add_argument('--monitor-port', type=int, default=8870)
    parser.add_argument('--speed', type=float, default=8)
    parser.add_argument('--try-seconds', type=float, default=64)
    parser.add_argument('--think-seconds', type=float, default=8)
    parser.add_argument('--slots', type=int, default=3, help='run slots: runs open at once, on the gateway and the fake host')
    parser.add_argument('--cast', choices=('crowd', 'timeshare'), default='crowd', help='crowd: the nine-person story; timeshare: the six-person time-share story')
    parser.add_argument('--no-progress-seconds', type=float, default=7200, help='offline: fake seconds without a Try before the fake host stops a run')
    parser.add_argument('--scale', type=float, default=1)
    parser.add_argument('--drop-seconds', type=float, default=5)
    parser.add_argument('--fault', nargs=2, action='append', default=[], metavar=('SECONDS', 'JSON'), type=str, help='at this second, set this fault on the fake host')
    parser.add_argument('--only', nargs='*', help='only these lanes, for example U1 U2')
    parser.add_argument('--hold', action='store_true', help='keep the monitor open afterwards')
    parser.add_argument('--real', action='store_true', help='real runs: needs a release; see PLAN.md')
    parser.add_argument('--release-commit', help='the commit the control session released')
    parser.add_argument('--run-root', help='real: the folder holding runs on the run host')
    parser.add_argument('--host-prefix', default='', help='real: prefix for read-only commands on the run host, such as an ssh command')
    parser.add_argument('--disk-floor-gb', type=float, default=130)
    parser.add_argument('--test-budget-gb', type=float, default=25)
    parser.add_argument('--run-budget-gb', type=float, default=20)
    parser.add_argument('--run-cap-seconds', type=float)
    parser.add_argument('--quota-url', default='http://127.0.0.1:8769/api/codex-quota', help="or 'local' to read this machine's Codex session logs")
    parser.add_argument('--quota-stop-percent', type=float, default=77)
    parser.add_argument('--seed', type=int, default=101)
    parser.add_argument('--model-source', choices=('codex', 'parcc'), default='codex', help="what each person picks in 03 Model source: Lab Codex (gpt-6.1-sol high) or PARCC")
    args = parser.parse_args();args.fault = [(float(at), value) for at, value in args.fault]
    raise SystemExit(real(args) if args.real else offline(args))


if __name__ == '__main__':
    main()
