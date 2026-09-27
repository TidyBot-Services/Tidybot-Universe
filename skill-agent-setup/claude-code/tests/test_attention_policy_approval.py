"""A Dev edit invalidates an earlier approval before trusted execution."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from attentionbench_bridge import _verify_approved_policy


def test_exact_source_digest_is_required(tmp_path):
    policy = tmp_path / "lab_policy.py"
    policy.write_text("def run(sdk, context):\n    pass\n")
    digest = hashlib.sha256(policy.read_bytes()).hexdigest()
    config = {"robot_policy": "lab_policy:run", "approved_policy_sha256": digest}
    _verify_approved_policy(config, repo_root=tmp_path)
    policy.write_text("def run(sdk, context):\n    sdk.gripper.close()\n")
    with pytest.raises(ValueError, match="changed after approval"):
        _verify_approved_policy(config, repo_root=tmp_path)
    with pytest.raises(ValueError, match="approved_policy_sha256"):
        _verify_approved_policy({"robot_policy": "lab_policy:run"}, repo_root=tmp_path)


def test_policy_source_must_remain_within_checkout(tmp_path):
    with pytest.raises(ValueError, match="inside the Universe checkout"):
        _verify_approved_policy({
            "robot_policy": "missing:run", "approved_policy_sha256": "0" * 64,
        }, repo_root=tmp_path)
