"""A stand-in for the harness: same routes, same signature check, same JSON shapes, no simulator, no model and no disk.

The real gateway and the real page sit in front of it, so everything between a browser and the harness is the real code.
Its clock runs faster than the wall (--speed). It also keeps the one rule the real host must keep: only one run on the
robot. A start while another run is on the robot is recorded as a violation, and the monitor shows it.

GET /sim/state and POST /sim/fault answer without a signature, on loopback only, for the monitor and for fault tests.
"""
import argparse
import copy
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import random
import re
import threading
import time
from urllib.parse import parse_qs, urlsplit

from remote_gateway.upstream import control_token

TEMPLATES = json.loads((Path(__file__).parent / 'fixtures/templates.json').read_text(encoding='utf-8'))
ROUTE = re.compile(r'/api/dev-v2/experiments/(devexp-[a-f0-9]{24})(?:/(.*))?')
HELP_POLICIES = {'reactive_help', 'agent_initiated_help', 'retry_k_then_ask'}
ENDED = {'completed', 'failed', 'cancelled'}
VIEWS = {None: 'Overview', 'external': 'Overview', 'side': 'Side', 'top': 'Top-down', 'left_wrist': 'Left wrist', 'right_wrist': 'Right wrist'}


def jpeg(text, sequence):
    """A small changing picture, so a stuck stream can be seen."""
    from PIL import Image, ImageDraw
    image = Image.new('RGB', (480, 270), (30 + sequence * 7 % 120, 60, 90));draw = ImageDraw.Draw(image)
    x = sequence * 11 % 440
    draw.rectangle((x, 180, x + 40, 220), fill=(240, 180, 60))
    draw.text((12, 12), text, fill=(255, 255, 255))
    out = io.BytesIO();image.save(out, 'JPEG', quality=60)
    return out.getvalue()


class Run:
    """One run's life as a list of (phase, seconds), walked by the clock."""
    def __init__(self, experiment_id, key, owner, selection, created_at):
        self.experiment_id, self.key, self.owner, self.selection, self.created_at = experiment_id, key, owner, selection, created_at
        self.state, self.reason = 'planned', None
        self.started_at = None;self.tries = [];self.executing = None;self.wait = None;self.sequence = 0
        self.helped = [];self.phase = 'not started'


