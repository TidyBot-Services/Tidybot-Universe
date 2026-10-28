"""Dev source rules, available without the Memory service.

The service keeps its own attention_memory_service.source_policy implementation.
Cross-tree contract tests keep tier, recorded-limit and drift semantics aligned.
"""
from pathlib import Path

LEGACY_SOURCE_BYTES = 16384
DEV_SOURCE_BYTES = 32768


def dev_tier(config):
    return (config.get('budget_tier') == 'dev'
            and config.get('budget_boundary', config.get('runner_boundary')) == 'dev_loop')


def source_limit(config, manifest=None):
    if config.get('dev_mode') == 'agent':
        if not dev_tier(config):raise ValueError('agent_requires_dev_tier')
        if manifest is not None and manifest.get('source_size_limit_bytes',131072)!=131072:
            raise ValueError('agent_source_limit_mismatch')
        return 131072
    if manifest is not None and 'source_size_limit_bytes' in manifest:
        return manifest['source_size_limit_bytes']
    return DEV_SOURCE_BYTES if dev_tier(config) else LEGACY_SOURCE_BYTES


def source_file(lock, name):
    path = Path(name)
    root = Path(lock['source_root'])
    if not path.is_relative_to(root):
        return False
    rel = path.relative_to(root)
    return ('model' not in rel.parts and name not in lock.get('model_sources', {})
            and name != lock['planner_lock']['path']
            and (str(rel)=='benchmarks/attention_harness/dev_failure_rules.json'
                 or rel.parts[0] in {'openarm365', 'openarm365_dev', 'openarm365_dev_v2', 'curobo_v2'} and path.suffix == '.py'
                 or str(rel).startswith('benchmarks/attention_harness/') and path.suffix in {'.py', '.js', '.html', '.css'}))


def drift_record(config, origin, current, origin_sha, current_sha):
    """Only source contents/sets may differ. Resources and planner remain exact."""
    if origin == current:
        return None
    if not dev_tier(config):
        raise ValueError('source evidence digest drift')
    if {k:v for k,v in origin.items() if k not in ('files','source_sets')} != {k:v for k,v in current.items() if k not in ('files','source_sets')}:
        raise ValueError('non-source identity drift')
    if set(origin['source_sets']) != set(current['source_sets']):
        raise ValueError('SDK source root drift')
    differing=[]
    for name in sorted(set(origin['files']) | set(current['files'])):
        old=origin['files'].get(name);new=current['files'].get(name)
        if old != new:
            if not source_file(origin,name):raise ValueError('non-source identity drift: '+name)
            differing.append(dict(path=name,origin_sha256=old,current_sha256=new))
    for lock in (origin,current):
        for folder,names in lock['source_sets'].items():
            if any(n not in lock['files'] or not Path(n).is_relative_to(folder) for n in names):
                raise ValueError('incomplete source file set')
    return dict(schema_version='attentionbench.source-drift.v1',origin_lock_sha256=origin_sha,
                current_lock_sha256=current_sha,files=differing)
