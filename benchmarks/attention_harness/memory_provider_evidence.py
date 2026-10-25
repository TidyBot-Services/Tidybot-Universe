"""Frozen provider identity and usage checks, shared by all evidence roles.

Kept identical on executor and authority sides; neither trusts request metadata
as authorization for the Codex request-ID exception.
"""
import json


def frozen_provider(manifest, config, role, require):
    selection = manifest.get('selection', {})
    source = selection.get('model_source', config.get('model_source', 'parcc'))
    require(source in {'parcc', 'codex'} and all(
        value.get('model_source', source) == source for value in (manifest, config)),
        'Frozen provider source differs')
    if source != 'codex':
        return source, None
    lock = manifest.get('model', {})
    if role == 'advisor':
        selected = selection.get('advisor_proxy')
        configured = config.get('advisor_proxy')
        require(all(isinstance(value, dict) and value.get('model_source', source) == source
                    for value in (selected, configured) if value is not None),
                'Frozen Advisor provider source differs')
        if selected is not None and configured is not None:
            require(selected.get('model') == configured.get('model'), 'Frozen Advisor model differs')
        lock = selected or configured or lock
    require(isinstance(lock, dict) and isinstance(lock.get('model'), str) and lock['model'].strip()
            and lock.get('model_source', source) == source, 'Locked Codex model required')
    return source, lock['model']


def verify_usage(usage, require, *, codex=False):
    require(isinstance(usage, dict) and all(type(usage.get(k)) is int and usage[k] >= 0
            for k in ('total_tokens', 'prompt_tokens', 'completion_tokens'))
            and usage['total_tokens'] == usage['prompt_tokens'] + usage['completion_tokens'],
            'Complete provider usage required')
    if codex:
        raw = usage.get('codex_usage')
        require(isinstance(raw, dict) and all(type(raw.get(k)) is int and raw[k] >= 0
                for k in ('input_tokens', 'output_tokens'))
                and raw['input_tokens'] == usage['prompt_tokens']
                and raw['output_tokens'] == usage['completion_tokens']
                and ('cached_input_tokens' not in raw or
                     (type(raw['cached_input_tokens']) is int
                      and 0 <= raw['cached_input_tokens'] <= raw['input_tokens'])),
                'Codex usage differs from provider usage')


def verify_codex_record(record, model, require):
    require(record.get('model_source') == 'codex' and record.get('model') == model,
            'Codex provider model/source differs from frozen role')
    verify_usage(record.get('usage'), require, codex=True)


def verify_provider(request, output, wire, receipt, actual, manifest, config, role, require):
    source, model = frozen_provider(manifest, config, role, require)
    body = wire.get('body', {})
    rid = wire.get('request_id') or body.get('id')
    require(wire.get('http_status') == 200 and (source == 'codex' or
            (isinstance(rid, str) and rid)), 'Successful original provider response/request ID required')
    usage = body.get('usage')
    verify_usage(usage, require, codex=source == 'codex')
    if source == 'codex':
        require(request.get('model_source') == source and request.get('model') == model
                and body.get('model') == model and body.get('object') == 'chat.completion',
                'Codex provider model/source differs from frozen role')
        verify_codex_record(output, model, require)
    choices = body.get('choices')
    require(isinstance(choices, list) and len(choices) > 0 and isinstance(choices[0], dict)
            and isinstance(choices[0].get('message'), dict), 'Provider answer required')
    if source == 'codex':
        require(len(choices) == 1 and choices[0].get('finish_reason') == 'stop'
                and choices[0]['message'].get('role') == 'assistant', 'Codex answer identity incomplete')
    content = choices[0]['message'].get('content')
    require(isinstance(content, str) and content.strip(), 'Nonempty provider answer required')
    # Canonical JSON distinguishes integers from booleans/floats even in extra
    # usage fields. String equality preserves every byte of the decoded answer.
    canonical = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    require(canonical(receipt.get('usage')) == canonical(output.get('usage')) == canonical(usage)
            and output.get('content') == content, 'Provider output/usage differs from original wire')
    require(all(actual.get(k) == usage[v] for k, v in (
            ('provider_tokens', 'total_tokens'), ('input_tokens', 'prompt_tokens'),
            ('output_tokens', 'completion_tokens'))), 'Ledger provider costs disagree')
    return 'codex_exec' if source == 'codex' else 'parcc_http'
