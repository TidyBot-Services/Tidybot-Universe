"""Owner-scoped volatile credentials and one-shot run pipes."""
import atexit
import ctypes
from dataclasses import dataclass, field
import json
import math
import os
import re
import resource
import signal
import subprocess
import threading
import time
import weakref
from urllib.parse import urlsplit
from .dev_agent_provider import ProviderError, presets
from .dev_agent_secret import Secret

KEY_ENV = ('PARCC_API_KEY','LITELLM_KEY','OPENAI_API_KEY','DEEPSEEK_API_KEY','ANTHROPIC_API_KEY')
_RUN_KEYS = {}
_RUN_ROLES = {}
_VAULTS = weakref.WeakSet()
_SIGNAL_INSTALLED = False


def credential_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9-]{1,32}', value):
        raise ProviderError('credential_id_invalid', 'Invalid credential name')
    return value


def keyless_env(env): return {k:v for k,v in env.items() if k not in KEY_ENV}


def _clear_vaults():
    for vault in list(_VAULTS): vault.clear_all()
    _RUN_KEYS.clear(); _RUN_ROLES.clear()


def _install_cleanup():
    global _SIGNAL_INSTALLED
    if _SIGNAL_INSTALLED or threading.current_thread() is not threading.main_thread(): return
    for sig in (signal.SIGINT, signal.SIGTERM):
        previous = signal.getsignal(sig)
        def handler(signum, frame, previous=previous):
            _clear_vaults()
            if callable(previous): return previous(signum, frame)
            if previous == signal.SIG_IGN: return
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)
        signal.signal(sig, handler)
    _SIGNAL_INSTALLED = True


atexit.register(_clear_vaults)


@dataclass(repr=False)
class RunKeys:
    _keys: dict = field(repr=False)
    roles: dict
    def __repr__(self): return '<RunKeys>'
    def __reduce__(self): raise TypeError('run keys cannot be serialized')


class ProviderKeyVault:
    def __init__(self, clock=time.time, default_ttl=86400):
        self.clock = clock; self.default_ttl = default_ttl
        self._entries = {}; self._lock = threading.RLock()
        _VAULTS.add(self); _install_cleanup()
    def __reduce__(self): raise TypeError('vault cannot be serialized')
    def _purge(self):
        now = self.clock()
        for name in [k for k,v in self._entries.items() if v['expires_at']<=now]: del self._entries[name]
    def purge_expired(self):
        with self._lock: self._purge()
    def put(self, owner, credential_id, provider_id, secret, *, base_url=None, ttl=None):
        credential_name(credential_id)
        if not isinstance(provider_id,str) or provider_id not in presets(): raise ProviderError('provider_unknown', 'Unknown provider')
        secret = secret if isinstance(secret, Secret) else Secret(secret)
        if not secret: raise ProviderError('provider_key_missing', 'A nonempty key is required')
        ttl = self.default_ttl if ttl is None else ttl
        if type(ttl) not in (int,float) or not math.isfinite(ttl) or ttl<=0:
            raise ProviderError('credential_ttl_invalid', 'TTL must be positive and finite')
        with self._lock:
            self._purge()
            self._entries[owner,credential_id] = dict(secret=secret,provider=provider_id,base_url=base_url,
                                                    expires_at=self.clock()+ttl)
    def list(self, owner):
        with self._lock:
            self._purge()
            return [dict(credential_id=k[1],provider=v['provider'],base_url_host=urlsplit(v['base_url'] or presets()[v['provider']].chat_url).hostname,
                         expires_at=v['expires_at']) for k,v in self._entries.items() if k[0]==owner]
    def clear(self, owner, credential_id=None):
        with self._lock:
            self._purge()
            names = [k for k in self._entries if k[0]==owner and (credential_id is None or k[1]==credential_id)]
            for name in names: del self._entries[name]
            return len(names)
    def clear_all(self):
        with self._lock: self._entries.clear()
    def take_for_run(self, owner, roles):
        keys = {}; public = {}
        with self._lock:
            self._purge()
            for role, desc in roles.items():
                if role not in ('dev','eval','advisor'): raise ProviderError('role_invalid', 'Invalid role')
                name = credential_name(desc['credential'])
                if role != 'dev' and name == 'lab': continue
                value = self._entries.get((owner,name))
                if value is None and role=='dev' and name=='lab' and desc['provider']=='parcc':
                    from .dev_agent_parcc_bridge import parcc_key,BridgeError
                    try:value=dict(secret=Secret(parcc_key()),provider='parcc',base_url=None)
                    except BridgeError:pass
                if value is None: raise ProviderError('provider_key_missing', 'Missing or expired credential for role '+role)
                if value['provider'] != desc['provider']:
                    raise ProviderError('provider_mismatch', 'Credential provider differs for role '+role)
                keys[name] = value['secret']
                public[role] = dict(credential=name,provider=value['provider'],model=desc['model'],base_url=value['base_url'],
                                    base_url_host=urlsplit(value['base_url'] or presets()[value['provider']].chat_url).hostname,
                                    key_source='lab' if name=='lab' else 'user')
        return RunKeys(keys, public)


