"""Explicit approved bimanual deployment, independent of retired fixed entries."""
from pathlib import Path
import hashlib
from .identity import load_lock,verify_files,verify_client_imports,IdentityError,OFFICIAL_COMMIT
from .native_session import NativeV2Service
LOCK_PATH=Path('/home/truares/Documents/Codex/2026-10-12/curobov2-openarm-ui-ui-runner-sdk/outputs/deployment-13/runtime-lock.json')

def deployment_contract():
 lock,sha=load_lock(LOCK_PATH);root=Path(lock['project_root'])
 config=root/'config/openarm_bimanual.yml';parameters=root/'openarm_bimanual/configs/robot_parameters.json'
 if (root!=LOCK_PATH.parent/'runtime/project' or lock['library_revision']!=OFFICIAL_COMMIT
     or str(config)not in lock['robot_configs']):raise IdentityError('Bimanual deployment identity differs')
 return dict(schema='attentionbench.official-v2-bimanual-deployment/1',official_commit=OFFICIAL_COMMIT,
  parent_lock_sha256=lock['provenance']['parent_lock_sha256'],lock_path=str(LOCK_PATH),lock_sha256=sha,
  python=lock['python'],project_root=str(root),robot_config=str(config),
  robot_config_sha256=lock['robot_configs'][str(config)],task_source=str(root/'native-task-source'),
  parameters=dict(path=str(parameters),sha256=lock['files'][str(parameters)]),
  native_adapter='openarm-bimanual-v2/1',grasp_schema='openarm.grasp/2',formal_eligible=False)

def attest_runtime(config):
 contract=deployment_contract()
 if config['v2_deployment']!=contract:raise IdentityError('UI/Runner deployment binding differs')
 lock,_=load_lock(LOCK_PATH);verify_files(lock);verify_client_imports(lock)
 return contract

def owner(evidence,*,config,deadline,cancel_event=None):
 contract=attest_runtime(config)
 return NativeV2Service(lock_path=LOCK_PATH,robot_config=contract['robot_config'],task_source=contract['task_source'],
  parameters_path=contract['parameters']['path'],evidence=evidence,deadline=deadline,
  task=config['task_id'],seed=config['seed'],arm=config['robot']['execution_arm'],
  cancel_check=lambda:bool(cancel_event and cancel_event.is_set()),
  cancel_kind=lambda:getattr(cancel_event,'operator_action','cancel'))
