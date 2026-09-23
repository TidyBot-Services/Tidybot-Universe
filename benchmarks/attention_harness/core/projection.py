"""UI-facing read model projected from persisted orchestrator state."""

from __future__ import annotations

from typing import Any

from .store import AttentionStore, StateConflictError


class AttentionProjection:
    """Build the six required UI panels without introducing a second state store."""

    def __init__(self, store: AttentionStore) -> None:
        self.store = store

    def snapshot(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run is None:
            raise StateConflictError(f"run {run_id!r} does not exist")
        events = self.store.events()
        attempts = _entities(events, "attempts", run_id=run_id)
        attempt_ids = {item["attempt_id"] for item in attempts}
        traces = [item for item in _entities(events, "traces") if item["attempt_id"] in attempt_ids]
        trace_ids = {item["trace_id"] for item in traces}
        requests = []
        responses = []
        for event in events:
            if event["entity_type"] not in {"requests", "request"}:
                continue
            value = event["payload"].get("request", event["payload"])
            if value.get("trace_id") in trace_ids:
                requests.append(value)
                response = event["payload"].get("response")
                if response is not None:
                    responses.append(response)
        latest_requests = {item["request_id"]: item for item in requests}
        memories = [
            memory.artifact()
            for memory in self.store.list_memories()
            if memory.source_trace_id in trace_ids
        ]
        return {
            "schema_version": "attentionbench.ui-projection.v1",
            "run_context": {
                "suite": run["suite"],
                "task_id": run["task_id"],
                "run_id": run_id,
                "policy_id": run["policy_id"],
                "execution_target": run["execution_target"],
                "assistance_mode": run["assistance_mode"],
                "locked": run["status"] != "created",
            },
            "resource_budget": self.store.resource_status(run_id),
            "autonomous_work": {"attempts": attempts},
            "attention_inbox": list(latest_requests.values()),
            "request_detail": {
                "traces": traces,
                "responses": responses,
                "memories": memories,
            },
            "live_station": {
                "execution_target": run["execution_target"],
                "status": run["status"],
                "emergency_interrupt_available": True,
            },
        }


def _entities(events: list[dict[str, Any]], entity_type: str, **matches: Any) -> list[dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    singular = entity_type[:-1] if entity_type.endswith("s") else entity_type
    for event in events:
        if event["entity_type"] not in {entity_type, singular}:
            continue
        value = event["payload"].get(singular, event["payload"])
        if all(value.get(key) == expected for key, expected in matches.items()):
            values[event["entity_id"]] = value
    return list(values.values())
