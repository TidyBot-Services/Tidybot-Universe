"""The monitor: folds events.jsonl into one lane per person, and adds what can be read without a person's help.

It only reads. The sim lock is read from /proc/locks, because trying it would take it. If a source cannot be read, the
page says so; nothing here hides an error or shows an old value as if it were new.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen

PAGE = Path(__file__).parent / 'monitor.html'
LABELS = dict(waiting='未开始', login='登录中', lobby='在大厅', queued='排队中', running='运行中', reply='等回复', offline='断线', finished='已结束', cancelled='已取消', failed='出错停止', watching='观看中')
LINE = re.compile(r'[a-f0-9]{64}')


def new_lane(row):
    return dict(lane=row['lane'], name=row.get('name', ''), story=row.get('story', ''), nickname=None, status='waiting', label=LABELS['waiting'], position=None, action='', action_since=None,
                run_id=None, countdown='', segments=[], problems=[], notices=0, expected=0, checks=[], done=None, message='', result=None, live=False, offline=False, page={})


def status_of(lane):
    page = lane['page'];card = page.get('card') or {}
    if lane['done'] is False:return 'failed'
    if lane['offline']:return 'offline'
    if lane['story'] == '访客':return 'finished' if lane['done'] else 'watching' if page.get('lobby') else 'login' if lane['action'] else 'waiting'
    if page.get('reply') and page.get('viewing') == lane['run_id']:return 'reply'
    if card.get('state') in ('queued', 'running', 'finished', 'cancelled'):return card['state']
    if lane['done'] is True:return 'finished'
    if page.get('lobby'):return 'lobby'
    return 'login' if lane['action'] else 'waiting'


def fold(rows, now=None):
    """Everything the page shows about people, from the log alone, so a finished test can be read back the same way."""
    lanes, order, problems, checks, actions, began, ended = {}, [], [], [], [], None, None
    for row in rows:
        kind = row.get('kind');at = row.get('at', 0)
        if kind == 'begin':
            began = at
            for person in row['people']:
                lanes[person['lane']] = new_lane(person);order.append(person['lane'])
            continue
        if kind == 'end':
            ended = at;continue
        if kind == 'guard':
            problems.append(dict(at=at, lane='-', name='安全线', source='测试叫停', message=row.get('message', ''), level='error'));continue
        lane = lanes.get(row.get('lane'))
        if lane is None:continue
        if kind == 'action':
            lane['action'] = row['action'] if row['phase'] == 'start' else lane['action']
            if row['phase'] == 'start':lane['action_since'] = at
            actions.append(dict(at=at, lane=lane['lane'], name=lane['name'], action=row['action'], phase=row['phase'], seconds=row.get('seconds')))
        elif kind == 'state':
            for field in ('run_id', 'nickname', 'countdown', 'offline'):
                if field in row and row[field] is not None:lane[field] = row[field]
            if 'page' in row:
                lane['page'] = row['page'];lane['position'] = (row['page'].get('card') or {}).get('position');lane['live'] = row['page'].get('live', False);lane['nickname'] = row['page'].get('nickname') or lane['nickname']
                if row['page'].get('message'):lane['message'] = row['page']['message']
            if 'finished' in row:lane['result'] = row['finished']
        elif kind == 'problem':
            item = dict(at=at, lane=lane['lane'], name=lane['name'], source=row.get('source'), message=row.get('message', ''), level=row.get('level', 'error'))
            problems.append(item);lane['problems'].append(item)
        elif kind == 'http':
            if row.get('expected'):lane['expected'] += 1
            elif row.get('level') == 'notice':lane['notices'] += 1
            else:
                item = dict(at=at, lane=lane['lane'], name=lane['name'], source=f'HTTP {row["status"]}', message=f'{row["method"]} {row["route"]}', level='error')
                problems.append(item);lane['problems'].append(item)
        elif kind == 'check':
            item = dict(at=at, lane=lane['lane'], name=lane['name'], check=row['check'], ok=row['ok'], detail=row.get('detail', ''))
            checks.append(item);lane['checks'].append(item)
            if not row['ok']:
                failed = dict(at=at, lane=lane['lane'], name=lane['name'], source='检查未通过', message=f'{row["check"]}：{row.get("detail", "")}', level='error')
                problems.append(failed);lane['problems'].append(failed)
        elif kind == 'done':
            lane['done'] = row['ok'];lane['action'] = '完成' if row['ok'] else '已停止'
            if not row['ok']:
                item = dict(at=at, lane=lane['lane'], name=lane['name'], source='用户流程中断', message=row.get('error', ''), level='error')
                problems.append(item);lane['problems'].append(item)
        status = status_of(lane)
        if not lane['segments'] or lane['segments'][-1]['status'] != status:
            if lane['segments']:lane['segments'][-1]['to'] = at
            lane['segments'].append(dict(status=status, label=LABELS[status], at=at, to=None))
        lane['status'], lane['label'] = status, LABELS[status]
    return dict(began=began, ended=ended, lanes=[lanes[key] for key in order], problems=problems, checks=checks, actions=actions[-60:])


def verdict(folded, violations=()):
    """Passing means nothing went wrong anywhere. An unfinished test has no verdict yet."""
    lanes = folded['lanes']
    errors = [p for p in folded['problems'] if p['level'] == 'error']
    finished = folded['ended'] is not None
    failed_checks = [c for c in folded['checks'] if not c['ok']]
    bad = len(errors) + len(violations) + sum(lane['done'] is False for lane in lanes)
    state = 'fail' if bad else 'pass' if finished and lanes else 'running'
    return dict(verdict=state, errors=len(errors), notices=sum(l['notices'] for l in lanes), expected_refusals=sum(l['expected'] for l in lanes), checks=len(folded['checks']), checks_failed=len(failed_checks),
                people=len(lanes), people_done=sum(l['done'] is not None for l in lanes), violations=len(violations))


def read_events(path):
    rows = []
    try:
        with open(path, encoding='utf-8') as stream:
            for line in stream:
                try:rows.append(json.loads(line))
                except ValueError:rows.append(dict(kind='bad-line'))
    except FileNotFoundError:pass
    return rows


def audit(path):
    """What the gateway itself logged: refusals and slow routes, whatever the page noticed."""
    routes = {};statuses = {}
    try:
        with open(path, encoding='utf-8') as stream:lines = stream.readlines()[-20000:]
    except OSError:return None
    for line in lines:
        try:row = json.loads(line)
        except ValueError:continue
        statuses[str(row.get('status'))] = statuses.get(str(row.get('status')), 0) + 1
        routes.setdefault(row.get('route', '?'), []).append(row.get('ms', 0))
    table = []
    for route, values in routes.items():
        values.sort()
        table.append(dict(route=route, count=len(values), p50=values[len(values) // 2], p95=values[int(len(values) * .95)], worst=values[-1]))
    table.sort(key=lambda r:-r['p95'])
    return dict(statuses=statuses, routes=table[:12], total=sum(statuses.values()))


def lock_holder(path):
    """Whether a process holds the sim lock, without touching it."""
    if not path:return dict(state='未使用')
    try:inode = os.stat(path).st_ino
    except OSError:return dict(state='锁文件不存在')
    with open('/proc/locks', encoding='ascii', errors='replace') as stream:
        for line in stream:
            parts = line.split()
            if len(parts) > 5 and parts[1] == 'FLOCK' and parts[5].split(':')[-1] == str(inode):return dict(state='占用中', pid=parts[4])
    return dict(state='空闲')


class Sources:
    """Slow reads happen here, on their own clock, so the page never waits for a stuck source. Each keeps its own error."""
    def __init__(self, args):
        self.args = args;self.values = {};self.failures = {};self.lock = threading.Lock()

    def read(self, name, fetch):
        try:value = fetch()
        except Exception as error:
            with self.lock:self.failures[name] = dict(at=time.time(), message=f'{type(error).__name__}: {error}')
            return
        with self.lock:
            self.values[name] = dict(at=time.time(), value=value);self.failures.pop(name, None)

    def url(self, base, path):
        with urlopen(base + path, timeout=4) as response:return json.load(response)

    def loop(self):
        args = self.args
        while True:
            self.read('lobby', lambda:self.url(args.gateway, '/api/lobby'))
            if args.fake:self.read('fake', lambda:self.url(args.fake, '/sim/state'))
            self.read('audit', lambda:audit(args.dir / 'gateway-state/audit.jsonl'))
            self.read('lock', lambda:lock_holder(args.sim_lock))
            def disk():
                usage = shutil.disk_usage(args.disk_path);return dict(free_gb=round(usage.free / 2**30, 1), used_percent=round(usage.used / (usage.used + usage.free) * 100, 1), floor_gb=args.disk_floor_gb)
            self.read('disk', disk)
            time.sleep(2)

    def snapshot(self):
        with self.lock:return dict(values=dict(self.values), failures=dict(self.failures))


def state(args, sources):
    now = time.time();rows = read_events(args.dir / 'events.jsonl');folded = fold(rows, now);read = sources.snapshot()
    values = read['values'];fake = (values.get('fake') or {}).get('value') or {};violations = fake.get('violations') or []
    totals = verdict(folded, violations)
    shots = {}
    for lane in folded['lanes']:
        path = args.dir / 'shots' / f'{lane["lane"]}.jpg'
        if path.exists():shots[lane['lane']] = int(path.stat().st_mtime)
    last = rows[-1].get('at') if rows else None
    return dict(now=now, mode=args.mode, **folded, totals=totals, shots=shots, last_event=last, sources={k: dict(age=round(now - v['at'], 1)) for k, v in values.items()}, source_failures=read['failures'],
                lobby=(values.get('lobby') or {}).get('value'), fake=fake or None, audit=(values.get('audit') or {}).get('value'), lock=(values.get('lock') or {}).get('value'), disk=(values.get('disk') or {}).get('value'))


class Handler(BaseHTTPRequestHandler):
    server_version = 'sim-monitor'
    def log_message(self, *args):pass

    def send(self, raw, kind, status=200):
        self.send_response(status);self.send_header('Content-Type', kind);self.send_header('Content-Length', str(len(raw)));self.send_header('Cache-Control', 'no-store')
        self.end_headers();self.wfile.write(raw)

    def do_GET(self):
        path = self.path.split('?')[0];args = self.server.args
        if path == '/':return self.send(PAGE.read_bytes(), 'text/html; charset=utf-8')
        if path == '/api/state':return self.send(json.dumps(state(args, self.server.sources), ensure_ascii=False).encode(), 'application/json; charset=utf-8')
        match = re.fullmatch(r'/shots/([A-Z]\d+)\.jpg', path)
        if match:
            try:return self.send((args.dir / 'shots' / f'{match[1]}.jpg').read_bytes(), 'image/jpeg')
            except OSError:pass
        self.send(b'not found', 'text/plain', 404)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8870)
    parser.add_argument('--dir', type=Path, required=True)
    parser.add_argument('--gateway', required=True)
    parser.add_argument('--fake')
    parser.add_argument('--mode', default='offline')
    parser.add_argument('--sim-lock')
    parser.add_argument('--disk-path', default='/')
    parser.add_argument('--disk-floor-gb', type=float, default=130)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler);server.daemon_threads = True;server.args = args;server.sources = Sources(args)
    threading.Thread(target=server.sources.loop, daemon=True).start()
    server.serve_forever()


if __name__ == '__main__':
    main()
