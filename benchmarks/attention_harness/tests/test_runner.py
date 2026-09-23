from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.attention_harness import runner


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
