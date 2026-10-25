"""C10a synthetic provider identities; no provider or simulator calls."""
import copy
import json
from unittest.mock import patch

import pytest

from benchmarks.attention_harness.memory_provider_evidence import verify_provider
from benchmarks.attention_harness.memory_advisor_evidence import require
from benchmarks.attention_harness.core.dev_store import DevConflict as CONFLICT



@pytest.mark.parametrize('role', ['model', 'advisor'])
@pytest.mark.parametrize('fault', [None, 'parcc', 'missing_lock', 'model', 'request_model',
    'output_model', 'request_source', 'output_source', 'config_source', 'status', 'object',
    'role', 'finish', 'empty', 'total', 'bool', 'negative', 'missing_usage', 'raw_missing',
    'raw_input', 'raw_output', 'cached_bool', 'cached_excess', 'output_usage',
    'receipt_usage', 'output_content', 'cost'])
def test_codex_receipt_requires_frozen_role_and_complete_usage(role, fault):
    locked = {'model': 'controlled_dev_model', 'advisor': 'controlled_advisor_model'}
    model = locked[role]
    manifest = {'selection': {'model_source': 'codex', 'advisor_proxy': {'model': locked['advisor']}},
                'model': {'model': locked['model']}}
    config = {'model_source': 'codex', 'advisor_proxy': {'model': locked['advisor']}}
    usage = {'total_tokens': 7, 'prompt_tokens': 4, 'completion_tokens': 3,
             'codex_usage': {'input_tokens': 4, 'output_tokens': 3, 'cached_input_tokens': 2}}
    request = {'model_source': 'codex', 'model': model}
    output = {'model_source': 'codex', 'model': model, 'usage': copy.deepcopy(usage), 'content': 'answer\n'}
    receipt = {'usage': copy.deepcopy(usage)}
    wire = {'http_status': 200, 'request_id': None, 'body': {'id': None, 'object': 'chat.completion',
        'model': model, 'usage': usage, 'choices': [{'message': {'role': 'assistant', 'content': 'answer\n'}, 'finish_reason': 'stop'}]}}
    actual = {'provider_tokens': 7, 'input_tokens': 4, 'output_tokens': 3}
    body = wire['body']; choice = body['choices'][0]
    if fault == 'parcc':
        manifest['selection']['model_source'] = config['model_source'] = 'parcc'
    if fault == 'missing_lock':
        manifest.pop('model'); manifest['selection'].pop('advisor_proxy'); config.pop('advisor_proxy')
    if fault == 'model': body['model'] = 'unlocked'
    if fault == 'request_model': request['model'] = 'unlocked'
    if fault == 'output_model': output['model'] = 'unlocked'
    if fault == 'request_source': request.pop('model_source')
    if fault == 'output_source': output.pop('model_source')
    if fault == 'config_source': config['model_source'] = 'parcc'
    if fault == 'status': wire['http_status'] = 500
    if fault == 'object': body['object'] = 'unknown'
    if fault == 'role': choice['message']['role'] = 'user'
    if fault == 'finish': choice['finish_reason'] = 'length'
    if fault == 'empty': choice['message']['content'] = output['content'] = '  '
    if fault == 'total': usage['total_tokens'] = 8
    if fault == 'bool': usage['prompt_tokens'] = True
    if fault == 'negative': usage['prompt_tokens'] = -1
    if fault == 'missing_usage': usage.pop('completion_tokens')
    if fault == 'raw_missing': usage.pop('codex_usage')
    if fault == 'raw_input': usage['codex_usage']['input_tokens'] = 5
    if fault == 'raw_output': usage['codex_usage']['output_tokens'] = 4
    if fault == 'cached_bool': usage['codex_usage']['cached_input_tokens'] = True
    if fault == 'cached_excess': usage['codex_usage']['cached_input_tokens'] = 5
    if fault == 'output_usage': output['usage']['codex_usage']['cached_input_tokens'] = 1
    if fault == 'receipt_usage': receipt['usage']['codex_usage']['cached_input_tokens'] = 1
    if fault == 'output_content': output['content'] = 'answer'
    if fault == 'cost': actual['input_tokens'] = 5
    def verify():
        return verify_provider(request, output, wire, receipt, actual, manifest, config, role, require)
    if fault:
        with pytest.raises(CONFLICT): verify()
    else:
        assert verify() == 'codex_exec'
        usage['codex_usage'].pop('cached_input_tokens')
        receipt['usage'] = copy.deepcopy(usage); output['usage'] = copy.deepcopy(usage)
        assert verify() == 'codex_exec'  # Client only guarantees input/output.


