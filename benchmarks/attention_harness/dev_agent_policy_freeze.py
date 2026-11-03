"""Creation seals and read-time checks, exclusively for agent policy v2."""
import hashlib
from pathlib import Path

V1 = 'agent-policy-v1'
V2 = 'agent-policy-v2'
SOURCES = ('dev_agent_policies.py', 'dev_agent_attention_v2.py')


def source_hashes():
    return {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in SOURCES}


def freeze(manifest):
    from .dev_agent_policies import parameters_sha256
    manifest['agent_policy_version'] = V2
    manifest['selection']['agent_policy_version'] = V2
    for row in (*manifest['configs'].values(), *manifest['cells']):
        row['agent_policy_version'] = V2
    manifest['agent_policy_parameters_sha256'] = {
        policy: parameters_sha256(policy, params) for policy, params in manifest['policies'].items()}
    manifest['agent_policy_sources_sha256'] = source_hashes()


def validate(manifest):
    version = manifest.get('agent_policy_version', V1)
    rows = [manifest['selection'], *manifest['configs'].values(), *manifest['cells']]
    if version not in (V1, V2) or any(row.get('agent_policy_version', V1) != version for row in rows):
        raise ValueError('agent_policy_version_mismatch')
    if version == V1:
        return  # Old manifests are read without rewriting or adding seals.
    from .dev_agent_policies import normalize_parameters, parameters_sha256
    if manifest.get('agent_budget',{}).get('help')!=manifest['selection']['help_budget']:
        raise ValueError('agent_help_budget_mismatch')
    policies = manifest['policies']
    if set(policies) != set(manifest['selection']['policies']):
        raise ValueError('agent_policy_selection_mismatch')
    if manifest['selection'].get('policy_parameters') != policies:
        raise ValueError('agent_policy_parameters_mismatch')
    for policy, params in policies.items():
        if normalize_parameters(policy, params) != params:
            raise ValueError('agent_policy_parameters_mismatch')
    expected = {p: parameters_sha256(p, params) for p, params in policies.items()}
    if manifest.get('agent_policy_parameters_sha256') != expected:
        raise ValueError('agent_policy_parameters_digest_mismatch')
    if manifest.get('agent_policy_sources_sha256') != source_hashes():
        raise ValueError('agent_policy_source_drift')


def validate_comparison(manifests):
    """Cross-run admission is explicit; a single-cell check cannot establish it."""
    versions = {m.get('agent_policy_version', V1) for m in manifests if m.get('dev_mode') == 'agent'}
    if len(versions) > 1:
        raise ValueError('mixed_agent_policy_versions')
    from .dev_ledger import encoded
    fields = ('agent_provider', 'dev_mode', 'model', 'dev_model', 'eval_model', 'advisor_model',
              'dev_reasoning_effort', 'agent_budget', 'cell_budget', 'target_depth',
              'max_versions', 'wall_limit', 'budget_tier', 'model_source',
              'scene_contract', 'reproducible_reset', 'agent_policy_sources_sha256',
              'memory_authority', 'run_notes', 'memory_family', 'trace_visible', 'help_pricing')
    config_fields = ('task_id', 'seed', 'layout_id', 'style_id', 'scene_reset_seed',
                     'scene_contract', 'execution_settings', 'planner_source', 'sdk_call_limit',
                     'horizon', 'attempt_wall_seconds', 'eval_agent', 'advisor_proxy',
                     'dev_sdk_sha256', 'dev_sdk_prompt_sha256')
    preregistered = {}
    for manifest in manifests:
        for policy, params in manifest['policies'].items():
            if policy in preregistered and preregistered[policy] != params:
                raise ValueError('comparison_policy_parameters_differ')
            preregistered[policy] = params
    keys = {encoded({**{k: m.get(k) for k in fields},
                     'configs': {name: {k: c.get(k) for k in config_fields}
                                 for name, c in m['configs'].items()},
                     'initial_sources': {task: r['sha256'] for task,r in m['initial_sources'].items()},
                     'tasks': m['selection']['tasks'], 'seeds': m['selection']['seeds'],
                     'help_budget': m['selection']['help_budget']}) for m in manifests}
    if len(keys) > 1:
        raise ValueError('comparison_conditions_differ')
