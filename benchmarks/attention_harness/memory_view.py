"""Read model and operator controls for Memory, shared by the UI server and tools.

The UI shows and changes exactly the records the agent retrieves: it reads them
from the Memory Service (HTTP, or the in-process service over the same store)
and never keeps its own copy. Only lifecycle actions need operator authority,
which is separate from the read key and is never sent to the browser.
"""

from __future__ import annotations

import re
import time
from typing import Any

# Operator actions the UI may request. "expiry" changes when a memory stops being usable.
ACTIONS = ("disable", "enable", "rollback", "expiry", "level")
_TEXT = re.compile(r"^[^\x00-\x1f\x7f]{1,200}$")


class MemoryView:
    def __init__(self, gateway: Any, *, source: str, can_operate: bool) -> None:
        self.gateway = gateway
        self.source = source
        self.can_operate = can_operate

    # -- construction ------------------------------------------------------------

    @classmethod
    def local(cls, store_path, *, operate: bool = False) -> "MemoryView":
        """Read (and, if explicitly allowed, operate) the Memory held in the run's own store."""
        from attention_memory_service import MemoryService
        return cls(MemoryService(store_path), source="local", can_operate=operate)

    @classmethod
    def service(cls, base_url: str, *, api_key: str, operator_key: str | None = None) -> "MemoryView":
        from attention_memory_service import MemoryServiceClient
        return cls(MemoryServiceClient(base_url, api_key=api_key, operator_key=operator_key, timeout=180),
                   source="service", can_operate=bool(operator_key))

    def public(self) -> dict[str, Any]:
        return {"source": self.source, "can_operate": self.can_operate}

    def dev_control(self) -> dict[str, Any]:
        # The Dev v2 card only operates through a Service client carrying an
        # operator key; local write access is not an operator credential.
        return {"source": self.source, "can_operate": self.source == "service" and self.can_operate}

    # -- reading -----------------------------------------------------------------

    def catalogue(self, status: str | None = None) -> list[dict[str, Any]]:
        return self.gateway.list_memories(status=status)

    def dev_catalogue(self) -> list[dict[str, Any]]:
        """The Service's public Dev v2/test-seeded allowlist (no guidance)."""
        return self.gateway.list_dev_v2_memories()

    def dev_public(self, memory_id: str) -> dict[str, Any]:
        # The public endpoint rejects IDs outside these two provenance channels.
        memory = self.gateway.get_dev_v2_memory_public(memory_id)
        return {"memory": memory, "history": self.gateway.history(memory_id)}

    def detail(self, memory_id: str) -> dict[str, Any]:
        """Everything the operator needs to judge one memory, from the authoritative records."""
        try:
            record = self.gateway.get_memory(memory_id).artifact()
            provenance = self.gateway.provenance(memory_id)
            plan = self.gateway.get_plan(memory_id)
            pairs = self.gateway.list_pairs(memory_id)
            impact = self.gateway.impact_report(memory_id)
            decision = self.gateway.decision(memory_id)
            history = self.gateway.history(memory_id)
            uses = self.gateway.uses(memory_id)
        except Exception as error:  # an unreadable record is shown as such, never hidden
            return {"memory_id": memory_id, "error": f"{type(error).__name__}: {error}"[:300]}
        keys = ("source_kind", "source_run_id", "source_attempt_id", "source_trace_id", "request_id",
                "response_id", "responder", "human_attention_seconds", "artifact_sha256",
                "source_policy_id", "source_policy_sha256", "source_seed")
        return {
            "memory_id": memory_id,
            "record": record,
            "provenance": {
                **{key: provenance.get(key) for key in keys},
                # Links to the raw evidence, by identity and digest, not by filesystem path.
                "raw_evidence": [{"evidence_id": item["evidence_id"], "kind": item["kind"],
                                  "sha256": item["sha256"]}
                                 for item in provenance.get("raw_evidence_refs", [])],
            },
            "validation": {
                "source_drift": (decision or {}).get("source_drift",impact.get("source_drift",[])),
                "planned_seeds": None if plan is None else list(plan["seeds"]),
                "completed_pairs": [pair["seed"] for pair in pairs],
                "decision": None if decision is None else {
                    "decision": decision["decision"], "failed_gates": decision["failed_gates"],
                    "messages": decision["failed_gate_messages"], "decided_at": decision["decided_at"], "source_drift": decision.get("source_drift",[])},
                "empirical_confidence": impact["empirical_confidence"],
                "confidence_95ci": impact["treatment_success_rate_95ci"],
                "control_successes": impact["control_successes"],
                "treatment_successes": impact["treatment_successes"],
                "supporting_successes": impact["supporting_successes"],
                "counterexamples": impact["counterexamples"],
                "safety_regressions": impact["safety_regressions"],
                "validated_scope": impact["validated_scope"],
            },
            "history": history,
            "uses": [{"use_id": use["use_id"], "memory_version": use["memory_version"],
                      "run_id": use["run_id"], "attempt_id": use["attempt_id"],
                      "used_at": use["used_at"], "outcome": use["outcome"]} for use in uses],
        }

    # -- operating ---------------------------------------------------------------

    def operate(self, memory_id: str, action: str, *, actor: Any, reason: Any,
                expires_at: Any = None, dev_v2: bool = False, level=None, opened_at=None, edit=None) -> dict[str, Any]:
        if action not in ACTIONS:
            raise ValueError(f"unsupported memory action: {action}")
        if not self.can_operate or (dev_v2 and self.source != "service"):
            raise PermissionError("Memory operator authority is not configured on this server")
        if action == 'level':
            if not dev_v2:raise ValueError('level confirmation requires Dev Memory')
            if not isinstance(actor,str) or not _TEXT.fullmatch(actor.strip() or '\x00'):
                raise ValueError('actor must be 1-200 printable characters')
            actor=actor.strip()
            return self.gateway.confirm_note_level(memory_id=memory_id, level=level, actor=actor, opened_at=opened_at, edit=edit)
        for name, value in (("actor", actor), ("reason", reason)):
            if not isinstance(value, str) or not _TEXT.fullmatch(value.strip() or "\x00"):
                raise ValueError(f"{name} must be 1-200 printable characters")
        actor, reason = actor.strip(), reason.strip()
        if dev_v2:
            self.gateway.get_dev_v2_memory_public(memory_id)
        if action == "expiry":
            if (isinstance(expires_at, bool) or not isinstance(expires_at, (int, float))
                    or expires_at != expires_at or abs(expires_at) == float("inf")):
                raise ValueError("expires_at must be a finite Unix timestamp")
            if dev_v2 and expires_at <= time.time():
                raise ValueError("expires_at must be in the future")
            record = self.gateway.set_expiry(memory_id, expires_at=float(expires_at),
                                             actor=actor, reason=reason)
        else:
            record = getattr(self.gateway, action)(memory_id, actor=actor, reason=reason)
        return record.artifact()
