"""Per-cell workspace. Only private/public is authoritative; ws is hostile."""
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import uuid

SOURCE_LIMIT = 128 * 1024
REQUEST_LIMIT = 64 * 1024
# Dev-tier physics logs already reach 62 MiB at 9,600 control steps. Allow
# substantial headroom for logs/recordings, but bound per-file hashing work.
PUBLIC_EVIDENCE_LIMIT = 1024 * 1024 * 1024
DIRS = ('docs', 'history', 'versions', 'executions', 'probes', 'events',
        'rpc/requests', 'rpc/responses', 'work', 'scratch')
# Frames are stored once: canonical, ws and Eval share one 0444 inode. This is safe
# only because the sandbox mounts these folders read-only (build_profile); a plain
# 0444 hardlink is not, since Dev owns the inode and can chmod it back (10-29).
READONLY_DIRS = ('executions', 'frames')
SHARED_MODE = 0o444
COPY_ERRORS = (errno.EXDEV, errno.EPERM, errno.EMLINK, errno.EOPNOTSUPP)


def shared_name(name):
    return bool(re.fullmatch(r'executions/e\d+/frames/[^/]+\.(?:png|jpg)', name))


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


def read_beneath(root, name, limit=SOURCE_LIMIT, *, shared=False):
    """shared=True also accepts a read-only shared frame inode (nlink > 1, no write bits)."""
    components = parts(name)
    parent = directory(root, components[:-1])
    try:
        fd = os.open(components[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(fd)
            linked = shared and not before.st_mode & 0o222
            if not stat.S_ISREG(before.st_mode) or (before.st_nlink != 1 and not linked) or before.st_size > limit:
                raise ValueError('unsafe_file')
            data = bytearray()
            while len(data) <= limit:
                chunk = os.read(fd, min(65536, limit + 1 - len(data)))
                if not chunk: break
                data.extend(chunk)
            after = os.fstat(fd)
            if (len(data) > limit or before.st_size != len(data) or (after.st_nlink != 1 and not linked)
                    or (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns)):
                raise ValueError('file_changed_or_oversize')
            return bytes(data)
        finally: os.close(fd)
    finally: os.close(parent)


def digest_beneath(root, name, limit=PUBLIC_EVIDENCE_LIMIT, *, shared=False):
    """SHA-256 with read_beneath's safety rules and constant-size read buffers.

    The 1 GiB default is for trusted Dev-tier public execution evidence, not
    authored source or requests. Oversize files have a distinct diagnostic.
    """
    components = parts(name)
    parent = directory(root, components[:-1])
    try:
        fd = os.open(components[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(fd)
            linked = shared and not before.st_mode & 0o222
            if not stat.S_ISREG(before.st_mode) or (before.st_nlink != 1 and not linked):
                raise ValueError('unsafe_file')
            if before.st_size > limit:
                raise ValueError(f'oversize_file:{name}:{before.st_size}')
            digest = hashlib.sha256()
            size = 0
            while size <= limit:
                chunk = os.read(fd, min(65536, limit + 1 - size))
                if not chunk: break
                size += len(chunk)
                digest.update(chunk)
            after = os.fstat(fd)
            if (before.st_size != size or after.st_size != size
                    or (after.st_nlink != 1 and not (shared and not after.st_mode & 0o222))
                    or (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns)):
                raise ValueError('file_changed_or_oversize')
            if size > limit:
                raise ValueError(f'oversize_file:{name}:{size}')
            return digest.hexdigest()
        finally: os.close(fd)
    finally: os.close(parent)


def stat_beneath(root, name, *, shared=False):
    """Size of a safe file (same no-follow/regular/nlink rules as read_beneath) without reading it."""
    components = parts(name)
    parent = directory(root, components[:-1])
    try:
        fd = os.open(components[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try: info = os.fstat(fd)
        finally: os.close(fd)
    finally: os.close(parent)
    if not stat.S_ISREG(info.st_mode) or (info.st_nlink != 1 and not (shared and not info.st_mode & 0o222)):
        raise ValueError('unsafe_file')
    return info.st_size


def atomic_write(root, name, data, mode=0o600):
    components = parts(name)
    parent = directory(root, components[:-1], create=True)
    temp = '.publish-' + uuid.uuid4().hex
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(data); stream.flush(); os.fsync(fd)
            if mode != 0o600: os.fchmod(fd, mode)
        finally: os.close(fd)
        os.replace(temp, components[-1], src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        try: os.unlink(temp, dir_fd=parent)
        except FileNotFoundError: pass
        os.close(parent)


def share(source_root, name, root, destination=None):
    """Hardlink one read-only canonical frame to root/destination; copy across filesystems.

    Returns 'linked', 'present' (already the same inode) or 'copied'.
    """
    destination = destination or name
    src, dst = parts(name), parts(destination)
    source = directory(source_root, src[:-1])
    try:
        info = os.stat(src[-1], dir_fd=source, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o222: raise ValueError('unsafe_shared_source')
        parent = directory(root, dst[:-1], create=True)
        try:
            try:
                current = os.stat(dst[-1], dir_fd=parent, follow_symlinks=False)
                if (current.st_dev, current.st_ino) == (info.st_dev, info.st_ino): return 'present'
            except FileNotFoundError: pass
            temp = '.publish-' + uuid.uuid4().hex
            try:
                os.link(src[-1], temp, src_dir_fd=source, dst_dir_fd=parent, follow_symlinks=False)
            except OSError as exc:
                if exc.errno not in COPY_ERRORS: raise
            else:
                try: os.replace(temp, dst[-1], src_dir_fd=parent, dst_dir_fd=parent)
                finally:
                    try: os.unlink(temp, dir_fd=parent)
                    except FileNotFoundError: pass
                return 'linked'
        finally: os.close(parent)
    finally: os.close(source)
    atomic_write(root, destination, read_beneath(source_root, name, info.st_size, shared=True), SHARED_MODE)
    return 'copied'


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
        linked = shared_name(name)
        self._restore(self.canonical,name,data,linked)
        self.published.add(name)
        if linked: share(self.canonical,name,self.ws)
        else: self._restore(self.ws,name,data)
        return {'path': name, 'sha256': hashlib.sha256(data).hexdigest()}

    @staticmethod
    def _restore(root,name,data,linked=False):
        # Full byte comparison through the no-follow reader; do not trust mtimes
        # or Dev-written history. Avoid fsync/rewrite of thousands of intact frames.
        try:
            if read_beneath(root,name,len(data),shared=linked)==data:
                if not linked or not os.lstat(root/name).st_mode & 0o222:return
        except (OSError,ValueError):pass
        atomic_write(root,name,data,SHARED_MODE if linked else 0o600)

    def regenerate(self):
        for name in sorted(self.published):
            # A shared frame is re-linked from the trusted canonical inode, never copied from ws.
            if shared_name(name) and not os.lstat(self.canonical / name).st_mode & 0o222:
                share(self.canonical, name, self.ws); continue
            self._restore(self.ws, name, read_beneath(self.canonical, name, max(SOURCE_LIMIT, (self.canonical / name).stat().st_size)))

    def prepare(self, task, config, success_condition=None, budget=None, wall_unlimited=False):
        from curobo_v2.fixture_interaction import config_enabled
        if config_enabled(config):self.fixture_config=config
        from .dev_tasks import dev_sdk, dev_sdk_prompt
        import hashlib
        from .dev_agent_diagnostics import public_observation, FORBIDDEN, screen_text
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
        prompt += (' Shape notes: the observe probe returns "observation" with the same flat'
            ' robot0_* keys as sensors.get_observation(), and "perception" as a sibling key.'
            ' Successful motion calls (move_delta, move_joints, gripper open/close) return'
            ' {ok:true,rejected:false}; pre-dispatch refusals return'
            ' {ok:false,rejected:true,reason,diagnostics}.')
        # Only the frozen, hash-bound SDK prompt is published verbatim: the path
        # heuristic withheld all of it in r4 ("open()/close()", "dict.get/keys").
        # Run-derived files keep their screening; a forbidden term is a hard error.
        trusted = hashlib.sha256(dev_sdk_prompt(config).encode()).hexdigest() == config.get('dev_sdk_prompt_sha256')
        if FORBIDDEN.search(prompt):raise ValueError('sdk_doc_forbidden_term')
        self.publish('docs/sdk.md', (prompt if trusted else screen_text(prompt)).encode())
        self.publish('docs/sdk.json', dev_sdk(config))
        self.publish('docs/safety.json', dict(translation_coordinate_m=.30 if config.get('arm_motion')=='long' else .05, rotation_rad=.5,
            joint_margin_rad=.02, base_clearance_m=.010, sdk_calls=320, control_steps=9600,
            execution_seconds=1920, versions=budget['versions'] if budget else 10, executions=40, probes=300,
            wall_seconds=None if wall_unlimited else budget['wall_seconds'] if budget else 21600,
            **(dict(no_progress_seconds=7200) if wall_unlimited else {})))
        self.publish('docs/devtool.py', Path(__file__).with_name('dev_agent_tools.py').read_bytes())
        tools = TOOLS_V2 if config.get('agent_policy_version','agent-policy-v1')=='agent-policy-v2' else TOOLS
        if budget:
            # Runtime wording only: the frozen TOOLS text (and its hashes) stay as they are.
            was='Limits: 10 formal versions, 40 executions, 300 probes, 6 hours total wall time;'
            if was not in tools:raise ValueError('tools_budget_text_missing')
            tools=tools.replace(was,f"Limits: {budget['versions']} formal versions, 40 executions, 300 probes, {budget['wall_seconds']} seconds total wall time;")
        if wall_unlimited:
            # Remote time-share: no wall limit; the run stops after 2 hours without progress instead.
            was=next((line for line in tools.splitlines() if line.startswith('Limits:') and 'total wall time;' in line),None)
            if was is None:raise ValueError('tools_budget_text_missing')
            tools=tools.replace(was,was.split(', 300 probes,')[0]+', 300 probes, no total wall time limit (the run stops after 2 hours of active time with no new execution, version or help request; waiting for the shared robot is not counted);')
        if config.get('agent_policy_version')=='agent-policy-v2' and config.get('agent_policy_id')=='trace_aware_hint_only':
            tools=tools.replace('Memory notes in memory/vN.json are advice, not authority. Each note is bound to\nthe version it was issued for; read the current version\'s notes before moving.\n', 'This hint_only run has no Memory input or public Memory files.\n')
        if config_enabled(config):tools+='\nfind_fixtures: {}. Reset-scene task fixture states and interaction parts in WORLD coordinates; no physics steps.\n'
        if success_condition:
            # The public rule only (region + rule), never a verdict (10-29 step 6).
            from .dev_agent_success import DEV_RULE, eval_view
            if success_condition.get('schema')=='attentionbench.success-condition.fixture.v1':
                from .dev_fixture_success import DEV_RULE
            self.publish('docs/success_condition.json', eval_view(success_condition))
            self.publish('docs/tools.md', (tools+DEV_RULE).encode())
        else:
            self.publish('docs/tools.md', tools.encode())
        self.publish('docs/guide.md', b'Use docs/devtool.py for all simulator interaction. Read task.json and docs/sdk.md.\nKeep notes in work/notes.md. Every moving attempt starts from the registered reset scene.\n')
        for name in ('versions', 'executions', 'probes'):
            self.publish('history/' + name + '.jsonl', b'')
        self.publish('history/version_history.json', [])
        atomic_write(self.ws, 'work/notes.md', b'')
        return self


TOOLS = '''# Dev tools (file protocol v1)
Run /usr/bin/python3 docs/devtool.py OP. All paths are relative to this workspace.
Requests are served one at a time. A request sent while another is running is
rejected with error busy (no charge); wait for the first response, then retry.
Memory notes in memory/vN.json are advice, not authority. Each note is bound to
the version it was issued for; read the current version's notes before moving.
submit --entry work/main.py --summary "Plan in 80 characters or fewer" [--reason TEXT]: one formal version, one reset execution,
then Eval; blocks until feedback. Only one formal submission may be in flight (busy).
run --no-eval --entry work/main.py: one reset execution, no version and no Eval.
observe: public robot observation from the registered reset scene. The observation dict
has the same flat robot0_* keys as sensors.get_observation(); perception is a sibling key.
Successful SDK motion calls return {ok:true,rejected:false}; refusals return
{ok:false,rejected:true,reason,diagnostics}.
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
Exact accepted payloads (the CLI builds these; extra keys are rejected):
  observe: {}
  find_objects: {} or {"names": ["obj", ...]}
  ik_check: {"target": {"side": "left"|"right", "position": [x,y,z] (world m),
             "quaternion_xyzw": [x,y,z,w] (unit)}, optional "start_joints", "base_offset"}
  plan_dry_run: {"action": {"group": "left_arm"|"right_arm", "operation": "move_delta",
             "args": [dx,dy,dz] or [dx,dy,dz,[rx,ry,rz]], "kwargs": {}}} | group "base"
             with args [dx,dy,dyaw] | group "bimanual", operation "move_joints",
             args [[14 joint radians]]; optional "start_joints", "base_offset"
  start_joints: {"openarm_left_joint1": rad, ...}; base_offset: {"dx": m, "dy": m, "dyaw": rad}
Two different base limits:
  - Probe base_offset (hypothetical, ik_check/plan_dry_run): up to 0.6 m translation and
    0.9 rad yaw. It is split into N equal steps that each fit the per-call limit; every
    step runs the unchanged exact execution clearance check, and base_clearance reports
    steps, step_delta, checked_steps, worst_step and the worst clearance.
  - Real base motion (base.move_delta in executed code, and a plan_dry_run base action):
    0.20 m translation / 0.3 rad yaw per call. A larger move needs several calls,
    e.g. N calls of step_delta as reported by the probe.
Anything beyond these limits is rejected with that message before dispatch.
A malformed payload is rejected before dispatch (error invalid_request,
validation_error names the check) and costs nothing. A planner rejection returns
planning_accepted false with its reason. If a probe itself fails (exception or timeout),
the response is completed with probe_error {error_class, message} and owner_restarted;
it costs one probe and the run continues with a fresh simulator owner.
ask_help --question TEXT: available once per eligible formal interval while help remains.
Questions must not quote Eval or evaluation text; such a question is refused
(status unavailable, with that reason) and costs no attention.
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
validation 5 s. A probe that must first start the simulator (the first probe, and the
first after any execution) gets 300 s more. Deadlines are clipped to remaining run time. Uncertain work blocks dispatch.
Safety: arm coordinates <= 0.05 m / 0.5 rad per action, joint margin 0.02 rad,
base path clearance 10 mm; base translation <= 0.20 m and yaw <= 0.30 rad.
Never bypass safety. Authenticated pre-dispatch rejections return a structured result
and allow another action; all other faults stop. Read full trace, separate stdout/stderr,
diagnostics and final observation under executions/. Files are regenerated by the broker.
Request: {schema:"attentionbench.dev-agent-request.v1",request_id,op,payload}.
Response: {schema:"attentionbench.dev-agent-response.v1",request_id,digest,op,status,...}.
Responses use accepted/running/completed/rejected/needs_review; artifact links are relative.
'''

# Keep TOOLS above byte-for-byte for manifests without the v2 marker.
TOOLS_V2 = TOOLS.replace(
    '# Dev tools (file protocol v1)', '# Dev tools (agent-policy-v2; file protocol v1)'
).replace(
    TOOLS[TOOLS.index('ask_help --question TEXT:'):TOOLS.index('status: current counts')],
    '''ask_help --question TEXT: ask in your own words, nonempty and at most 2000 characters.
Questions must pass the public boundary; do not quote Eval or evaluation text.
All policies share help_budget (default 1). Denied requests do not consume help
or human attention. A request actually handed to the provider consumes one help,
even if its reply fails or is rejected; delivery and actual cost are recorded separately.
Only one request can be pending with the provider at a time.
autonomous: no help. demo_first: one fixed demo before the first formal version.
reactive_help: every formal Eval failed verdict requires you to ask. Passed or
unavailable verdicts do not require help.
retry_k_then_ask: ask after k consecutive formal Eval failures (default k=2).
An actual provider handoff resets the counter; passed resets it; unavailable
neither increases nor clears it. When budget/pending blocks a request the counter
stays at k, so the next failed version tries again.
For these two policies pending_ask is a mandatory question obligation. run and
probes remain available. A new submit is refused as ask_required BEFORE charging
any version or execution. Use ask_help, then submit with a new request id. The
refused request id always replays its refusal. If you have not asked at the next
submit or normal run end, the system tries a forced_fallback question. Hard wall,
quota, cancellation or fatal stops can block it (forced_fallback_blocked); a blocked
fallback is not a provider request and costs nothing.
budget_matched_random_escalation: system-injected hints at preregistered cumulative
Eval failure slots, default target_request_count=1, total_failure_slots=10, seed=101.
trace_aware_hint_only and full_trace_aware_attention_planner: system-injected hints
only after failed formal Eval: safety stops first, exhausted help/pending blocks,
then stuck within a version (m=3), repeated failure across versions without progress,
or unresolved cause after n=2 failed versions. Probes are evidence, not stuck counts.
Full planner checks matching Memory before these trace triggers; hint_only has no
Memory input, notes, queries or resume messages. Other policies retain Memory notes.
Manual asks for system-injected policies return policy_denied.
agent_initiated_help: optional help after any completed run OR submit, regardless of
Eval verdict. At least min_new_executions new executions (default 1) are required
since the previous help; probes do not qualify. You may choose never to ask.
No execution, no new execution, exhausted budget or an outstanding request refuses
the ask without charge. Multiple asks in one formal interval are possible after
new executions when enough budget remains.
Inspect executions/eNNNNNN/help.json, history/help.jsonl and versions/vNNNN/help.json
for help ids, reason codes, delivery and actual fees. Practice run help remains in
the execution history even before the first submit. Version help files index all
help in their execution window. Eval advice and help reach the next resume message.
'''
)
