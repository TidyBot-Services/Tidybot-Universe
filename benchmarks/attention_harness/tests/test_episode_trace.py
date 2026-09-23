from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from benchmarks.attention_harness.artifacts import write_episode_artifacts
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.episode_trace import persist_episode_trace
from benchmarks.attention_harness.robocasa_native.trace import (
    persist_robocasa_episode_trace,
)


def _episode_dir(tmp_path: Path, name: str = "cube_lift-seed101-test") -> Path:
    episode_dir = tmp_path / name
    episode_dir.mkdir()
    observation = {
        "agentview_image": np.zeros((2, 2, 3), dtype=np.uint8),
        "robot0_eef_pos": np.zeros(3, dtype=np.float64),
    }
    write_episode_artifacts(
        episode_dir,
        result={"status": "failed"},
        trace=[
            {
                "step": 0,
                "action": [0.0] * 7,
                "reward": 0.0,
                "done": False,
                "observation_sha256": "a" * 64,
            }
        ],
        initial_observation=observation,
        final_observation=observation,
    )
    return episode_dir


def _failed_sdk_event() -> dict:
    return {
        "event_id": "sdk-0",
        "sequence": 0,
        "timestamp": 0.2,
        "source": "robot_sdk.gripper",
        "event_type": "sdk.gripper_command",
        "operation": "close",
        "status": "failed",
        "duration_ms": 5.0,
        "arguments": {"settle_steps": 10, "object_pose": [1.0, 2.0, 3.0]},
        "result": {},
        "error": {"type": "GraspError", "message": "gripper missed"},
    }


def test_failed_episode_persists_linked_raw_and_advisor_traces(
    tmp_path: Path,
) -> None:
    episode_dir = _episode_dir(tmp_path)
    code_path = episode_dir / "generated_policy.py"
    code_path.write_text("from robot_sdk import gripper\ngripper.close()\n", encoding="utf-8")
    action_trace = json.loads((episode_dir / "trace.jsonl").read_text())
    kwargs = dict(
        episode_dir=episode_dir,
        suite="robosuite",
        task_id="cube_lift",
        seed=101,
        policy_id="parcc-generated",
        developer_model="parcc/GLM",
        execution_target="robosuite_sim",
        execution_status="failed",
        native_success=False,
        elapsed_seconds=2.5,
        action_trace=[action_trace],
        sdk_trace=[_failed_sdk_event()],
        error="GraspError: gripper missed",
        stdout="attempting grasp\n",
        stderr="gripper missed\n",
        exit_code=1,
        code_path=code_path,
        runtime={"service": "robosuite_sim"},
        token_limit=100,
        tokens_used=12,
    )
    link = persist_episode_trace(**kwargs)

    store = AttentionStore(episode_dir / "attention.sqlite3")
    raw = store.get_raw_trace(link["raw_trace_id"])
    advisor = store.get_trace(link["advisor_trace_id"])
    assert raw["outcome"]["native_success"] is False
    assert raw["metadata"]["suite"] == "robosuite"
    assert [event["timestamp"] for event in raw["events"]] == sorted(
        event["timestamp"] for event in raw["events"]
    )
    assert raw["code"]["artifact_uri"].endswith("/generated_policy.py")
    assert advisor["raw_trace_id"] == raw["raw_trace_id"]
    assert advisor["failure"]["stage"] == "grasp"
    serialized = json.dumps(advisor, sort_keys=True).lower()
    assert "native_success" not in serialized
    assert "object_pose" not in serialized
    assert "reward" not in serialized
    assert Path(link["bundle"]).is_file()

    # Finalization can safely be retried after a process restart.
    event_count = len(store.events())
    assert persist_episode_trace(**kwargs) == link
    assert len(AttentionStore(episode_dir / "attention.sqlite3").events()) == event_count


def test_successful_episode_keeps_raw_trace_without_advisor_packet(
    tmp_path: Path,
) -> None:
    episode_dir = _episode_dir(tmp_path, "cube_lift-seed101-success")
    link = persist_episode_trace(
        episode_dir=episode_dir,
        suite="robosuite",
        task_id="cube_lift",
        seed=101,
        policy_id="frozen-public",
        developer_model="scripted",
        execution_target="robosuite_sim",
        execution_status="completed",
        native_success=True,
        elapsed_seconds=1.0,
        action_trace=[],
        sdk_trace=[],
    )

    store = AttentionStore(episode_dir / "attention.sqlite3")
    assert store.get_raw_trace(link["raw_trace_id"]) is not None
    assert link["advisor_trace_id"] is None
    assert store.get_run(link["run_id"])["status"] == "completed"


def test_robocasa_bridge_uses_same_trace_contract(tmp_path: Path) -> None:
    episode_dir = _episode_dir(tmp_path, "counter_to_sink-seed101-test")
    link = persist_robocasa_episode_trace(
        episode_dir=episode_dir,
        task_id="counter_to_sink",
        seed=101,
        policy_id="reactive_help",
        developer_model="test-model",
        execution_status="failed",
        native_success=False,
        elapsed_seconds=1.0,
        action_trace=[],
        sdk_trace=[_failed_sdk_event()],
        error="GraspError: gripper missed",
    )

    store = AttentionStore(episode_dir / "attention.sqlite3")
    assert store.get_run(link["run_id"])["suite"] == "robocasa"
    assert store.get_run(link["run_id"])["execution_target"] == "robocasa_sim"
    assert store.get_trace(link["advisor_trace_id"])["schema_version"] == (
        "attentionbench.advisor-trace-packet.v1"
    )
