"""HTTP side of the session key vault (/api/provider/*), shared by the UI servers.

Same routes, validation and error contract as dev_ui_server.provider_route; a key only ever passes through
Secret and scrub on its way in, and no response carries it.
"""
import json
import re
import secrets
from urllib.parse import urlsplit
from .dev_agent_provider import ProviderError, list_models, presets, probe, resolve_provider
from .dev_agent_secret import Secret, scrub

ROUTE = re.compile(r'/api/provider/credentials(?:/([a-z0-9-]{1,32})(/test|/models)?)?')


def owner_of(handler):
    return 'local'


def is_provider_path(path):
    return path.startswith('/api/provider/')


def handle(handler, *, vault, token, allow_custom=False, allow_private_hosts=(), owner_of=owner_of):
    """Serve one request; `handler` supplies .command, .path, .headers, .rfile and .reply(payload, status)."""
    values = []

    def provider_spec(provider, base=None):
        return resolve_provider(provider, base, allow_custom=allow_custom, allow_private_hosts=allow_private_hosts)

    try:
        if not secrets.compare_digest(handler.headers.get('X-Attention-Token', ''), token):
            raise ProviderError('control_token_required', 'Control token required')
        parts = urlsplit(handler.path)
        path = parts.path
        if parts.query:
            raise ProviderError('provider_request_invalid', 'Query parameters are not accepted')
        size = int(handler.headers.get('Content-Length', 0))
        if not 0 <= size <= 8192:
            raise ProviderError('provider_body_invalid', 'Bounded JSON body required')
        body = {}
        if handler.command in ('PUT', 'POST'):
            if handler.headers.get('Content-Type', '').split(';')[0] != 'application/json' or not size:
                raise ProviderError('provider_body_invalid', 'Bounded JSON body required')
            try:
                body = json.loads(handler.rfile.read(size))
            except (ValueError, UnicodeError):
                raise ProviderError('provider_body_invalid', 'Invalid JSON body') from None
            if not isinstance(body, dict):
                raise ProviderError('provider_body_invalid', 'JSON object required')
            if isinstance(body.get('key'), str):
                values.append(Secret(body['key']))
        owner = owner_of(handler)
        if path == '/api/provider/presets' and handler.command == 'GET':
            handler.reply([dict(id=p.id, label=p.label, needs_key=p.key_source == 'user',
                                base_url=p.chat_url.rsplit('/chat/completions', 1)[0],
                                suggested_models=p.suggested_models, capabilities=p.capabilities, thinking=p.thinking, vision_model=p.vision_model)
                           for p in presets().values()])
            return
        match = ROUTE.fullmatch(path)
        if not match:
            raise ProviderError('provider_route_unknown', 'Unknown provider resource')
        name, test = match.groups()
        if handler.command == 'PUT' and not test:
            if set(body) - {'provider', 'key', 'base_url'}:
                raise ProviderError('provider_body_invalid', 'Unexpected credential fields')
            if not isinstance(body.get('provider'), str) or not isinstance(body.get('key'), str):
                raise ProviderError('provider_body_invalid', 'Provider and key strings required')
            name = name or body['provider']
            spec = provider_spec(body['provider'], body.get('base_url'))
            vault.put(owner, name, spec.id, values[0], base_url=body.get('base_url'))
            row = next(r for r in vault.list(owner) if r['credential_id'] == name)
            handler.reply(dict(ok=True, credential_id=name, provider=spec.id, key_set=True, expires_at=row['expires_at']))
            return
        if handler.command == 'GET' and not test:
            rows = vault.list(owner)
            if name is not None:
                rows = [r for r in rows if r['credential_id'] == name]
            handler.reply(rows)
            return
        if handler.command == 'DELETE' and not test:
            handler.reply(dict(ok=True, cleared=vault.clear(owner, name)))
            return
        if handler.command == 'GET' and test == '/models':
            row = next((r for r in vault.list(owner) if r['credential_id'] == name), None)
            if row is None:
                raise ProviderError('provider_key_missing', 'Missing or expired credential')
            keys = vault.take_for_run(owner, {'dev': dict(credential=name, provider=row['provider'], model='models')})
            desc = keys.roles['dev']
            secret = keys._keys[name]
            values.append(secret)
            handler.reply(dict(models=list_models(provider_spec(desc['provider'], desc['base_url']), secret)))
            return
        if handler.command == 'POST' and test == '/test':
            if set(body) - {'model', 'role'}:
                raise ProviderError('provider_body_invalid', 'Unexpected test fields')
            model = body.get('model')
            role = body.get('role', 'dev')
            if not isinstance(model, str) or not model.strip() or len(model) > 128 or role not in ('dev', 'eval', 'advisor'):
                raise ProviderError('provider_body_invalid', 'Valid model and role required')
            row = next((r for r in vault.list(owner) if r['credential_id'] == name), None)
            if row is None:
                raise ProviderError('provider_key_missing', 'Missing or expired credential for role ' + role)
            keys = vault.take_for_run(owner, {'dev': dict(credential=name, provider=row['provider'], model=model)})
            desc = keys.roles['dev']
            secret = keys._keys[name]
            values.append(secret)
            result = probe(provider_spec(desc['provider'], desc['base_url']), secret, model, role=role)
            handler.reply(dict(ok=result.ok, checks=result.checks, errors=result.errors))
            return
        raise ProviderError('provider_route_unknown', 'Unknown provider operation')
    except Exception as exc:
        message, redacted = scrub(str(exc), values)
        status = 403 if isinstance(exc, ProviderError) and exc.code == 'control_token_required' else 400
        handler.reply(dict(error=dict(code=exc.code if isinstance(exc, ProviderError) else 'provider_request_invalid',
                                      message=message, redacted=redacted)), status)
