"""Provider descriptions, URL admission and bounded connectivity checks."""
from dataclasses import dataclass, field, replace
import ipaddress
import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import base64
from .dev_agent_secret import scrub
from . import dev_agent_vision_probe as vision_probe


class ProviderError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    chat_url: str
    models_url: str | None
    key_source: str = 'user'
    token_param: str = 'max_tokens'
    reasoning_param: str | None = None
    capabilities: dict = field(default_factory=lambda: dict.fromkeys(('tools','streaming','vision','json_schema'), 'unknown'))
    suggested_models: tuple = ()
    # The one model the provider documents as reading images; Eval and Advisor must use it where the provider has several.
    vision_model: str | None = None
    response_format: str = 'json_schema'
    # Fixed request fields every call to this provider carries (bridge, direct client and probe alike).
    extra_body: dict = field(default_factory=dict)

    @property
    def thinking(self):
        """'off' when this preset switches the provider's thinking mode off; shown in the UI and the manifest."""
        return 'off' if (self.extra_body.get('thinking') or {}).get('type') == 'disabled' else None


def presets():
    base_url='https://litellm.parcc.upenn.edu/v1'
    return {
        # Real check (2026-11-05): PARCC applies response_format to every turn, so a role that needs tools answers at once
        # without reading anything; the schema goes in the instructions instead.
        'parcc': ProviderSpec('parcc', 'PARCC', base_url+'/chat/completions', base_url+'/models',
                              'lab-file', reasoning_param='reasoning_effort', suggested_models=('parcc/GLM',), response_format='prompt'),
        'openai': ProviderSpec('openai', 'OpenAI', 'https://api.openai.com/v1/chat/completions',
                               'https://api.openai.com/v1/models', token_param='max_completion_tokens', reasoning_param='reasoning_effort'),
        'deepseek': ProviderSpec('deepseek', 'DeepSeek', 'https://api.deepseek.com/v1/chat/completions',
                                 'https://api.deepseek.com/v1/models', response_format='json_object',
                                 # DeepSeek thinks by default and then demands reasoning_content back on every later request
                                 # that carries tools; the bridge cannot supply it, so thinking stays off (disclosed as thinking=off).
                                 extra_body={'thinking': {'type': 'disabled'}},
                                 # Real check (2026-11-04): deepseek-v4-pro accepts an image part and ignores it; only deepseek-flash reads it.
                                 vision_model='deepseek-flash'),
        'openai-compatible': ProviderSpec('openai-compatible', 'OpenAI compatible', '', None),
    }


def resolve_provider(provider_id, base_url=None, *, allow_custom=False, allow_private_hosts=(), public_gateway_mode=False):
    spec = presets().get(provider_id) if isinstance(provider_id,str) else None
    if spec is None: raise ProviderError('provider_unknown', 'Unknown provider')
    if provider_id != 'openai-compatible':
        if base_url is not None: raise ProviderError('base_url_not_allowed', 'Preset URLs cannot be overridden')
        override=os.getenv('PARCC_URL') if provider_id=='parcc' and not public_gateway_mode else None
        if override:
            try:
                url=urllib.parse.urlsplit(override)
                port=url.port
                if not url.hostname or url.username is not None or url.password is not None or any(c.isspace() or ord(c)<32 for c in override) or any(c in override for c in ('?','#','\\')):raise ValueError()
            except ValueError:raise ProviderError('base_url_invalid','Invalid PARCC URL') from None
            if url.scheme!='https':raise ProviderError('base_url_not_https','Provider URL must use HTTPS')
            spec=replace(spec,chat_url=override,models_url=override.rsplit('/',2)[0]+'/models')
        return spec
    if not allow_custom: raise ProviderError('custom_base_url_disabled', 'Custom provider URLs are disabled')
    try:
        if not isinstance(base_url, str) or any(c.isspace() or ord(c)<32 for c in base_url): raise ValueError()
        url = urllib.parse.urlsplit(base_url)
        host = url.hostname
        port = url.port
        if not host or url.username is not None or url.password is not None or '?' in base_url or '#' in base_url or '\\' in base_url: raise ValueError()
    except ValueError:
        raise ProviderError('base_url_invalid', 'Invalid provider URL') from None
    allowed = host in allow_private_hosts
    try: loopback = ipaddress.ip_address(host).is_loopback
    except ValueError: loopback = host == 'localhost'
    if url.scheme != 'https' and not (url.scheme == 'http' and allowed and loopback):
        raise ProviderError('base_url_not_https', 'Provider URL must use HTTPS')
    try:
        addresses = socket.getaddrinfo(host, port or (443 if url.scheme=='https' else 80), type=socket.SOCK_STREAM)
        if not addresses: raise OSError()
        if not allowed and any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ProviderError('base_url_private_host', 'Provider URL must resolve to public addresses')
    except ProviderError: raise
    except (OSError, ValueError):
        raise ProviderError('base_url_dns_failed', 'Provider host could not be resolved') from None
    base = base_url.rstrip('/')
    return replace(spec, chat_url=base+'/chat/completions', models_url=base+'/models')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None


