import json
import urllib.request

import pytest

from benchmarks.attention_harness import dev_agent_config
from benchmarks.attention_harness.dev_agent_parcc_bridge import IMAGE_NOTICE, BridgeError, ParccBridge, to_chat
from benchmarks.attention_harness.dev_agent_session import CodexAgentSession

SECRET = 'sk-test-SECRET-never-logged'


def test_to_chat_maps_items_tools_and_images():
    chat = to_chat(dict(model='parcc/GLM', reasoning=dict(effort='high'), tool_choice='auto', parallel_tool_calls=True,
        input=[dict(type='message', role='developer', content=[dict(type='input_text', text='rules')]),
               dict(type='message', role='user', content=[dict(type='input_text', text='go')]),
               dict(type='reasoning', summary=[], encrypted_content=None),
               dict(type='message', role='assistant', content=[dict(type='output_text', text='running')]),
               dict(type='function_call', name='exec_command', arguments='{"cmd":"ls"}', call_id='c1'),
               dict(type='function_call', name='view_image', arguments='{"path":"f.png"}', call_id='c2'),
               dict(type='function_call_output', call_id='c1', output='a.py'),
               dict(type='function_call_output', call_id='c2', output=[dict(type='input_image', image_url='data:x')])],
        tools=[dict(type='function', name='exec_command', description='d', parameters=dict(type='object', properties={}))]))
    m = chat['messages']
    assert [x['role'] for x in m] == ['system', 'user', 'assistant', 'tool', 'tool']
    assert m[2]['content'] == 'running' and [c['id'] for c in m[2]['tool_calls']] == ['c1', 'c2']
    assert m[4]['content'] == IMAGE_NOTICE
    assert chat['tools'][0]['function']['name'] == 'exec_command' and chat['reasoning_effort'] == 'high'


@pytest.mark.parametrize('body', [dict(model='parcc/GLM', input=[dict(type='local_shell_call')]),
                                  dict(model='parcc/GLM', input=[], tools=[dict(type='web_search')])])
def test_to_chat_fails_closed(body):
    with pytest.raises(BridgeError): to_chat(body)


def _post(bridge, body):
    request = urllib.request.Request(bridge.base_url + '/responses', data=json.dumps(body).encode(), method='POST',
                                     headers={'Content-Type': 'application/json', 'Authorization': 'Bearer codex-side'})
    with urllib.request.urlopen(request, timeout=10) as response:
        return [json.loads(line[5:]) for line in response.read().decode().splitlines() if line.startswith('data:')]


def test_bridge_streams_responses_events_and_logs_without_key(tmp_path):
    seen = []
    def upstream(chat, key):
        seen.append((chat, key))
        yield dict(_request_id='req-1')
        yield dict(model='parcc/GLM', choices=[dict(delta=dict(reasoning_content='think'))])
        yield dict(model='parcc/GLM', choices=[dict(delta=dict(content='ok'))])
        yield dict(model='parcc/GLM', choices=[dict(delta=dict(tool_calls=[dict(index=0, id='call_9', function=dict(name='exec_command', arguments='{"cmd":'))]))])
        yield dict(model='parcc/GLM', choices=[dict(delta=dict(tool_calls=[dict(index=0, function=dict(arguments='"ls"}'))]), finish_reason='tool_calls')])
        yield dict(model='parcc/GLM', choices=[], usage=dict(prompt_tokens=10, completion_tokens=5, completion_tokens_details=dict(reasoning_tokens=2)))
    bridge = ParccBridge(tmp_path, key=SECRET, upstream=upstream)
    try:
        assert bridge.server.server_address[0] == '127.0.0.1'
        events = _post(bridge, dict(model='parcc/GLM', input=[dict(type='message', role='user', content='hi')]))
    finally:
        bridge.close()
    assert seen[0][1] == SECRET
    done = [e['item'] for e in events if e['type'] == 'response.output_item.done']
    assert [i['type'] for i in done] == ['reasoning', 'message', 'function_call']
    assert done[2]['call_id'] == 'call_9' and json.loads(done[2]['arguments']) == dict(cmd='ls')
    usage = events[-1]['response']['usage']
    assert events[-1]['type'] == 'response.completed' and usage['input_tokens'] == 10 and usage['total_tokens'] == 15
    log = (tmp_path / 'calls.jsonl').read_text()
    row = json.loads(log)
    assert row['upstream_models'] == ['parcc/GLM'] and row['upstream_request_id'] == 'req-1'
    assert SECRET not in log and 'codex-side' not in log and 'Authorization' not in log


def test_bridge_refuses_other_models(tmp_path):
    bridge = ParccBridge(tmp_path, key=SECRET, upstream=lambda chat, key: iter(()))
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(bridge, dict(model='gpt-6.1-sol', input=[]))
    finally:
        bridge.close()
    assert exc.value.code == 400


def test_session_routes_only_glm_through_bridge(tmp_path, monkeypatch):
    monkeypatch.setenv('PARCC_API_KEY', SECRET)
    factory = lambda ws, prompt: ['codex', '-a', 'never', 'exec', '-C', str(ws), '--json', '-m', 'gpt-6-astra',
                                  '-c', 'model_reasoning_effort="low"', prompt]
    (tmp_path / 'ws').mkdir(); (tmp_path / 'p').mkdir()
    sol = CodexAgentSession(tmp_path / 'ws', tmp_path / 'p', model='gpt-6.1-sol', command_factory=factory)
    assert 'model_provider="parcc"' not in sol.command(False) and sol.bridge is None
    glm = CodexAgentSession(tmp_path / 'ws', tmp_path / 'p', model='parcc/GLM', command_factory=factory)
    try:
        command = glm.command(False)
        assert command[command.index('-m') + 1] == 'parcc/GLM' and 'model_provider="parcc"' in command
        assert glm.bridge.base_url in ' '.join(command) and SECRET not in ' '.join(command)
        glm.thread_id = 't1'
        assert glm.command(True).count('model_provider="parcc"') == 1
    finally:
        glm.bridge.close()


def test_config_allows_glm_for_dev_only():
    base = dict(dev_mode='agent', tasks=['t'], seeds=[102], policies=['p'], mode='interactive')
    assert dev_agent_config.normalize(dict(base, dev_model='parcc/GLM'))['dev_model'] == 'parcc/GLM'
    with pytest.raises(ValueError, match='unsupported_agent_model'):
        dev_agent_config.normalize(dict(base, eval_model='parcc/GLM'))
    with pytest.raises(ValueError, match='unsupported_agent_model'):
        dev_agent_config.normalize(dict(base, advisor_model='parcc/GLM'))
