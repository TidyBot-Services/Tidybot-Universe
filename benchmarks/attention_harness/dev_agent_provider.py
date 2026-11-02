"""Provider descriptions, URL admission and bounded connectivity checks."""
from dataclasses import dataclass, field, replace
import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from .dev_agent_secret import scrub


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


def presets():
    from .dev_agent_parcc_bridge import PARCC_CHAT_URL
    return {
        'parcc': ProviderSpec('parcc', 'PARCC', PARCC_CHAT_URL, PARCC_CHAT_URL.rsplit('/', 2)[0]+'/models',
                              'lab-file', reasoning_param='reasoning_effort', suggested_models=('parcc/GLM',)),
        'openai': ProviderSpec('openai', 'OpenAI', 'https://api.openai.com/v1/chat/completions',
                               'https://api.openai.com/v1/models', token_param='max_completion_tokens', reasoning_param='reasoning_effort'),
        'deepseek': ProviderSpec('deepseek', 'DeepSeek', 'https://api.deepseek.com/v1/chat/completions',
                                 'https://api.deepseek.com/v1/models'),
        'openai-compatible': ProviderSpec('openai-compatible', 'OpenAI compatible', '', None),
    }


def resolve_provider(provider_id, base_url=None, *, allow_custom, allow_private_hosts=()):
    spec = presets().get(provider_id) if isinstance(provider_id,str) else None
    if spec is None: raise ProviderError('provider_unknown', 'Unknown provider')
    if provider_id != 'openai-compatible':
        if base_url is not None: raise ProviderError('base_url_not_allowed', 'Preset URLs cannot be overridden')
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
    @property
    def ok(self):
        required = ('auth','chat','streaming','tools')
        return all(self.checks[k]=='ok' for k in required) and self.checks['vision'] != 'fail'


def probe(spec, secret, model, *, role, transport=None):
    checks = dict.fromkeys(('auth','chat','streaming','tools','vision'), 'unknown'); errors = []
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
            return ProbeResult(checks, errors)
    content = 'Call the ping tool.'
    if role in ('eval','advisor'):
        content = [dict(type='text', text=content), dict(type='image_url', image_url=dict(url='data:image/png;base64,'
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII='))]
    body = dict(model=model, messages=[dict(role='user',content=content)], stream=True,
                tools=[dict(type='function',function=dict(name='ping',parameters=dict(type='object',properties={})))],
                tool_choice=dict(type='function',function=dict(name='ping')))
    body[spec.token_param] = 8
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
        if role in ('eval','advisor'): checks['vision'] = 'ok'
        if not tool: errors.append(dict(code='provider_tools_failed',message='Dev requires tool calls',upstream_status=None,redacted=False))
    except Exception as exc:
        failed(exc, 'chat'); checks['streaming'] = 'fail'
        if role in ('eval','advisor'): checks['vision'] = 'fail'
    return ProbeResult(checks, errors)
