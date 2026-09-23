from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from benchmarks.attention_harness.advisor_run import run_advised_parcc_episode
from benchmarks.attention_harness.artifacts import create_episode_dir, write_episode_artifacts
from benchmarks.attention_harness.core.advisor import AdvisorTransportReply
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.episode_trace import persist_episode_trace
from benchmarks.attention_harness.parcc_advisor import ADVICE_SCHEMA_VERSION


def test_failed_attempt_requests_glm_hint_and_retries_in_same_run(tmp_path: Path) -> None:
    calls = []
    sleeps = []
    advisor_requests = []

    def fake_attempt(**kwargs):
        calls.append(kwargs)
        index = kwargs["attention_attempt_index"]
        episode_dir = create_episode_dir(kwargs["artifact_root"], kwargs["task_id"], kwargs["seed"])
        code_path = episode_dir / "generated_policy.py"
        code_path.write_text("from robot_sdk import sensors\nsensors.get_observation()\n", encoding="utf-8")
        observation = {"agentview_image": np.zeros((2, 2, 3), dtype=np.uint8)}
        result = {
            "artifact_dir": str(episode_dir),
            "native_success": index == 1,
            "status": "completed",
        }
        write_episode_artifacts(
            episode_dir,
            result=result,
            trace=[],
            initial_observation=observation,
            final_observation=observation,
        )
        result["attention_trace"] = persist_episode_trace(
            episode_dir=episode_dir,
            suite="robosuite",
            task_id=kwargs["task_id"],
            seed=kwargs["seed"],
            policy_id="parcc-generated",
            developer_model="parcc/GLM",
            execution_target="robosuite_sim",
            execution_status="completed",
            native_success=result["native_success"],
            elapsed_seconds=1.0,
            action_trace=[],
            code_path=code_path,
            token_limit=kwargs["attention_token_limit"],
            tokens_used=10,
            assistance_credits=kwargs["attention_assistance_credits"],
            store_path=kwargs["attention_store_path"],
            run_id=kwargs["attention_run_id"],
            attempt_index=index,
            finalize_run=kwargs["attention_finalize_run"],
            execution_budget_seconds=kwargs["attention_execution_budget_seconds"],
        )
        return result

    def fake_glm(request):
        advisor_requests.append(request)
        return AdvisorTransportReply(
            content=json.dumps(
                {
                    "schema_version": ADVICE_SCHEMA_VERSION,
                    "request_type": "hint",
                    "diagnosis": "The goal was not visible as complete.",
                    "guidance": "Recheck the public RGB-D view before moving.",
                    "caution": "The object position is uncertain.",
                    "confidence": 0.5,
                }
            ),
            model="parcc/GLM",
            latency_seconds=0.4,
            attempts=1,
            usage={"total_tokens": 7},
        )

    summary = run_advised_parcc_episode(
        task_id="cube_lift",
        seed=101,
        artifact_root=tmp_path,
        advisor_transport=fake_glm,
        attempt_executor=fake_attempt,
        sleeper=sleeps.append,
    )
    store = AttentionStore(Path(summary["store"]))
    assert summary["native_success"] is True
    assert len(summary["attempts"]) == 2
    assert calls[0]["advisor_guidance"] is None
    assert calls[1]["advisor_guidance"] == "Recheck the public RGB-D view before moving."
    assert calls[1]["previous_policy"].startswith("from robot_sdk")
    assert len(advisor_requests[0]["messages"][-1]["content"]) == 3
    assert summary["requests"][0]["provider_model"] == "parcc/GLM"
    assert summary["resource_usage"]["assistance"]["used"] == 1
    assert summary["resource_usage"]["tokens"]["used"] == 27
    assert sleeps == [2.0]
    assert store.get_run(summary["run_id"])["status"] == "completed"
    assert Path(summary["bundle"]).is_file()
