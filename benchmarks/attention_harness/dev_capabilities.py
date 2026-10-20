"""Child roles receive only capabilities necessary for their work."""
import os


def execution_environment(root, extra=None):
    # The scheduler needs online Memory only. Import/operator/human capabilities
    # belong to independent authority processes, including file locations.
    env={k:v for k,v in os.environ.items() if not (
        k.startswith('ATTENTION_MEMORY_') and k!='ATTENTION_MEMORY_API_KEY'
        or any(s in k.upper() for s in ('IMPORTER','OPERATOR','HUMAN','CAPABILITY')))}
    env.update(PYTHONPATH=str(root),PYTHONDONTWRITEBYTECODE='1')
    env.update(extra or {})
    return env


def provider_environment(root, model_source='parcc'):
    env=execution_environment(root)
    if model_source=='codex':
        from .codex_client import codex_environment
        return {**codex_environment(), 'PYTHONPATH': str(root), 'PYTHONDONTWRITEBYTECODE': '1'}
    return {k:v for k,v in env.items() if not k.startswith('ATTENTION_MEMORY_')}
