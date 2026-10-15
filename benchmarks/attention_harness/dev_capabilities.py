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


def provider_environment(root):
    env=execution_environment(root)
    return {k:v for k,v in env.items() if not k.startswith('ATTENTION_MEMORY_')}
