"""Loopback Responses->Chat bridge so the agent Dev can run on PARCC GLM (10-31, line GL).

Codex CLI 0.165 only speaks the Responses wire API; PARCC serves parcc/GLM on
/v1/chat/completions only (/v1/responses is 404). The bridge keeps the whole Codex
harness (tools, sandbox, isolation, resume) and swaps only the model provider.
It binds 127.0.0.1 on an ephemeral port, reads the key from the existing key file
or environment, and never logs the key or request headers. One row per upstream
call goes to calls.jsonl with the model PARCC actually reported.
GLM is text-only: image parts become a text notice (Dev cannot see frames).
"""
import hmac
import http.server
import itertools
import json
import os
from pathlib import Path
import threading
import time
import urllib.error
import urllib.request
import uuid

from .dev_agent_secret import Secret, scrub
from .dev_agent_provider import presets

PARCC_CHAT_URL = presets()['parcc'].chat_url
PARCC_DEV_MODELS = frozenset({'parcc/GLM'})
IMAGE_NOTICE = '[image omitted: this model accepts text only and cannot view images]'
MAX_TOKENS = 32768


class BridgeError(ValueError): pass


def parcc_key():
    key = os.getenv('PARCC_API_KEY') or os.getenv('LITELLM_KEY')
    path = Path.home() / '.litellm-key'
    if not key and path.is_file():
        key = path.read_text().strip()
    if not key: raise BridgeError('parcc_key_missing')
    return key


def _text(parts):
    if isinstance(parts, str): return parts
    out = []
    for part in parts or []:
        kind = part.get('type')
        if kind in ('input_text', 'output_text', 'text', 'summary_text'): out.append(part.get('text', ''))
        elif kind in ('input_image', 'image_url'): out.append(IMAGE_NOTICE)
        else: raise BridgeError('unsupported_content_part:' + str(kind))
    return '\n'.join(out)


def _content(parts, vision):
    if not vision or isinstance(parts, str) or parts is None:return _text(parts)
    result=[]
    for part in parts:
        kind=part.get('type')
        if kind in ('input_text','output_text','text','summary_text'):
            result.append(dict(type='text',text=part.get('text','')))
        elif kind in ('input_image','image_url'):
            import base64
            url=part.get('image_url')
            if isinstance(url,dict):url=url.get('url')
            try:
                header,data=url.split(',',1)
                if header not in ('data:image/png;base64','data:image/jpeg;base64') or not data:raise ValueError()
                base64.b64decode(data,validate=True)
            except (AttributeError,TypeError,ValueError):raise BridgeError('unsupported_image_source') from None
            result.append(dict(type='image_url',image_url=dict(url=url)))
        else:raise BridgeError('unsupported_content_part:'+str(kind))
    if not any(p['type']=='image_url' for p in result):return '\n'.join(p['text'] for p in result)
    return result


def _response_format(body, chat, provider):
    text=body.get('text')
    if not isinstance(text,dict) or 'format' not in text:return
    fmt=text['format']
    if (not isinstance(fmt,dict) or fmt.get('type')!='json_schema'
            or set(fmt)-{'type','name','schema','strict'} or not isinstance(fmt.get('name'),str)
            or not fmt['name'] or not isinstance(fmt.get('schema'),dict)):
        raise BridgeError('unsupported_text_format')
    mode=provider.response_format if provider is not None else 'json_schema'
    if mode=='json_schema':
        chat['response_format']=dict(type='json_schema',json_schema=dict(
            name=fmt['name'],schema=fmt['schema'],strict=bool(fmt.get('strict',False))))
    elif mode in ('json_object','prompt'):
        if mode=='json_object':chat['response_format']=dict(type='json_object')
        instruction=('Return JSON matching this schema: ' if mode=='json_object' else
            'When you are done, your final message must be only one JSON object matching this schema, with no Markdown and no other text: ')+json.dumps(fmt['schema'],separators=(',',':'))
        system=next((m for m in chat['messages'] if m['role']=='system'),None)
        if system is None:chat['messages'].insert(0,dict(role='system',content=instruction))
        elif isinstance(system['content'],str):system['content']+='\n'+instruction
        else:system['content'].append(dict(type='text',text=instruction))
    else:raise BridgeError('unsupported_text_format')


