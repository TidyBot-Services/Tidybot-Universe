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
        ("pick_place_counter_to_cabinet", "PickPlaceCounterToCabinet"),
        ("turn_on_microwave", "TurnOnMicrowave"),
        ("turn_off_stove", "TurnOffStove"),
        ("open_drawer", "OpenDrawer"),
        ('close_blender_lid', 'CloseBlenderLid'),
        ('open_stand_mixer_head', 'OpenStandMixerHead'),
        ('coffee_setup_mug', 'CoffeeSetupMug'),
        ('prepare_coffee', 'PrepareCoffee'),
        ('kettle_boiling', 'KettleBoiling'),
        ('heat_kebab_sandwich', 'HeatKebabSandwich'),
        ('categorize_condiments', 'CategorizeCondiments'),
        ('weigh_ingredients', 'WeighIngredients'),
        ('arrange_tea', 'ArrangeTea'),
    ]
}


def get_task(task_id):
    if task_id not in TASKS:
        raise ValueError("unsupported RoboCasa365 phase-1 task")
    return TASKS[task_id]
