"""Provisional Memory proposals from effective AdvisorProxy answers (plan 0d, 2026-10-22).

After the execution of version H settles, H's help answer becomes a proposal
when H-1 failed with a public signature and H either no longer shows that
signature or got further in the action trace. Any asking policy qualifies.
The proposal carries no authority: the offline importer submits the raw public
feedback of both versions and the Memory Service rechecks everything itself.
"""
import hashlib
import json
import re
from pathlib import Path

from .dev_loop import immutable, sha

SCHEMA = 'attentionbench.dev-v2-provisional-proposal.v1'
PROVIDERS = ('advisor', 'human_proxy')
_IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')
_OPERATION = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\Z')
EVAL_CLASSES = ('timeout', 'planner_rejected', 'joint_limit', 'not_completed', 'other', 'none')


class NotStaged(Exception):
    """A help answer that is not a provisional source; the reason is recorded."""


def derive_signature(feedback):
    from attention_memory_service.failure_signature import derive_signature as derive
    try:
        return derive(feedback)
    except ValueError as exc:
        raise NotStaged(str(exc)) from exc


def signature_present(feedback, signature):
    # Same rule as the Service (attention_memory_service.dev_v2.signature_present).
    from attention_memory_service.dev_v2 import signature_present as service_rule
    return service_rule(feedback, signature)


def progress(feedback):
    """Completed/succeeded events before the first failed (or erroring) event."""
    completed = 0
    for event in feedback.get('action_trace', []):
        if event.get('status') == 'failed' or event.get('error'):
            break
        completed += event.get('status') in {'completed', 'succeeded'}
    return completed


def effective_reason(origin, helped, signature):
    if not signature_present(helped, signature):
        return 'signature_changed'
    if progress(helped) > progress(origin):
        return 'progressed'
    raise NotStaged('not_effective')


def eval_reason_class(review):
    """Small deterministic class of version O's Eval objective reason."""
    if not isinstance(review, dict):
        return 'none'
    text = review.get('feedback', review.get('reason'))
    if not isinstance(text, str) or not text.strip():
        return 'none'
    text = text.casefold()
    if re.search(r'time[ds]? ?out|timed out|timeout', text):
        return 'timeout'
    if re.search(r'planning ?rejected|planner rejected|plan(?:ning)? (?:was )?rejected|planningrejected', text):
        return 'planner_rejected'
    if re.search(r'joint[ _-]?limit', text):
        return 'joint_limit'
    if re.search(r'not (?:been )?complete|did not complete|incomplete|never (?:grasp|reach|place|complete)', text):
        return 'not_completed'
    return 'other'


def _ref(path):
    path = Path(path)
    if not path.is_file():
        raise NotStaged('missing_evidence:' + path.name)
    return {'uri': str(path.resolve()), 'sha256': sha(path)}


def _origin_review(cell, origin_version):
    job = next((j for j in cell.get('jobs', []) if j.get('version') == origin_version and j.get('kind') == 'eval'
                and j.get('state') == 'settled'), None)
    receipt = (job or {}).get('receipt') or {}
    ref = receipt.get('eval_review') or receipt.get('review')
    if not isinstance(ref, dict) or not Path(ref.get('uri', '/missing')).is_file() or sha(ref['uri']) != ref.get('sha256'):
        return None
    return json.loads(Path(ref['uri']).read_bytes())


def run_note_link(store, key, version, directory):
    """Return the provenance link only for a digest-verified local run note."""
    cell = store.cell(key)
    run_note = Path(directory) / 'memory-run-note.json'
    if cell.get('run_notes') is True and run_note.is_file():
        from .memory_run_notes import events, PREFIX
        created = next((e for kind, e in events(store, key)
                        if kind == PREFIX + 'created' and e.get('help_version') == version), None)
        if created and sha(run_note) == created['note']['sha256']:
            note = json.loads(run_note.read_bytes())
            return dict(experiment_id=cell['experiment_id'], cell=key,
                        version=version, run_note_id=note['note_id'])
    return None


