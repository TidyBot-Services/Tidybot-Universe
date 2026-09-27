"""Formal runner interface checks; no real suite is accepted by these tests."""

import hashlib

import pytest

from benchmarks.attention_harness.formal_runner_boundary import (
    FormalRunRequest, run_with_formal_boundary,
)


class FakeRunner:
    def __init__(self, suite, *, cancellation=True):
        self.suite = suite
        self.cancellation = cancellation

    def execute(self, request):
        root = request.artifact_root
        root.mkdir(parents=True, exist_ok=True)
        artifacts = {}
        for name in ("trace", "safety", "sandbox_receipt", "native_result"):
            path = root / f"{name}.json"
            path.write_text("{}")
            artifacts[name] = {"uri": str(path),
                               "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        return {
            "schema_version": "attentionbench.formal-runner-result.v1",
            "suite": request.suite, "task_id": request.task_id,
            "seed": request.seed, "policy_sha256": request.policy_sha256,
            "config_sha256": request.config_sha256,
            "native_success": False,
            "native_evaluator": {"native_success": False},
            "sandbox": {"process_isolated": True, "sdk_rpc_only": True,
                        "deadline_enforced": True,
                        "action_cancellation_verified": self.cancellation},
            "artifacts": artifacts,
        }


@pytest.mark.parametrize("suite", ["robocasa", "robosuite"])
def test_formal_boundary_requires_registered_suite_and_evidence(tmp_path, suite):
    policy = tmp_path / "policy.py"
    policy.write_text("pass\n")
    request = FormalRunRequest(
        suite=suite, task_id="task", seed=101,
        policy_code_path=policy,
        policy_sha256=hashlib.sha256(policy.read_bytes()).hexdigest(),
        config_sha256="a" * 64, artifact_root=tmp_path / "artifacts",
        overall_deadline_seconds=300,
    )
    with pytest.raises(RuntimeError, match="no suite-matched"):
        run_with_formal_boundary(request, runner=None)
    with pytest.raises(RuntimeError, match="cancellation evidence"):
        run_with_formal_boundary(request, runner=FakeRunner(suite, cancellation=False))
    checked = run_with_formal_boundary(request, runner=FakeRunner(suite))
    assert checked["boundary_checked"] is True
    assert checked["formal_eligible"] is False
    assert checked["formal_blockers"]
