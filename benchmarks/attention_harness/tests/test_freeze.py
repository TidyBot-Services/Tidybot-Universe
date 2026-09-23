from __future__ import annotations

import json

import pytest

from benchmarks.attention_harness.freeze import FreezeError, create_manifest, verify_manifest


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def test_repository_manifest_is_deterministic_and_allows_explicit_heldout(tmp_path) -> None:
    first = create_manifest()
    second = create_manifest()
    assert first == second
    assert first["d6_parity"]["agreement"] == 1.0
    assert first["heldout_ready"] is True
    manifest = tmp_path / "manifest.json"
    _write(manifest, first)
    assert verify_manifest(manifest)["verified"] is True
    assert verify_manifest(manifest, require_heldout_ready=True)["heldout_ready"] is True


def test_modified_frozen_file_is_rejected(tmp_path) -> None:
    root = tmp_path / "repo"
    frozen = root / "frozen.txt"
    frozen.parent.mkdir(parents=True)
    frozen.write_text("changed", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    _write(
        manifest,
        {
            "file_sha256": {"frozen.txt": "0" * 64},
            "freeze_digest_sha256": "0" * 64,
            "heldout_ready": True,
        },
    )
    with pytest.raises(FreezeError, match="file_mismatches"):
        verify_manifest(manifest, repo_root=root)
