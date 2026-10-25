"""Public generation feedback projection, shared with the offline authority.

Vendored byte-for-byte as attention_memory_service.feedback_evidence so the
standalone service uses the same pure functions without importing a runner.
"""
import json
from pathlib import PurePath


def visible_review(record, *, dev=False):
    """Project persisted v2 and v3 reviews without rewriting old artifacts."""
    reason=record.get('reason',record.get('feedback',''))
    result={'passed':record.get('passed'),'reason':reason}
    if not dev:result['reference_suggestion']=record.get('reference_suggestion','')
    return result


def public_failure(version, feedback, rule):
    """The loop's public completion rule; not offline native failure attribution."""
    failed = (not version['valid']
              or feedback.get('execution_status') in {'failed', 'timeout', 'timed_out'}
              or bool(feedback.get('public_error'))
              or any(e.get('error') or e.get('status') == 'failed'
                     for e in feedback.get('action_trace', []))
              or rule['no_declared_completion_is_failure'])
    return dict(public_failure=failed, public_failure_rule='attentionbench.public-failure.v1')


def verify_feedback(packet, sealed, *, audience, version, cell, manifest, read, eval_job, require):
    """Allow only authenticated loop additions; never strip arbitrary metadata.

    read must enforce the original digest and, at the authority, catalog coverage.
    eval_job is lazy so exact historical packets need no new evidence.
    """
    require(audience in ('dev', 'advisor'), 'unsupported feedback audience')
    reason = 'online help source/public failed trace mismatch'
    require(isinstance(packet, dict) and isinstance(sealed, dict), reason)
    keys = {'eval_review', 'public_failure', 'public_failure_rule'}
    added = set(packet) - set(sealed)
    if not added:
        require(packet == sealed, reason)
        return 'exact'
    require(added <= keys and {k: v for k, v in packet.items() if k not in added} == sealed, reason)
    expected = {}
    if 'eval_review' in added:
        job = eval_job()
        require(job is not None and job.get('state') == 'settled', 'sealed Eval job required')
        ref = job.get('receipt', {}).get('eval_review') or job.get('receipt', {}).get('review')
        require(ref is not None, 'sealed Eval review required')
        review = read(ref)
        require(review.get('status') == 'completed' and type(review.get('passed')) is bool,
                'completed sealed Eval review required')
        expected['eval_review'] = visible_review(review, dev=(audience == 'dev'))
    if added & {'public_failure', 'public_failure_rule'}:
        rule = manifest.get('public_failure_rule')
        require(isinstance(rule, dict) and rule == cell.get('runtime', {}).get('public_failure_rule')
                and type(rule.get('no_declared_completion_is_failure')) is bool,
                'frozen public failure rule differs from runtime')
        identity = manifest.get('identity_lock', {})
        require(bool(identity), 'frozen failure rules identity required')
        lock = read(dict(uri=identity.get('uri', identity.get('path')), sha256=identity['sha256']))
        refs = [dict(uri=name, sha256=sha) for name, sha in lock['files'].items()
                if PurePath(name).name == 'dev_failure_rules.json']
        require(len(refs) == 1, 'sealed failure rules missing or ambiguous')
        # Offline attribution is separately frozen. It is not the public flag
        # algorithm; that flag uses the manifest's public_failure_rule above.
        rules = read(refs[0])
        require(rules.get('schema') == 'attentionbench.failure-classification.v1',
                'unsupported sealed failure rules')
        expected.update(public_failure(version, sealed, rule))
    # JSON equality preserves boolean versus numeric types in derived metadata.
    require(all(json.dumps(packet[k], sort_keys=True, allow_nan=False) ==
                json.dumps(expected[k], sort_keys=True, allow_nan=False) for k in added), reason)
    return 'with_eval_review'
