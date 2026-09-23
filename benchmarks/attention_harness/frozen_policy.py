"""Frozen, public-observation task policies and their execution boundary."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .sandbox import SandboxResult, execute_policy, validate_policy


POLICY_ROOT = Path(__file__).resolve().parent / "protocol" / "v1" / "policies"


@dataclass(frozen=True)
class FrozenPolicy:
    task_id: str
    path: Path

    def source(self) -> str:
        code = self.path.read_text(encoding="utf-8")
        validate_policy(code)
        return code

    def sha256(self) -> str:
        return hashlib.sha256(self.source().encode("utf-8")).hexdigest()


FROZEN_POLICIES = {
    task_id: FrozenPolicy(task_id, POLICY_ROOT / f"{task_id}.py")
    for task_id in ("cube_lift", "cube_stack")
}


def get_frozen_policy(task_id: str) -> FrozenPolicy:
    try:
        policy = FROZEN_POLICIES[task_id]
    except KeyError as exc:
        raise ValueError(f"no frozen public policy for {task_id!r}") from exc
    policy.source()
    return policy


def execute_frozen_policy(
    *,
    task_id: str,
    service_url: str,
    output_path: Path,
    timeout_seconds: float,
) -> SandboxResult:
    policy = get_frozen_policy(task_id)
    return execute_policy(
        code_path=policy.path,
        service_url=service_url,
        task_id=task_id,
        output_path=output_path,
        timeout_seconds=timeout_seconds,
    )