class World:
    def __init__(self, *, speed=1.0, secret=b'', tries=3, try_seconds=40, think_seconds=10, seed=1):
        self.speed, self.secret, self.tries, self.try_seconds, self.think_seconds = speed, secret, tries, try_seconds, think_seconds
        self.lock = threading.RLock();self.runs = {};self.random = random.Random(seed);self.violations = [];self.fault = {};self.calls = 0;self.frames = 0;self.credentials = {}

    # The clock
    def on_robot(self):
        return [run for run in self.runs.values() if run.state == 'running']

    def start(self, run):
        with self.lock:
            others = [r.experiment_id for r in self.on_robot() if r is not run]
            if others:
                self.violations.append(dict(at=time.time(), started=run.experiment_id, while_running=others))
                return False
            run.state, run.started_at, run.phase = 'running', time.time(), 'turn running'
            threading.Thread(target=self.live, args=(run,), daemon=True).start()
            return True

    def pause(self, run, seconds):
        """Wait out a phase. False when the run was stopped meanwhile."""
        end = time.time() + seconds / self.speed
        while time.time() < end:
            if run.state != 'running':return False
            time.sleep(.05)
        return run.state == 'running'

    def live(self, run):
        help_wanted = run.selection['policies'][0] in HELP_POLICIES and run.selection.get('human_reply_seconds')
        for number in range(1, self.tries + 1):
            eid = 'e%06d' % number
            run.phase = 'turn running'
            if not self.pause(run, self.think_seconds):return
            run.phase, run.executing = 'waiting on execution', dict(execution_id=eid, number=number, submitted=False, started_at=time.time())
            if not self.pause(run, self.try_seconds):return
            run.executing = None
            run.tries.append(dict(execution_id=eid, number=number, submitted=False, ok=False, kind='hold', why='The gripper closed but did not hold the object'))
            if help_wanted and number == 1:
                if not self.ask(run, eid):return
        with self.lock:
            if run.state == 'running':run.state, run.reason, run.phase = 'failed', 'wall_stop', 'stopped'

    def ask(self, run, eid):
        seconds = run.selection['human_reply_seconds'];shown = seconds / self.speed
        run.phase = 'help'
        run.wait = dict(execution_id=eid, question='The gripper did not hold the object. What should I try next?', deadline_at=time.time() + shown, seconds=shown, cell=run.key)
        end = run.wait['deadline_at']
        while time.time() < end and run.wait and run.state == 'running':time.sleep(.05)
        if run.state != 'running':return False
        if run.wait:run.helped.append(dict(execution_id=eid, provider='advisor', at=time.time()));run.wait = None
        return True

    # What the harness answers
    def row(self, run):
        row = copy.deepcopy(TEMPLATES['row'])
        row.update(experiment_id=run.experiment_id, key=run.key, session_id='v2-' + run.key, state=run.state, experiment_state=run.state, reason=run.reason, created_at=run.created_at,
                   policy_id=run.selection['policies'][0], seed=run.selection['seeds'][0], task_id=run.selection['tasks'][0], mode=run.selection['mode'], owner=run.owner)
        if run.state == 'running' and run.wait:row['human_wait'] = copy.deepcopy(run.wait)
        return row

    def view(self, run):
        view = copy.deepcopy(TEMPLATES['view']);agent, compat = view['agent'], view['dev_v2_compat']
        ended = run.state in ENDED;planned = run.state == 'planned'
        shown = 'created' if planned else run.state
        view.update(key=run.key, experiment_id=run.experiment_id, state=shown, reason=run.reason, policy_id=run.selection['policies'][0], seed=run.selection['seeds'][0],
                    task_id=run.selection['tasks'][0], mode=run.selection['mode'])
        agent.update(phase=run.phase, reason=run.reason, tries=copy.deepcopy(run.tries), executing=None if ended else run.executing, human_wait=None if ended else copy.deepcopy(run.wait),
                     wall_seconds=int(time.time() - run.started_at) if run.started_at else 0)
        compat.update(key=run.key, experiment_id=run.experiment_id, state=shown, reason=run.reason, policy_id=view['policy_id'], seed=view['seed'], task_id=view['task_id'], mode=view['mode'])
        if planned:
            compat.update(status='not started', lifecycle=dict(phase='prepared', can_start=True, can_stop=False, start_label='Start run', experiment_id=run.experiment_id,
                                                               scope=[dict(task_id=view['task_id'], seed=view['seed'], state=shown)], scope_count=1, scheduler_active=False))
            compat['run_controls'] = dict(compat['run_controls'], state='created', can_start=True, can_pause=False)
        elif not ended:
            compat.update(lifecycle=dict(phase={'waiting on execution': 'sdk', 'help': 'advisor'}.get(run.phase, 'model'), can_stop=True))
            compat['run_controls'] = dict(compat['run_controls'], state='running', can_start=False, can_pause=True, current_step='on the robot' if run.executing else 'thinking')
        return view

    def experiment(self, run):
        value = copy.deepcopy(TEMPLATES['experiment']);cell = dict(value['cells'][0], key=run.key, experiment_id=run.experiment_id, state=run.state) if value.get('cells') else dict(key=run.key)
        value.update(experiment_id=run.experiment_id, state='created' if run.state == 'planned' else run.state, reason=run.reason, created_at=run.created_at, cancelled=run.state == 'cancelled', cells=[cell])
        return value

    def live_state(self, run, view):
        row = dict(state='waiting_first_frame', monitor_state='waiting_first_frame', age_seconds=None, server_time=time.time(), sequence=None, version=None, episode_id=None, frame_sha256=None,
                   stale_after_s=5, phase='unknown', cell_state=run.state, waiting_since=None)
        if run.state in ENDED:return dict(row, state='terminal_saved', monitor_state='terminal_saved')
        executing = run.executing  # The run's own thread clears this at any moment.
        if not executing:return row
        sequence = int((time.time() - executing['started_at']) * 4)
        if self.fault.get('frames_stalled'):sequence = 0
        raw = self.frame(run, view, sequence, executing)
        return dict(row, state='live', monitor_state='live', age_seconds=.1, sequence=sequence, version=executing['number'], episode_id=executing['execution_id'], frame_sha256=hashlib.sha256(raw).hexdigest())

    def frame(self, run, view, sequence, executing):
        return jpeg(f'{run.experiment_id[-6:]} {VIEWS.get(view, view)} try {executing["number"]} #{sequence}', sequence)

    def summary(self):
        with self.lock:
            return dict(on_robot=[r.experiment_id for r in self.on_robot()], violations=self.violations[-20:], calls=self.calls, frames=self.frames, fault=self.fault,
                        runs={r.experiment_id: dict(state=r.state, reason=r.reason, owner=r.owner, phase=r.phase, tries=len(r.tries), helped=r.helped, waiting=bool(r.wait), dev_model=r.selection.get('dev_model'),
                                                                     quota_stop_percent=r.selection.get('quota_stop_percent'), eval_provider=r.selection['agent_provider']['roles']['eval']['provider']) for r in self.runs.values()})


