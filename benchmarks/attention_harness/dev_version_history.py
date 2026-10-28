"""Bounded, public-only recollection for newly opted-in Dev runs."""
import hashlib
import json
from collections import Counter

from .dev_help import public_screen

MAX_ENTRIES = 12
MAX_BYTES = 16000


def enabled(config):
    return config.get('budget_tier') == 'dev' and config.get('dev_version_history') is True


def encoded(value):
    # Match model_messages' wire serialization, including UTF-8 and spaces.
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode()


def bounded(entries, omitted=0):
    result = {'entries': list(entries), 'omitted': omitted}
    while len(result['entries']) > MAX_ENTRIES or len(encoded(result)) > MAX_BYTES:
        result['entries'].pop(0)
        result['omitted'] += 1
    public_screen(result)
    return result


def summarize(version, source, feedback, review=None, help_content=None):
    """Copy an allowlist, never a trace, observation, receipt, or source body."""
    from .eval_agent import visible_review
    trace = feedback.get('action_trace', [])
    counts = Counter((row.get('source'), row.get('operation')) for row in trace)
    moves = [row.get('arguments', {}) for row in trace
             if row.get('source') is None and row.get('operation') == 'move_delta'
             and row.get('status') == 'completed' and not row.get('error')]
    failed = next((row for row in trace if row.get('status') == 'failed' or row.get('error')), None)
    opinion = visible_review(review, dev=True) if review is not None else None
    if opinion is not None:
        opinion['reason'] = opinion['reason'][:400]
    entry = {
        'version': version,
        'execution_status': feedback.get('execution_status'),
        'public_error': (feedback.get('public_error') or '')[:400],
        'failed_operation': ({key: failed.get(key) for key in ('source', 'operation', 'arguments')}
                             if failed is not None else None),
        'operation_counts': {
            'by_operation': [{'source': src, 'operation': op, 'count': count}
                             for (src, op), count in sorted(counts.items(), key=lambda item: encoded(item[0]))],
            'base_moves': {'count': len(moves), **{axis: sum(args.get(axis, 0) for args in moves)
                                                 for axis in ('dx', 'dy', 'dyaw')}}},
        'eval': opinion,
        'help': help_content[:400] if isinstance(help_content, str) else None,
        'source_sha256': hashlib.sha256(source).hexdigest(),
        'source_bytes': len(source),
    }
    public_screen(entry)
    return entry


def build(cell, *, trace_visible=True):
    """Use settled public evidence and help actually present in generation input.

    The immediately preceding version remains: its compact summary, hash and
    help are not an exact duplicate of public_feedback. Older feedback is read
    from the next generation's request where available, preserving its public
    projection (including trace visibility and omitted events).
    """
    from .dev_v2_loop import checked, eval_ref, job_for

    def read(ref):
        return json.loads(checked(ref).read_bytes())

    versions = sorted(cell['versions'], key=lambda row: row['version'])
    executed = [v for v in versions if (job_for(cell, v['version'], 'execute') or {}).get('state') == 'settled']
    packets = {}

    def packet(version):
        number = version['version']
        if number not in packets:
            ref = version.get('model_input')
            packets[number] = json.loads(read(ref)['messages'][-1]['content']) if ref else {}
        return packets[number]

    entries = []
    for version in executed[-MAX_ENTRIES:]:
        number = version['version']
        feedback = read(job_for(cell, number, 'execute')['receipt']['public_feedback'])
        following = next((v for v in versions if v['version'] == number + 1), None)
        shown = packet(following).get('public_feedback') if following else None
        if shown is not None:
            feedback = shown
        if not trace_visible:
            feedback = {k: v for k, v in feedback.items() if k != 'action_trace'}
        review = None
        job = job_for(cell, number, 'eval')
        if job and job['state'] == 'settled' and eval_ref(job.get('receipt', {})):
            record = read(eval_ref(job['receipt']))
            if record.get('status') == 'completed' and type(record.get('passed')) is bool:
                # A historical projection may have omitted the opinion entirely.
                review = shown.get('eval_review') if shown is not None else record
        help_value = packet(version).get('attention_control', {}).get('help') or {}
        help_content = help_value.get('content') if help_value.get('provider') != 'cache' and not help_value.get('rejected') else None
        entries.append(summarize(number, checked(version['source']).read_bytes(), feedback, review, help_content))
    return bounded(entries, max(0, len(executed) - MAX_ENTRIES))
