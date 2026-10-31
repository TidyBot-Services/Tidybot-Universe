"""Read-only discovery of interrupted origin artifacts for operator recovery."""
import hashlib
import json
from pathlib import Path

from .core.dev_store import DevConflict


RESULT_FIELDS = ('validation', 'validator_result', 'validation_result', 'verdict',
                 'result', 'candidate', 'candidate_id', 'service_candidate')
RESULT_FILES = ('validation.json', 'verdict.json', 'trial.json')


def interrupted_artifacts(provision, config, current, dispatch, evidence, provenance):
    """Record paths, never move exports/replays or infer that they can be reused.

    Older dispatches did not record the randomly allocated validator directory.
    Match native replay ownership or the sealed policy digest conservatively;
    retain unbound incomplete arms separately rather than claim unique ownership.
    A matching independent result blocks abandonment even without a receipt.
    """
    key=current['origin_key']
    if any(k in dispatch for k in RESULT_FIELDS) or dispatch.get('catalogs') or any((evidence/name).exists() for name in RESULT_FILES):
        raise DevConflict('This origin has a validator result or service candidate and cannot be abandoned.')
    export_key=dispatch.get('export_key',current.get('export_key',key))
    if not isinstance(export_key,str) or Path(export_key).name!=export_key or not (export_key==key or export_key.startswith(key+'-')):
        raise DevConflict('The interrupted origin export key is invalid.')
    if current.get('export_key',export_key)!=export_key:
        raise DevConflict('The interrupted origin export keys differ.')
    export=provision.root/'memory-origin-imports'/export_key
    validator=Path(config['validator_root']).absolute()
    if export.resolve()!=export or validator.resolve()!=validator:
        raise DevConflict('Interrupted origin artifact paths must be canonical.')
    summary=current['note']['source_summary'];cell=summary['cell'];version=summary['version']
    digest=provenance['origin']['digests']['origin_source']
    matched=[];unattributed=[]
    for arm in sorted(validator.glob('arm-*')):
        if arm.resolve()!=arm or not arm.is_dir():
            raise DevConflict('The validator replay directory is not canonical.')
        results=[json.loads((arm/name).read_bytes()) for name in RESULT_FILES if (arm/name).exists()]
        owns_result=any(r.get('execution_import_id') in (key,export_key) for r in results)
        policies=list(arm.glob('policy-replays/*/policy.py'))
        same_policy=any(hashlib.sha256(p.read_bytes()).hexdigest()==digest for p in policies)
        contexts=[json.loads(p.read_bytes()) for p in arm.glob('policy-replays/*/executions/*/recording_context.json')]
        owns_replay=any(c.get('run_id')=='run:origin-replay:'+cell and c.get('attempt_id')==f'origin-replay:{cell}:{version}' for c in contexts)
        if owns_result or (results and (same_policy or owns_replay)):
            raise DevConflict('This origin has a validator result and cannot be abandoned.')
        if same_policy or owns_replay:matched.append(str(arm))
        elif not results and not policies and not contexts:unattributed.append(str(arm))
    return dict(export_key=export_key,partial_export_dir=str(export),
                validator_replay_dirs=matched,unattributed_validator_replay_dirs=unattributed)
