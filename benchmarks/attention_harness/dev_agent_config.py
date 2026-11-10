"""Creation-only options for the single-cell native-agent condition."""
import math

NO_HELP_POLICIES={'autonomous','demo_first'}
FIELDS={'dev_mode','dev_model','dev_reasoning_effort','eval_model','quota_stop_percent','advisor_model','agent_policy_version','agent_provider','agent_limits','human_reply_seconds'}
# The Agent envelope. A run may lower versions and run time (cheaper tests); it can never raise them.
DEFAULT_VERSIONS=10
DEFAULT_WALL_SECONDS=21600
MIN_WALL_SECONDS=1200
MODELS={'gpt-6.1-sol','gpt-6-astra','gpt-6-sol'}
DEFAULT_MODEL='gpt-6.1-sol'
# AdvisorProxy on agent runs (10-29). Absent in older selections, which keep the fixed one-call Advisor.
ADVISOR_MODELS={'gpt-6-astra','gpt-6.1-sol'}
# Lab Dev also supports PARCC GLM through the text-only loopback Responses bridge.
DEV_MODELS=MODELS|{'parcc/GLM'}
# PARCC is the free lab source for all three roles. GLM reads text only. Qwen reads images and calls tools; as Eval it often
# ended on prose instead of the JSON verdict (real check 2026-11-05: 1 of 6), so since 2026-11-11 (user) it may run Eval
# with the verdict repair in dev_agent_eval. gpt-oss-120b reads text only and PARCC runs no tool calls for it
# (2026-11-11), so it is offered only to the Advisor, a single call. Dev stays on GLM (user, 2026-11-11).
PARCC_MODELS={'dev':{'parcc/GLM'},'eval':{'parcc/GLM','parcc/Qwen'},'advisor':{'parcc/GLM','parcc/Qwen','openai/gpt-oss-120b'}}
TEXT_ONLY_MODELS={'parcc/GLM','openai/gpt-oss-120b'}


def lab_parcc(desc):
    return isinstance(desc,dict) and desc.get('credential')=='lab' and desc.get('provider')=='parcc'


def text_only(desc):
    """A role that is sent no images: a lab model that reads none, or a model declared so after its image check failed."""
    return isinstance(desc,dict) and (desc.get('vision') is False or (lab_parcc(desc) and desc.get('model') in TEXT_ONLY_MODELS))


def routed(desc):
    """A role that runs through a provider bridge instead of Codex: a user's key, or the lab's PARCC key."""
    return isinstance(desc,dict) and (desc.get('credential','lab')!='lab' or lab_parcc(desc))


def every_role(value,test):
    provider=value.get('agent_provider')
    if not isinstance(provider,dict):return False
    roles=provider.get('roles')
    return isinstance(roles,dict) and all(isinstance(roles.get(role),dict) and test(roles[role]) for role in ('dev','eval','advisor'))


def all_user_roles(value):
    return every_role(value,lambda desc:desc.get('credential','lab')!='lab')


def display_source(value):
    """Keep the frozen execution schema; label providers in public views."""
    return 'byok' if all_user_roles(value) else 'parcc' if every_role(value,lab_parcc) else value.get('model_source','codex')


def reject_secret_fields(value):
    if isinstance(value,dict):
        if any(str(k).lower() in ('key','api_key','secret','token','authorization') for k in value):
            raise ValueError('agent_provider_secret_field_forbidden')
        for item in value.values():reject_secret_fields(item)
    elif isinstance(value,(list,tuple)):
        for item in value:reject_secret_fields(item)