def open_request(request, *, timeout=20):
    # Neither ambient proxies nor redirects may move a credential to another host.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout)


@dataclass
class ProbeResult:
    checks: dict
    errors: list
    needs_vision: bool = False
    needs_tools: bool = True
    @property
    def ok(self):
        required = ('auth','chat') + (('streaming','tools') if self.needs_tools else ()) + (('vision',) if self.needs_vision else ())
        return all(self.checks[k]=='ok' for k in required) and self.checks['vision'] != 'fail'


def probe(spec, secret, model, *, role, transport=None):
    """Dev and Eval work through tool calls. The Advisor is one plain chat call, and advisor-text is an Advisor sent no images."""
    checks = dict.fromkeys(('auth','chat','streaming','tools','vision'), 'unknown'); errors = []
    advisor = role in ('advisor','advisor-text')
    outcome = lambda: ProbeResult(checks, errors, needs_vision=role in ('eval','advisor'), needs_tools=not advisor)
    send = transport or open_request
    def request(url, body=None):
        return send(urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
            headers={'Authorization':'Bearer '+secret.reveal(), 'Content-Type':'application/json'}), timeout=20)
    def failed(exc, stage):
        status = getattr(exc, 'code', None)
        raw = exc.read(8192).decode(errors='replace') if isinstance(exc, urllib.error.HTTPError) else '%s: %s' % (type(exc).__name__, exc)
        message, redacted = scrub(raw, [secret])
        errors.append(dict(code='provider_'+stage+'_failed', message=message, upstream_status=status, redacted=redacted))
        checks[stage] = 'fail'
        if status in (401,403): checks['auth'] = 'fail'
    if spec.models_url:
        try:
            with request(spec.models_url) as response: json.loads(response.read(65536))
            checks['auth'] = 'ok'
        except Exception as exc:
            failed(exc, 'auth')
            return outcome()
    if advisor:
        plain = dict(model=model, stream=False, messages=[dict(role='user', content='Reply with ok.')])
        plain[spec.token_param] = 512
        plain.update(json.loads(json.dumps(spec.extra_body)))
        try:
            with request(spec.chat_url, plain) as response: value = json.loads(response.read(65536))
            if 'error' in value: raise ValueError(json.dumps(value['error']))
            if not value.get('choices'): raise ValueError('provider_returned_no_choices')
            checks.update(auth='ok', chat='ok')
        except Exception as exc:
            failed(exc, 'chat')
            if role == 'advisor': checks['vision'] = 'fail'
        if role == 'advisor-text' or checks['chat'] != 'ok': return outcome()
    body = dict(model=model, messages=[dict(role='user',content='Call the ping tool.')], stream=True,
                tools=[dict(type='function',function=dict(name='ping',parameters=dict(type='object',properties={})))],
                tool_choice='auto')  # Forcing a named tool is rejected by thinking models; runs use auto too.
    body[spec.token_param] = 512  # Room for a thinking model to reason before it calls the tool.
    body.update(json.loads(json.dumps(spec.extra_body)))
    if not advisor:
      try:
        done = False; tool = False; chunks = 0; total = 0
        with request(spec.chat_url, body) as response:
            for raw in response:
                total += len(raw)
                if total > 65536: raise ValueError('probe_response_too_large')
                if not raw.startswith(b'data:'): continue
                data = raw[5:].strip()
                if data == b'[DONE]': done = True; break
                chunk = json.loads(data)
                if 'error' in chunk: raise ValueError(json.dumps(chunk['error']))
                for choice in chunk.get('choices', []):
                    chunks += 1
                    tool |= any(c.get('function',{}).get('name')=='ping' for c in choice.get('delta',{}).get('tool_calls',[]))
        if not done or not chunks: raise ValueError('incomplete_provider_stream')
        checks.update(auth='ok',chat='ok',streaming='ok',tools='ok' if tool else 'fail')
        if not tool: errors.append(dict(code='provider_tools_failed',message='This model must support tool calls',upstream_status=None,redacted=False))
      except Exception as exc:
        failed(exc, 'chat'); checks['streaming'] = 'fail'
        if role in ('eval','advisor'): checks['vision'] = 'fail'
    if role in ('eval','advisor') and checks['chat'] == 'ok':
        # A 200 proves nothing: some models accept an image part and silently ignore it. Ask about a picture whose answer we know.
        picture = 'data:image/png;base64,' + base64.b64encode(vision_probe.text_png()).decode()
        seen = dict(model=model, stream=False, messages=[dict(role='user', content=[dict(type='text', text=vision_probe.PROMPT),
                    dict(type='image_url', image_url=dict(url=picture))])])
        seen[spec.token_param] = 512
        seen.update(json.loads(json.dumps(spec.extra_body)))
        try:
            with request(spec.chat_url, seen) as response: value = json.loads(response.read(65536))
            answer = ((value.get('choices') or [{}])[0].get('message') or {}).get('content') or ''
            if vision_probe.reads_text(answer): checks['vision'] = 'ok'
            else:
                checks['vision'] = 'fail'
                advice = ('Use %s.' % spec.vision_model) if spec.vision_model and spec.vision_model != model else 'Choose a model that can see images.'
                message, redacted = scrub('This model does not recognise images: asked to read a test image that says "HELLO 4821", it answered: %s. '
                                          'It accepts images without looking at them. %s' % (answer.strip()[:120] or '(nothing)', advice), [secret])
                errors.append(dict(code='provider_vision_failed', message=message, upstream_status=None, redacted=redacted))
        except Exception as exc:
            failed(exc, 'vision')
    return outcome()


