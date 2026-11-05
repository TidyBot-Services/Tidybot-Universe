"""Model routes: who answers for Dev, Eval and Advisor, and where a route's key comes from.

Standard library only. A key is read here and sent once to the local UI's in-memory vault; it is never
printed, logged or written to a file.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import shutil
import subprocess

ROLES = ('dev', 'eval', 'advisor')


@dataclass(frozen=True)
class Route:
    name: str
    provider: str | None
    env: tuple = ()
    key_file: str | None = None
    models: dict = field(default_factory=dict)
    hint: str = ''


ROUTES = {
    'codex': Route('codex', None, hint='run codex login'),
    'parcc': Route('parcc', 'parcc', ('PARCC_API_KEY', 'LITELLM_KEY'), '.litellm-key',
                   # GLM reads text only, but Qwen rarely ends an Eval on the required JSON (real check 2026-11-05).
                   {'dev': 'parcc/GLM', 'eval': 'parcc/GLM', 'advisor': 'parcc/Qwen'},
                   'set PARCC_API_KEY or write the key to ~/.litellm-key'),
    'deepseek': Route('deepseek', 'deepseek', ('DEEPSEEK_API_KEY',),
                      models=dict.fromkeys(ROLES, 'deepseek-flash'), hint='set DEEPSEEK_API_KEY'),
    'openai': Route('openai', 'openai', ('OPENAI_API_KEY',), hint='set OPENAI_API_KEY and pass --dev-model'),
    'custom': Route('custom', 'openai-compatible', hint='pass --base-url, --api-key-env and --dev-model'),
}
# Automatic choice: the account first, then keys. A custom endpoint is always asked for by name.
AUTO_ORDER = ('codex', 'parcc', 'deepseek', 'openai')


def codex_logged_in() -> bool:
    codex = shutil.which('codex')
    if not codex:
        return False
    try:
        return subprocess.run([codex, 'login', 'status'], capture_output=True, timeout=60, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def find_key(route: Route, api_key_env: str | None = None, home: Path | None = None) -> str | None:
    for name in (api_key_env,) if api_key_env else route.env:
        if os.environ.get(name):
            return os.environ[name]
    if route.key_file:
        try:
            return (Path.home() if home is None else home).joinpath(route.key_file).read_text().strip() or None
        except OSError:
            return None
    return None


def available() -> list[str]:
    """Routes this machine can use now, without asking for anything."""
    found = ['codex'] if codex_logged_in() else []
    return found + [name for name in AUTO_ORDER[1:] if find_key(ROUTES[name])]


def choose(requested: str) -> Route:
    if requested != 'auto':
        return ROUTES[requested]
    found = available()
    if found:
        return ROUTES[found[0]]
    raise LookupError('No model route is ready. ' + '; '.join(
        f'{name}: {ROUTES[name].hint}' for name in AUTO_ORDER))


def role_models(route: Route, dev: str | None, eval_model: str | None, advisor: str | None) -> dict:
    """Named models win; a role left out falls back to the route's default, then to Dev's model."""
    dev = dev or route.models.get('dev')
    if not dev:
        raise ValueError(f'Route {route.name} has no default model: pass --dev-model')
    return {'dev': dev, 'eval': eval_model or route.models.get('eval') or dev,
            'advisor': advisor or route.models.get('advisor') or dev}
