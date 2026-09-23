"""Frozen RoboCasa task registry for AttentionBench."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RobocasaTaskSpec:
    task_id: str
    environment_id: str
    goal: str
    destination_debug_key: str


ROBOCASA_TASKS = {
    "counter_to_cab": RobocasaTaskSpec(
        task_id="counter_to_cab",
        environment_id="RoboCasa-Pn-P-Counter-To-Cab-v0",
        goal="Pick the target object from the counter and place it in the cabinet.",
        destination_debug_key="cab",
    ),
    "counter_to_sink": RobocasaTaskSpec(
        task_id="counter_to_sink",
        environment_id="RoboCasa-Pn-P-Counter-To-Sink-v0",
        goal="Pick the target object from the counter and place it in the sink.",
        destination_debug_key="sink",
    ),
}


def get_robocasa_task(task_id: str) -> RobocasaTaskSpec:
    try:
        return ROBOCASA_TASKS[task_id]
    except KeyError as exc:
        raise ValueError(
            f"unknown RoboCasa task {task_id!r}; choose one of {sorted(ROBOCASA_TASKS)}"
        ) from exc
