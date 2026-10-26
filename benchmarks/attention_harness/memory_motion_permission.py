"""Import-only permission for frozen simulated Dev Memory validation evidence."""

ACTION = 'dev_tier_sim_motion'
PURPOSE = 'memory_validation'


def validation_scope(cell, version, source, config, bundle, purpose):
    from .dev_planner import binding
    if purpose != PURPOSE:
        raise ValueError('simulated Dev motion is only for Memory validation imports')
    if (config.get('budget_tier') != 'dev' or config.get('runner_boundary') != 'dev_loop'
            or config.get('budget_boundary', 'dev_loop') != 'dev_loop'
            or bundle.get('runner_boundary') != 'dev_loop'
            or bundle.get('channel') != 'dev_v2' or bundle.get('formal_eligible') is not False
            or bundle.get('entry_lock') is not None):
        raise ValueError('simulated Dev motion requires frozen dev tier and dev boundary')
    if (bundle.get('suite') != 'robocasa365'
            or any(v.get('execution_target', 'robocasa365_sim') != 'robocasa365_sim' for v in (config, bundle))):
        raise ValueError('simulated Dev motion requires robocasa365_sim')
    scope = binding(cell, version, source, config)
    if scope is None:
        raise ValueError('simulated Dev motion requires a motion inventory')
    return dict(scope, action=ACTION, actor=ACTION)
