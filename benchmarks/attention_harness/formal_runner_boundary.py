"""Suite-neutral contract for a future formal generated-policy runner.

The current seven-policy CLI is a trusted-callback *development* runner.  A
formal suite adapter must implement this protocol independently for RoboCasa
and Robosuite, return persisted sandbox/safety/native-evaluator evidence, and
pass a separately frozen acceptance matrix.  Merely satisfying this interface
does not make a run formal-eligible.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .policy_input import public_attention_input


@dataclass(frozen=True)
class FormalRunRequest:
    suite: str
    task_id: str
    seed: int
    policy_code_path: Path
    policy_sha256: str
    config_sha256: str
    artifact_root: Path
    overall_deadline_seconds: float
    config_path: Path | None = None
    run_id: str | None = None
    attempt_id: str | None = None
    attention_input: dict[str, Any] | None = None

    def validate(self) -> None:
        if (self.run_id is None) != (self.attempt_id is None):
            raise ValueError("formal run and attempt identities must be paired")
        if self.run_id is not None and (not self.run_id.startswith("run:")
                                        or not self.attempt_id.startswith("attempt:")):
            raise ValueError("invalid formal Attention identity")
        public_attention_input(self.attention_input)
        if self.suite not in {"robocasa", "robosuite"}:
            raise ValueError("formal runner requires a primary simulator suite")
        if not self.task_id or isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("formal runner requires a task and integer seed")
        if (isinstance(self.overall_deadline_seconds, bool)
                or not isinstance(self.overall_deadline_seconds, (int, float))
                or not math.isfinite(self.overall_deadline_seconds)
                or self.overall_deadline_seconds <= 0):
            raise ValueError("formal runner requires a finite positive overall deadline")
        if not self.policy_code_path.is_file():
            raise ValueError("formal policy source is missing")
        actual = hashlib.sha256(self.policy_code_path.read_bytes()).hexdigest()
        if actual != self.policy_sha256:
            raise ValueError("formal policy source differs from approved digest")
        if len(self.config_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.config_sha256):
            raise ValueError("formal run requires frozen config SHA-256")
        if self.config_path is not None:
            if not self.config_path.is_file():
                raise ValueError("formal config source is missing")
            if hashlib.sha256(self.config_path.read_bytes()).hexdigest() != self.config_sha256:
                raise ValueError("formal config source differs from approved digest")


class FormalSuiteRunner(Protocol):
    suite: str

    def execute(self, request: FormalRunRequest) -> dict[str, Any]: ...


def run_with_formal_boundary(
    request: FormalRunRequest, *, runner: FormalSuiteRunner | None,
) -> dict[str, Any]:
    """Check a suite adapter's evidence; never self-certify formal acceptance."""
    request.validate()
    if runner is None or runner.suite != request.suite:
        raise RuntimeError("no suite-matched formal runner registered")
    result = runner.execute(request)
    if not isinstance(result, dict) or result.get("schema_version") != "attentionbench.formal-runner-result.v1":
        raise RuntimeError("formal runner returned no versioned result")
    for key, expected in (
        ("suite", request.suite), ("task_id", request.task_id),
        ("seed", request.seed), ("policy_sha256", request.policy_sha256),
        ("config_sha256", request.config_sha256),
    ):
        if result.get(key) != expected:
            raise RuntimeError(f"formal runner result disagrees on {key}")
    if request.run_id is not None:
        for key in ("run_id", "attempt_id"):
            if result.get(key) != getattr(request, key):
                raise RuntimeError(f"formal runner result disagrees on {key}")
    sandbox = result.get("sandbox")
    if (not isinstance(sandbox, dict)
            or sandbox.get("process_isolated") is not True
            or sandbox.get("sdk_rpc_only") is not True
            or sandbox.get("deadline_enforced") is not True
            or sandbox.get("action_cancellation_verified") is not True):
        raise RuntimeError("formal runner lacks sandbox/deadline/cancellation evidence")
    evaluator = result.get("native_evaluator")
    if (not isinstance(evaluator, dict)
            or not isinstance(evaluator.get("native_success"), bool)
            or evaluator.get("native_success") is not result.get("native_success")):
        raise RuntimeError("native evaluator evidence disagrees with result")
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise RuntimeError("formal runner did not persist evidence artifacts")
    root = request.artifact_root.resolve()
    for name in ("trace", "safety", "sandbox_receipt", "native_result"):
        ref = artifacts.get(name)
        if not isinstance(ref, dict) or not isinstance(ref.get("uri"), str):
            raise RuntimeError(f"formal runner lacks {name} artifact")
        path = Path(ref["uri"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise RuntimeError(f"formal runner {name} artifact is outside run root")
        if hashlib.sha256(path.read_bytes()).hexdigest() != ref.get("sha256"):
            raise RuntimeError(f"formal runner {name} artifact digest mismatch")
        if request.run_id is not None:
            artifact = json.loads(path.read_text(encoding="utf-8"))
            if name in {"trace", "native_result"} and artifact.get("status") != result.get("status"):
                raise RuntimeError(f"formal runner {name} status mismatch")
            if name == "native_result" and artifact.get("native_success") is not result["native_success"]:
                raise RuntimeError("formal runner native result mismatch")
            for key in ("suite", "task_id", "seed", "policy_sha256", "config_sha256"):
                if name == "trace" and artifact.get(key) != getattr(request, key):
                    raise RuntimeError(f"formal runner trace disagrees on {key}")
            for key in ("run_id", "attempt_id"):
                if artifact.get(key) != getattr(request, key):
                    raise RuntimeError(f"formal runner {name} disagrees on {key}")
    # This boundary checks integrity and shape.  It is not the acceptance
    # matrix, freeze authority, or proof of hostile-code OS containment.
    return {
        **result,
        "formal_eligible": False,
        "formal_blockers": [
            "independent sandbox and cancellation acceptance not frozen",
            "task/seed/config stability matrix not formally accepted",
        ],
        "boundary_checked": True,
    }
