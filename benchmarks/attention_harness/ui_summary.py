"""Aggregate terminal Attention runs without exposing per-attempt evaluator internals."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .core.store import AttentionStore


def comparison_summary(store: AttentionStore, selected_run_id: str) -> dict[str, Any]:
    selected = store.get_run(selected_run_id)
    if selected is None:
        raise ValueError("run not found")
    conditions = {
        key: selected.get(key)
        for key in ("suite", "task_id", "execution_target", "assistance_mode",
                    "developer_model", "evaluator_model")
    }
    conditions["budget"] = selected["budget"]
    runs = {}
    attempts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in store.events():
        if event["entity_type"] in {"run", "runs"}:
            run = store.get_run(event["entity_id"])
            if run is not None:
                runs[event["entity_id"]] = run
        elif event["entity_type"] in {"attempt", "attempts"}:
            value = event["payload"].get("attempt", event["payload"])
            attempts[value["run_id"]].append(value)
    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    infrastructure_run_ids = {event["payload"].get("run_id") for event in store.events()
                              if event["event_type"] in {"work.blocked", "work.failed"}}
    for run in runs.values():
        if run["status"] not in {"completed", "failed"}:
            continue
        if run["run_id"] in infrastructure_run_ids:
            continue
        if any(run.get(key) != value for key, value in conditions.items() if key != "budget"):
            continue
        if run["budget"] != conditions["budget"]:
            continue
        terminal = {item["attempt_id"]: item for item in attempts.get(run["run_id"], [])}
        if not terminal or any(item["status"] == "running" for item in terminal.values()):
            continue
        rows[run["policy_id"]].append({
            "seed": run["seed"],
            "success": any(item.get("native_success") is True for item in terminal.values()),
            "assistance_used": store.budget_status(run["run_id"])["used"],
        })
    seed_sets = [sorted(item["seed"] for item in group) for group in rows.values()]
    comparable = (
        len(seed_sets) > 1
        and all(seeds == seed_sets[0] for seeds in seed_sets[1:])
        and all(len(seeds) == len(set(seeds)) for seeds in seed_sets)
    )
    policies = []
    for policy_id, values in sorted(rows.items()):
        count = len(values)
        successes = sum(item["success"] for item in values)
        policies.append({
            "policy_id": policy_id,
            "successes": successes,
            "runs": count,
            "success_rate": successes / count,
            "average_assistance_used": sum(item["assistance_used"] for item in values) / count,
            "seeds": sorted(item["seed"] for item in values),
        })
    return {"conditions": conditions, "comparable": comparable, "policies": policies}
