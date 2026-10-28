"""Per-cell workspace. Only private/public is authoritative; ws is hostile."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import uuid

SOURCE_LIMIT = 128 * 1024
REQUEST_LIMIT = 64 * 1024
DIRS = ('docs', 'history', 'versions', 'executions', 'probes', 'events',
        'rpc/requests', 'rpc/responses', 'work', 'scratch')


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2) + '\n').encode()


def parts(name):
    if not isinstance(name, str) or not name or '\\' in name or '\x00' in name:
        raise ValueError('invalid_path')
    p = PurePosixPath(name)
    if p.is_absolute() or any(x in ('', '.', '..') for x in name.split('/')):
        raise ValueError('invalid_path')
    return p.parts


def directory(root, components=(), *, create=False):
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in components:
            if create:
                try: os.mkdir(component, 0o700, dir_fd=fd)
                except FileExistsError: pass
            new = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_beneath(root, name, limit=SOURCE_LIMIT):
    components = parts(name)
    parent = directory(root, components[:-1])
    try:
        fd = os.open(components[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
                raise ValueError('unsafe_file')
            data = bytearray()
            while len(data) <= limit:
                chunk = os.read(fd, min(65536, limit + 1 - len(data)))
                if not chunk: break
                data.extend(chunk)
            after = os.fstat(fd)
            if (len(data) > limit or before.st_size != len(data) or after.st_nlink != 1
                    or (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns)):
                raise ValueError('file_changed_or_oversize')
            return bytes(data)
        finally: os.close(fd)
    finally: os.close(parent)


def atomic_write(root, name, data):
    components = parts(name)
    parent = directory(root, components[:-1], create=True)
    temp = '.publish-' + uuid.uuid4().hex
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(data); stream.flush(); os.fsync(fd)
        finally: os.close(fd)
        os.replace(temp, components[-1], src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        try: os.unlink(temp, dir_fd=parent)
        except FileNotFoundError: pass
        os.close(parent)


class Workspace:
    def __init__(self, agent_root):
        self.root = Path(agent_root).absolute()
        self.ws, self.private = self.root / 'ws', self.root / 'private'
        self.canonical = self.private / 'public'
        for path in (self.root, self.ws, self.private, self.canonical):
            path.mkdir(exist_ok=True, parents=True, mode=0o700)
            fd = directory(path); os.close(fd)
        self.published = set()

    def publish(self, name, value):
        data = value if isinstance(value, bytes) else encode(value)
        self._restore(self.canonical,name,data)
        self.published.add(name)
        self._restore(self.ws,name,data)
        return {'path': name, 'sha256': hashlib.sha256(data).hexdigest()}

    @staticmethod
    def _restore(root,name,data):
        # Full byte comparison through the no-follow reader; do not trust mtimes
        # or Dev-written history. Avoid fsync/rewrite of thousands of intact frames.
        try:
            if read_beneath(root,name,len(data))==data:return
        except (OSError,ValueError):pass
        atomic_write(root,name,data)

    def regenerate(self):
        for name in sorted(self.published):
            self._restore(self.ws, name, read_beneath(self.canonical, name, max(SOURCE_LIMIT, (self.canonical / name).stat().st_size)))

    def prepare(self, task, config):
        from .dev_tasks import dev_sdk, dev_sdk_prompt
        from .dev_agent_diagnostics import public_observation, screen_text
        if config.get('budget_tier') != 'dev' or config.get('runner_boundary') != 'dev_loop':
            raise ValueError('agent_requires_dev_tier')
        for name in DIRS:
            fd = directory(self.ws, parts(name), create=True); os.close(fd)
        prompt = dev_sdk_prompt(config).replace(
            'rejection is a public error, never bypass it.',
            'an authenticated pre-dispatch rejection returns {ok:false,rejected:true,reason,diagnostics}; '
            'it consumes one SDK call and zero motion. Inspect it and choose another safe action. '
            'Fatal errors and faults after motion stop execution; never bypass safety checks.')
        import re
        prompt = re.sub(r'The SDK raises RuntimeError carrying.*?Never bypass',
            'Authenticated pre-dispatch refusals return a rejected result so another safe action can be tried. Never bypass',
            prompt)
        prompt += ' In agent mode print(..., file=stderr) writes the separate authored stderr stream.'
        prompt = prompt.replace('Repair runtime errors before the next full attempt.',
                                'Recoverable rejections do not alone fail a completed execution.')
        self.publish('task.json', public_observation(task))
        self.publish('docs/sdk.md', screen_text(prompt).encode())
        self.publish('docs/sdk.json', dev_sdk(config))
        self.publish('docs/safety.json', dict(translation_coordinate_m=.05, rotation_rad=.5,
            joint_margin_rad=.02, base_clearance_m=.010, sdk_calls=320, control_steps=9600,
            execution_seconds=1920, versions=10, executions=40, probes=300, wall_seconds=21600))
        self.publish('docs/devtool.py', Path(__file__).with_name('dev_agent_tools.py').read_bytes())
        self.publish('docs/tools.md', TOOLS.encode())
        self.publish('docs/guide.md', b'Use docs/devtool.py for all simulator interaction. Read task.json and docs/sdk.md.\nKeep notes in work/notes.md. Every moving attempt starts from the registered reset scene.\n')
        for name in ('versions', 'executions', 'probes'):
            self.publish('history/' + name + '.jsonl', b'')
        self.publish('history/version_history.json', [])
        atomic_write(self.ws, 'work/notes.md', b'')
        return self


TOOLS = '''# Dev tools (file protocol v1)
Run /usr/bin/python3 docs/devtool.py OP. All paths are relative to this workspace.
submit --entry work/main.py [--reason TEXT]: one formal version, one reset execution,
then Eval; blocks until feedback. Only one formal submission may be in flight (busy).
run --no-eval --entry work/main.py: one reset execution, no version and no Eval.
observe: public robot observation from the registered reset scene.
find_objects [--names JSON]: public object positions (world metres), reset scene.
ik_check --target JSON [--start-joints JSON] [--base-offset JSON]: target {side, position, quaternion_xyzw};
plan_dry_run --action JSON [--start-joints JSON] [--base-offset JSON]: one {group, operation, args, kwargs}.
Both planning probes use reset obstacles; start_joints maps named arm joints to radians.
base_offset is {dx,dy,dyaw}, in the SAME mount frame/metres/radians as base.move_delta.
It hypothetically moves the reset base (optionally with start_joints), using only forward
kinematics, then restores all state. It never steps physics or issues a trajectory.
base_clearance reports accepted, minimum_clearance_m, minimum_clearance_pair.obstacle,
start_minimum_clearance_m, and the unchanged execution path/start thresholds. R4 uses 10 mm path clearance
and a 2 mm absolute start floor with its existing near-start allowance; the exact
execution check decides acceptance. Read required_clearance_m and absolute_min_distance_m.
The arm result is evaluated even when base_clearance.accepted is false; both checks
must pass. Public diagnostics include joint margins versus 0.02 rad; missing planner
fields are marked missing, never fabricated. Each offset probe costs ONE of 300 probes.
Example: scan offsets before spending moving executions (replace target with your pose):
```python
import json, subprocess
for dy in (0.03, 0.06, 0.09):
    subprocess.run(["/usr/bin/python3", "docs/devtool.py", "ik_check",
        "--target", json.dumps({"side":"right", "position":[0.5,0.1,0.9],
                                "quaternion_xyzw":[0,0,0,1]}),
        "--base-offset", json.dumps({"dx":0,"dy":dy,"dyaw":0})], check=False)
```
Held objects/attachments report unsupported_scope: held_object. No physics is stepped.
A planning acceptance is not a guarantee of dynamic execution success.
ask_help --question TEXT: available once per eligible formal interval while help remains.
reactive_help: eligible after formal Eval fail OR unavailable, until the next submit.
retry_k_then_ask: eligible after k consecutive formal Eval failures (default k=2);
passed/unavailable resets that streak. Neither probes nor no-eval runs create eligibility.
autonomous: no help. demo_first: fixed demo before v1, no manual help.
budget_matched_random_escalation, trace_aware_hint_only and
full_trace_aware_attention_planner: system injects a hint only on a policy grant at
a formal boundary; manual asks return policy_denied. Reactive/threshold asks outside
an unused eligible interval return not_eligible. Denied asks cost no attention.
Inspect versions/vNNNN/help.json and submit responses for eligibility, instructions
and hints. Eval advice and help are also delivered in the next resume message.
status: current counts and in-flight work. wait ID: resume polling an existing request.
All commands accept --timeout SECONDS; work is not cancelled by client timeout.
Use --request-id ID to retry the exact request; changing its payload is a conflict.
Use print(...) for stdout and print(..., file=stderr) for stderr.
work/submission.json may declare {"entry":"main.py","modules":["helpers.py"]}.
Default entry: main.py. Flat helper modules contain functions/constants and SDK imports.
Use explicit from helpers import name (no aliases). One shared namespace; conflicting
names, cycles, dynamic imports, helper side effects and defaults are rejected. Original
UTF-8 sources and flattened bundle must EACH fit 131072 bytes. Sources freeze before dispatch.
Limits: 10 formal versions, 40 executions, 300 probes, 6 hours total wall time;
per execution 1920 seconds, 320 SDK calls and 9600 control steps. Every accepted
probe, including rejection/timeout, counts. Invalid formal code consumes a version.
Observe/find timeout 10 s, planning probes 240 s, execution 1920 s, Eval 900 s;
validation 5 s. Deadlines are clipped to remaining run time. Uncertain work blocks dispatch.
Safety: arm coordinates <= 0.05 m / 0.5 rad per action, joint margin 0.02 rad,
base path clearance 10 mm; base translation <= 0.20 m and yaw <= 0.30 rad.
Never bypass safety. Authenticated pre-dispatch rejections return a structured result
and allow another action; all other faults stop. Read full trace, separate stdout/stderr,
diagnostics and final observation under executions/. Files are regenerated by the broker.
Request: {schema:"attentionbench.dev-agent-request.v1",request_id,op,payload}.
Response: {schema:"attentionbench.dev-agent-response.v1",request_id,digest,op,status,...}.
Responses use accepted/running/completed/rejected/needs_review; artifact links are relative.
'''
