"""Main-project deployment policy for the frozen official V2 runtime.

Branch availability is explicit. An unadapted branch cannot start a legacy
executor while migration is in progress. This is not end-to-end acceptance.
"""
from pathlib import Path
import hashlib
import json
from .identity import IdentityError, load_lock, verify_client_imports, verify_files

OFFICIAL_COMMIT = '78fd485fa82d9b9a063fb4985e371814587e666a'
R12_ROOT = Path('/home/truares/Documents/Codex/2026-10-11/goal-curobov2-nvidia-service-1-service/outputs/revisions/r12')
R12_LOCK_SHA256 = '207d7e82a716cd8dd71a1f6ba56fe3843edf365a913065ab937a24238128399c'
MAIN_DEPLOYMENT_ROOT = Path('/home/truares/Documents/Codex/2026-10-12/goal-curobov2-robocasa-robocasa365-v2-only/outputs/deployments/main-r09')


def retired_entry(name):
    raise IdentityError(
        f'V2-only: {name} has no integrated authenticated V2 executor; '
        'legacy planning and direct motion are disabled. No fallback is permitted.'
    )


def fixed_deployment():
    """Return the explicit approved adapter deployment; no environment override."""
    path = MAIN_DEPLOYMENT_ROOT/'runtime-lock.json'
    lock, sha = load_lock(path)
    if (lock['project_root'] != str(MAIN_DEPLOYMENT_ROOT/'runtime/project') or lock.get('official_only') is not True
            or lock['library_revision'] != OFFICIAL_COMMIT
            or lock['library_base'] != OFFICIAL_COMMIT):
        raise IdentityError('V2-only deployment differs from the approved adapter deployment with inherited official identity')
    verify_files(lock)
    verify_client_imports(lock)
    return path, lock


def fixed_owner(evidence, *, task, seed, arm, deadline, cancel_event=None, parameters_path):
    from .native_session import NativeV2Service
    path, lock = fixed_deployment()
    project = Path(lock['project_root'])
    parameters = project/'openarm_robocasa/configs/robot_parameters_v5.json'
    if (arm not in {'left', 'right'} or type(seed) is not int
            or hashlib.sha256(Path(parameters_path).read_bytes()).hexdigest()
            != hashlib.sha256(parameters.read_bytes()).hexdigest()):
        raise IdentityError('Fixed V2 deployment does not match requested arm/seed/native parameters')
    return NativeV2Service(
        lock_path=path, robot_config=project/'config/openarm_fixed.yml',
        task_source=project/'native-task-source', evidence=Path(evidence),parameters_path=parameters,
        deadline=deadline, task=task, seed=seed, arm=arm,
        cancel_check=lambda: bool(cancel_event and cancel_event.is_set()))


def deployment_contract():
    path,lock=fixed_deployment()
    project=Path(lock['project_root'])
    parameter=project/'openarm_robocasa/configs/robot_parameters_v5.json'
    config=project/'config/openarm_fixed.yml'
    return {'schema':'attentionbench.official-v2-deployment.v1',
        'official_commit':OFFICIAL_COMMIT,'parent_lock_sha256':R12_LOCK_SHA256,
        'lock_path':str(path),'lock_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'python':lock['python'],'prefix':lock['prefix'],'library_root':lock['library_root'],
        'project_root':lock['project_root'],'robot_config':str(config),
        'robot_config_sha256':lock['robot_configs'][str(config)],
        'task_source':str(project/'native-task-source'),
        'parameters':{'path':str(parameter),'sha256':lock['files'][str(parameter)]},
        'native_assets':lock['native_assets']}
