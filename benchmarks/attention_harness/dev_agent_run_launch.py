"""Shared, volatile credential admission for all operator run launchers."""
from dataclasses import dataclass
from urllib.parse import urlsplit

from .dev_agent_config import text_only
from .dev_agent_provider import ProviderError, probe, resolve_provider
from .dev_agent_secret import scrub


class RunPreflightError(ProviderError):
    def __init__(self, body):
        self.body = body
        super().__init__(body['error']['code'], body['error']['message'])


def preflight(take, roles, provider_spec, *, probe_provider=None):
    """Take one snapshot and validate it without publishing credentials or state."""
    run_keys = take(roles)
    for role, desc in run_keys.roles.items():
        secret = run_keys._keys[desc['credential']]
        spec = provider_spec(desc['provider'], desc['base_url'])
        if roles[role].get('base_url_host') != urlsplit(spec.chat_url).hostname:
            raise RunPreflightError(dict(error=dict(code='provider_mismatch',
                message='Credential host differs for role '+role, redacted=False, role=role)))
        # A lab role on a text-only model is sent no images, so it is checked like Dev.
        result = (probe_provider or probe)(spec, secret, desc['model'], role='dev' if text_only(desc) else role)
        if not result.ok:
            raise RunPreflightError(dict(error=dict(code='provider_preflight_failed',
                message='\n'.join(e['message'] for e in result.errors),
                redacted=any(e['redacted'] for e in result.errors), role=role),
                checks=result.checks, errors=result.errors))
    return run_keys


@dataclass(repr=False)
class RunCredentials:
    vault: object
    owner: str
    allow_custom: bool = False
    allow_private_hosts: tuple = ()

    def take(self, roles):
        return self.vault.take_for_run(self.owner, roles)

    def spec(self, provider, base=None):
        return resolve_provider(provider, base, allow_custom=self.allow_custom,
                                allow_private_hosts=self.allow_private_hosts)


def prepare_run_keys(manifest, keys=None):
    if 'agent_provider' not in manifest:
        return None
    if keys is None:
        from .dev_agent_key_vault import ProviderKeyVault
        keys = RunCredentials(ProviderKeyVault(), 'local')
    return preflight(keys.take, manifest['agent_provider']['roles'], keys.spec)


def daily_error(exc):
    """Daily UI consumes error as text; retain the old structured body as detail."""
    message, redacted = scrub(str(exc), ())
    body = exc.body if isinstance(exc, RunPreflightError) else dict(
        error=dict(code=exc.code, message=message, redacted=redacted))
    return dict(error=f'{message} ({exc.code})', detail=body['error'],
                **{k:v for k,v in body.items() if k != 'error'})