def encode_for_pipe(run_keys):
    return json.dumps(dict(keys={k:v.reveal() for k,v in run_keys._keys.items()},roles=run_keys.roles)).encode()


def install_run_keys_from_fd(fd):
    harden_process()
    try:
        with os.fdopen(fd, 'rb') as stream: raw = stream.read(65537)
        if len(raw)>65536: raise ValueError()
        value = json.loads(raw)
        # Plain credential maps also support the documented stdin contract.
        keys = value['keys'] if 'keys' in value else value
        installed = {credential_name(k):Secret(v) for k,v in keys.items()}
        roles = value.get('roles', {}) if 'keys' in value else {}
        if not isinstance(roles, dict): raise ValueError()
    except Exception:
        raise ProviderError('key_pipe_invalid', 'Invalid credential pipe') from None
    _RUN_KEYS.clear(); _RUN_KEYS.update(installed)
    _RUN_ROLES.clear(); _RUN_ROLES.update(roles)


def install_run_keys_from_stdin(): install_run_keys_from_fd(0)


def run_key(credential_id):
    try: return _RUN_KEYS[credential_id]
    except KeyError: raise ProviderError('provider_key_missing', 'Run credential is missing') from None


def run_role(role): return dict(_RUN_ROLES.get(role, {}))


def assert_run_clean(*, roots=(), sqlite_paths=(), extra_blobs=(), values=()):
    if not _RUN_KEYS:return
    from .canary_scan import scan
    extra_blobs=(*extra_blobs,*(json.dumps(value,default=str) for value in values))
    if scan(_RUN_KEYS.values(),roots=roots,sqlite_paths=sqlite_paths,extra_blobs=extra_blobs,include_processes=False):
        raise ProviderError('provider_secret_in_export','Credential material detected; export refused')


def launch_run_with_keys(argv, run_keys, **popen_kwargs):
    env = popen_kwargs.get('env')
    popen_kwargs['env'] = keyless_env(os.environ if env is None else env)
    popen_kwargs['close_fds'] = True
    read_fd, write_fd = os.pipe()
    try:
        fds = tuple(popen_kwargs.pop('pass_fds', ())) + (read_fd,)
        process = subprocess.Popen([*argv,'--key-fd',str(read_fd)], pass_fds=fds, **popen_kwargs)
    except BaseException:
        os.close(write_fd)
        raise
    finally: os.close(read_fd)
    def feed():
        try:
            with os.fdopen(write_fd, 'wb') as stream: stream.write(encode_for_pipe(run_keys))
        except (BrokenPipeError, OSError): pass
    threading.Thread(target=feed, name='run-key-pipe', daemon=True).start()
    return process


def harden_process():
    try: resource.setrlimit(resource.RLIMIT_CORE, (0,0))
    except (OSError, ValueError): pass
    try: ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)
    except (OSError, AttributeError): pass