_NOT_CHAT = re.compile(r'embed|whisper|tts|dall-e|moderation|transcribe|image|audio|realtime|davinci|babbage', re.I)


def list_models(spec, secret, *, transport=None):
    """Model ids the provider's /models lists for this key, minus obvious non-chat ones; for a picker only."""
    if not spec.models_url: raise ProviderError('models_unavailable', 'This provider does not list models')
    send = transport or open_request
    request = urllib.request.Request(spec.models_url, headers={'Authorization':'Bearer '+secret.reveal()})
    try:
        with send(request, timeout=20) as response: raw = response.read(1 << 20)
        clean, _ = scrub(raw.decode(errors='replace'), [secret])
        rows = json.loads(clean).get('data')
        if not isinstance(rows, list): raise ValueError('models_list_invalid')
    except Exception as exc:
        status = getattr(exc, 'code', None)
        detail = exc.read(2048).decode(errors='replace') if isinstance(exc, urllib.error.HTTPError) else '%s: %s' % (type(exc).__name__, exc)
        message, _ = scrub(detail, [secret])
        raise ProviderError('provider_models_failed', message if status is None else 'HTTP %s: %s' % (status, message)) from None
    ids = {r['id'] for r in rows if isinstance(r, dict) and isinstance(r.get('id'), str) and 0 < len(r['id']) <= 128 and not _NOT_CHAT.search(r['id'])}
    return sorted(ids)[:500]
