from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from attentionbench_bridge import build_attention_command


def test_formal_graph_routes_to_formal_cli_only_after_exact_approval(tmp_path):
    policy = tmp_path / "benchmarks/attention_harness/protocol/v2/policies/p.py"
    policy.parent.mkdir(parents=True)
    policy.write_text("from robot_sdk import sensors\nsensors.find_objects()\n")
    config_file = tmp_path / "benchmarks/attention_harness/protocol/v2/formal.json"
    config_file.write_text("{}")
    service = tmp_path / "service"
    service.mkdir()
    config = {
        "suite": "robosuite", "task": "cube_lift", "seed": 101,
        "attention_policy": "reactive_help", "runner_boundary": "formal",
        "generated_policy_file": str(policy.relative_to(tmp_path)),
        "formal_config_file": str(config_file.relative_to(tmp_path)),
        "service_source_root": str(service),
        "approved_policy_sha256": hashlib.sha256(policy.read_bytes()).hexdigest(),
        "approved_config_sha256": hashlib.sha256(config_file.read_bytes()).hexdigest(),
    }
    with pytest.raises(ValueError, match="explicit post-Dev"):
        build_attention_command(config, repo_root=tmp_path)
    config["approved_generated_policy"] = True
    command = build_attention_command(config, repo_root=tmp_path)
    assert "benchmarks.attention_harness.formal_attention_cli" in command
    assert "benchmarks.attention_harness.sim_gt_attention_cli" not in command
    policy.write_text("from robot_sdk import sensors\nsensors.get_observation()\n")
    with pytest.raises(ValueError, match="approved_policy_sha256"):
        build_attention_command(config, repo_root=tmp_path)
