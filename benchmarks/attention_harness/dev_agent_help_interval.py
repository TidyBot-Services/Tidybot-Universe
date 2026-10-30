"""Execution help frozen into a formal interval for the D7.1 v1 verifier.

The compatibility job is an attested projection, never a new ledger job. Keep
its original execution receipt so the offline importer can check it again.
"""
import json
import re

from .memory_provisional import NotStaged

V2 = 'agent-policy-v2'


def delivered(answer):
    return (answer.get('provider') == 'advisor' and answer.get('delivered') is True
            and answer.get('status') == 'delivered' and not answer.get('cache_hit')
            and not answer.get('rejected'))


def interval_help(jobs, version):
    """Use reservation order at accepted submits, including equal clock ticks.

    Reopened help uses its new reservation timestamp. A submit's own proactive
    help belongs to the following interval, even before that submit settles.
    """
    if version < 2:
        return []
    boundaries = []
    for number in (version - 1, version):
        matches = [(i, j) for i, j in enumerate(jobs) if j['kind'] == 'request'
                   and j['state'] == 'settled' and j.get('receipt', {}).get('op') == 'submit'
                   and j['receipt'].get('version_id') == f'v{number:04d}'
                   and j['receipt'].get('execution_id')
                   and j['receipt'].get('execution_dispatched') is not False
                   and j['receipt'].get('result', {}).get('outcome', {}).get('status') != 'invalid_submission']
        if len(matches) != 1:
            raise NotStaged('formal_interval_missing_or_ambiguous')
        i, job = matches[0]
        boundaries.append((job['reserved_at'], i))
    return [j for i, j in enumerate(jobs)
            if j['kind'] == 'help' and j['state'] == 'settled'
            and re.fullmatch(r'help:e\d+', j['id']) and delivered(j.get('receipt', {}))
            and boundaries[0] < (j['reserved_at'], i) < boundaries[1]
            and boundaries[0][0] <= j.get('claimed_at', float('-inf'))
                <= j.get('settled_at', float('inf')) <= boundaries[1][0]]


def answer_projection(raw, version):
    eid = raw.get('execution_id')
    if (not isinstance(eid, str) or not re.fullmatch(r'e\d+', eid)
            or raw.get('help_id') != 'help:' + eid or not delivered(raw)):
        raise NotStaged('invalid_execution_help')
    return dict(raw, version_id=f'v{version - 1:04d}', help_execution_id=eid,
                execution_help=raw)


def job_projection(raw, version):
    if raw['id'] != raw['receipt'].get('help_id'):
        raise NotStaged('execution_help_identity_drift')
    return dict(raw, id=f'help:v{version - 1:04d}',
                receipt=answer_projection(raw['receipt'], version), execution_job=raw)


def delivery_projection(raw, answer):
    if (raw.get('op') != 'ask_help' or raw.get('status') != 'completed'
            or raw.get('result') != answer['execution_help']):
        raise NotStaged('help_delivery_missing_or_ambiguous')
    return dict(raw, result=answer, execution_delivery=raw)


def freeze_help(workspace, jobs, version):
    matches = interval_help(jobs, version)
    if len(matches) != 1:
        # Staging records the explicit ambiguity; D7 still exports the version.
        return dict(status='not_requested', agent_policy_version=V2,
                    help_execution_ids=[j['receipt']['execution_id'] for j in matches])
    job = matches[0]
    eid = job['receipt']['execution_id']
    from .dev_agent_workspace import read_beneath
    try:
        raw = json.loads(read_beneath(workspace.canonical, f'executions/{eid}/help.json'))
    except FileNotFoundError:
        # The settled receipt is the same public execution record, including
        # the execution reference, even if publishing was interrupted.
        raw = job['receipt']
    if raw != job['receipt']:
        raise NotStaged('execution_help_receipt_drift')
    return answer_projection(raw, version)


def check_projection(sealed, jobs, version, answer, delivery):
    """Offline attestation: compare originals and rederive every changed field."""
    matches = interval_help(jobs, version)
    if len(matches) != 1 or sealed != job_projection(matches[0], version):
        raise NotStaged('execution_help_ledger_drift')
    if answer != answer_projection(matches[0]['receipt'], version):
        raise NotStaged('execution_help_receipt_drift')
    expected = (sealed['receipt'] if answer.get('system_injected') is True else
                delivery_projection(delivery.get('execution_delivery', {}), answer))
    if delivery != expected:
        raise NotStaged('execution_help_delivery_drift')
