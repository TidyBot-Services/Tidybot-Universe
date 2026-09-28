"""Durable, fail-closed dispatch of candidate Memory paired validation.

The formal task run and this development-only validation have separate receipts.
The Memory Service remains the sole authority for pair registration and promotion.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import fcntl
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
        approval: dict[str, Any] | None = None,
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
        self.approval = approval or {}

    def _spec(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "source_run": str(self.source_run),
            "source_run_sha256": self.source_run_sha256,
            "cases": list(self.cases),
            "validation_policy": str(self.validation_policy),
            "approved_policy_sha256": self.approved_policy_sha256,
            "assistance_credits": self.assistance_credits,
            "approval": self.approval,
        }

    def _write(self, state: dict[str, Any], status: str) -> None:
        state["status"] = status
        _save(self.state_path, state)

    def _check_inputs(self) -> None:
        if _sha(self.source_run) != self.source_run_sha256:
            raise ValueError("formal source run SHA-256 changed")
        run = json.loads(self.source_run.read_text(encoding="utf-8"))
        provenance = self.agent.service.provenance(self.memory_id)
        linked_request = any(
            item.get("request_id") == provenance["request_id"]
            for item in run.get("requests", []) if isinstance(item, dict)
        )
        linked_attempt = any(
            item.get("attention_trace", {}).get("attempt_id") == provenance["source_attempt_id"]
            and item.get("attention_trace", {}).get("run_id") == provenance["source_run_id"]
            for item in run.get("attempts", []) if isinstance(item, dict)
        )
        if (run.get("formal_eligible") is not False
                or run.get("runner_boundary", {}).get("mode") != "formal"
                or not run.get("attempts")
                or not linked_request or not linked_attempt):
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

    def _receipt(self, evidence: TrialEvidence) -> dict[str, Any]:
        safety = evidence.safety_artifact.resolve()
        receipt = {
            "attempt_id": evidence.attempt_id,
            "safety_artifact": str(safety),
            "safety_sha256": _sha(safety),
            "config_sha256": evidence.config_sha256,
        }
        root = safety.parent
        if self.approval.get("suite") == "robocasa":
            for name in ("result.json", "trace.jsonl", "attention_bundle.json", "trial_config.json"):
                path = root / name
                if not path.is_file():
                    raise ValueError(f"RoboCasa arm is missing {name}")
                receipt[name] = {"path": str(path.resolve()), "sha256": _sha(path)}
        if evidence.service_stop_artifact is not None:
            path = evidence.service_stop_artifact.resolve()
            receipt["service_stop"] = {"path": str(path), "sha256": _sha(path)}
        elif self.approval.get("suite") == "robocasa":
            raise ValueError("RoboCasa arm is missing the dual-Service stop receipt")
        self._read_receipt(receipt)
        return receipt

    @staticmethod
    def _read_receipt(receipt: dict[str, Any]) -> TrialEvidence:
        safety = Path(receipt["safety_artifact"])
        if _sha(safety) != receipt["safety_sha256"]:
            raise ValueError("saved trial safety SHA-256 changed")
        for name in ("result.json", "trace.jsonl", "attention_bundle.json", "trial_config.json"):
            if name in receipt and _sha(Path(receipt[name]["path"])) != receipt[name]["sha256"]:
                raise ValueError(f"saved trial {name} SHA-256 changed")
        stop = receipt.get("service_stop")
        if stop is not None:
            path = Path(stop["path"])
            if _sha(path) != stop["sha256"]:
                raise ValueError("saved trial dual-Service stop SHA-256 changed")
            services = json.loads(path.read_text(encoding="utf-8")).get("services", {})
            if (set(services) != {"agent", "simulator"}
                    or not all(services[name].get("process_group_gone") is True
                               for name in ("agent", "simulator"))):
                raise ValueError("saved trial dual-Service stop is not complete")
        return TrialEvidence(receipt["attempt_id"], safety, receipt["config_sha256"],
                             None if stop is None else Path(stop["path"]))

    def run(self) -> dict[str, Any]:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.state_path.with_name(self.state_path.name + ".lock")
        with lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                return self._run_locked()
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _run_locked(self) -> dict[str, Any]:
        spec = self._spec()
        if self.state_path.exists():
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if state.get("spec") != spec:
                raise ValueError("validation task differs from its frozen specification")
            for arms in state.get("arms", {}).values():
                for receipt in arms.values():
                    self._read_receipt(receipt)
            if state.get("status") == "blocked":
                # A blocked task still refers to the frozen source and approved
                # policy and to the same Service-owned pairs. Re-read their
                # Safety digests before reusing a terminal task receipt.
                self._check_inputs()
                current_pairs = {
                    str(item["seed"]): item
                    for item in self.agent.service.list_pairs(self.memory_id)
                }
                if state.get("pairs") != current_pairs:
                    raise ValueError("saved validation pairs differ from Memory Service")
                current_impact = (self.agent.service.impact_report(self.memory_id)
                                  if current_pairs else None)
                if state.get("impact") is not None and current_impact != state["impact"]:
                    raise ValueError("saved validation impact differs from Memory Service")
                return state
        else:
            state = {"schema_version": "attentionbench.memory-validation-task.v1",
                     "spec": spec, "status": "scheduled", "formal_eligible": False,
                     "arms": {},
                     "pairs": {}, "reused_existing_pairs": []}
            self._write(state, "scheduled")
        try:
            self._check_inputs()
            if state.get("inflight"):
                raise RuntimeError("previous arm has no durable receipt; outcome uncertain, refusing replay")
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
                    for receipt in state["arms"].get(str(seed), {}).values():
                        self._read_receipt(receipt)
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
                        state["inflight"] = {"seed": seed, "arm": name}
                        self._write(state, "arm_started")
                        item = self.executor(trial)
                        if not isinstance(item, TrialEvidence):
                            raise TypeError("trial executor must return TrialEvidence")
                        arm_receipts[name] = self._receipt(item)
                        state.pop("inflight", None)
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
                # Engineering restart probe: pause only after a durable pair
                # checkpoint, without changing trials or the promotion gate.
                pause = float(os.environ.get("ATTENTIONBENCH_PAIR_CHECKPOINT_PAUSE", "0"))
                if pause > 0:
                    time.sleep(min(pause, 60.0))
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