def stage(store, key, version, directory, *, agent_help_evidence=None):
    """Write ``memory-provisional-proposal.json`` for version H when it qualifies.

    Returns the queue ref, or None when version H has no AdvisorProxy answer.
    Raises NotStaged with a reason for an answer that does not qualify.
    """
    directory = Path(directory)
    help_path = directory / 'help.json'
    if version < 2 or not help_path.is_file():
        return None
    help_value = json.loads(help_path.read_bytes())
    if help_value.get('provider') not in PROVIDERS:
        return None  # Demo, cache and live human help are not sources.
    content = help_value.get('content')
    if help_value.get('rejected') or not isinstance(content, str) or not content.strip():
        raise NotStaged('help_rejected_or_empty')
    from .dev_planner_memory import memory_scope
    cell = store.cell(key)
    config = json.loads((directory.parent / 'config.json').read_bytes())
    from .memory_fixture_guard import fixture_reason
    if fixture_reason(config):raise NotStaged('test_fixture_receipt_excluded_from_memory')
    if memory_scope(config, cell.get('mode', 'experiment'))['level'] != 'scene':
        raise NotStaged('no_scene_contract')
    origin_dir = directory.parent / f'v{version - 1}'
    origin_feedback = _ref(origin_dir / 'public-feedback.json')
    help_feedback = _ref(directory / 'public-feedback.json')
    origin_value = json.loads(Path(origin_feedback['uri']).read_bytes())
    helped_value = json.loads(Path(help_feedback['uri']).read_bytes())
    signature = derive_signature(origin_value)
    try:
        from attention_memory_service.dev_v2 import validate_signature
        validate_signature(signature, origin_value)
    except Exception as exc:  # The Service would refuse it; do not queue.
        raise NotStaged('signature_invalid') from exc
    reason = effective_reason(origin_value, helped_value, signature)
    record = json.loads((directory / 'version.json').read_bytes())
    model_input = record.get('model_input')
    if agent_help_evidence is None and (not isinstance(model_input, dict) or sha(model_input['uri']) != model_input['sha256']):
        raise NotStaged('missing_evidence:model-input.json')
    # The exact Memory tuple is computed here, where the runtime SDK still
    # equals the run's frozen identity; the importer only adds its namespace.
    try:
        from .dev_planner_memory import context
        memory_context = context(config, 'importer-namespace')
        memory_context.pop('namespace')
    except Exception as exc:
        raise NotStaged('context_unavailable:' + type(exc).__name__) from exc
    refs = dict(origin_public_feedback=origin_feedback, origin_source=_ref(origin_dir / 'policy.py'),
                help=_ref(help_path), **({'help_model_input': {'uri': model_input['uri'], 'sha256': model_input['sha256']}}
                   if agent_help_evidence is None else {'agent_help_evidence': agent_help_evidence}),
                help_source=_ref(directory / 'policy.py'), help_public_feedback=help_feedback)
    proposal = dict(schema_version=SCHEMA, experiment_id=cell['experiment_id'], cell=key,
                    origin_version=version - 1, help_version=version, policy_id=cell['policy_id'],
                    provider=help_value['provider'], failure_signature=signature, effective_reason=reason,
                    origin_progress=progress(origin_value), help_progress=progress(helped_value),
                    eval_reason_class=eval_reason_class(_origin_review(cell, version - 1)),
                    context=memory_context, refs=refs, help_content=content, status='pending_import', authority_candidate=False,
                    unvalidated=True, excluded_from_results=True)
    proposal['source_version_evidence'] = _ref(directory / 'version.json')
    if agent_help_evidence is not None:
        proposal.update(origin=dict(channel='dev_agent'), provenance=dict(channel='dev_agent'))
    link = run_note_link(store, key, version, directory)
    if link is not None:
        proposal['from_run_note'] = link
    target = directory / 'memory-provisional-proposal.json'
    if target.exists():
        # Immutable: a resumed version reuses the proposal it already wrote.
        if json.loads(target.read_bytes()) != proposal:
            raise NotStaged('proposal_already_differs')
        ref = {'uri': str(target.resolve()), 'sha256': sha(target)}
    else:
        ref = immutable(target, proposal)
    with store.tx() as db:
        exists = db.execute("SELECT 1 FROM v2_events WHERE kind=? AND payload LIKE ?",
                            ('planner_memory_offline_queued', '%' + ref['sha256'] + '%')).fetchone()
        if not exists:
            store.event(db, 'planner_memory_offline_queued', dict(cell=key, version=version, kind='provisional', queue=ref))
    return ref


def stage_safely(store, key, version, directory):
    """Never break or slow the run: any problem becomes a skip event."""
    from .memory_run_notes import create
    create(store, key, version, directory)
    try:
        return stage(store, key, version, directory)
    except Exception as exc:
        reason = str(exc) if isinstance(exc, NotStaged) else 'error:' + type(exc).__name__
        try:
            with store.tx() as db:
                store.event(db, 'planner_memory_provisional_skipped', dict(cell=key, version=version, reason=reason[:200]))
        except Exception:
            pass
        return None


def memory_id(experiment_id, cell, help_version):
    return 'p-' + hashlib.sha256(f'{experiment_id}:{cell}:{help_version}'.encode()).hexdigest()[:24]
