#!/usr/bin/env python3
"""Start a locked development-tier agent run and collect a collaborator summary."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.attentionbench import control, hostenv, installer, routes, setup

TEMPLATE = ROOT / 'config/attentionbench/run-request.template.json'
TERMINAL = {'completed', 'failed', 'cancelled', 'needs_review', 'condition_invalid', 'not_started'}
EID = re.compile(r'devexp-[a-f0-9]{24}\Z')
# The UI creates a run in a subprocess it allows 60 s (daily_dev_v2), then starts the worker.
CREATE_TIMEOUT = 180
# A busy machine can miss a few polls; a run must not die for that. 15 misses is about five minutes.
POLL_FAILURES = 15


def request_body(task: str, seed: int, minutes: float, overrides: dict | None = None, provider: dict | None = None) -> dict:
    if not re.fullmatch(r'[a-z][a-z0-9_]*', task):
        raise ValueError('task must be a task id containing lowercase letters, digits and underscores')
    if seed < 0 or minutes <= 0:
        raise ValueError('seed must be non-negative and minutes must be positive')
    if overrides and set(overrides) - {'task_id', 'seed', 'wall_limit', 'goal_key', 'request_key'}:
        raise ValueError('request override is not allowed')
    body = json.loads(TEMPLATE.read_text())
    selection = body['selection']
    key = f'collab-{task}-seed{seed}-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{secrets.token_hex(3)}'
    selection.update(tasks=[task], seeds=[seed], wall_limit=int(minutes * 60) + 240,
                     goal_key=key, request_key=key)
    if overrides:
        for name, value in overrides.items():
            if name == 'task_id':
                selection['tasks'] = [value]
            elif name == 'seed':
                selection['seeds'] = [value]
            else:
                selection[name] = value
    def forbidden(value):
        if isinstance(value, dict):
            return any(k in {'key', 'api_key'} or forbidden(v) for k, v in value.items())
        if isinstance(value, list):
            return any(forbidden(v) for v in value)
        return False
    if forbidden(body):
        raise ValueError('request template contains a forbidden credential field')
    if any(selection[role] != 'gpt-6.1-sol' for role in ('dev_model', 'eval_model', 'advisor_model')):
        raise ValueError('run request models differ from the L2 lock')
    if provider:
        # Runs on another model say so in the manifest, and the shorter limits are frozen with them.
        selection.update(agent_provider=provider, agent_limits={
            'versions': selection['max_versions'], 'wall_seconds': max(3600, int(minutes * 60) + 240)})
    return body


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.load(response)


def post_json(url: str, token: str, body: dict, timeout: float = 15, method: str = 'POST') -> dict:
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method=method, headers={
        'X-Attention-Token': token, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(exc.read().decode('utf-8', errors='replace')) from exc


def checked_role(check, role: str, candidates: list[str]) -> tuple[str, bool, list[str]]:
    """The first candidate model that can do this role's work, and whether it can read images.

    Dev has to pass every check. Eval and Advisor are allowed a model that cannot read images, and
    the run then says so; any other failure means the next candidate, then an error.
    """
    problems = []
    for model in candidates:
        result = check(model, role)
        if result.get('ok'):
            return model, True, problems
        failed = [name for name, value in result.get('checks', {}).items() if value == 'fail']
        if role != 'dev' and failed == ['vision']:
            return model, False, problems
        problems.append(f'{model}: ' + '; '.join(error.get('message', '') for error in result.get('errors', [])))
    raise installer.StepError('model', f'no model passed the {role} check. ' + ' | '.join(problems),
                              'Check the key, the model name and that the provider is reachable from this machine.')


def prepare_route(base: str, token: str, route: routes.Route, args: argparse.Namespace) -> tuple[dict, list[str]]:
    """Give the key to the local UI's memory, check each role, and describe the roles for the run."""
    key = routes.find_key(route, args.api_key_env)
    if not key:
        raise installer.StepError('model', f'route {route.name} has no key', route.hint)
    credential = 'run-' + route.name
    url = f'{base}/api/provider/credentials/{credential}'
    post_json(url, token, {'provider': route.provider, 'key': key, **({'base_url': args.base_url} if args.base_url else {})}, method='PUT')
    models = routes.role_models(route, args.dev_model, args.eval_model, args.advisor_model)
    named = {'dev': args.dev_model, 'eval': args.eval_model, 'advisor': args.advisor_model}
    check = lambda model, role: post_json(url + '/test', token, {'model': model, 'role': role}, timeout=120)
    roles, warnings = {}, []
    for role in routes.ROLES:
        # A route's default model may be down; Dev's model can stand in unless a model was named.
        candidates = [models[role]] + ([models['dev']] if not named[role] and models[role] != models['dev'] else [])
        model, sees, problems = checked_role(check, role, candidates)
        if problems:
            warnings.append(f'{role}: using {model} because ' + ' | '.join(problems))
        roles[role] = {'credential': credential, 'provider': route.provider, 'model': model}
        if not sees:
            roles[role]['vision'] = False
            warnings.append(f'{role.capitalize()} did not see images: {model} cannot read them, so it judges from text alone.')
    return {'roles': roles}, warnings


