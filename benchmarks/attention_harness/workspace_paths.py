"""Lazy, machine-local paths for AttentionBench code."""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import legacy_paths


class WorkspacePathError(RuntimeError):
    """The local path registry cannot provide a required key."""


_registry: dict[str, str] | None = None


def registry_path() -> Path:
    root = legacy_paths.workspace_root()
    if root is None:
        raise WorkspacePathError(
            'Cannot find workspace for paths.json; set ATTENTIONBENCH_WORKSPACE to '
            'the workspace containing development/ and experiments/; '
            'template: config/attentionbench/paths.template.json'
        )
    return root / 'paths.json'


def _error(key: str, path: Path, detail: str) -> WorkspacePathError:
    return WorkspacePathError(
        f'{detail}: add "{key}" to {path}; template: '
        'config/attentionbench/paths.template.json'
    )


def _load(key: str) -> dict[str, str]:
    global _registry
    if _registry is None:
        try:
            path = registry_path()
        except WorkspacePathError as exc:
            raise WorkspacePathError(
                f'Cannot find paths.json for "{key}": {exc}; add "{key}" to '
                'the workspace paths.json; template: config/attentionbench/paths.template.json'
            ) from exc
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise _error(key, path, f'Cannot read path registry ({exc})') from exc
        if (not isinstance(data, dict) or data.get('schema') != 'attentionbench.paths.v1'
                or not isinstance(data.get('paths'), dict)
                or any(not isinstance(k, str) or not isinstance(v, str)
                       for k, v in data['paths'].items())):
            raise _error(key, path, 'Invalid path registry schema')
        _registry = data['paths']
    return _registry


def get(key: str) -> Path:
    return Path(get_str(key))


def get_str(key: str) -> str:
    paths = _load(key)
    if key not in paths:
        raise _error(key, registry_path(), 'Unknown path key')
    value = paths[key]
    return value if Path(value).is_absolute() else os.path.join(str(registry_path().parent), value)


class LazyPath(os.PathLike[str]):
    """A path constant whose registry lookup waits until the path is used."""

    def __init__(self, key: str):
        self.key = key

    def __fspath__(self) -> str:
        return get_str(self.key)

    def __str__(self) -> str:
        return get_str(self.key)

    def __repr__(self) -> str:
        return f'LazyPath({self.key!r})'

    def __truediv__(self, other: object) -> Path:
        return get(self.key) / other

    def __rtruediv__(self, other: object) -> Path:
        return Path(other) / get(self.key)

    def __getattr__(self, name: str):
        return getattr(get(self.key), name)

    def __eq__(self, other: object) -> bool:
        return get(self.key) == other

    def __hash__(self) -> int:
        return hash(get(self.key))


def lazy(key: str) -> LazyPath:
    return LazyPath(key)


def clear_cache() -> None:
    global _registry
    _registry = None
    legacy_paths.workspace_root.cache_clear()