@pytest.mark.parametrize('id_at', ['request_id', 'body_id', 'absent'])
@pytest.mark.parametrize('bad_usage', [False, True])
def test_parcc_request_id_rule_unchanged(id_at, bad_usage):
    usage = {'total_tokens': 7, 'prompt_tokens': 4, 'completion_tokens': 3}
    if bad_usage: usage['total_tokens'] = 8
    output = {'content': 'answer', 'usage': usage}
    wire = {'http_status': 200, 'body': {'usage': usage, 'choices': [{'message': {'content': 'answer'}}]}}
    if id_at == 'request_id': wire['request_id'] = 'parcc-id'
    if id_at == 'body_id': wire['body']['id'] = 'parcc-id'
    def verify():
        return verify_provider({}, output, wire, {'usage': usage},
            {'provider_tokens': 7, 'input_tokens': 4, 'output_tokens': 3}, {}, {}, 'model', require)
    if id_at == 'absent' or bad_usage:
        with pytest.raises(CONFLICT): verify()
    else: assert verify() == 'parcc_http'


def codex_writer(write, *, fault=None):
    """Write sealed fixtures in the actual Codex adapter's artifact format."""
    def converted(path, value):
        if isinstance(value, dict):
            if path.name == 'config.json':
                value['model_source'] = 'codex'
                value.setdefault('advisor_proxy', {})['model'] = 'controlled_advisor_model'
            if value.get('schema') == 'attentionbench.dev-experiment.v3':
                value.setdefault('selection', {}).update(model_source='codex', advisor_proxy={'model': 'controlled_advisor_model'})
                value['model'] = {'model': 'controlled_dev_model'}
            role = 'advisor' if path.name.startswith('advisor-') else 'model'
            model = 'controlled_advisor_model' if role == 'advisor' else 'controlled_dev_model'
            if path.name in ('advisor-input.json', 'model-input.json', 'advisor-output.json', 'model-output.json') and ('messages' in value or 'content' in value):
                value.update(model_source='codex', model=model)
            if 'usage' in value and value['usage']:
                value['usage']['codex_usage'] = {'input_tokens': 4, 'output_tokens': 3, 'cached_input_tokens': 2}
            if 'http_status' in value:
                value['request_id'] = None
                value['body'].update(id=None, model=model, object='chat.completion')
                value['body']['choices'][0]['message']['role'] = 'assistant'
                value['body']['choices'][0]['finish_reason'] = 'stop'
                if fault and role == 'advisor':
                    if fault == 'model': value['body']['model'] = 'controlled_dev_model'
                    if fault == 'usage': value['body']['usage'] = dict(value['body']['usage'], total_tokens=8)
            if value.get('schema_version') == 'attentionbench.advisor-proxy.v3':
                value.update(model_source='codex', model='controlled_advisor_model')
                if fault == 'model': value['model'] = 'unlocked'
                if fault == 'usage': value['usage'] = dict(value['usage'], total_tokens=8)
        return write(path, value)
    return converted

@pytest.mark.parametrize('planner', [False, True])
@pytest.mark.parametrize('fault', [None, 'model', 'usage'])
def test_codex_origin_and_export(tmp_path, planner, fault):
    from benchmarks.attention_harness.tests.test_memory_advisor_source_c10 import make_fake
    from benchmarks.attention_harness.tests.autovalidate_fakes import Fixture
    from benchmarks.attention_harness.tests.test_memory_origin_c9 import synthetic_export, snapshot
    from benchmarks.attention_harness import memory_origin_evidence as origin
    from benchmarks.attention_harness.dev_v2_memory_import import prepare
    with patch.object(Fixture, 'write', staticmethod(codex_writer(Fixture.write, fault=fault))):
        fake = make_fake(tmp_path, planner=planner)
    before = snapshot(fake.f.root)
    if fault:
        with pytest.raises(CONFLICT): origin.locate(fake.provision, {'memory_id': fake.mid})
    else:
        found = origin.locate(fake.provision, {'memory_id': fake.mid})
        assert found['provider_identity'] == 'codex_exec'
        assert found['provider_identities'] == {'model': 'codex_exec', 'advisor': 'codex_exec'}
        with synthetic_export(fake):
            ctx, sources = origin.export_context(fake.provision, found)
            prepare(found['store'], 'failed', 2, ctx['namespace'], fake.f.root/'export', fake.f.root,
                    source=origin.source_prerequisites(found), source_catalogs=sources)
    assert fake.replays == 0 and not fake.starts
    if fault: assert snapshot(fake.f.root) == before
