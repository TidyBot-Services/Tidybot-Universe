"""Creation-only options for the single-cell native-agent condition."""
import math

FIELDS={'dev_mode','dev_model','dev_reasoning_effort','eval_model','quota_stop_percent'}
MODELS={'gpt-6-astra','gpt-6-sol'}


def normalize(selection, *, validation_origin=None):
    if selection.get('dev_mode')!='agent':raise ValueError('agent_mode_required')
    if selection.get('budget_tier','dev')!='dev' or 'configuration' in selection:
        raise ValueError('agent_requires_modern_dev_tier')
    if validation_origin is not None or any(selection.get(k) for k in
            ('validation_experiment','positive_control','memory_authority','planner_features','memory_family','fork')):
        raise ValueError('agent_memory_or_control_not_supported')
    if selection.get('model_source','codex')!='codex':raise ValueError('agent_requires_codex')
    if any(len(selection.get(k,[]))!=1 for k in ('tasks','seeds','policies')):
        raise ValueError('agent_single_cell_required')
    if selection.get('mode','experiment') not in ('experiment','interactive'):raise ValueError('agent_mode_not_supported')
    result=dict(dev_mode='agent',dev_model=selection.get('dev_model','gpt-6-astra'),
                eval_model=selection.get('eval_model','gpt-6-astra'),
                dev_reasoning_effort=selection.get('dev_reasoning_effort','high'),
                quota_stop_percent=selection.get('quota_stop_percent',90))
    if result['dev_model'] not in MODELS or result['eval_model'] not in MODELS:raise ValueError('unsupported_agent_model')
    if result['dev_reasoning_effort'] not in ('low','medium','high'):raise ValueError('unsupported_agent_effort')
    quota=result['quota_stop_percent']
    if type(quota) not in (int,float) or not math.isfinite(quota) or not 0<quota<=100:raise ValueError('invalid_quota_stop_percent')
    return result


def configuration(selection):
    options=normalize(selection)
    from .dev_configuration import normalize_configuration
    clean={k:v for k,v in selection.items() if k not in FIELDS}
    clean.update(model_source='codex',target_depth=10,target_valid_executions=10,max_versions=10,
                 coverage='budget_limited',attempt_wall_seconds=1920,wall_limit=21600)
    return dict(normalize_configuration(clean),**options)


def refuse_memory(value):
    """Admission guard used before import or old validation provisioning."""
    if value.get('dev_mode')=='agent' or value.get('selection',{}).get('dev_mode')=='agent':
        raise ValueError('agent_memory_auto_import_and_validation_unsupported')
