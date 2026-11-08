"""Pinned Codex 0.155.1 isolation, fail closed (REVIEW R1).

D1 discovery, 2026-10-28: `exec --strict-config --ignore-user-config` accepts
features.{web_search_request,web_search_cached,standalone_web_search,js_repl,
code_mode,code_mode_host,code_mode_only,multi_agent,multi_agent_v2,plugins,
browser_use,browser_use_external,browser_use_full_cdp_access,computer_use,
memories,hooks,skill_search,skill_mcp_dependency_install,skip_host_skill_discovery,
apps,in_app_browser,in_app_local_automation,remote_plugin,recommended_plugins,
image_generation,shell_snapshot,workspace_dependencies}; web_search="disabled",
mcp_servers={}, skills.config=[] also parse. Unknown features.skills/features.mcp
and a negative-control feature are REJECTED (not silently ignored).
`features --strict-config` is unsupported. js_repl is listed as removed;
web_search_request/cached are deprecated, so top-level web_search is disabled too.
Skip-host-skill-discovery must be TRUE, not false. Plugins/apps and host discovery
are disabled together; a fresh workspace contains no .agents/.codex instructions.
`exec --help` explicitly says --ignore-user-config still uses CODEX_HOME for auth.
Additional accepted false keys: code_mode_buffered_exec, code_mode_interrupt,
code_mode_prewarm, multi_agent_mode. features.code_mode_host=false is accepted but
must NOT be set: in 0.155.1 it makes every tool call fail ("code-mode host is
disabled"), so it is not in DISABLED. Real canary (Claude, outside the Codex
sandbox, 2026-10-28) passed: shell/`..`/symlink/view_image/apply_patch/curl all
denied, workspace read+write OK. --ask-for-approval must precede exec; sandbox
requires -P ws even when default_permissions is set. The actual D1 preflight was
blocked by the enclosing sandbox: synthetic bubblewrap mount registry lock under
/tmp/codex-bwrap-synthetic-mount-targets-1000 was read-only. No fallback was used.
Actual authenticated execution is NOT attested here until the opt-in real canary
passes; no inference call is made by preflight. Keep the default CODEX_HOME.

The installed executable is inside the operator-excluded ~/.codex tree. D1 does
not hash it during development. codex_identity() hashes only at an authorized
runtime launch; it must never be invoked by tests against the excluded real file.
Failure to hash or pin identity refuses launch, never substitutes an empty digest.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

DISABLED = '''standalone_web_search js_repl
code_mode code_mode_only code_mode_buffered_exec code_mode_interrupt
code_mode_prewarm multi_agent multi_agent_v2 multi_agent_mode plugins
browser_use browser_use_external browser_use_full_cdp_access computer_use memories
hooks skill_search skill_mcp_dependency_install apps in_app_browser
in_app_local_automation remote_plugin recommended_plugins image_generation
shell_snapshot workspace_dependencies'''.split()


# 2026-10-29: bumped from 0.155.1 (gpt-6.1-sol needs the newer CLI); re-verified by preflight + canary.
PINNED_VERSION = 'codex-cli 0.165.0'


class IsolationUnavailable(RuntimeError): pass


def codex_home():
    return Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex')


def service_ports():
    """The UI and Memory ports this workspace uses, so the sandbox check tries the real ones."""
    raw=os.environ.get('ATTENTIONBENCH_SERVICE_PORTS','')
    return [int(port) for port in raw.split(',') if port.isdigit()] or [8769,8878]


def real_binary():
    executable = shutil.which('codex')
    if not executable: raise IsolationUnavailable('codex_missing')
    return Path(subprocess.check_output(['readlink', '-f', executable], text=True).strip())


def codex_identity():
    path = real_binary()
    version = subprocess.check_output(['codex', '--version'], text=True).strip()
    if version != PINNED_VERSION: raise IsolationUnavailable('unpinned_codex_version')
    return dict(path=str(path), version=version, sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def build_profile(workspace):
    from .dev_agent_workspace import READONLY_DIRS
    workspace = Path(workspace).resolve(strict=True)
    if not workspace.is_dir(): raise IsolationUnavailable('workspace_missing')
    binary = real_binary()
    q = json.dumps
    # Shared frame inodes live under these folders; read-only mounts stop chmod/write/unlink.
    readonly = ''.join(', ' + q(name) + ' = "read"' for name in READONLY_DIRS if (workspace / name).is_dir())
    filesystem = '{ ":minimal" = "read", ":project_roots" = { "." = "write"' + readonly + ' }, ' + \
        q(str(binary.parent)) + ' = "read", ' + q(str(codex_home() / 'tmp/arg0')) + ' = "read" }'
    return ['-c', 'permissions.ws.filesystem=' + filesystem, '-c', 'default_permissions="ws"']


def feature_args():
    result = ['--strict-config', '--ignore-user-config', '--ignore-rules', '--skip-git-repo-check']
    for name in DISABLED:
        result += ['-c', 'features.' + name + '=false']
    for setting in ('features.skip_host_skill_discovery=true', 'web_search="disabled"',
                    'mcp_servers={}', 'skills.config=[]', 'shell_environment_policy.inherit="none"',
                    'shell_environment_policy.set={ PATH="/usr/bin:/bin", PYTHONDONTWRITEBYTECODE="1" }'):
        result += ['-c', setting]
    return result


def controller_command(workspace, prompt):
    return ['codex', '-a', 'never', 'exec', *build_profile(workspace), *feature_args(),
            '-C', str(Path(workspace).resolve()), '--json',
            '-m', 'gpt-6-astra', '-c', 'model_reasoning_effort="low"', prompt]


def verify_runtime(workspace, private_dir, *, planner_port=None, run=subprocess.run):
    """Synthetic negative canaries; never opens actual forbidden host files.

    Connections occur ONLY in the nested network namespace. A missing namespace,
    unexpected file visibility, missing receipt or process error refuses launch.
    planner_port is required from the trusted frozen run config, never guessed.
    """
    hidden_paths = [p for p in os.environ.get('ATTENTION_PREFLIGHT_HIDDEN_PATHS', '').split(os.pathsep) if p]
    if any(not Path(p).is_absolute() for p in hidden_paths):
        raise IsolationUnavailable('preflight_hidden_path_must_be_absolute')
    ws, private = Path(workspace).resolve(strict=True), Path(private_dir).resolve(strict=True)
    if private == ws or private.is_relative_to(ws): raise IsolationUnavailable('private_inside_workspace')
    if not shutil.which('bwrap'): raise IsolationUnavailable('bubblewrap_missing')
    if type(planner_port) is not int or not 0 < planner_port < 65536:
        raise IsolationUnavailable('planner_port_required')
    repo = Path(__file__).resolve().parents[2]
    # Test path visibility via stat, never read auth or repository contents.
    with tempfile.TemporaryDirectory(prefix='d1-canary-', dir=ws.parent) as temp:
        sibling = Path(temp)
        (sibling / 'outside.txt').write_text('SYNTHETIC OUTSIDE CANARY')
        (sibling / 'reference-policies').mkdir()
        (sibling / 'reference-policies/answer.py').write_text('SYNTHETIC REFERENCE')
        synthetic = private / ('d1-canary-' + sibling.name)
        synthetic.mkdir()
        native = synthetic / 'native_result.json'
        native.write_text('SYNTHETIC PRIVATE CANARY')
        from .dev_agent_workspace import READONLY_DIRS, SHARED_MODE
        readonly = [name for name in READONLY_DIRS if (ws / name).is_dir()]
        for name in readonly:
            canary = ws / name / '.ro-canary'; canary.write_text('SYNTHETIC SHARED CANARY'); canary.chmod(SHARED_MODE)
        link = ws / ('outside-' + sibling.name)
        link.symlink_to(sibling / 'outside.txt')
        paths = [str(sibling / 'outside.txt'), str(sibling / 'reference-policies/answer.py'),
                 str(native), str(repo / 'benchmarks/attention_harness/dev_tasks.py'),
                 str(codex_home() / 'auth.json'), str(link),
                 str(ws / '..' / sibling.name / 'outside.txt'), *hidden_paths]
        script = '''import json,os,pathlib,socket
paths=__PATHS__
checks={"outside_hidden":all(not os.path.exists(p) for p in paths)}
# Ancestor scaffolding may exist for ws/runtime mounts, but must expose no siblings.
home=pathlib.Path.home()
allowed=__ANCESTORS__
checks["home_listing_restricted"]=all(str(p) in allowed for p in home.iterdir()) if home.exists() else True
p=pathlib.Path(".preflight-write")
p.write_text("workspace-ok")
checks["workspace_rw"]=p.read_text()=="workspace-ok"
p.unlink()
ro=True
for d in __READONLY__:
 for name,fn in (("create",lambda:open(d+"/.ro-new","w").close()),("chmod",lambda:os.chmod(d+"/.ro-canary",0o644)),
                 ("append",lambda:open(d+"/.ro-canary","ab").close()),("unlink",lambda:os.unlink(d+"/.ro-canary"))):
  try:fn();ro=False
  except OSError:pass
checks["shared_readonly"]=ro
checks["network_denied"]=True
for port in __PORTS__:
 s=None
 try:s=socket.socket();s.settimeout(.2);s.connect(("127.0.0.1",port));checks["network_denied"]=False
 except OSError:pass
 finally:
  if s is not None:s.close()
print(json.dumps(checks))
'''.replace('__PATHS__', repr(paths)).replace('__PORTS__', repr([*service_ports(), planner_port])).replace('__READONLY__', repr(readonly)).replace(
    '__ANCESTORS__', repr([str(p) for target in (ws, real_binary(), codex_home()/'tmp/arg0') for p in target.parents]))
        command = ['codex', 'sandbox', '-P', 'ws', *build_profile(ws), '-C', str(ws), '--', '/usr/bin/python3', '-I', '-c', script]
        try:
            completed = run(command, cwd=ws, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
            receipt = {'command': command, 'returncode': completed.returncode,
                       'stdout': completed.stdout, 'stderr': completed.stderr}
            (private / 'isolation-preflight.json').write_text(json.dumps(receipt, indent=2))
            if completed.returncode: raise IsolationUnavailable('sandbox_preflight_failed: ' + completed.stderr[-1000:])
            try: checks = json.loads(completed.stdout)
            except ValueError as exc: raise IsolationUnavailable('invalid_preflight_receipt') from exc
            if set(checks) != {'outside_hidden', 'home_listing_restricted', 'workspace_rw', 'shared_readonly', 'network_denied'} or not all(v is True for v in checks.values()):
                raise IsolationUnavailable('boundary_check_failed')
            return receipt
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise IsolationUnavailable('sandbox_runtime_unavailable') from exc
        finally:
            link.unlink(missing_ok=True); native.unlink(); synthetic.rmdir()
            for name in readonly:
                (ws / name / '.ro-canary').unlink(missing_ok=True); (ws / name / '.ro-new').unlink(missing_ok=True)


def launch_controller(workspace, private_dir, prompt, *, planner_port):
    """D2 entry point: no controller may start without identity and fresh canaries.

    The trusted caller must be authorized to hash its installed runtime. Tests in
    restricted development environments use a fake binary identity instead.
    """
    identity = codex_identity()
    verify_runtime(workspace, private_dir, planner_port=planner_port)
    private = Path(private_dir)
    frozen = private / 'codex-identity.json'
    if frozen.exists() and json.loads(frozen.read_text()) != identity:
        raise IsolationUnavailable('runtime_identity_changed')
    from .dev_agent_workspace import atomic_write, encode
    atomic_write(private, 'codex-identity.json', encode(identity))
    events = (private / 'controller.jsonl').open('ab')
    errors = (private / 'controller.stderr').open('ab')
    try:
        return subprocess.Popen(controller_command(workspace, prompt), cwd=workspace,
                                stdin=subprocess.DEVNULL, stdout=events, stderr=errors,
                                start_new_session=True)
    finally:
        events.close(); errors.close()
