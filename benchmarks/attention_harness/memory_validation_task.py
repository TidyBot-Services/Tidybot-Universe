"""Durable, fail-closed dispatch of candidate Memory paired validation.

The formal task run and this development-only validation have separate receipts.
The Memory Service remains the sole authority for pair registration and promotion.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .memory_agent import MemoryAgent, TrialEvidence, TrialExecutor


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _save(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class MemoryValidationTask:
    def __init__(
        self, *, agent: MemoryAgent, memory_id: str, source_run: Path,
        source_run_sha256: str, cases: tuple[dict[str, Any], ...],
        validation_policy: Path, approved_policy_sha256: str,
        executor: TrialExecutor, state_path: Path, assistance_credits: int = 0,
    ) -> None:
        self.agent = agent
        self.memory_id = memory_id
        self.source_run = source_run.resolve()
        self.source_run_sha256 = source_run_sha256
        self.cases = cases
        self.validation_policy = validation_policy.resolve()
        self.approved_policy_sha256 = approved_policy_sha256
        self.executor = executor
        self.state_path = state_path.resolve()
        self.assistance_credits = assistance_credits

    def _spec(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "source_run": str(self.source_run),
            "source_run_sha256": self.source_run_sha256,
            "cases": list(self.cases),
            "validation_policy": str(self.validation_policy),
            "approved_policy_sha256": self.approved_policy_sha256,
            "assistance_credits": self.assistance_credits,
        }

    def _write(self, state: dict[str, Any], status: str) -> None:
        state["status"] = status
        _save(self.state_path, state)

    def _check_inputs(self) -> None:
        if _sha(self.source_run) != self.source_run_sha256:
            raise ValueError("formal source run SHA-256 changed")
        run = json.loads(self.source_run.read_text(encoding="utf-8"))
        if (run.get("formal_eligible") is not False
                or run.get("runner_boundary", {}).get("mode") != "formal"
                or not run.get("attempts")
                or self.memory_id not in [
                    request.get("candidate_memory_id") for request in run.get("requests", [])
                ]):
            raise ValueError("candidate is not linked to a verified formal source run")
        for attempt in run["attempts"]:
            formal = attempt.get("formal_runner_result", {})
            trace = attempt.get("attention_trace", {})
            if (formal.get("boundary_checked") is not True
                    or formal.get("formal_eligible") is not False
                    or formal.get("run_id") != trace.get("run_id")
                    or formal.get("attempt_id") != trace.get("attempt_id")
                    or formal.get("native_evaluator", {}).get("evaluated") is not True
                    or formal.get("native_evaluator", {}).get("native_success")
                       is not attempt.get("native_success")):
                raise ValueError("formal source attempt evidence is incomplete")
            artifacts = formal.get("artifacts", {})
            for name in ("trace", "safety", "sandbox_receipt", "native_result"):
                item = artifacts.get(name, {})
                if not isinstance(item.get("uri"), str) or _sha(Path(item["uri"])) != item.get("sha256"):
                    raise ValueError(f"formal source {name} SHA-256 changed")
        if _sha(self.validation_policy) != self.approved_policy_sha256:
            raise ValueError("approved validation policy SHA-256 changed")

    def _receipt(self, evidence: TrialEvidence) -> dict[str, str]:
        safety = evidence.safety_artifact.resolve()
        return {
            "attempt_id": evidence.attempt_id,
            "safety_artifact": str(safety),
            "safety_sha256": _sha(safety),
            "config_sha256": evidence.config_sha256,
        }

    @staticmethod
    def _read_receipt(receipt: dict[str, str]) -> TrialEvidence:
        safety = Path(receipt["safety_artifact"])
        if _sha(safety) != receipt["safety_sha256"]:
            raise ValueError("saved trial safety SHA-256 changed")
        return TrialEvidence(receipt["attempt_id"], safety, receipt["config_sha256"])

    def run(self) -> dict[str, Any]:
        spec = self._spec()
        if self.state_path.exists():
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if state.get("spec") != spec:
                raise ValueError("validation task differs from its frozen specification")
            if state.get("status") == "blocked":
                return state
        else:
            state = {"schema_version": "attentionbench.memory-validation-task.v1",
                     "spec": spec, "status": "scheduled", "arms": {},
                     "pairs": {}, "reused_existing_pairs": []}
            self._write(state, "scheduled")
        try:
            self._check_inputs()
            if state["status"] in {"gate_pending", "trusted"}:
                memory = self.agent.service.get_memory(self.memory_id)
                if memory.status.value == "trusted":
                    state["promotion"] = {"memory_id": memory.memory_id,
                                          "version": memory.version,
                                          "status": "trusted"}
                    self._write(state, "trusted")
                    return state
                if state["status"] == "trusted":
                    raise ValueError("trusted receipt disagrees with Memory Service")
            preflight = self.agent.preflight_validation(
                self.memory_id, cases=self.cases,
                assistance_credits=self.assistance_credits,
                validation_policy_sha256=self.approved_policy_sha256,
            )
            state["preflight"] = preflight
            self._write(state, "preflight_verified")
            plan = self.agent.plan_validation(
                self.memory_id, cases=self.cases,
                assistance_credits=self.assistance_credits,
            )
            state["plan"] = self.agent.service.get_plan(self.memory_id)
            self._write(state, "planned")
            existing = {item["seed"]: item for item in self.agent.service.list_pairs(self.memory_id)}
            for control, treatment in plan:
                seed = control.seed
                if seed in existing:
                    state["pairs"][str(seed)] = existing[seed]
                    if seed not in state["reused_existing_pairs"] and str(seed) not in state["arms"]:
                        state["reused_existing_pairs"].append(seed)
                    self._write(state, "pair_registered")
                    continue
                arm_receipts = state["arms"].setdefault(str(seed), {})
                evidence = []
                for name, trial in (("control", control), ("treatment", treatment)):
                    if name in arm_receipts:
                        item = self._read_receipt(arm_receipts[name])
                    else:
                        item = self.executor(trial)
                        if not isinstance(item, TrialEvidence):
                            raise TypeError("trial executor must return TrialEvidence")
                        arm_receipts[name] = self._receipt(item)
                        self._write(state, "arm_executed")
                    evidence.append(item)
                if (not evidence[0].config_sha256
                        or evidence[0].config_sha256 != evidence[1].config_sha256):
                    raise ValueError("paired trial configurations differ")
                pair = self.agent.service.record_pair(
                    memory_id=self.memory_id,
                    control_attempt_id=evidence[0].attempt_id,
                    treatment_attempt_id=evidence[1].attempt_id,
                    control_safety=evidence[0].safety_artifact,
                    treatment_safety=evidence[1].safety_artifact,
                )
                state["pairs"][str(seed)] = pair
                self._write(state, "pair_registered")
            state["impact"] = self.agent.service.impact_report(self.memory_id)
            self._write(state, "gate_pending")
            promoted = self.agent.request_promotion(self.memory_id)
            state["promotion"] = {"memory_id": promoted.memory_id,
                                  "version": promoted.version,
                                  "status": promoted.status.value}
            self._write(state, "trusted")
        except Exception as exc:
            state["blocker"] = f"{type(exc).__name__}: {exc}"[:1000]
            self._write(state, "blocked")
        return state
