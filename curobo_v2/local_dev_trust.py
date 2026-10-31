"""Resolve development pins from repository policy before protected local policy."""
import hashlib
import json
from pathlib import Path


def _repository_entries():
    from . import identity
    raw = identity.DEPLOYMENT_REGISTRY.read_bytes()
    current = hashlib.sha256(raw).hexdigest()
    if identity._registry_digest is not None and current != identity._registry_digest:
        raise identity.IdentityError('Deployment trust policy changed during this process')
    return json.loads(raw)


def dev05_pin():
    from . import identity
    from openarm365_dev import config
    entries = _repository_entries()
    author = entries.get(config.LOCK_SHA)
    if isinstance(author, dict) and author.get('lock_path') == str(config.LOCK):
        return {'lock_path': str(config.LOCK), 'sha256': config.LOCK_SHA}
    local = identity._local_registry()
    if local is None:
        raise ValueError('No authenticated development-05 lock')
    lock, sha, path = identity._local_entry(local, 'chain', 'd05')
    if not identity._locally_trusted_dev(lock, sha, path, 'dev', 'dev_loop'):
        raise ValueError('Unapproved local development-05 lock')
    return {'lock_path': str(path), 'sha256': sha}


def dev06_pin():
    from . import identity, execution_settings as es
    entries = _repository_entries()
    matches = [(sha, entry) for sha, entry in entries.items()
               if isinstance(entry, dict) and entry.get('profile') == es.PROFILE
               and entry.get('allowed_tiers') == ['dev']]
    if len(matches) == 1:
        sha, entry = matches[0]
        if entry.get('lock_path') != es.D06_LOCK or entry.get('derived_from_sha256') != es.D05_SHA:
            raise ValueError('Only registered deployment-06 derived from deployment-05 is accepted')
        _, actual = identity.load_lock(es.D06_LOCK, tier='dev', boundary='dev_loop')
        if actual != sha:
            raise ValueError('Registered deployment-06 digest mismatch')
        return {'lock_path': es.D06_LOCK, 'sha256': sha}
    if matches:
        raise ValueError('Exactly one registered deployment-06 required')
    local = identity._local_registry()
    if local is None:
        raise ValueError('No authenticated development-06 lock')
    lock, sha, path = identity._local_entry(local, 'chain', 'd06')
    if not identity._locally_trusted_dev(lock, sha, path, 'dev', 'dev_loop'):
        raise ValueError('Unapproved local development-06 lock')
    return {'lock_path': str(path), 'sha256': sha}