def command(action: str, ws: Path, ui_port: int, memory_port: int) -> None:
    argv = [sys.executable, str(Path(control.__file__)), action, '--workspace', str(ws),
            '--ui-port', str(ui_port), '--memory-port', str(memory_port)]
    result = subprocess.run(argv, text=True, capture_output=True)
    if result.stdout:
        print(result.stdout, end='', flush=True)
    if result.returncode:
        raise installer.StepError(action, (result.stderr or result.stdout).strip(),
                                  f'Inspect {ws / "data/run"} logs and retry {action}.')


def summary(ws: Path, eid: str, key: str, snapshot: dict, route: str = 'codex', warnings: tuple = ()) -> dict:
    cells = snapshot.get('cells', [])
    versions = [version for cell in cells for version in cell.get('versions', [])]
    verdicts = [version.get('eval_verdict', version.get('eval')) for version in versions
                if version.get('eval_verdict', version.get('eval')) is not None]
    run_dir = ws / 'data/run'
    run_dir.mkdir(parents=True, exist_ok=True)
    experiment_file = run_dir / f'{eid}-experiment.json'
    experiment_file.write_text(json.dumps(snapshot, indent=2) + '\n')
    result = {
        'experiment_id': eid, 'request_key': key, 'final_state': snapshot.get('state'),
        'version_count': len(versions), 'eval_verdict': verdicts[-1] if verdicts else None,
        'model_route': route, 'warnings': list(warnings),
    }
    summary_file = run_dir / f'{eid}-summary.json'
    result['l3_files'] = [str(summary_file), str(experiment_file)]
    summary_file.write_text(json.dumps(result, indent=2) + '\n')
    return result


