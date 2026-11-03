"""A strict stand-in for DeepSeek's chat endpoint, used as the ChatBridge upstream.

Each rule carries where it comes from:
  OBSERVED  seen from the real API on 2026-11-04 (byok_real_check / the Run UI)
  DOCS      stated in api-docs.deepseek.com/guides/thinking_mode (not yet reproduced against the real API)
  SHAPE     the OpenAI chat contract every provider is expected to follow
"""
import io
import json
import re
import urllib.error
from jsonschema import Draft202012Validator


def refusal(url, message):
    body = json.dumps(dict(error=dict(message=message, type='invalid_request_error', param=None, code='invalid_request_error')))
    return urllib.error.HTTPError(url, 400, 'Bad Request', {}, io.BytesIO(body.encode()))


def validate(chat, *, vision_models=('deepseek-v4-pro',), url='https://api.deepseek.com/v1/chat/completions'):
    """Raises HTTPError(400) the way the real API does; returns whether thinking is on."""
    thinking = (chat.get('thinking') or {}).get('type') != 'disabled'  # DOCS: enabled by default
    messages, tools = chat.get('messages') or [], chat.get('tools') or []
    if thinking and isinstance(chat.get('tool_choice'), dict):  # OBSERVED
        raise refusal(url, 'Thinking mode does not support this tool_choice')
    for index, message in enumerate(messages):
        content = message.get('content')
        parts = content if isinstance(content, list) else []
        if any(p.get('type') == 'image_url' for p in parts) and chat['model'] not in vision_models:  # OBSERVED
            raise refusal(url, '.messages[%d].image[0]: You have uploaded an unsupported image. Please make sure your image is valid '
                               'and has one of the following formats: webp, png, jpeg, and gif.' % index)
        if message['role'] not in ('system', 'user', 'assistant', 'tool'):  # SHAPE
            raise refusal(url, 'Invalid role: %s' % message['role'])
        if content is None and not (message['role'] == 'assistant' and message.get('tool_calls')):  # SHAPE
            raise refusal(url, 'messages[%d]: content is required' % index)
        if message['role'] == 'tool':  # SHAPE: a tool message answers a tool call of the assistant message right before it
            before = index - 1
            while before >= 0 and messages[before]['role'] == 'tool': before -= 1
            ids = {c['id'] for c in (messages[before].get('tool_calls') or [])} if before >= 0 else set()
            if message.get('tool_call_id') not in ids:
                raise refusal(url, 'messages[%d]: tool message must follow an assistant tool_calls message with that id' % index)
        if message['role'] == 'assistant' and message.get('tool_calls'):
            answered = set()
            for later in messages[index + 1:]:
                if later['role'] != 'tool': break
                answered.add(later.get('tool_call_id'))
            if {c['id'] for c in message['tool_calls']} - answered:
                raise refusal(url, 'messages[%d]: tool_calls without a matching tool message' % index)
        if thinking and tools and message['role'] == 'assistant' and 'reasoning_content' not in message:  # DOCS
            raise refusal(url, 'The reasoning_content in the thinking mode must be passed back to the API.')
    for tool in tools:  # SHAPE
        function = tool.get('function') or {}
        if tool.get('type') != 'function' or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', function.get('name', '')):
            raise refusal(url, 'Invalid tool definition: %r' % function.get('name'))
        parameters = function.get('parameters')
        if not isinstance(parameters, dict) or parameters.get('type') != 'object':
            raise refusal(url, 'tools.function.parameters must be a JSON schema object')
        try: Draft202012Validator.check_schema(parameters)
        except Exception as exc: raise refusal(url, 'Invalid tool schema: %s' % exc) from None
    if not isinstance(chat.get('max_tokens'), int) or chat['max_tokens'] < 1:  # SHAPE
        raise refusal(url, 'max_tokens must be a positive integer')
    return thinking


class StrictDeepSeek:
    """ChatBridge upstream: validates every request, then answers with reasoning (when thinking is on) and a short text.
    `finish` and `partial_tool` let a test make the reply stop at the token limit."""
    def __init__(self, *, vision_models=('deepseek-v4-pro',), finish='stop', partial_tool=None):
        self.bodies, self.vision_models, self.finish, self.partial_tool = [], vision_models, finish, partial_tool

    def __call__(self, chat, key, **kwargs):
        self.bodies.append(json.loads(json.dumps(chat)))
        thinking = validate(chat, vision_models=self.vision_models, url=kwargs.get('url', 'https://api.deepseek.com/v1/chat/completions'))
        yield dict(_request_id='strict-%d' % len(self.bodies))
        if thinking:
            yield dict(model=chat['model'], choices=[dict(delta=dict(reasoning_content='Let me think about it. '))])
        if self.partial_tool:
            yield dict(model=chat['model'], choices=[dict(delta=dict(tool_calls=[dict(index=0, id='call_cut', function=dict(
                name=self.partial_tool[0], arguments=self.partial_tool[1]))]), finish_reason=self.finish)])
        else:
            yield dict(model=chat['model'], choices=[dict(delta=dict(content='ok'), finish_reason=self.finish)],
                       usage=dict(prompt_tokens=10, completion_tokens=2, total_tokens=12))