class Handler(BaseHTTPRequestHandler):
    server_version = 'fake-harness'
    def log_message(self, *args):pass

    @property
    def world(self):return self.server.world

    def answer(self, value, status=200, kind='application/json', headers=None):
        raw = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.send_response(status);self.send_header('Content-Type', kind);self.send_header('Content-Length', str(len(raw)))
        for name, text in (headers or {}).items():self.send_header(name, text)
        self.end_headers();self.wfile.write(raw)

    def signed(self, body):
        owner, auth = self.headers.get('X-Attention-Owner'), self.headers.get('X-Attention-Gateway-Auth', '')
        match = re.fullmatch(r'v1\.(\d+)\.([a-f0-9]{64})', auth)
        if not owner or not match or abs(time.time() - int(match[1])) > 30:return False
        expected = hmac.new(self.world.secret, '\n'.join((owner, match[1], self.command, self.path, hashlib.sha256(body).hexdigest())).encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, match[2]) and self.headers.get('X-Attention-Token') == control_token(self.world.secret)

    def serve(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0) or 0));world = self.world
        parts = urlsplit(self.path);path = parts.path
        if path == '/sim/state':return self.answer(world.summary())
        if path == '/sim/fault' and self.command == 'POST':
            world.fault = json.loads(body or b'{}')
            return self.answer(world.fault)
        if not self.signed(body):return self.answer(dict(error='unsigned'), 401)
        world.calls += 1
        delay = world.fault.get('slow_ms')
        if delay:time.sleep(delay / 1000)
        if world.fault.get('down'):return self.answer(dict(error='harness is down'), 503)
        owner = self.headers['X-Attention-Owner']
        if path == '/api/run-options':return self.answer(TEMPLATES['options'])
        if path == '/api/runs':
            with world.lock:return self.answer(dict(runs=[], launches=[], dev_sessions=[world.row(r) for r in world.runs.values()]))
        if path == '/api/home/status':return self.answer(dict(recent=[], readiness=dict(glm=dict(state='ready'), qwen=dict(state='ready'))))
        if path == '/api/provider/credentials':return self.answer([])
        if path == '/api/provider/presets':return self.answer([])
        if path == '/api/dev-v2/experiments' and self.command == 'POST':return self.create(owner, json.loads(body))
        match = ROUTE.fullmatch(path)
        if match:return self.run_route(owner, match[1], match[2] or '', parse_qs(parts.query), body)
        self.answer(dict(error='not found'), 404)
    do_GET = do_POST = do_PUT = do_DELETE = serve

    def create(self, owner, value):
        world = self.world;selection = value['selection']
        with world.lock:
            identity = hashlib.sha256(f'{selection["request_key"]}'.encode()).hexdigest()
            experiment_id = 'devexp-' + identity[:24]
            run = world.runs.setdefault(experiment_id, Run(experiment_id, identity, owner, selection, time.time()))
        self.answer(dict(experiment_id=run.experiment_id, cells=[dict(key=run.key)], worker_started=False))

    def run_route(self, owner, experiment_id, suffix, query, body):
        world = self.world;run = world.runs.get(experiment_id)
        if run is None:return self.answer(dict(error='not found'), 404)
        if self.command == 'POST':return self.command_route(owner, run, suffix, body)
        if not suffix:return self.answer(world.experiment(run))
        if suffix == 'report':return self.answer(dict(experiment_id=experiment_id, state=run.state))
        if suffix == 'export.json':return self.answer(dict(experiment_id=experiment_id))
        tail = suffix.split('/', 2)[-1]
        view = query.get('view', [None])[0]
        if tail == 'view':return self.answer(world.view(run))
        if tail == 'live':return self.answer(world.live_state(run, view))
        if tail == 'live/frame':
            state = world.live_state(run, view)
            if state['sequence'] is None:return self.answer(dict(state, error='No published frame', code='no_frame'), 404)
            raw = world.frame(run, view, state['sequence'], dict(number=state['version']));world.frames += 1
            return self.answer(raw, 200, 'image/jpeg', {'X-Frame-Sequence': str(state['sequence']), 'X-Preview-State': 'live', 'X-Episode-Id': state['episode_id'], 'X-Frame-Version': str(state['version']),
                                                        'X-Frame-Sha256': state['frame_sha256'], 'X-Frame-Age': '0.1'})
        return self.answer(dict(error='not found'), 404)

    def command_route(self, owner, run, suffix, body):
        world = self.world
        if run.owner != owner:return self.answer(dict(error='not owner'), 403)  # The gateway must stop this first.
        if suffix == 'run':
            if world.fault.get('start_fails') in (True, owner):return self.answer(dict(error='cannot start at /home/someone/x: simulated start failure'), 500)
            if not world.start(run):return self.answer(dict(error='Another run is on the robot.'), 409)
            return self.answer(dict(worker_started=True))
        if suffix == 'cancel':
            with world.lock:
                if run.state in ('running', 'planned'):run.state, run.reason, run.phase, run.executing, run.wait = 'cancelled', 'operator_cancel', 'stopped', None, None
            return self.answer(dict(ok=True))
        if suffix == 'pause':return self.answer(dict(ok=True))
        if suffix == 'help-reply':
            value = json.loads(body)
            with world.lock:
                if not run.wait or value.get('execution_id') != run.wait['execution_id']:return self.answer(dict(error='This request is no longer waiting for a reply.'), 409)
                run.helped.append(dict(execution_id=run.wait['execution_id'], provider='skip' if value.get('skip') else 'human', content=value.get('content'), at=time.time()))
                run.wait = None
            return self.answer(dict(status='received', kind='skip' if value.get('skip') else 'reply'))
        return self.answer(dict(error='not found'), 404)


def create_server(port, world):
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler);server.daemon_threads = True;server.world = world
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--secret-file', type=Path, required=True)
    parser.add_argument('--speed', type=float, default=4)
    parser.add_argument('--tries', type=int, default=3)
    parser.add_argument('--try-seconds', type=float, default=40)
    parser.add_argument('--think-seconds', type=float, default=10)
    args = parser.parse_args()
    world = World(speed=args.speed, secret=args.secret_file.read_bytes(), tries=args.tries, try_seconds=args.try_seconds, think_seconds=args.think_seconds)
    create_server(args.port, world).serve_forever()


if __name__ == '__main__':
    main()
