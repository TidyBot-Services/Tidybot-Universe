"""Tasks owned and exposed by the Robosuite simulator service."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    robosuite_env: str
    prompt: str
    object_keys: tuple[str, ...]


TASKS: dict[str, TaskSpec] = {
    "cube_lift": TaskSpec(
        task_id="cube_lift",
        robosuite_env="Lift",
        prompt="Lift the cube clear of the table.",
        object_keys=("cube_pos",),
    ),
    "cube_stack": TaskSpec(
        task_id="cube_stack",
        robosuite_env="Stack",
        prompt="Place cube A on top of cube B.",
        object_keys=("cubeA_pos", "cubeB_pos"),
    ),
}


def get_task(task_id: str) -> TaskSpec:
    try:
        return TASKS[task_id]
    except KeyError as exc:
        choices = ", ".join(sorted(TASKS))
        raise ValueError(f"Unknown task {task_id!r}; choose one of: {choices}") from exc
