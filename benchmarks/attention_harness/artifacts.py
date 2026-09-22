"""Stable episode artifact writer."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np


def create_episode_dir(root: Path, task_id: str, seed: int) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    episode_dir = root / f"{task_id}-seed{seed}-{stamp}"
    episode_dir.mkdir(parents=True, exist_ok=False)
    return episode_dir


def write_episode_artifacts(
    episode_dir: Path,
    *,
    result: Mapping[str, Any],
    trace: list[dict[str, Any]],
    initial_observation: Mapping[str, np.ndarray],
    final_observation: Mapping[str, np.ndarray],
) -> None:
    (episode_dir / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    with (episode_dir / "trace.jsonl").open("w", encoding="utf-8") as stream:
        for event in trace:
            stream.write(json.dumps(event, sort_keys=True) + "\n")
    np.savez_compressed(episode_dir / "initial_observation.npz", **initial_observation)
    np.savez_compressed(episode_dir / "final_observation.npz", **final_observation)
