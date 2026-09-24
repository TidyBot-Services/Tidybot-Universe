"""Evidence-gated v2 memory on top of the frozen v1 AttentionStore.

The sidecar tables are additive: v1 records and its freeze manifest are not
modified. A v1 ``trusted`` label alone is deliberately insufficient for v2.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .attention_modes import AssistanceMode, RequestState
from .core.memory import MemoryManager
from .core.models import MemoryRecord, MemoryStatus, RequestType
from .core.store import AttentionStore, StateConflictError
from .seed_guard import classify_seed


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _encoded(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class MemoryV2Manager:
    """Convert costed advice into artifacts and validate it with paired runs."""

    def __init__(self, store: AttentionStore) -> None:
        self.store = store
        with sqlite3.connect(store.path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS memory_v2_provenance "
                "(memory_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS memory_v2_pairs "
                "(pair_id TEXT PRIMARY KEY, memory_id TEXT NOT NULL, "
                "seed INTEGER NOT NULL, payload TEXT NOT NULL, "
                "UNIQUE(memory_id, seed))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS memory_v2_plans "
                "(memory_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )

    def record_plan(self, memory_id: str, plan: dict[str, Any]) -> dict[str, Any]:
        provenance = self.provenance(memory_id)
        memory = self.store.get_memory(memory_id)
        if memory is None or memory.status is not MemoryStatus.CANDIDATE:
            raise StateConflictError("validation plan requires a candidate memory")
        applicability = provenance["artifact"]["applicability"]
        if plan.get("schema_version") != "attentionbench.memory-validation-plan.v2":
            raise ValueError("unsupported memory validation plan")
        if plan.get("memory_id") != memory_id:
            raise StateConflictError("validation plan memory ID mismatch")
        if any(plan.get(key) != applicability[key] for key in ("suite", "task_id", "perception_mode")):
            raise StateConflictError("validation plan applicability mismatch")
        if plan.get("policy_id") != provenance["source_policy_id"]:
            raise StateConflictError("validation plan policy mismatch")
        seeds = plan.get("seeds")
        if (not isinstance(seeds, list) or len(seeds) < 5
                or any(isinstance(seed, bool) or not isinstance(seed, int) for seed in seeds)
                or len(set(seeds)) != len(seeds)):
            raise ValueError("validation plan needs five distinct development seeds")
        if any(classify_seed(seed) != "dev" for seed in seeds):
            raise PermissionError("validation plan is development-only")
        credits = plan.get("assistance_credits")
        if isinstance(credits, bool) or not isinstance(credits, int) or credits < 0:
            raise ValueError("validation plan credits must be nonnegative")
        encoded = _encoded(plan)
        with sqlite3.connect(self.store.path) as db:
            row = db.execute(
                "SELECT payload FROM memory_v2_plans WHERE memory_id=?", (memory_id,)
            ).fetchone()
            if row is not None:
                if row[0] != encoded:
                    raise StateConflictError("validation plan is immutable once recorded")
                return json.loads(row[0])
            if db.execute(
                "SELECT 1 FROM memory_v2_pairs WHERE memory_id=? LIMIT 1", (memory_id,)
            ).fetchone() is not None:
                raise StateConflictError("validation plan cannot be backfilled after pairs")
            db.execute(
                "INSERT INTO memory_v2_plans(memory_id, payload) VALUES (?, ?)",
                (memory_id, encoded),
            )
        return plan

    def get_plan(self, memory_id: str) -> dict[str, Any] | None:
        self.provenance(memory_id)
        with sqlite3.connect(self.store.path) as db:
            row = db.execute(
                "SELECT payload FROM memory_v2_plans WHERE memory_id=?", (memory_id,)
            ).fetchone()
        return None if row is None else json.loads(row[0])

    def create_candidate_from_response(
        self,
        *,
        memory_id: str,
        request_id: str,
        guidance: str,
        repair: str,
        applicability: dict[str, str],
        created_at: float,
        artifact_kind: str = "text_hint",
        human_attention_seconds: float = 0.0,
    ) -> MemoryRecord:
        if artifact_kind not in {"text_hint", "policy_patch", "skill"}:
            raise ValueError("unsupported structured memory artifact kind")
        if not guidance.strip() or not repair.strip():
            raise ValueError("guidance and repair must be nonempty")
        if set(("perception_mode", "suite", "task_id")) - applicability.keys():
            raise ValueError("memory requires explicit mode, suite, and task")
        if applicability["perception_mode"] not in {"sim_gt", "vision"}:
            raise ValueError("unknown perception mode")
        if human_attention_seconds < 0:
            raise ValueError("human attention cost cannot be negative")
        request = self.store.get_request(request_id)
        if request is None or request.state is not RequestState.ANSWERED or not request.response_id:
            raise StateConflictError("memory requires an answered request")
        if request.request_type is not RequestType.HINT:
            raise StateConflictError("only a hint may become a reusable memory artifact")
        response = self.store.get_response(request.response_id)
        trace = self.store.get_trace(request.trace_id)
        run = self.store.get_run(request.run_id)
        if response is None or trace is None or run is None:
            raise StateConflictError("request, response, trace, and run must exist")
        if response["request_id"] != request_id or trace["run_id"] != request.run_id:
            raise StateConflictError("response or trace lineage mismatch")
        if run["assistance_mode"] != request.mode.value:
            raise StateConflictError("request mode differs from source run")
        if self.store.budget_status(request.run_id)["used"] < 1:
            raise StateConflictError("source assistance request has no committed cost")
        if run["suite"] != applicability["suite"] or run["task_id"] != applicability["task_id"]:
            raise StateConflictError("memory applicability does not match source run")
        raw = self.store.get_raw_trace(trace["raw_trace_id"])
        if raw is None or raw["metadata"].get("runtime", {}).get("perception_mode") != applicability["perception_mode"]:
            raise StateConflictError("source trace lacks matching perception provenance")
        source_attempt = self.store.get_attempt(request.attempt_id)
        if (
            source_attempt is None
            or source_attempt["run_id"] != request.run_id
            or source_attempt["native_success"] is not False
            or raw["outcome"].get("native_success") is not False
        ):
            raise StateConflictError("memory source must be an evaluator-confirmed failed attempt")
        responder = response["responder"]
        if responder == "advisor_proxy":
            source_kind = "advisor_proxy"
        elif responder == "human" and request.mode is AssistanceMode.LIVE_HUMAN_FIRST:
            source_kind = "human"
        elif responder == "curated_human_packet":
            source_kind = "curated_human_packet"
        else:
            raise StateConflictError("response origin is not a recognized assistance source")
        if source_kind == "advisor_proxy" and human_attention_seconds:
            raise ValueError("proxy latency is not human attention")
        if source_kind != "advisor_proxy" and human_attention_seconds <= 0:
            raise ValueError("human-sourced guidance requires positive attention cost")
        artifact = {
            "schema_version": "attentionbench.memory-artifact.v2",
            "kind": artifact_kind,
            "guidance": guidance.strip(),
            "repair": repair.strip(),
            "applicability": applicability,
        }
        artifact_hash = _digest(_encoded(artifact).encode())
        evidence_ids = tuple(item["evidence_id"] for item in trace["evidence"])
        candidate = MemoryRecord(
            memory_id=memory_id,
            version=1,
            source_trace_id=request.trace_id,
            guidance=guidance.strip(),
            candidate_repair=repair.strip(),
            applicability=applicability,
            evidence_refs=evidence_ids,
            created_at=created_at,
        )
        provenance = {
            "schema_version": "attentionbench.memory-provenance.v2",
            "memory_id": memory_id,
            "source_kind": source_kind,
            "source_run_id": request.run_id,
            "source_attempt_id": request.attempt_id,
            "source_trace_id": request.trace_id,
            "source_seed": run["seed"],
            "source_policy_id": run["policy_id"],
            "source_developer_model": run["developer_model"],
            "source_execution_target": run["execution_target"],
            "request_id": request_id,
            "response_id": request.response_id,
            "responder": response["responder"],
            "assistance_credits": 1,
            "human_attention_seconds": human_attention_seconds,
            "artifact": artifact,
            "artifact_sha256": artifact_hash,
        }
        existing = self._provenance(memory_id)
        if existing is not None:
            if existing != provenance:
                raise StateConflictError("conflicting memory provenance")
            return self.store.get_memory(memory_id)
        MemoryManager(self.store).add_candidate(candidate)
        with sqlite3.connect(self.store.path) as db:
            db.execute(
                "INSERT INTO memory_v2_provenance(memory_id, payload) VALUES (?, ?)",
                (memory_id, _encoded(provenance)),
            )
        return candidate

    def provenance(self, memory_id: str) -> dict[str, Any]:
        value = self._provenance(memory_id)
        if value is None:
            raise StateConflictError("memory has no v2 provenance")
        memory = self.store.get_memory(memory_id)
        if memory is None or memory.guidance != value["artifact"]["guidance"] or memory.candidate_repair != value["artifact"]["repair"]:
            raise StateConflictError("memory artifact differs from core record")
        if _digest(_encoded(value["artifact"]).encode()) != value["artifact_sha256"]:
            raise StateConflictError("memory artifact digest mismatch")
        return value

    def retrieve(self, context: dict[str, Any], *, now: float) -> list[MemoryRecord]:
        if context.get("perception_mode") not in {"sim_gt", "vision"}:
            raise ValueError("retrieval requires explicit perception mode")
        matches = MemoryManager(self.store).retrieve(context, now=now)
        result = []
        for item in matches:
            if self._provenance(item.memory_id) is None:
                continue
            self.provenance(item.memory_id)
            if all(item.applicability.get(key) == context.get(key) for key in ("perception_mode", "suite", "task_id")):
                result.append(item)
        return result

    def record_pair(
        self,
        *,
        memory_id: str,
        control_attempt_id: str,
        treatment_attempt_id: str,
        control_safety: Path,
        treatment_safety: Path,
    ) -> dict[str, Any]:
        """Register a dev-seed pair; outcomes and costs are read from the store.

        Safety files must be independent monitor outputs with an integer
        ``unsafe_attempts`` field. They are hashed here and rechecked at gate.
        """
        provenance = self.provenance(memory_id)
        control = self._trial(control_attempt_id)
        treatment = self._trial(treatment_attempt_id)
        for field in ("suite", "task_id", "seed", "policy_id", "execution_target", "developer_model"):
            if control["run"][field] != treatment["run"][field]:
                raise StateConflictError(f"paired runs differ on {field}")
        seed = control["run"]["seed"]
        if classify_seed(seed) != "dev":
            raise PermissionError("memory validation must use development seeds")
        if control["run_id"] == treatment["run_id"]:
            raise StateConflictError("control and treatment must be different runs")
        if control["mode"] != treatment["mode"] or control["mode"] != provenance["artifact"]["applicability"]["perception_mode"]:
            raise StateConflictError("paired perception mode mismatch")
        if control["run"]["suite"] != provenance["artifact"]["applicability"]["suite"] or control["run"]["task_id"] != provenance["artifact"]["applicability"]["task_id"]:
            raise StateConflictError("paired task mismatch")
        if control["run"]["budget"] != treatment["run"]["budget"]:
            raise StateConflictError("paired budgets differ")
        plan = self.get_plan(memory_id)
        if plan is not None and (
            seed not in plan["seeds"]
            or control["run"]["policy_id"] != plan["policy_id"]
            or control["run"]["budget"]["assistance_credits"] != plan["assistance_credits"]
        ):
            raise StateConflictError("paired run differs from recorded validation plan")
        if not control["policy_sha256"] or control["policy_sha256"] != treatment["policy_sha256"]:
            raise StateConflictError("paired policy code differs")
        if control["memory_ids"] or control["exposure"] != "none":
            raise StateConflictError("control must not receive memory")
        if treatment["memory_ids"] != [memory_id] or treatment["exposure"] != "candidate_validation":
            raise StateConflictError("treatment trace must attest use of this candidate only")
        safety = [
            self._safety(control_safety, control_attempt_id),
            self._safety(treatment_safety, treatment_attempt_id),
        ]
        pair_id = f"pair:{memory_id}:{seed}"
        payload = {
            "schema_version": "attentionbench.memory-pair.v2",
            "pair_id": pair_id,
            "memory_id": memory_id,
            "seed": seed,
            "control_attempt_id": control_attempt_id,
            "treatment_attempt_id": treatment_attempt_id,
            "control_safety": safety[0],
            "treatment_safety": safety[1],
        }
        with sqlite3.connect(self.store.path) as db:
            try:
                db.execute(
                    "INSERT INTO memory_v2_pairs(pair_id, memory_id, seed, payload) "
                    "VALUES (?, ?, ?, ?)",
                    (pair_id, memory_id, seed, _encoded(payload)),
                )
            except sqlite3.IntegrityError as exc:
                row = db.execute("SELECT payload FROM memory_v2_pairs WHERE pair_id=?", (pair_id,)).fetchone()
                if row is None or json.loads(row[0]) != payload:
                    raise StateConflictError("conflicting validation pair or reused seed") from exc
        return payload

    def impact_report(self, memory_id: str) -> dict[str, Any]:
        provenance = self.provenance(memory_id)
        pairs = self.list_pairs(memory_id)
        control_success = treatment_success = control_credits = treatment_credits = 0
        control_seconds = treatment_seconds = control_unsafe = treatment_unsafe = 0.0
        control_gpu = treatment_gpu = control_tokens = treatment_tokens = 0.0
        for pair in pairs:
            control = self._trial(pair["control_attempt_id"])
            treatment = self._trial(pair["treatment_attempt_id"])
            if control["run"]["seed"] != pair["seed"] or treatment["run"]["seed"] != pair["seed"]:
                raise StateConflictError("validation seed changed")
            if control["memory_ids"] or control["exposure"] != "none" or treatment["memory_ids"] != [memory_id] or treatment["exposure"] != "candidate_validation":
                raise StateConflictError("validation exposure changed")
            control_success += int(control["success"])
            treatment_success += int(treatment["success"])
            control_credits += control["credits"]
            treatment_credits += treatment["credits"]
            control_seconds += control["seconds"]
            treatment_seconds += treatment["seconds"]
            control_gpu += control["gpu_seconds"]
            treatment_gpu += treatment["gpu_seconds"]
            control_tokens += control["tokens"]
            treatment_tokens += treatment["tokens"]
            control_unsafe += self._verify_safety(pair["control_safety"])
            treatment_unsafe += self._verify_safety(pair["treatment_safety"])
        n = len(pairs)
        return {
            "memory_id": memory_id,
            "source_kind": provenance["source_kind"],
            "source_human_attention_seconds": provenance["human_attention_seconds"],
            "paired_dev_seeds": n,
            "control_successes": control_success,
            "treatment_successes": treatment_success,
            "success_gain": treatment_success - control_success,
            "success_rate_gain": (treatment_success - control_success) / n if n else None,
            "future_assistance_credits_saved": control_credits - treatment_credits,
            "robot_seconds_saved": control_seconds - treatment_seconds,
            "gpu_seconds_saved": control_gpu - treatment_gpu,
            "tokens_saved": int(control_tokens - treatment_tokens),
            "control_unsafe_attempts": int(control_unsafe),
            "treatment_unsafe_attempts": int(treatment_unsafe),
            "human_seconds_per_success_gain": (
                provenance["human_attention_seconds"] / (treatment_success - control_success)
                if provenance["source_kind"] in {"human", "curated_human_packet"} and treatment_success > control_success
                else None
            ),
        }

    def list_pairs(self, memory_id: str) -> list[dict[str, Any]]:
        self.provenance(memory_id)
        with sqlite3.connect(self.store.path) as db:
            rows = db.execute(
                "SELECT payload FROM memory_v2_pairs WHERE memory_id=? ORDER BY seed",
                (memory_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def validate_and_promote(self, memory_id: str, *, minimum_pairs: int = 5) -> MemoryRecord:
        report = self.impact_report(memory_id)
        memory = self.store.get_memory(memory_id)
        if memory is None or memory.status not in {
            MemoryStatus.CANDIDATE, MemoryStatus.VALIDATED, MemoryStatus.TRUSTED,
        }:
            raise StateConflictError("memory is not eligible for v2 validation")
        if report["paired_dev_seeds"] < minimum_pairs or minimum_pairs < 5:
            raise StateConflictError("at least five distinct paired dev seeds are required")
        if report["treatment_unsafe_attempts"] > report["control_unsafe_attempts"]:
            raise StateConflictError("safety regression blocks memory promotion")
        if report["success_gain"] <= 0:
            raise StateConflictError("no measured success gain")
        if report["treatment_successes"] < 3:
            raise StateConflictError("insufficient treatment successes")
        if report["treatment_successes"] / report["paired_dev_seeds"] < 0.75:
            raise StateConflictError("treatment success fraction is below 0.75")
        if memory.status is MemoryStatus.TRUSTED:
            return memory
        manager = MemoryManager(self.store)
        if memory.status is MemoryStatus.CANDIDATE:
            manager.record_validation(
                memory_id,
                successes=report["treatment_successes"],
                failures=report["paired_dev_seeds"] - report["treatment_successes"],
            )
        updated = self.store.get_memory(memory_id)
        if updated.status is not MemoryStatus.VALIDATED:
            raise StateConflictError("core validation confidence gate failed")
        return manager.promote(memory_id)

    def _trial(self, attempt_id: str) -> dict[str, Any]:
        attempt = self.store.get_attempt(attempt_id)
        if attempt is None or attempt["status"] not in {"succeeded", "failed"} or attempt["native_success"] is None:
            raise StateConflictError("trial attempt lacks completed native outcome")
        run = self.store.get_run(attempt["run_id"])
        if run is None:
            raise StateConflictError("trial run missing")
        raw = next((item for item in self.store._list_payloads("raw_traces") if item["attempt_id"] == attempt_id), None)
        if raw is None or not raw["outcome"].get("evaluator_authoritative") or raw["outcome"].get("native_success") != attempt["native_success"]:
            raise StateConflictError("trial lacks agreeing native trace outcome")
        runtime = raw["metadata"].get("runtime", {})
        ids = runtime.get("memory_ids", [])
        if not isinstance(ids, list):
            raise StateConflictError("invalid memory exposure trace")
        retrieval_events = [
            event["arguments"].get("memory_id")
            for event in raw["events"]
            if event["event_type"] == "attention.memory_retrieval"
            and event["status"] == "completed"
        ]
        if ids != retrieval_events:
            raise StateConflictError("memory exposure list disagrees with retrieval events")
        resources = self.store.resource_status(attempt["run_id"])
        return {
            "run_id": attempt["run_id"], "run": run, "success": attempt["native_success"],
            "mode": runtime.get("perception_mode"), "memory_ids": ids,
            "exposure": runtime.get("memory_exposure"),
            "policy_sha256": runtime.get("policy_sha256"),
            "credits": self.store.budget_status(attempt["run_id"])["used"],
            "seconds": float(raw["outcome"]["elapsed_seconds"]),
            "gpu_seconds": float(resources["gpu_seconds"]["used"]),
            "tokens": int(resources["tokens"]["used"]),
        }

    @staticmethod
    def _safety(path: Path, expected_attempt_id: str) -> dict[str, str]:
        path = path.resolve()
        data = path.read_bytes()
        value = json.loads(data)
        count = value.get("unsafe_attempts")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("safety monitor artifact requires nonnegative unsafe_attempts")
        if value.get("source") != "independent_safety_monitor":
            raise ValueError("safety evidence must come from independent monitor")
        if not isinstance(value.get("attempt_id"), str) or not value["attempt_id"]:
            raise ValueError("safety evidence must bind to an attempt")
        if value["attempt_id"] != expected_attempt_id:
            raise StateConflictError("safety evidence belongs to another attempt")
        return {"uri": str(path), "sha256": _digest(data), "attempt_id": value["attempt_id"]}

    @staticmethod
    def _verify_safety(reference: dict[str, str]) -> int:
        path = Path(reference["uri"])
        if _digest(path.read_bytes()) != reference["sha256"]:
            raise StateConflictError("safety evidence changed after registration")
        MemoryV2Manager._safety(path, reference["attempt_id"])
        return int(json.loads(path.read_text())["unsafe_attempts"])

    def _provenance(self, memory_id: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.store.path) as db:
            row = db.execute(
                "SELECT payload FROM memory_v2_provenance WHERE memory_id=?", (memory_id,)
            ).fetchone()
        return None if row is None else json.loads(row[0])