def to_chat(body, provider=None, *, vision=False):
    """Responses request body -> chat/completions body. Fail closed on unknown items/tools."""
    messages = []
    if body.get('instructions'): messages.append(dict(role='system', content=body['instructions']))
    pending=[];call_order=[]
    def flush_images():
        if pending:
            ordered=sorted(pending,key=lambda pair:call_order.index(pair[0]) if pair[0] in call_order else len(call_order))
            messages.append(dict(role='user',content=[part for _,parts in ordered for part in parts]))
            pending.clear()
    for item in body.get('input', []):
        kind = item.get('type', 'message')
        if kind not in ('function_call_output','reasoning'):flush_images()
        if kind == 'message':
            role = item.get('role')
            if role not in ('system', 'developer', 'user', 'assistant'): raise BridgeError('unsupported_role:' + str(role))
            messages.append(dict(role='system' if role == 'developer' else role, content=_content(item.get('content'),vision)))
        elif kind == 'function_call':
            call = dict(id=item['call_id'], type='function', function=dict(name=item['name'], arguments=item.get('arguments') or '{}'))
            last = messages[-1] if messages else None
            if last and last['role'] == 'assistant':
                last.setdefault('tool_calls', []).append(call)
            else:
                messages.append(dict(role='assistant', content=None, tool_calls=[call]))
            call_order=[c['id'] for c in messages[-1]['tool_calls']]
        elif kind == 'function_call_output':
            content=_content(item.get('output'),vision)
            if isinstance(content,list):
                pending.append((item['call_id'],[p for p in content if p['type']=='image_url']))
                content='\n'.join(p['text'] for p in content if p['type']=='text') or '[image attached in the next message]'
            messages.append(dict(role='tool', tool_call_id=item['call_id'], content=content))
        elif kind == 'reasoning':
            continue  # GLM's template drops earlier reasoning; nothing encrypted to replay.
        else:
            raise BridgeError('unsupported_input_item:' + str(kind))
    flush_images()
    tools = []
    for tool in body.get('tools', []):
        if tool.get('type') != 'function': raise BridgeError('unsupported_tool:' + str(tool.get('type')))
        tools.append(dict(type='function', function=dict(name=tool['name'], description=tool.get('description', ''),
                                                         parameters=tool.get('parameters') or dict(type='object', properties={}))))
    chat = dict(model=body['model'], messages=messages, stream=True, stream_options=dict(include_usage=True),
                max_tokens=MAX_TOKENS)
    if tools:
        chat.update(tools=tools, tool_choice=body.get('tool_choice', 'auto'))
        if 'parallel_tool_calls' in body: chat['parallel_tool_calls'] = body['parallel_tool_calls']
    effort = (body.get('reasoning') or {}).get('effort')
    if provider is not None:
        chat[provider.token_param] = chat.pop('max_tokens')
    reasoning_param = provider.reasoning_param if provider is not None else 'reasoning_effort'
    if effort and reasoning_param: chat[reasoning_param] = effort
    _response_format(body,chat,provider)
    if provider is not None:chat.update(json.loads(json.dumps(provider.extra_body)))
    return chat