def provider_roles(value):
    from .dev_agent_key_vault import credential_name
    from .dev_agent_provider import presets
    from urllib.parse import urlsplit
    if not isinstance(value,dict) or set(value)!={'roles'}:raise ValueError('invalid_agent_provider')
    roles=value['roles'];result={}
    if not isinstance(roles,dict) or 'dev' not in roles or set(roles)-{'dev','eval','advisor'}:raise ValueError('invalid_agent_provider_roles')
    for role,desc in roles.items():
        if not isinstance(desc,dict) or not {'credential','provider','model'}<=set(desc) or set(desc)-{'credential','provider','model','base_url_host','key_source','thinking','vision'}:
            raise ValueError('invalid_agent_provider_role')
        # Only Eval and Advisor look at images, and the only statement allowed is "this one cannot".
        if 'vision' in desc and (desc['vision'] is not False or role=='dev'):raise ValueError('invalid_agent_provider_vision')
        credential_name(desc['credential'])
        if not isinstance(desc['provider'],str) or desc['provider'] not in presets():raise ValueError('unsupported_agent_provider')
        model=desc['model']
        if not isinstance(model,str) or not model.strip() or len(model)>128:raise ValueError('invalid_agent_provider_model')
        if role=='dev' and model=='parcc/GLM' and desc['provider']!='parcc':raise ValueError('agent_provider_model_mismatch')
        if desc['credential']=='lab':
            if desc['provider'] not in ('openai','parcc'):raise ValueError('lab_provider_not_supported')
            allowed=PARCC_MODELS[role] if desc['provider']=='parcc' else DEV_MODELS if role=='dev' else ADVISOR_MODELS if role=='advisor' else MODELS
            if model not in allowed:raise ValueError('unsupported_agent_model')
        host=desc.get('base_url_host') if desc['provider']=='openai-compatible' else urlsplit(presets()[desc['provider']].chat_url).hostname
        if host is not None and (not isinstance(host,str) or not host or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-:' for c in host)):
            raise ValueError('invalid_base_url_host')
        result[role]=dict(credential=desc['credential'],provider=desc['provider'],model=model,base_url_host=host,
                          key_source='lab' if desc['credential']=='lab' else 'user')
        # Disclosed, never silent: a preset that switches the provider's thinking mode off says so in the manifest.
        thinking=presets()[desc['provider']].thinking
        if desc.get('thinking',thinking)!=thinking:raise ValueError('invalid_agent_provider_thinking')
        if thinking:result[role]['thinking']=thinking
        if 'vision' in desc:result[role]['vision']=False
    return dict(roles=result)


def agent_limits(value):
    """Validated reduced budget, or None when the request equals the official budget (nothing to freeze)."""
    if not isinstance(value,dict) or set(value)!={'versions','wall_seconds'}:raise ValueError('invalid_agent_limits')
    versions,wall=value['versions'],value['wall_seconds']
    if type(versions) is not int or type(wall) is not int:raise ValueError('invalid_agent_limits')
    if versions>DEFAULT_VERSIONS or wall>DEFAULT_WALL_SECONDS:raise ValueError('agent_limits_exceed_envelope')
    if versions<1 or wall<MIN_WALL_SECONDS:raise ValueError('agent_limits_below_minimum')
    if (versions,wall)==(DEFAULT_VERSIONS,DEFAULT_WALL_SECONDS):return None
    return dict(versions=versions,wall_seconds=wall)


def normalize(selection, *, validation_origin=None):
    reject_secret_fields(selection)
    if selection.get('dev_mode')!='agent':raise ValueError('agent_mode_required')
    if selection.get('budget_tier','dev')!='dev' or 'configuration' in selection:
        raise ValueError('agent_requires_modern_dev_tier')
    origin=validation_origin or selection.get('agent_validation')
    if origin is not None:
        from .memory_agent_validation import validate_options
        validate_options(selection,origin)
    if any(selection.get(k) for k in ('validation_experiment','positive_control','planner_features','fork')):
        raise ValueError('agent_memory_or_control_not_supported')
    if selection.get('model_source','codex')!='codex':raise ValueError('agent_requires_codex')
    if any(len(selection.get(k,[]))!=1 for k in ('tasks','seeds','policies')):
        raise ValueError('agent_single_cell_required')
    if selection.get('mode','experiment') not in (('diagnostic',) if origin else ('experiment','interactive')):raise ValueError('agent_mode_not_supported')
    if selection.get('trace_visible',True) is not True:raise ValueError('agent_requires_full_public_trace')
    result=dict(dev_mode='agent',dev_model=selection.get('dev_model',DEFAULT_MODEL),
                eval_model=selection.get('eval_model',DEFAULT_MODEL),
                dev_reasoning_effort=selection.get('dev_reasoning_effort','high'),
                quota_stop_percent=selection.get('quota_stop_percent',90))
    version=selection.get('agent_policy_version','agent-policy-v1')
    if version not in ('agent-policy-v1','agent-policy-v2'):raise ValueError('unsupported_agent_policy_version')
    if version=='agent-policy-v2':result['agent_policy_version']=version
    if 'agent_provider' in selection:
        result['agent_provider']=provider_roles(selection['agent_provider'])
        roles=result['agent_provider']['roles']
        result['dev_model']=roles['dev']['model']
        for role in ('eval','advisor'):
            if role in roles:result[role+'_model']=roles[role]['model']
    roles=result.get('agent_provider',{}).get('roles',{})
    if 'agent_provider' not in selection and result['dev_model'] not in DEV_MODELS:raise ValueError('unsupported_agent_model')
    if not routed(roles.get('eval')) and result['eval_model'] not in MODELS:raise ValueError('unsupported_agent_model')
    if result['dev_reasoning_effort'] not in ('low','medium','high'):raise ValueError('unsupported_agent_effort')
    if 'advisor_model' in selection or 'advisor' in roles:
        model=roles['advisor']['model'] if 'advisor' in roles else selection['advisor_model']
        if not routed(roles.get('advisor')) and model not in ADVISOR_MODELS:raise ValueError('unsupported_agent_model')
        result['advisor_model']=model
    if origin is not None:result['agent_validation']=origin
    if 'agent_limits' in selection:
        if origin is not None:raise ValueError('agent_limits_not_supported_for_validation')
        limits=agent_limits(selection['agent_limits'])
        if limits:result['agent_limits']=limits
    if 'human_reply_seconds' in selection:
        from .dev_agent_human_reply import MIN_SECONDS,MAX_SECONDS
        seconds=selection['human_reply_seconds']
        if type(seconds) is not int or not MIN_SECONDS<=seconds<=MAX_SECONDS:raise ValueError('invalid_human_reply_seconds')
        # A person's words change the run, so a frozen comparison stays Advisor only.
        if selection.get('mode','experiment')!='interactive' or version!='agent-policy-v2':raise ValueError('human_reply_needs_interactive_policy_v2')
        result['human_reply_seconds']=seconds
    elif (origin is None and selection.get('mode')=='interactive' and version=='agent-policy-v2'
          and any(policy not in NO_HELP_POLICIES for policy in selection.get('policies',()))):
        # Interactive means a person is there: help goes to them first, on any entry to the same harness.
        from .dev_agent_human_reply import DEFAULT_SECONDS
        result['human_reply_seconds']=DEFAULT_SECONDS
    quota=result['quota_stop_percent']
    if type(quota) not in (int,float) or not math.isfinite(quota) or not 0<quota<=100:raise ValueError('invalid_quota_stop_percent')
    return result


def configuration(selection):
    options=normalize(selection)
    from .dev_configuration import normalize_configuration
    clean={k:v for k,v in selection.items() if k not in FIELDS|{'agent_validation'}}
    clean.update(model_source='codex',target_depth=1 if options.get('agent_validation') else 10,
                 target_valid_executions=1 if options.get('agent_validation') else 10,
                 max_versions=1 if options.get('agent_validation') else 10,
                 coverage='budget_limited',attempt_wall_seconds=1920)
    if not options.get('agent_validation'):clean['wall_limit']=21600
    roles=options.get('agent_provider',{}).get('roles',{})
    for role,field in (('eval','eval_agent'),('advisor','advisor_proxy')):
        if routed(roles.get(role)) and field in clean:
            from .codex_client import MODEL
            clean[field]=dict(clean[field],model=MODEL,model_source='codex')
    result=dict(normalize_configuration(clean),**options)
    if 'advisor_model' in options:
        result['advisor_proxy']=dict(result['advisor_proxy'],model=options['advisor_model'])
        # Same creation option set: the (disabled) one-call Eval block records the agent Eval model.
        result['eval_agent']=dict(result['eval_agent'],model=options['eval_model'])
    elif routed(roles.get('eval')):
        result['eval_agent']=dict(result['eval_agent'],model=options['eval_model'])
    for role,field in (('eval','eval_agent'),('advisor','advisor_proxy')):
        if routed(roles.get(role)):
            result[field]=dict(result[field],model=roles[role]['model'],model_source='parcc' if lab_parcc(roles[role]) else 'byok')
            if role=='advisor':result[field]['max_attempts']=1  # Transport owns bounded retries.
    if text_only(roles.get('advisor')):
        from .dev_advisor_proxy import text_only as text_advisor
        result['advisor_proxy']=dict(text_advisor(result['advisor_proxy']),model=roles['advisor']['model'])
    return result


def refuse_memory(value):
    """Admission guard used before import or old validation provisioning."""
    if value.get('dev_mode')=='agent' or value.get('selection',{}).get('dev_mode')=='agent':
        if value.get('validation_experiment') is True:
            from .memory_agent_validation import validate_manifest
            validate_manifest(value)
            return
        raise ValueError('agent_memory_auto_import_and_validation_unsupported')
