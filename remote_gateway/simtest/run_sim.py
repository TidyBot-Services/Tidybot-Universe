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
# Everything a limit can open, at what the harness can honour. One run on the robot and the disk lines are not limits here: see PLAN.md.
NO_NEW_RUNS = Path.home() / '.config/disk-hygiene/no-new-runs'
OPEN_LIMITS = dict(versions=10, help_requests=10, runs_per_day=100000, wall_seconds=21600, active_runs=1000, requests_per_second=100000, request_burst=1000000,
                   live_requests_per_second=100000, live_request_burst=1000000, logins_per_hour=100000)


def wait_up(url, seconds=20):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):return
        except OSError:time.sleep(.2)
    raise SystemExit(f'没有起来：{url}')


def gateway_config(directory, *, port, upstream, sim_lock=None, stop_files=()):
    value = dict(state_dir=str(directory / 'gateway-state'), upstream=upstream, ui_dir=str(ROOT / 'benchmarks/attention_harness/ui'), listen_port=port, cookie_secure=False, stop_files=list(stop_files),
                 limits=OPEN_LIMITS, sim_lock=str(sim_lock) if sim_lock else None)
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


def outcome_checks(port, events, cast, codes):
    """What the person saw is one thing; this asks the fake host what really reached it."""
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/sim/state', timeout=5) as response:world = json.load(response)
    rows = [json.loads(line) for line in events.open(encoding='utf-8')]
    log = sim_users.Log(events, codes)
    for run_id, run in world['runs'].items():
        if run['dev_model'] != 'gpt-6-astra' or run['quota_stop_percent'] != 77 or run['eval_provider'] != 'openai':
            log.write(lane='-', kind='guard', message=f'run {run_id[-8:]} 的配置不对：{run}')
    wanted = {'认真回复': 'human', '关页再回来': 'human', '断线重连': 'human', '跳过求助': 'skip', '不回复': 'advisor'}
    for lane, name, story in cast:
        run_id = next((row['run_id'] for row in reversed(rows) if row.get('lane') == lane and row.get('run_id') and story != '访客'), None)
        run = world['runs'].get(run_id)
        if story not in wanted or run is None:continue
        got = [item['provider'] for item in run['helped']]
        log.write(lane=lane, name=name, kind='check', check=f'后端收到的求助结果是 {wanted[story]}', ok=wanted[story] in got, detail=f'后端记录 {got}')
    log.write(lane='-', kind='check-end')
    # The verdict is read after these lines are in, so a wrong outcome cannot pass.
    time.sleep(1.5)


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
    config = gateway_config(directory, port=args.gateway_port, upstream=f'http://127.0.0.1:{args.fake_port}')
    state = directory / 'gateway-state'
    for name in ('gateway.sqlite3', 'audit.jsonl', 'gateway.secret'):(state / name).unlink(missing_ok=True)
    gateway_cli(config, 'secret')
    processes = [spawn(['-m', 'remote_gateway.simtest.fake_harness', '--port', str(args.fake_port), '--secret-file', str(state / 'gateway.secret'), '--speed', str(args.speed), '--try-seconds', str(args.try_seconds), '--think-seconds', str(args.think_seconds)], directory / 'fake.log')]
    try:
        wait_up(f'http://127.0.0.1:{args.fake_port}/sim/state')
        processes.append(spawn(['-m', 'remote_gateway.simtest.codex_gateway', '--config', str(config)], directory / 'gateway.log'))
        wait_up(f'http://127.0.0.1:{args.gateway_port}/login')
        processes.append(spawn(['-m', 'remote_gateway.simtest.monitor', '--port', str(args.monitor_port), '--dir', str(directory), '--gateway', f'http://127.0.0.1:{args.gateway_port}', '--fake', f'http://127.0.0.1:{args.fake_port}', '--mode', 'offline'], directory / 'monitor.log'))
        wait_up(f'http://127.0.0.1:{args.monitor_port}/api/state')
        cast = [c for c in CAST if c[0] in args.only] if args.only else CAST
        codes = invites(config, len(cast), directory / 'invites.json')
        people = [(lane, name, story, code) for (lane, name, story), code in zip(cast, codes)]
        print(f'监控页 http://127.0.0.1:{args.monitor_port}/', flush=True)
        settings = sim_users.Settings(base=f'http://127.0.0.1:{args.gateway_port}', out=directory, scale=args.scale, drop_seconds=args.drop_seconds, reply_command=(sys.executable, '-B', '-m', 'remote_gateway.simtest.reply_writer', '--canned'), reply_source='canned')
        for at, value in args.fault:threading.Timer(at, send_fault, (args.fake_port, value)).start()
        asyncio.run(sim_users.run_all(settings, people, directory / 'events.jsonl', codes))
        outcome_checks(args.fake_port, directory / 'events.jsonl', cast, codes)
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
        with urllib.request.urlopen(self.args.quota_url, timeout=8) as response:return json.load(response)['used_percent']

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
    with urllib.request.urlopen(args.quota_url, timeout=8) as response:quota = json.load(response)
    print('开始前 Codex 额度：', quota, flush=True)
    if quota['used_percent'] >= args.quota_stop_percent:raise SystemExit(f'Codex 额度 {quota["used_percent"]}%，到了停线，不开始')
    monitor = spawn(['-m', 'remote_gateway.simtest.monitor', '--port', str(args.monitor_port), '--dir', str(directory), '--gateway', f'http://127.0.0.1:{args.gateway_port}', '--mode', 'real',
                     *(['--sim-lock', value['sim_lock']] if value.get('sim_lock') else []), '--disk-floor-gb', str(args.disk_floor_gb)], directory / 'monitor.log')
    try:
        wait_up(f'http://127.0.0.1:{args.monitor_port}/api/state')
        cast = [c for c in CAST if c[0] in args.only] if args.only else CAST
        codes = invites(config, len(cast), directory / 'invites.json')
        shared = {};log = sim_users.Log(directory / 'events.jsonl', codes)
        guard = DiskGuard(args, config, shared, log);guard.start()
        settings = sim_users.Settings(base=f'http://127.0.0.1:{args.gateway_port}', out=directory, scale=args.scale, drop_seconds=args.drop_seconds, seed=args.seed, run_cap_seconds=args.run_cap_seconds,
                                      reply_command=(sys.executable, '-B', '-m', 'remote_gateway.simtest.reply_writer'), reply_source='codex')
        print(f'监控页 http://127.0.0.1:{args.monitor_port}/', flush=True)
        asyncio.run(sim_users.run_all(settings, [(lane, name, story, code) for (lane, name, story), code in zip(cast, codes)], directory / 'events.jsonl', codes, shared))
        guard.done.set()
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
    parser.add_argument('--quota-url', default='http://127.0.0.1:8769/api/codex-quota')
    parser.add_argument('--quota-stop-percent', type=float, default=77)
    parser.add_argument('--seed', type=int, default=101)
    args = parser.parse_args();args.fault = [(float(at), value) for at, value in args.fault]
    raise SystemExit(real(args) if args.real else offline(args))


if __name__ == '__main__':
    main()
