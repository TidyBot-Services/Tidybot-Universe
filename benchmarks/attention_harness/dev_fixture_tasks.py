"""Explicit screened R5 product selections; pilot and historical records stay frozen."""
from dataclasses import replace
import json
from curobo_v2.fixture_interaction import PROFILE,PILOT_SEEDS
from openarm365_dev_v2.fixture_contract import checked,contract_ref
from .dev_tasks import FIXTURE_TASKS,ROOT

EXCLUSIONS=tuple(json.loads(checked('scene_contracts/fixture-product-seeds.json').read_bytes())['exclusions'])
FAMILIES={'A':('Open and close fixtures',('open_cabinet','close_blender_lid','open_drawer')),
          'B':('Buttons and knobs',('turn_on_microwave','turn_off_stove','open_stand_mixer_head')),
          'C':('Place and start',('coffee_setup_mug','prepare_coffee','kettle_boiling','heat_kebab_sandwich')),
          'D':('Cabinet pick and place',('pick_place_counter_to_cabinet','categorize_condiments','weigh_ingredients','arrange_tea'))}
HELD_OUT={'open_drawer','open_stand_mixer_head','heat_kebab_sandwich','arrange_tea'}
SHARED={'open_cabinet','pick_place_counter_to_cabinet'}
# Set aside by the user 2026-11-10 (~/下载/success-teleport-20261110/REPORT.md): the head falls shut on seed 101
# and the lock button has no effect upstream. Hidden from New run and refused for new runs; old runs still open.
SET_ASIDE={'open_stand_mixer_head':'set aside 2026-11-10: the stand mixer head falls shut when released on seed 101 and its lock button has no effect'}
INTERACTIVE_ONLY='R5 fixture runs are interactive only until a measured fixture scene contract exists (scene positions are not verified)'
SCENE_VERIFICATION='fixture_contract_only'

def exclusion(task,seed):return next((r for r in EXCLUSIONS if (r['task_id'],r['seed'])==(task,seed)),None)
def seeds(task):return tuple(s for s in PILOT_SEEDS[task] if exclusion(task,s) is None)
def task_record(task):return replace(FIXTURE_TASKS[task],seeds=seeds(task),initial_policy=str(ROOT/'openarm365_dev_v2/policies/fixture_observe.py'))
def validate(task,seed):
    if task not in FIXTURE_TASKS:raise ValueError('R5 requires a screened fixture task')
    blocked=exclusion(task,seed)
    if blocked:raise ValueError(f"{task} seed {seed}: {blocked['reason']} ({', '.join(blocked['evidence_paths'])})")
    if type(seed) is not int or seed not in seeds(task):raise ValueError('seed must be a registered R5 development seed')
    return task_record(task)

def prepare_selection(selection):
    profile=selection.get('rules_profile')
    tasks=selection.get('tasks',[])
    if profile is not None and profile!=PROFILE:raise ValueError('unknown rules_profile')
    fixture=profile==PROFILE or any(t in FIXTURE_TASKS and t not in SHARED for t in tasks)
    if not fixture:return selection
    if selection.get('mode','experiment')!='interactive':raise ValueError(INTERACTIVE_ONLY)
    if selection.get('reproducible_reset') is True:raise ValueError('R5 fixture runs cannot claim reproducible_reset: true (scene positions are not verified)')
    if not isinstance(tasks,list) or not tasks:raise ValueError('explicit fixture task selection required')
    if selection.get('budget_tier','dev')!='dev':raise ValueError('fixture profile requires dev tier')
    for task in tasks:
        if task in SET_ASIDE:raise ValueError(f'{task}: {SET_ASIDE[task]}')
        for seed in selection.get('seeds',[101]):validate(task,seed)
    if selection.get('scene_contract') is not None:raise ValueError('R5 uses fixture_scene_contract, not a measured scene_contract')
    return dict(selection,rules_profile=PROFILE)

def options():
    # Lazy: the native sim worker imports this module (via dev_config) without the Memory service on its path.
    from attention_memory_service.skill_tags import VERSION as SKILL_TAGS_VERSION, tags_for
    return [dict(task_record(t).public(),rules_profile=PROFILE,family=family,family_label=label,
        held_out=t in HELD_OUT,skill_tags=list(tags_for(t)),skill_tags_version=SKILL_TAGS_VERSION,explicit_only=True,rules_label='fixture rules (R5)',
        seed_options=[dict({'seed':s,'enabled':exclusion(t,s) is None},**({} if exclusion(t,s) is None else exclusion(t,s))) for s in PILOT_SEEDS[t]])
        for family,(label,tasks) in FAMILIES.items() for t in tasks if t not in SET_ASIDE]

def validate_contract(config):
    validate(config['task_id'],config['seed'])
    if config.get('fixture_scene_contract')!=contract_ref(config['task_id']):raise ValueError('R5 fixture reset contract mismatch')
    return config['fixture_scene_contract']
