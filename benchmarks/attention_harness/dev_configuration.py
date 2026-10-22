"""Experiment-specific coverage and worst-case resource arithmetic (no dispatch)."""
import math
from .dev_ledger import cost,RESOURCES,complete_resources,fill_optional
from openarm365_dev_v2.diag_budget import task_limits
FIELDS={'planner_features','memory_authority','scene_contract','reproducible_reset','goal_key','goal_limits','wall_limit','cell_budget','target_depth','target_valid_executions','max_versions','coverage','attempt_wall_seconds','startup_cleanup_seconds','model_reserve_seconds','max_input_bytes_per_request','public_failure_rule'}
PUBLIC_FAILURE_RULE={'schema':'attentionbench.public-failure.v1','validation_failure':True,'execution_statuses':['failed','timeout','timed_out'],'public_errors':True,'completion_signal':'none_registered','no_declared_completion_is_failure':True}
# The public trace has no authored completion signal in the current SDK contract.
# Successful process exit alone cannot declare task completion. No scoring is read.
# Per-request Dev input ceiling in UTF-8 bytes; raised from 30000 by the user on 2026-10-22
# after a run stopped at v8 with input_bound_exceeded. Cell token budgets scale with it.
MAX_INPUT_BYTES=150000

def normalize_configuration(selection):
 tier=selection.get('budget_tier','dev')
 source=selection.get('model_source','parcc')
 if source not in {'parcc','codex'} or (source=='codex' and (tier!='dev' or 'configuration' in selection)):raise ValueError('Codex model source requires modern dev tier')
 if source=='codex' and selection.get('model','gpt-6-astra')!='gpt-6-astra':raise ValueError('Codex model must be gpt-6-astra')
 if source=='codex' and selection.get('reasoning_effort','high')!='high':raise ValueError('Codex reasoning effort must be high')
 p=dict(target_depth=3,target_valid_executions=3,max_versions=5,coverage='strict_full_depth',attempt_wall_seconds=task_limits(tier)['attempt_seconds'],startup_cleanup_seconds=60,model_reserve_seconds=360 if tier=='dev' else 120,max_input_bytes_per_request=MAX_INPUT_BYTES,public_failure_rule=PUBLIC_FAILURE_RULE)
 p.update({k:v for k,v in selection.items() if k in FIELDS})
 if 'goal_key' in p:
  import re
  if not isinstance(p['goal_key'],str) or not re.fullmatch('[A-Za-z0-9_.-]{8,100}',p['goal_key']):raise ValueError('stable goal key required')
 if 'reproducible_reset' in p and type(p['reproducible_reset']) is not bool:raise ValueError('boolean reset contract required')
 if 'planner_features' in p:
  from .dev_planner import configuration,POLICY
  features=p['planner_features']
  if isinstance(features,dict) and set(features)=={'memory'}:
   features={**features,'human_wait_seconds':0,'background_seconds':0,'approval_rule':'none'}
  p['planner_features']=configuration(features)
  if selection.get('policies')!=[POLICY]:raise ValueError('planner features require only the full planner policy')
 if 'memory_authority' in p:
  # Shared Memory authority (plan 0d, second revision): every policy reads it.
  from .dev_planner import memory_settings
  memory_settings(p['memory_authority'])
  if (p.get('planner_features') or {}).get('memory') is not None:raise ValueError('Memory authority is bound once, outside planner features')
 if 'human_proxy' in selection:
  raise ValueError('legacy human_proxy can be read but not configured for a new v3 experiment')
 from .eval_agent import normalize as normalize_eval,default_config as eval_default
 from .dev_advisor_proxy import normalize as normalize_advisor,default_config as advisor_default
 p['eval_agent']=normalize_eval(selection.get('eval_agent',eval_default(False,source)),source)
 p['advisor_proxy']=normalize_advisor(selection.get('advisor_proxy',advisor_default(False,source)),source)
 if source=='codex':p['model_source']='codex'
 independent_eval_advisor(p['eval_agent'],p['advisor_proxy'])
 p['help_budget']=selection.get('help_budget',1)
 for k in ['target_depth','target_valid_executions','max_versions','attempt_wall_seconds','startup_cleanup_seconds','model_reserve_seconds','max_input_bytes_per_request']:
  if type(p[k]) is not int or p[k]<1:raise ValueError('positive integer configuration required: '+k)
 if p['target_depth']>p['max_versions'] or p['target_valid_executions']>p['max_versions']:raise ValueError('depth/executions must fit maximum versions')
 if p['coverage'] not in {'strict_full_depth','budget_limited'}:raise ValueError('unknown coverage')
 if p['public_failure_rule']!=PUBLIC_FAILURE_RULE:raise ValueError('unregistered public failure rule')
 # SDK Safety ceilings are preserved; lowering an experiment wall is supported.
 if p['attempt_wall_seconds']>task_limits(tier)['attempt_seconds'] or p['model_reserve_seconds'] not in ((120,240,360) if tier=='dev' else (120,)):raise ValueError('SDK/provider deadline contract exceeded')
 if tier=='dev':p['budget_tier']='dev'
 if p['max_input_bytes_per_request']>MAX_INPUT_BYTES:raise ValueError('public input contract exceeded')
 return p

