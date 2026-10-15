"""Find things that lived at an old Desktop path after the workspace was reorganized.

The reorganization moved repositories and evidence under ``AttentionBench-Workspace``
and deliberately left no stand-in entries on the Desktop. Catalogs and frozen
packages still record the old absolute paths, and those records are history that
must not be rewritten. ``resolve`` answers "where is that now?" at read time:

* a path that exists is returned unchanged, so nothing changes where nothing moved;
* a missing ``/home/truares/桌面/<name>/...`` is looked up in the workspace;
* stored strings are never edited, and every fallback is logged once.

The lookup uses the workspace's own ``path-map.tsv`` (rows marked ``move``) when it
is present and falls back to finding the top-level name under the workspace folders.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

DESKTOP = Path("/home/truares/桌面")
WORKSPACE_ENV = "ATTENTIONBENCH_WORKSPACE"
# Workspace folders searched, in order, when the path map has no exact entry.
SEARCH = ("development", "experiments", "environments", "openarm", "diagnostics",
          "logs-and-misc", "archive")

_log = logging.getLogger(__name__)
_announced: set[str] = set()


@lru_cache(maxsize=None)
def workspace_root(start: str | None = None) -> Path | None:
    """The AttentionBench-Workspace folder this code runs inside, if any."""
    override = os.environ.get(WORKSPACE_ENV)
    if override:
        root = Path(override)
        return root if root.is_dir() else None
    here = Path(start or __file__).resolve()
    for parent in here.parents:
        if (parent / "development").is_dir() and (parent / "experiments").is_dir():
            return parent
    return None


@lru_cache(maxsize=None)
def _moves(root: str) -> tuple[tuple[Path, Path], ...]:
    """(old, new) pairs from the workspace path map, longest old path first."""
    table = Path(root) / "path-map.tsv"
    pairs: list[tuple[Path, Path]] = []
    if table.is_file():
        for line in table.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#"):
                continue
            columns = line.split("\t")
            if len(columns) >= 3 and columns[2].strip() == "move":
                pairs.append((Path(columns[0]), Path(columns[1])))
    return tuple(sorted(pairs, key=lambda pair: -len(pair[0].parts)))


def clear_cache() -> None:
    """For tests that build a throwaway workspace."""
    workspace_root.cache_clear()
    _moves.cache_clear()
    _announced.clear()


def resolve(path: str | os.PathLike[str]) -> Path:
    """The current location of ``path``; the path itself when it exists or is unknown."""
    original = Path(path)
    if original.exists():
        return original
    try:
        relative = original.relative_to(DESKTOP)
    except ValueError:
        return original
    root = workspace_root()
    if root is None or not relative.parts:
        return original
    found = None
    for old, new in _moves(str(root)):
        try:
            tail = original.relative_to(old)
        except ValueError:
            continue
        candidate = new / tail
        if candidate.exists():
            found = candidate
            break
    if found is None:
        for folder in SEARCH:
            candidate = root / folder / relative
            if candidate.exists():
                found = candidate
                break
    if found is None:
        return original
    key = str(original)
    if key not in _announced:
        _announced.add(key)
        _log.info("legacy path %s is now %s", original, found)
    return found


def resolve_tree(value: Any) -> Any:
    """Copy of a JSON-like value with each missing Desktop path replaced by its new home.

    Only strings that start with the Desktop prefix, do not exist, and have a new
    location change; hashes, labels and every other value are returned untouched.
    """
    if isinstance(value, dict):
        return {key: resolve_tree(item) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_tree(item) for item in value]
    if isinstance(value, str) and value.startswith(str(DESKTOP) + "/"):
        return str(resolve(value))
    return value
