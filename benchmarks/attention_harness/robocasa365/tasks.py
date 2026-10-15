from dataclasses import dataclass


@dataclass(frozen=True)
class Task:
    task_id: str
    official_env: str


TASKS = {
    name: Task(name, env)
    for name, env in [
        ("pick_place_counter_to_sink", "PickPlaceCounterToSink"),
        ("open_cabinet", "OpenCabinet"),
    ]
}


def get_task(task_id):
    if task_id not in TASKS:
        raise ValueError("unsupported RoboCasa365 phase-1 task")
    return TASKS[task_id]
