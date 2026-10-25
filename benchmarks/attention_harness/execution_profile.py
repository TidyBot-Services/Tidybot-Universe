"""Display frozen execution identity without resolving today's deployment registry."""
from curobo_v2.execution_settings import D05_SHA


def config_metadata(config):
    block=config.get('execution_settings')
    return dict(execution_profile=block['profile'] if block else 'legacy',
                planner_lock_sha256=block['planner_deployment']['sha256'] if block else D05_SHA)


def manifest_metadata(manifest):
    rows=[config_metadata(c) for c in manifest.get('configs',{}).values()]
    if not rows:
        return {k:manifest.get(k,default) for k,default in config_metadata({}).items()}
    if any(row!=rows[0] for row in rows):
        raise ValueError('Mixed execution settings in one run; summarize profiles separately')
    return rows[0]
