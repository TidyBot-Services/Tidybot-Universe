"""Offline formal grasp freeze check; never starts a simulator or planner."""
import copy
import json
import sys
from pathlib import Path

from openarm365_dev_v2.dev_config import ROOT, make_lock, verify_lock
from openarm365_dev_v2.formal_config import make_formal_config, validate_formal_config, grasp_generation
from openarm365_dev_v2.scene_contract import registered_contract_ref


def main():
    output = ROOT / 'outputs' / 'formal-refreeze-20261113'
    output.mkdir(parents=True, exist_ok=True)
    stage = 'make_lock'
    summary = {'output_dir': str(output)}
    try:
        ref = make_lock(output / 'dev-identity-lock.json')
        stage = 'verify_lock'
        lock = verify_lock(ref)
        summary.update(dev_lock=ref, planner_lock=lock['planner_lock'])
        stage = 'make_formal_config'
        config = make_formal_config('pick_place_counter_to_sink', 102, ref,
            attempt_wall_seconds=480, whole_case_wall_seconds=600, sdk_call_limit=80,
            scene_contract=registered_contract_ref())
        path = output / 'formal-config.json'
        with path.open('x') as stream:
            stream.write(json.dumps(config, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
        path.chmod(0o444)
        stage = 'validate_formal_config'
        validate_formal_config(json.loads(path.read_bytes()))
        summary.update(config_path=str(path), validated=True, grasp_freeze=config['grasp_freeze'],
                       grasp_generation=grasp_generation(config),
                       settings={key: config[key] for key in (
                           'contact_exempt_objects', 'planner_mode', 'ik_candidates', 'ik_max_joint_jump')})
        rejected = {}
        for key, value in dict(contact_exempt_objects='none', planner_mode='plan',
                               ik_candidates=3, ik_max_joint_jump=.75).items():
            stage = 'reject_mutation:' + key
            changed = copy.deepcopy(config)
            changed[key] = value
            try:
                validate_formal_config(changed)
            except ValueError as exc:
                if 'grasp' not in str(exc):
                    raise
                rejected[key] = {'value': value, 'error': str(exc)}
            else:
                raise RuntimeError('formal grasp mutation was accepted: ' + key)
        summary.update(rejected_mutations=rejected, status='passed')
    except Exception as exc:
        summary.update(status='error', stage=stage, error=f'{type(exc).__name__}: {exc}')
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
        return 1
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
