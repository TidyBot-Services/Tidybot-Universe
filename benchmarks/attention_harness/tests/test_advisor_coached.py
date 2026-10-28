import hashlib

import pytest

from benchmarks.attention_harness import dev_advisor_proxy as advisor
from benchmarks.attention_harness.dev_configuration import independent_eval_advisor
from benchmarks.attention_harness.eval_agent import default_config as eval_default


def test_coached_variant_is_codex_only_and_labelled():
    config = advisor.normalize({'enabled': True, 'prompt_version': advisor.COACHED_PROMPT_VERSION}, 'codex')
    assert config['prompt_version'] == advisor.COACHED_PROMPT_VERSION
    assert config['prompt_sha256'] == hashlib.sha256(advisor.COACHED_SYSTEM.encode()).hexdigest()
    assert config['cache_namespace'] == 'advisor-coached'
    assert advisor.normalize(config, 'codex') == config
    with pytest.raises(ValueError):
        advisor.normalize({'enabled': True, 'prompt_version': advisor.COACHED_PROMPT_VERSION}, 'parcc')
    with pytest.raises(ValueError):
        advisor.normalize({**config, 'prompt_sha256': '0' * 64}, 'codex')
    independent_eval_advisor(eval_default(True, 'codex'), config)


def test_default_advisor_unchanged():
    config = advisor.normalize({'enabled': True}, 'codex')
    assert config['prompt_version'] == advisor.PROMPT_VERSION
    assert config['prompt_sha256'] == hashlib.sha256(advisor.SYSTEM.encode()).hexdigest()



def test_coached_prompt_reaches_the_model(monkeypatch):
    seen = {}

    class Response:
        content = 'Move the base first.'
        usage = {}

    def call(client, timeout, **kwargs):
        seen['system'] = kwargs['messages'][0]['content']
        return Response()

    monkeypatch.setattr(advisor.vision, '_call', call)
    monkeypatch.setattr(advisor, 'public_screen', lambda value: None)
    import benchmarks.attention_harness.dev_request_view as view
    monkeypatch.setattr(view, 'human_seconds', lambda evidence, answer: {})
    frame = {'view': 'v', 'sha256': 'a' * 64, 'filename': 'f.jpg', 'data_url': 'data:image/jpeg;base64,AA=='}
    config = advisor.normalize({'enabled': True, 'prompt_version': advisor.COACHED_PROMPT_VERSION}, 'codex')
    record = advisor.ask({"trace": [], "frames": [frame]}, config, object())
    assert seen['system'] == advisor.COACHED_SYSTEM
    assert record['prompt_version'] == advisor.COACHED_PROMPT_VERSION
