import json
import sys
import urllib.error
import urllib.request

import pytest

from benchmarks.attention_harness import dev_agent_config
from benchmarks.attention_harness.dev_agent_parcc_bridge import (IMAGE_NOTICE, TOOL_FAILURE_LIMIT, BridgeError, ParccBridge,
                                                                 failed_tool_streak, to_chat)
from benchmarks.attention_harness.dev_agent_session import CodexAgentSession, SessionError

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


def _failed_calls(count, last_output='exec_command failed: CreateProcess { message: "No such file" }'):
    calls = []
    for index in range(count):
        calls += [dict(type='function_call', name='exec_command', arguments='{"cmd":"pwd","workdir":"/gone"}', call_id=f'c{index}'),
                  dict(type='function_call_output', call_id=f'c{index}', output=last_output)]
    return calls


def test_streak_counts_only_calls_that_never_started():
    assert failed_tool_streak(_failed_calls(3)) == 3
    assert failed_tool_streak(_failed_calls(3) + [dict(type='message', role='assistant', content='retrying')]) == 3
    assert failed_tool_streak(_failed_calls(3) + _failed_calls(1, 'exit code 1')) == 0
    assert failed_tool_streak(_failed_calls(3) + [dict(type='message', role='user', content='Continue.')]) == 0
    assert failed_tool_streak(None) == 0


def test_bridge_ends_a_turn_stuck_on_a_call_that_cannot_start(tmp_path):
    seen = []
    def upstream(chat, key):
        seen.append(chat)
        yield dict(_request_id='req-1')
        yield dict(model='parcc/GLM', choices=[dict(delta=dict(content='ok'), finish_reason='stop')])
    bridge = ParccBridge(tmp_path, key=SECRET, upstream=upstream)
    try:
        assert _post(bridge, dict(model='parcc/GLM', input=_failed_calls(TOOL_FAILURE_LIMIT - 1)))
        with pytest.raises(urllib.error.HTTPError) as refused:
            _post(bridge, dict(model='parcc/GLM', input=_failed_calls(TOOL_FAILURE_LIMIT)))
    finally:
        bridge.close()
    assert refused.value.code == 400 and 'workdir' in refused.value.read().decode()
    assert len(seen) == 1


def test_a_failed_turn_tells_the_next_turn_why(tmp_path):
    # A real short process, never a fake one: cancel() calls os.killpg, and killpg(1) is kill(-1), which hits every process.
    events = [dict(type='thread.started', thread_id='t1'), dict(type='turn.failed', error=dict(message='The last 25 tool calls failed'))]
    script = 'import sys\nsys.stdin.read()\nprint(%r)\nsys.exit(1)' % '\n'.join(json.dumps(event) for event in events)
    (tmp_path / 'ws').mkdir(); (tmp_path / 'private').mkdir()
    session = CodexAgentSession(tmp_path / 'ws', tmp_path / 'private', command_factory=lambda ws, prompt: [
        sys.executable, '-c', script, '-m', 'x', '-c', 'model_reasoning_effort="low"'])
    with pytest.raises(SessionError, match='turn_failed: The last 25 tool calls failed'):
        list(session.start('go'))


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
