from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from benchmarks.attention_harness import runner
from benchmarks.attention_harness.core.store import AttentionStore


class ManifestChecked(RuntimeError):
    pass


def test_heldout_requires_both_opt_in_and_verified_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    with pytest.raises(PermissionError, match="explicit allow_heldout"):
        runner.run_episode(
            task_id="cube_lift",
            seed=1001,
            policy="frozen-public",
            artifact_root=tmp_path,
        )

    def checked(path, *, require_heldout_ready=False, **kwargs):
        assert path.name == "freeze_manifest.json"
        assert require_heldout_ready is True
        raise ManifestChecked

    monkeypatch.setattr(runner, "verify_manifest", checked)
    with pytest.raises(ManifestChecked):
        runner.run_episode(
            task_id="cube_lift",
            seed=1001,
            policy="frozen-public",
            artifact_root=tmp_path,
            allow_heldout=True,
        )


class FakeAdapter:
    def __init__(self, *_args, **_kwargs) -> None:
        self.trace = []
        self.metadata = {"backend": "fake-robosuite"}

    def reset(self, _seed):
        return self.observe()

    def observe(self):
        return {
            "agentview_image": np.zeros((2, 2, 3), dtype=np.uint8),
            "robot0_eef_pos": np.zeros(3, dtype=np.float64),
        }

    def no_op_action(self):
        return np.zeros(7, dtype=np.float64)

    def step(self, action):
        self.trace.append(
            {
                "step": len(self.trace),
                "action": np.asarray(action).tolist(),
                "reward": 0.0,
                "done": False,
                "observation_sha256": "a" * 64,
            }
        )

    def native_success(self):
        return False

    def close(self):
        pass


def test_runner_automatically_writes_raw_attention_trace(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(runner, "RobosuiteRobotBackend", FakeAdapter)
    result = runner.run_episode(
        task_id="cube_lift",
        seed=101,
        policy="no-op",
        artifact_root=tmp_path,
        service_url="http://fake-service",
    )

    link = result["attention_trace"]
    store = AttentionStore(Path(link["store"]))
    assert store.get_raw_trace(link["raw_trace_id"])["metadata"]["suite"] == "robosuite"
    assert link["advisor_trace_id"] is None
    assert Path(link["bundle"]).is_file()