def independent_eval_advisor(evaluation,advisor):
 """The only same-model exception: distinct prompts, cache namespaces and calls."""
 if evaluation['model']==advisor['model'] and (
  evaluation['prompt_version']==advisor['prompt_version'] or
  evaluation['prompt_sha256']==advisor['prompt_sha256'] or
  evaluation['cache_version']==advisor['cache_version'] or
  not evaluation.get('cache_namespace') or not advisor.get('cache_namespace') or
  evaluation['cache_namespace']==advisor['cache_namespace'] or
  not evaluation.get('session_namespace') or not advisor.get('session_namespace') or
  evaluation['session_namespace']==advisor['session_namespace']):
  raise ValueError('Eval and Advisor sharing a model require distinct prompts, cache namespaces and session namespaces')
 return True
def require_scene_gate(mode,selection,tasks,seeds):
 """Experiment-mode runs must start every cell from the registered physical scene.

 Without reproducible reset the same (task, seed) lands on different scenes between processes, so
 policies are no longer compared on equal ground. Fails closed at creation, before any dispatch:
 reproducible reset must be an explicit True, and a seed-keyed scene contract must cover every
 selected (task, seed). Diagnostic and interactive modes are exempt; they exist to look at scenes."""
 if mode!='experiment':return
 if selection.get('configuration') is not None:return  # legacy independent-ledger path (daily bridge) cannot express either option; not covered by this gate
 if selection.get('reproducible_reset') is not True:raise ValueError('experiment mode requires reproducible_reset: true')
 ref=selection.get('scene_contract')
 if not isinstance(ref,dict):raise ValueError('experiment mode requires a registered scene_contract')
 from openarm365_dev_v2.scene_contract import load_contract,seed_entry,SCHEMA_V2
 contract=load_contract(ref)
 if contract['schema']==SCHEMA_V2:
  for t in tasks:
   for seed in seeds:seed_entry(contract,t,seed)
 elif len(seeds)!=1:raise ValueError('a single-state scene contract covers one seed; use a seed-keyed (v2) contract')

def require_eval_gate(mode, selection):
 """Every comparable cell needs the same automatic completion signal."""
 if mode=='experiment' and (not isinstance(selection.get('eval_agent'),dict) or selection['eval_agent'].get('enabled') is not True):
  raise ValueError('experiment mode requires eval_agent.enabled: true (vision Eval for every executed version)')
def derive_cell_budget(p,sdk_calls=None,horizon=None):
 limits=task_limits(p.get('budget_tier','legacy'))
 sdk_calls=limits['sdk_calls'] if sdk_calls is None else sdk_calls
 horizon=limits['control_steps'] if horizon is None else horizon
 g=p['max_versions'];e=p['target_valid_executions'];h=p['help_budget'];i=p['max_input_bytes_per_request'];s=p['model_reserve_seconds']
 from .eval_agent import FRAME_TOKEN_BOUND,MAX_TOKENS
 eval_count=e if p.get('eval_agent',{}).get('enabled') else 0
 advisor=p.get('advisor_proxy') or {}
 reading=advisor.get('reading_seconds',30)
 provider_tokens=g*(i+8192)+eval_count*(i+10*FRAME_TOKEN_BOUND+MAX_TOKENS)+h*(i+1024)
 return dict(dev_calls=g,advisor_calls=h,eval_calls=eval_count,executions=e,provider_tokens=provider_tokens,input_tokens=(g+h+eval_count)*i+eval_count*10*FRAME_TOKEN_BOUND,output_tokens=g*8192+eval_count*MAX_TOKENS+h*1024,model_seconds=g*s+(h+eval_count)*120,help_wait_seconds=h*120+g*p.get('planner_features',{}).get('human_wait_seconds',0),help_count=h,help_credits=h,hint_human_seconds=h*(p['attempt_wall_seconds']+reading) if advisor.get('enabled') else 0,execution_seconds=e*(p['attempt_wall_seconds']+p['startup_cleanup_seconds']),sdk_calls=e*sdk_calls,control_steps=e*horizon)

def configured_budget(p):
 limits=task_limits(p.get('budget_tier','legacy'))
 required=derive_cell_budget(p,limits['sdk_calls'],limits['control_steps']);b=p.get('cell_budget',required);cost(b)
 if complete_resources(b):b=fill_optional(b)
 if set(b)!=set(RESOURCES):raise ValueError('complete cell budget required')
 from .free_models import limited_resources
 from .dev_experiment import MODEL
 limited=limited_resources(eval_config=p['eval_agent'],model_names=(('gpt-6-astra' if p.get('model_source')=='codex' else MODEL['model']),p['advisor_proxy']['model']),model_source=p.get('model_source','parcc'))
 if p['coverage']=='strict_full_depth' and any(b[k]<required[k] for k in limited):raise ValueError('strict cell budget below worst-case requirement')
 minimum=derive_cell_budget({**p,'max_versions':1,'target_valid_executions':1,'help_budget':0})
 if any(b[k]<minimum[k] for k in limited):raise ValueError('zero capacity budget refused')
 return b
def worst_slot(p):return (p['model_reserve_seconds']+120*(1+int(p.get('eval_agent',{}).get('enabled',False)))+p['attempt_wall_seconds']+p['startup_cleanup_seconds']+p.get('planner_features',{}).get('human_wait_seconds',0)+2*p.get('planner_features',{}).get('background_seconds',0))
