"""Create an empty operator store and its artifact directory.

Usage: python -m benchmarks.attention_harness.init_store --store PATH [--artifact-root DIR]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .core.store import AttentionStore


def init_store(store: Path, artifact_root: Path | None = None) -> Path:
    store = Path(store)
    artifacts = Path(artifact_root) if artifact_root is not None else store.parent / 'artifacts'
    store.parent.mkdir(parents=True, exist_ok=True)
    try:
        with store.open('xb'):
            pass
    except FileExistsError as exc:
        raise FileExistsError(f'AttentionStore already exists: {store}') from exc
    try:
        AttentionStore(store).close()
        artifacts.mkdir(parents=True, exist_ok=True)
    except Exception:
        store.unlink(missing_ok=True)
        raise
    return artifacts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', type=Path, required=True)
    parser.add_argument('--artifact-root', type=Path)
    args = parser.parse_args()
    try:
        artifacts = init_store(args.store, args.artifact_root)
    except FileExistsError as exc:
        parser.error(str(exc))
    print(f'AttentionStore: {args.store}\nArtifacts: {artifacts}')


if __name__ == '__main__':
    main()
