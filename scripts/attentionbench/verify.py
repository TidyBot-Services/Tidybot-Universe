#!/usr/bin/env python3
"""Prove a workspace works, without any model account. --model also checks one model route."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.attentionbench import hostenv, installer, routes

SCRIPTS = ROOT / 'scripts/attentionbench'


def _run(argv: list[str], *, cwd: Path = ROOT, timeout: int = 600, env: dict | None = None) -> tuple[bool, str]:
    try:
        result = subprocess.run(argv, cwd=cwd, env=env, text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f'timed out after {timeout} s'
    except OSError as exc:
        return False, str(exc)
    return result.returncode == 0, (result.stdout + result.stderr)[-1500:].strip()


def check_scene(ws: Path) -> tuple[bool, str]:
    """Simulator, assets and renderer: the registered scene must match and a frame must have content."""
    env = os.environ | {'PYTHONPATH': str(ROOT), 'PYTHONDONTWRITEBYTECODE': '1', 'MUJOCO_GL': 'egl'}
    ok, output = _run([str(ws / 'sim/runtime/venv/bin/python'), str(SCRIPTS / 'scene_fingerprint_check.py')], env=env)
    good = ok and 'matches_registered: True' in output and 'frame_rendered: True' in output
    return good, 'scene matches and a frame rendered' if good else output


def check_planner(ws: Path) -> tuple[bool, str]:
    """cuRobo on this GPU, through the same proof the chain ran at install time."""
    try:
        return True, f'planner proof passed: {installer.reprove(ws)}'
    except installer.StepError as exc:
        return False, str(exc)


def check_sandbox(ws: Path) -> tuple[bool, str]:
    """The agent sandbox itself, through the check every agent run makes before it starts."""
    env = os.environ | {'PYTHONPATH': str(ROOT), 'PYTHONDONTWRITEBYTECODE': '1'}
    ok, output = _run([str(ws / 'envs/ui/bin/python'), str(SCRIPTS / 'sandbox_check.py'), str(ws)], env=env, timeout=120)
    return ok, 'workspace sandbox denies what it must deny' if ok else output


def check_media(ws: Path) -> tuple[bool, str]:
    """Run recordings need H.264 encoding and ffprobe."""
    with tempfile.TemporaryDirectory(dir=ws / 'data/run') as temp:
        video = Path(temp) / 'check.mp4'
        ok, output = _run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=duration=1:size=64x64:rate=5',
                           '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(video)], timeout=60)
        if not ok:
            return False, output
        ok, output = _run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_name', '-of', 'csv=p=0', str(video)], timeout=60)
        return ok and 'h264' in output, 'H.264 recording works' if ok and 'h264' in output else output


def check_route(ws: Path, args: argparse.Namespace) -> tuple[bool, str]:
    """Ask the provider what each role needs. The codex route makes one short call on the user's quota."""
    if args.model == 'codex':
        ok, output = _run(['codex', 'exec', '--skip-git-repo-check', 'Reply with ok.'], timeout=120)
        return ok, 'codex answered' if ok else output
    env = os.environ | {'PYTHONPATH': str(ROOT), 'PYTHONDONTWRITEBYTECODE': '1'}
    argv = [str(ws / 'envs/ui/bin/python'), str(SCRIPTS / 'route_check.py'), args.model]
    for name in ('dev_model', 'eval_model', 'advisor_model', 'base_url', 'api_key_env'):
        if getattr(args, name):
            argv += ['--' + name.replace('_', '-'), getattr(args, name)]
    return _run(argv, env=env, timeout=180)


def run(ws: Path, args: argparse.Namespace | None = None) -> int:
    """Print one row per check and return how many failed."""
    checks = [('scene and renderer', check_scene), ('planner', check_planner), ('sandbox', check_sandbox), ('recording', check_media)]
    (ws / 'data/run').mkdir(parents=True, exist_ok=True)
    results = []
    for name, check in checks:
        ok, detail = check(ws)
        results.append({'check': name, 'status': 'PASS' if ok else 'FAIL', 'detail': detail})
        print(f'{results[-1]["status"]} {name}: {detail}', flush=True)
    if args is not None and args.model:
        ok, detail = check_route(ws, args)
        results.append({'check': 'model route ' + args.model, 'status': 'PASS' if ok else 'FAIL', 'detail': detail})
        print(f'{results[-1]["status"]} model route {args.model}: {detail}', flush=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    (ws / 'data/run' / f'verify-{stamp}.json').write_text(json.dumps(results, indent=2) + '\n')
    return sum(row['status'] == 'FAIL' for row in results)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace', default=hostenv.default_workspace(ROOT), help='defaults to the workspace holding this checkout')
    p.add_argument('--model', choices=tuple(routes.ROUTES), help='also check this model route')
    p.add_argument('--dev-model')
    p.add_argument('--eval-model')
    p.add_argument('--advisor-model')
    p.add_argument('--base-url')
    p.add_argument('--api-key-env')
    args = p.parse_args(argv)
    if not args.workspace:
        p.error('--workspace is required for a checkout outside a workspace')
    ws = Path(args.workspace).expanduser().resolve()
    if not (ws / 'paths.json').is_file():
        print(f'{ws} has not been set up (no paths.json)', file=sys.stderr)
        return 1
    hostenv.apply(ws, keep=(args.api_key_env,))
    return 1 if run(ws, args) else 0


if __name__ == '__main__':
    raise SystemExit(main())