def upstream_chunks(chat, key, *, url=PARCC_CHAT_URL, timeout=900):
    request = urllib.request.Request(url, data=json.dumps(chat).encode(), method='POST',
        headers={'Authorization': 'Bearer ' + (key.reveal() if isinstance(key, Secret) else key), 'Content-Type': 'application/json'})
    from .dev_agent_provider import open_request
    response = open_request(request, timeout=timeout)
    yield dict(_request_id=response.headers.get('x-litellm-call-id') or response.headers.get('x-request-id'))
    with response:
        for raw in response:
            line = raw.decode().strip()
            if not line.startswith('data:'): continue
            data = line[5:].strip()
            if data == '[DONE]': return
            chunk = json.loads(data)
            if 'error' in chunk: raise BridgeError(json.dumps(chunk['error']))
            yield chunk
    raise BridgeError('incomplete_provider_stream')


class _Stream:
    """Turns chat deltas into Responses SSE events."""
    def __init__(self, write, response_id):
        self.write, self.id = write, response_id
        self.output = []; self.reasoning = None; self.message = None; self.calls = {}

    def event(self, kind, **payload):
        payload['type'] = kind
        self.write(('event: %s\ndata: %s\n\n' % (kind, json.dumps(payload))).encode())

    def delta(self, delta):
        reasoning = delta.get('reasoning_content') or delta.get('reasoning')
        if reasoning:
            if self.reasoning is None:
                self.reasoning = dict(type='reasoning', id='rs_' + uuid.uuid4().hex, summary=[], text='', index=len(self.output))
                self.output.append(self.reasoning)
                self.event('response.output_item.added', output_index=self.reasoning['index'],
                           item=dict(type='reasoning', id=self.reasoning['id'], summary=[]))
                self.event('response.reasoning_summary_part.added', item_id=self.reasoning['id'],
                           output_index=self.reasoning['index'], summary_index=0, part=dict(type='summary_text', text=''))
            self.reasoning['text'] += reasoning
            self.event('response.reasoning_summary_text.delta', item_id=self.reasoning['id'],
                       output_index=self.reasoning['index'], summary_index=0, delta=reasoning)
        if delta.get('content'):
            if self.message is None:
                self.message = dict(type='message', id='msg_' + uuid.uuid4().hex, text='', index=len(self.output))
                self.output.append(self.message)
                self.event('response.output_item.added', output_index=self.message['index'],
                           item=dict(type='message', id=self.message['id'], role='assistant', status='in_progress', content=[]))
            self.message['text'] += delta['content']
            self.event('response.output_text.delta', item_id=self.message['id'], output_index=self.message['index'],
                       content_index=0, delta=delta['content'])
        for call in delta.get('tool_calls') or []:
            slot = self.calls.get(call.get('index', 0))
            if slot is None:
                slot = self.calls[call.get('index', 0)] = dict(type='function_call', id='fc_' + uuid.uuid4().hex,
                    call_id=call.get('id') or 'call_' + uuid.uuid4().hex, name='', arguments='', index=None)
                self.output.append(slot)
            function = call.get('function') or {}
            slot['name'] += function.get('name') or ''
            slot['arguments'] += function.get('arguments') or ''

    def finish(self, usage):
        items = []
        for index, item in enumerate(self.output):
            if item['type'] == 'reasoning':
                done = dict(type='reasoning', id=item['id'], summary=[dict(type='summary_text', text=item['text'])])
            elif item['type'] == 'message':
                done = dict(type='message', id=item['id'], role='assistant', status='completed',
                            content=[dict(type='output_text', text=item['text'], annotations=[])])
            else:
                done = dict(type='function_call', id=item['id'], call_id=item['call_id'], name=item['name'],
                            arguments=item['arguments'] or '{}', status='completed')
                self.event('response.output_item.added', output_index=index, item=dict(done, arguments=''))
            self.event('response.output_item.done', output_index=index, item=done)
            items.append(done)
        usage = usage or {}
        prompt, completion = usage.get('prompt_tokens') or 0, usage.get('completion_tokens') or 0
        reasoning = (usage.get('completion_tokens_details') or {}).get('reasoning_tokens') or 0
        cached = (usage.get('prompt_tokens_details') or {}).get('cached_tokens') or 0
        self.event('response.completed', response=dict(id=self.id, object='response', status='completed', output=items,
            usage=dict(input_tokens=prompt, input_tokens_details=dict(cached_tokens=cached), output_tokens=completion,
                       output_tokens_details=dict(reasoning_tokens=reasoning), total_tokens=prompt + completion)))
        return items