def execute(args: argparse.Namespace) -> int:
    if not args.workspace:
        raise installer.StepError('workspace', 'no workspace given', 'Pass the --workspace used by setup.sh.')
    ws = setup.workspace_path(args.workspace)
    if args.ui_port and args.ui_port == args.memory_port:
        raise ValueError('UI and Memory ports must differ')
    if not (ws / 'paths.json').is_file():
        raise installer.StepError('workspace', f'{ws} has not been set up (no paths.json)',
                                  f'Run setup.sh --workspace {ws} first.')
    hostenv.apply(ws)
    try:
        route = routes.choose(args.model)
    except LookupError as exc:
        raise installer.StepError('model', str(exc), 'Set up one model route and rerun; setup and verify work without one.') from exc
    if route.name == 'custom':
        if not (args.base_url and args.api_key_env and args.dev_model):
            raise installer.StepError('model', 'the custom route needs --base-url, --api-key-env and --dev-model', route.hint)
        os.environ['ATTENTION_ALLOW_CUSTOM_PROVIDER_URL'] = '1'
    body = request_body(args.task, args.seed, args.minutes)  # Refuse a bad request before anything starts.
    ui_port, memory_port = hostenv.choose_ports(ws, args.ui_port, args.memory_port)
    lock_handle = None
    if args.lock:
        lock_file = Path(args.lock)
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        lock_handle = lock_file.open('a+')
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_handle.close()
            raise installer.StepError('lock', f'{lock_file} is busy', 'Wait for the other run to finish.')
    try:
        base = f'http://127.0.0.1:{ui_port}'
        print(f'Model route: {route.name}', flush=True)
        if route.name == 'codex':
            # The quota reading from setup's warm-up is usually stale by now (runs accept 10 minutes).
            setup.warmup(False)
        command('start', ws, ui_port, memory_port)
        try:
            token = control.control_token(base + '/ui/')
            warnings = []
            if route.provider:
                provider, warnings = prepare_route(base, token, route, args)
                for warning in warnings:
                    print(f'WARNING: {warning}', flush=True)
                body = request_body(args.task, args.seed, args.minutes, provider=provider)
            created = post_json(base + '/api/dev-v2/experiments', token, body, timeout=CREATE_TIMEOUT)
            eid = created.get('experiment_id')
            if not isinstance(eid, str) or not EID.fullmatch(eid):
                raise RuntimeError(f'create response has no valid experiment_id: {created}')
            print(f'experiment_id: {eid}\nUI: {base}/ui/', flush=True)
            resource = base + '/api/dev-v2/experiments/' + eid
            deadline = time.monotonic() + args.minutes * 60
            latest = created
            interrupted = False
            misses = 0
            try:
                while True:
                    try:
                        latest = get_json(resource)
                        misses = 0
                    except (OSError, ValueError, http.client.HTTPException) as exc:
                        misses += 1
                        if misses >= POLL_FAILURES:
                            raise RuntimeError(f'the UI stopped answering: {exc}') from exc
                        print(f'UI did not answer ({exc}); trying again', flush=True)
                    if latest.get('state') in TERMINAL:
                        break
                    if time.monotonic() >= deadline:
                        print('Run time limit reached; cancelling.', flush=True)
                        break
                    time.sleep(min(20, max(0, deadline - time.monotonic())))
            except KeyboardInterrupt:
                interrupted = True
                print('Interrupted; cancelling.', flush=True)
            if latest.get('state') not in TERMINAL:
                reply = post_json(resource + '/cancel', token, {})
                print(f"cancel: state={reply.get('state')} cancelled={reply.get('cancelled')}", flush=True)
                cancel_deadline = time.monotonic() + 120
                while time.monotonic() < cancel_deadline:
                    latest = get_json(resource)
                    if latest.get('state') in TERMINAL:
                        break
                    time.sleep(2)
                if latest.get('state') not in TERMINAL:
                    raise RuntimeError(f'{eid} did not reach a terminal state after cancellation')
            result = summary(ws, eid, body['selection']['request_key'], latest, route.name, warnings)
            print(json.dumps(result, indent=2), flush=True)
            return 130 if interrupted else 0
        finally:
            if not args.no_stop:
                command('stop', ws, ui_port, memory_port)
    finally:
        if lock_handle:
            lock_handle.close()


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace', default=hostenv.default_workspace(ROOT), help='defaults to the workspace holding this checkout')
    p.add_argument('--model', choices=('auto', *routes.ROUTES), default='auto',
                   help='who answers for the agents; auto takes the first ready route')
    p.add_argument('--dev-model', help='model name; needed for openai and custom')
    p.add_argument('--eval-model')
    p.add_argument('--advisor-model')
    p.add_argument('--base-url', help='custom route: an OpenAI-compatible API base URL')
    p.add_argument('--api-key-env', help='custom route: the environment variable holding the key')
    p.add_argument('--task', default='pick_place_counter_to_sink')
    p.add_argument('--seed', type=int, default=101)
    # V2 (2026-11-02): no version within 30 minutes; L2 needed 37. 45 leaves room for one.
    p.add_argument('--minutes', type=float, default=45)
    p.add_argument('--lock')
    p.add_argument('--no-stop', action='store_true')
    p.add_argument('--ui-port', type=int, help='default: the workspace port, or the next free one')
    p.add_argument('--memory-port', type=int, help='default: the workspace port, or the next free one')
    return p


def main(argv: list[str] | None = None) -> int:
    try:
        return execute(parser().parse_args(argv))
    except installer.StepError as exc:
        print(str(exc), file=sys.stderr)
    except Exception as exc:
        print(installer.StepError('run', str(exc), 'Inspect data/run logs and rerun.'), file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
