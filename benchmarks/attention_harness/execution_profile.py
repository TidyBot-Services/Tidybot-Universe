"""Display frozen execution identity without resolving today's deployment registry."""
from curobo_v2.execution_settings import D05_SHA


def config_metadata(config):
    block=config.get('execution_settings')
    value=dict(execution_profile=block['profile'] if block else 'legacy',
                planner_lock_sha256=block['planner_deployment']['sha256'] if block else D05_SHA)
    if config.get('planner_source',{}).get('kind')=='dev-local':value.update(deployment_kind='dev-local',formal_eligible=False)
    from .dev_tasks import SDK_SEAL_KEYS
    value.update({k:config[k] for k in SDK_SEAL_KEYS if k in config})
    return value


def manifest_metadata(manifest):
    rows=[config_metadata(c) for c in manifest.get('configs',{}).values()]
    if not rows:
        value={k:manifest.get(k,default) for k,default in config_metadata({}).items()}
        if manifest.get('deployment_kind')=='dev-local':value.update(deployment_kind='dev-local',formal_eligible=False)
        return value
    if any(row!=rows[0] for row in rows):
        raise ValueError('Mixed execution settings in one run; summarize profiles separately')
    return rows[0]
