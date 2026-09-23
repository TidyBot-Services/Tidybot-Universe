"""Deterministic export of one run and its linked Attention records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .projection import AttentionProjection
from .store import AttentionStore, StateConflictError


def build_run_bundle(store: AttentionStore, run_id: str) -> dict[str, Any]:
    run = store.get_run(run_id)
    if run is None:
        raise StateConflictError(f"run {run_id!r} does not exist")
    linked_events = []
    linked_ids = {run_id}
    for event in store.events():
        payload_text = json.dumps(event["payload"], sort_keys=True)
        if event["entity_id"] in linked_ids or any(identifier in payload_text for identifier in linked_ids):
            linked_events.append(event)
            linked_ids.add(event["entity_id"])
    return {
        "schema_version": "attentionbench.run-bundle.v1",
        "run": run,
        "projection": AttentionProjection(store).snapshot(run_id),
        "events": linked_events,
    }


def write_run_bundle(store: AttentionStore, run_id: str, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    value = build_run_bundle(store, run_id)
    output.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return output