class _Truncated(Exception):
    pass


class ChatBridge:
    def __init__(self, log_dir, provider, key: Secret, *, token, upstream=upstream_chunks, vision=False, role='dev'):
        self.log_dir = Path(log_dir); self.log_dir.mkdir(parents=True, exist_ok=True)
        self.key = key if isinstance(key, Secret) else Secret(key); self.upstream = upstream
        self.provider = provider; self.token = token
        self.vision=vision;self.role=role
        if provider is not None and not token: raise BridgeError('bridge_token_required')
        self.counter = itertools.count(1); self.lock = threading.Lock()
        bridge = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args): pass  # never echo request lines or headers
            def do_POST(self):
                try: bridge.handle(self)
                except Exception:
                    # Never print a chained upstream exception on a disconnected client.
                    self.close_connection = True
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        # Handlers stay daemons: close() must return promptly even if the upstream stalls,
        # otherwise a cancelled Eval would wait out the upstream read timeout.
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, name='parcc-bridge', daemon=True)
        self.thread.start()

    @property
    def base_url(self): return 'http://127.0.0.1:%d/v1' % self.port

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def clean_row(self, row):
        redacted = False
        def clean(value):
            nonlocal redacted
            if isinstance(value, str):
                value, changed = scrub(value, [self.key]); redacted |= changed
            elif isinstance(value, dict): value = {clean(k):clean(v) for k,v in value.items()}
            elif isinstance(value, list): value = [clean(v) for v in value]
            return value
        row = clean(row)
        if redacted: row['redacted'] = True
        return row

    def record(self, row):
        row = self.clean_row(dict(row,role=self.role))
        with self.lock, (self.log_dir / 'calls.jsonl').open('a') as stream:
            stream.write(json.dumps(row, sort_keys=True) + '\n')

    def handle(self, handler):
        number = next(self.counter); started = time.time()
        row = dict(call=number, at=started, path=handler.path)
        if handler.path.rstrip('/') != '/v1/responses':
            row.update(status=404); self.record(row)
            handler.close_connection = True
            handler.send_response(404); handler.send_header('Content-Length', '0'); handler.end_headers(); return
        if self.token is not None and not hmac.compare_digest(
                handler.headers.get('Authorization', '').encode(), ('Bearer '+self.token).encode()):
            return self._error(handler, 401, 'bridge authorization required')
        try:
            body = json.loads(handler.rfile.read(int(handler.headers.get('Content-Length') or 0)))
            chat = to_chat(body, self.provider, vision=self.vision)
            row.update(requested_model=chat['model'], messages=len(chat['messages']), tools=len(chat.get('tools', [])),
                       reasoning_effort=chat.get('reasoning_effort'))
            if self.provider is None and chat['model'] not in PARCC_DEV_MODELS: raise BridgeError('model_not_bridged:' + chat['model'])
            chunks = (self.upstream(chat, self.key, url=self.provider.chat_url) if self.provider is not None
                      else self.upstream(chat, self.key))
            meta = next(chunks)
        except urllib.error.HTTPError as exc:
            detail, redacted = scrub(exc.read(8192).decode(errors='replace'), [self.key])
            if redacted: row['redacted'] = True
            row.update(status=exc.code, error=detail); self.record(row)
            # 429/5xx stay retryable for Codex; anything else is a hard request error.
            status = exc.code if exc.code == 429 or exc.code >= 500 else 400
            return self._error(handler, status, detail, redacted=redacted)
        except Exception as exc:
            row.update(status=502 if not isinstance(exc, BridgeError) else 400, error='%s: %s' % (type(exc).__name__, exc))
            row = self.clean_row(row); self.record(row)
            return self._error(handler, row['status'], row['error'], redacted=row.get('redacted', False))
        row['upstream_request_id'] = meta.get('_request_id')
        handler.send_response(200)
        handler.send_header('Content-Type', 'text/event-stream'); handler.send_header('Cache-Control', 'no-cache')
        handler.send_header('Connection', 'close'); handler.end_headers()
        def write(data):
            handler.wfile.write(data); handler.wfile.flush()
        stream = _Stream(write, 'resp_' + uuid.uuid4().hex)
        usage = None; models = set(); finish = None
        try:
            stream.event('response.created', response=dict(id=stream.id, object='response', status='in_progress', output=[]))
            for chunk in chunks:
                chunk = self.clean_row(chunk)
                if chunk.get('redacted'): row['redacted'] = True
                if chunk.get('model'): models.add(chunk['model'])
                if chunk.get('usage'): usage = chunk['usage']
                for choice in chunk.get('choices') or []:
                    stream.delta(choice.get('delta') or {})
                    finish = choice.get('finish_reason') or finish
            if finish == 'length' and self.provider is not None:  # lab (PARCC) bridges keep their old behaviour
                raise _Truncated('The provider stopped at its output token limit (finish_reason=length); the reply was truncated and is not used.')
            items = stream.finish(usage)
            row.update(status=200, upstream_models=sorted(models), finish_reason=finish, usage=usage,
                       output=[i['type'] + (':' + i['name'] if i['type'] == 'function_call' else '') for i in items])
        except _Truncated as exc:
            # The provider stopped at its output limit: whatever was streamed is cut off (a half tool call, half JSON).
            row.update(status='truncated', truncated=True, finish_reason=finish, usage=usage, error=str(exc))
            row = self.clean_row(row)
            try: stream.event('response.failed', response=dict(id=stream.id, status='failed',
                                                               error=dict(code='max_output_tokens', message=row['error'], redacted=row.get('redacted', False))))
            except OSError: pass
        except Exception as exc:
            row.update(status='stream_failed', error='%s: %s' % (type(exc).__name__, exc))
            row = self.clean_row(row)
            try: stream.event('response.failed', response=dict(id=stream.id, status='failed',
                                                               error=dict(code='server_error', message=row['error'], redacted=row.get('redacted', False))))
            except OSError: pass
        finally:
            row['seconds'] = round(time.time() - started, 3); self.record(row)
            handler.close_connection = True

    def _error(self, handler, status, message, *, redacted=False):
        message, changed = scrub(message, [self.key])
        data = json.dumps(dict(error=dict(message=message, type='parcc_bridge_error', code=str(status),
                                          redacted=redacted or changed))).encode()
        handler.close_connection = True
        handler.send_response(status); handler.send_header('Content-Type', 'application/json')
        handler.send_header('Content-Length', str(len(data))); handler.end_headers(); handler.wfile.write(data)


class ParccBridge(ChatBridge):
    def __init__(self, log_dir, *, key=None, upstream=upstream_chunks):
        super().__init__(log_dir, None, key or parcc_key(), token=None, upstream=upstream, role='parcc-lab')


def provider_args(base_url, *, token_env=None):
    """Codex -c overrides that route one session to the bridge (no env_key: the bridge holds the key)."""
    if token_env is not None:
        import re
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', token_env): raise BridgeError('invalid_token_env')
    extra = '' if token_env is None else ', env_key="%s"' % token_env
    return ['-c', 'model_providers.parcc={ name="PARCC (bridge)", base_url="%s", wire_api="responses", '
                  'stream_idle_timeout_ms = 900000, request_max_retries = 4, stream_max_retries = 2%s }' % (base_url, extra),
            '-c', 'model_provider="parcc"',
            # Codex has no metadata for parcc/GLM (GLM-5.3); PARCC accepts >400k input, so give it a sol-sized window.
            '-c', 'model_context_window=262144']
