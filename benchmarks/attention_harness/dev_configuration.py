"""Experiment-specific coverage and worst-case resource arithmetic (no dispatch)."""
import math
from .dev_ledger import cost,RESOURCES
from .dev_tasks import SDK
FIELDS={'planner_features','scene_contract','reproducible_reset','goal_key','goal_limits','wall_limit','cell_budget','target_depth','target_valid_executions','max_versions','coverage','attempt_wall_seconds','startup_cleanup_seconds','model_reserve_seconds','max_input_bytes_per_request','public_failure_rule'}
PUBLIC_FAILURE_RULE={'schema':'attentionbench.public-failure.v1','validation_failure':True,'execution_statuses':['failed','timeout','timed_out'],'public_errors':True,'completion_signal':'none_registered','no_declared_completion_is_failure':True}
# The public trace has no authored completion signal in the current SDK contract.
# Successful process exit alone cannot declare task completion. No scoring is read.
def normalize_configuration(selection):
 p=dict(target_depth=3,target_valid_executions=3,max_versions=5,coverage='strict_full_depth',attempt_wall_seconds=480,startup_cleanup_seconds=60,model_reserve_seconds=120,max_input_bytes_per_request=30000,public_failure_rule=PUBLIC_FAILURE_RULE)
 p.update({k:v for k,v in selection.items() if k in FIELDS})
 if 'goal_key' in p:
  import re
  if not isinstance(p['goal_key'],str) or not re.fullmatch('[A-Za-z0-9_.-]{8,100}',p['goal_key']):raise ValueError('stable goal key required')
 if 'reproducible_reset' in p and type(p['reproducible_reset']) is not bool:raise ValueError('boolean reset contract required')
 if 'planner_features' in p:
  from .dev_planner import configuration,POLICY
  p['planner_features']=configuration(p['planner_features'])
  if selection.get('policies')!=[POLICY]:raise ValueError('planner features require only the full planner policy')
 p['help_budget']=selection.get('help_budget',1)
 for k in ['target_depth','target_valid_executions','max_versions','attempt_wall_seconds','startup_cleanup_seconds','model_reserve_seconds','max_input_bytes_per_request']:
  if type(p[k]) is not int or p[k]<1:raise ValueError('positive integer configuration required: '+k)
 if p['target_depth']>p['max_versions'] or p['target_valid_executions']>p['max_versions']:raise ValueError('depth/executions must fit maximum versions')
 if p['coverage'] not in {'strict_full_depth','budget_limited'}:raise ValueError('unknown coverage')
 if p['public_failure_rule']!=PUBLIC_FAILURE_RULE:raise ValueError('unregistered public failure rule')
 # SDK Safety ceilings are preserved; lowering an experiment wall is supported.
 if p['attempt_wall_seconds']>SDK['attempt_seconds'] or p['model_reserve_seconds']!=120:raise ValueError('SDK/provider deadline contract exceeded')
 if p['max_input_bytes_per_request']>30000:raise ValueError('public input contract exceeded')
 return p
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
def derive_cell_budget(p,sdk_calls=80,horizon=2400):
 g=p['max_versions'];e=p['target_valid_executions'];h=p['help_budget'];i=p['max_input_bytes_per_request'];s=p['model_reserve_seconds']
 return dict(dev_calls=g,advisor_calls=h,eval_calls=0,executions=e,provider_tokens=g*(i+8192)+h*(i+2048),input_tokens=(g+h)*i,output_tokens=g*8192+h*2048,model_seconds=(g+h)*s,help_wait_seconds=h*s+g*p.get('planner_features',{}).get('human_wait_seconds',0),help_count=h,help_credits=h,execution_seconds=e*(p['attempt_wall_seconds']+p['startup_cleanup_seconds']),sdk_calls=e*sdk_calls,control_steps=e*horizon)
def configured_budget(p):
 required=derive_cell_budget(p,SDK['sdk_calls'],SDK['control_steps']);b=p.get('cell_budget',required);cost(b)
 if set(b)==set(RESOURCES)-{'eval_calls'}:b={**b,'eval_calls':0}
 if set(b)!=set(RESOURCES):raise ValueError('complete cell budget required')
 if p['coverage']=='strict_full_depth' and any(b[k]<v for k,v in required.items()):raise ValueError('strict cell budget below worst-case requirement')
 minimum=derive_cell_budget({**p,'max_versions':1,'target_valid_executions':1,'help_budget':0})
 if any(b[k]<v for k,v in minimum.items()):raise ValueError('zero capacity budget refused')
 return b
def worst_slot(p):return (2*p['model_reserve_seconds']+p['attempt_wall_seconds']+p['startup_cleanup_seconds']+p.get('planner_features',{}).get('human_wait_seconds',0)+2*p.get('planner_features',{}).get('background_seconds',0))
