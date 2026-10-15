"""Offline SHA-verified attribution. This module is never imported by Dev/Advisor."""
import json
from pathlib import Path
from .dev_loop import sha
from .core.dev_store import DevConflict
RULES=json.loads(Path(__file__).with_name('dev_failure_rules.json').read_bytes())
def read(ref):
 if sha(ref['uri'])!=ref['sha256']:raise DevConflict('failure evidence digest mismatch')
 return json.loads(Path(ref['uri']).read_bytes())
def classify(version,job=None):
 refs=[version['source']]
 try:
  source=version['source']
  if sha(source['uri'])!=source['sha256']:raise DevConflict('source digest mismatch')
  # validation_error is immutable in version.json (or an authenticated ledger version).
  if not version['valid']:return dict(category='code_validation',tags=[],evidence=refs,detail=version['validation_error'])
  if not job:return dict(category=None,tags=['not_started'],evidence=refs,detail='no execution')
  if job['state']!='settled':return dict(category='unknown',tags=['unknown'],evidence=refs,detail='unsettled execution')
  receipt=job['receipt'];bundle=read(receipt['bundle']);feedback=read(receipt['public_feedback']);sandbox=read(bundle['artifacts']['sandbox_receipt']);native=read(receipt['native_result'])
  refs += [receipt['bundle'],receipt['public_feedback'],bundle['artifacts']['sandbox_receipt'],receipt['native_result']]
  if bundle.get('infrastructure_failure') or bundle.get('outcome_unknown'):return dict(category='unknown',tags=['infra','unknown'],evidence=refs,detail=bundle.get('error'))
  if bundle.get('status')=='condition_invalid':return dict(category='scene_condition_invalid',tags=[],evidence=refs,detail='scene_contract_mismatch')
  if native.get('evaluated') is not True:return dict(category='unknown',tags=['unknown'],evidence=refs,detail='unevaluated native result')
  text=json.dumps([version.get('validation_error'),feedback.get('public_error'),feedback.get('action_trace'),bundle.get('error'),bundle.get('status'),sandbox.get('error')],ensure_ascii=False).lower()
  for category in ['sdk_usage','planning_execution']:
   if any(word in text for word in RULES[category]):return dict(category=category,tags=['success'] if native.get('native_success') is True else [],evidence=refs,detail=feedback.get('public_error') or bundle.get('error'))
  if native.get('native_success') is True:return dict(category=None,tags=['success'],evidence=refs,detail=None)
  if bundle.get('status')=='completed' and native.get('native_success') is False:return dict(category='task_incomplete',tags=[],evidence=refs,detail='completed process, independent task false')
  return dict(category='unknown',tags=['unknown'],evidence=refs,detail=feedback.get('public_error') or bundle.get('error'))
 except (OSError,ValueError,KeyError,TypeError,DevConflict) as exc:return dict(category='unknown',tags=['unknown'],evidence=refs,detail=str(exc))
