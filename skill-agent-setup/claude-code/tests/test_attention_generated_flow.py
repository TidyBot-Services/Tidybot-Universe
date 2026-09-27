"""Generated Dev policy and bounded diagnostic Eval contracts."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

from attention_eval import build_eval_packet, diagnose_attention  # noqa: E402
import attention_dev  # noqa: E402
from attentionbench_bridge import build_attention_command  # noqa: E402


def _generated_config(root: Path) -> dict:
    (root / "benchmarks/attention_harness").mkdir(parents=True)
    (root / "benchmarks/attention_harness/sim_gt_attention_cli.py").touch()
    source = root / "benchmarks/attention_harness/protocol/v2/policies/dev_smoke.py"
    source.parent.mkdir(parents=True)
    source.write_text("from robot_sdk import sensors\nsensors.find_objects()\n")
    return {
        "suite": "robosuite", "task": "cube_lift", "seed": 101,
        "attention_policy": "autonomous", "max_attempts": 1,
        "assistance_credits": 0, "runner_boundary": "generated_sandbox",
        "allow_generated_sandbox": True,
        "generated_policy_file": str(source.relative_to(root)),
    }


def test_generated_boundary_uses_sandbox_without_trusted_approval(tmp_path):
    config = _generated_config(tmp_path)
    cmd = build_attention_command(config, repo_root=tmp_path)
    assert "--generated-policy-file" in cmd
    assert "--robot-policy" not in cmd
    assert "--confirm-simulator-agent" not in cmd


def test_generated_boundary_rejects_source_escape_and_missing_opt_in(tmp_path):
    config = _generated_config(tmp_path)
    config["allow_generated_sandbox"] = False
    with pytest.raises(ValueError, match="opt-in"):
        build_attention_command(config, repo_root=tmp_path)
    config["allow_generated_sandbox"] = True
    config["generated_policy_file"] = "../outside.py"
    with pytest.raises(ValueError, match="under protocol"):
        build_attention_command(config, repo_root=tmp_path)


def test_bounded_dev_writes_valid_source_once(tmp_path, monkeypatch):
    config = _generated_config(tmp_path)
    source = tmp_path / config["generated_policy_file"]
    source.unlink()
    monkeypatch.setattr(attention_dev, "REPO_ROOT", tmp_path)

    class FakeClient:
        def chat(self, **kwargs):
            assert kwargs["max_tokens"] == 3072
            return SimpleNamespace(
                content="from robot_sdk import sensors\nsensors.find_objects()",
                usage={"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                attempts=1,
            )

    generated = attention_dev.generate_policy(config, graph_dir=tmp_path,
                                               client=FakeClient())
    assert source.is_file()
    assert generated["usage"]["total_tokens"] == 20
    assert generated["hypothesis"] == "unknown"
    assert generated["provider_attempts"] == 1
    assert Path(generated["responses_artifact"]).is_file()
    assert (tmp_path / "dev_generation.json").is_file()
    with pytest.raises(FileExistsError):
        attention_dev.generate_policy(config, graph_dir=tmp_path, client=FakeClient())


def test_bounded_dev_rejects_provider_retry_before_writing_source(tmp_path, monkeypatch):
    config = _generated_config(tmp_path)
    source = tmp_path / config["generated_policy_file"]
    source.unlink()
    monkeypatch.setattr(attention_dev, "REPO_ROOT", tmp_path)

    class RetryingClient:
        def chat(self, **kwargs):
            return SimpleNamespace(content="from robot_sdk import sensors\nsensors.find_objects()",
                                   usage={"total_tokens": 10}, attempts=2)

    with pytest.raises(ValueError, match="one provider attempt"):
        attention_dev.generate_policy(config, graph_dir=tmp_path, client=RetryingClient())
    assert not source.exists()


def test_bounded_dev_keeps_two_invalid_model_replies_without_source(tmp_path, monkeypatch):
    config = _generated_config(tmp_path)
    source = tmp_path / config["generated_policy_file"]
    source.unlink()
    monkeypatch.setattr(attention_dev, "REPO_ROOT", tmp_path)

    class InvalidClient:
        calls = 0

        def chat(self, **kwargs):
            self.calls += 1
            return SimpleNamespace(content="import os", usage={"total_tokens": 3}, attempts=1)

    client = InvalidClient()
    with pytest.raises(attention_dev.GeneratedPolicyError):
        attention_dev.generate_policy(config, graph_dir=tmp_path, client=client)
    assert client.calls == 2 and not source.exists()
    assert len(json.loads((tmp_path / "dev_generation_responses.json").read_text())["responses"]) == 2
    assert (tmp_path / "dev_generation_failure.json").is_file()


def test_formal_dev_generates_version_but_does_not_approve_it(tmp_path, monkeypatch):
    config = _generated_config(tmp_path)
    config["runner_boundary"] = "formal"
    source = tmp_path / config["generated_policy_file"]
    source.unlink()
    monkeypatch.setattr(attention_dev, "REPO_ROOT", tmp_path)

    class FakeClient:
        def chat(self, **kwargs):
            return SimpleNamespace(
                content="from robot_sdk import sensors\nsensors.find_objects()",
                usage={"total_tokens": 12}, attempts=1,
            )

    generated = attention_dev.generate_policy(config, graph_dir=tmp_path,
                                               client=FakeClient())
    assert generated["sha256"]
    assert "approved_generated_policy" not in generated
    with pytest.raises(ValueError, match="explicit post-Dev"):
        build_attention_command(config, repo_root=tmp_path)


def _eval_artifact(tmp_path: Path) -> Path:
    attempt_dir = tmp_path / "attempts" / "one"
    attempt_dir.mkdir(parents=True)
    bundle = attempt_dir / "attention_bundle.json"
    bundle.write_text(json.dumps({"events": [{
        "event_type": "raw_trace.created", "payload": {
            "outcome": {"status": "completed", "native_success": False},
            "events": [{"event_id": "action-log", "event_type": "robot.action_log",
                        "operation": "step_sequence", "status": "completed",
                        "result": {"action_count": 0}},
                       {"event_id": "native-evaluator", "event_type": "evaluator.result",
                        "operation": "native_success", "status": "completed"}],
        },
    }]}))
    artifact = tmp_path / "attention_run.json"
    artifact.write_text(json.dumps({
        "schema_version": "attentionbench.sim-gt-attention-run.v1",
        "suite": "robosuite", "task_id": "cube_lift", "seed": 101,
        "native_success": False, "attempts": [{
            "status": "completed", "native_success": False,
            "attention_trace": {"attempt_id": "attempt:one", "bundle": str(bundle)},
        }],
    }))
    return artifact


def test_eval_receives_bounded_evidence_and_persists_diagnosis(tmp_path):
    artifact = _eval_artifact(tmp_path)
    packet = build_eval_packet(artifact)
    assert packet["attempts"][0]["events"][0]["result"]["action_count"] == 0

    class FakeClient:
        def chat(self, **kwargs):
            assert "action-log" in kwargs["messages"][0]["content"]
            assert kwargs["max_tokens"] <= 2048
            return SimpleNamespace(
                content="attempt:one: action-log shows zero actions; add a bounded lift action.",
                usage={"total_tokens": 120}, attempts=1,
            )

    result = diagnose_attention(artifact, client=FakeClient())
    assert "action-log" in result["diagnosis"]
    assert Path(result["artifact"]).is_file()


def test_eval_rejects_trace_path_escape(tmp_path):
    artifact = _eval_artifact(tmp_path)
    run = json.loads(artifact.read_text())
    run["attempts"][0]["attention_trace"]["bundle"] = str(tmp_path.parent / "outside.json")
    artifact.write_text(json.dumps(run))
    with pytest.raises(ValueError, match="escapes"):
        build_eval_packet(artifact)
