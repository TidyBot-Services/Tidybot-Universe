"""Freeze the development validation plan before any paired trial."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

U=Path('/home/truares/桌面/Tidybot-Universe-attention-native')
M=Path('/home/truares/桌面/attention_memory_service')
C=Path('/home/truares/桌面/maniskill_sim-attention-variation')
A=Path('/home/truares/文档/Tidybot-Universe/agent_server')
T=Path('/home/truares/文档/Tidybot-Universe/sims/robocasa_tasks')
ROOT=Path(__file__).resolve().parent
sys.path[:0]=[str(U),str(M)]
from attention_memory_service import MemoryService
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.memory_agent import MemoryAgent
from benchmarks.attention_harness.robocasa_native.formal_services import source_identity

ID='candidate:attempt:attention-robocasa-counter_to_sink-seed101-59f3e6e1b326:0'
GRAPH=Path('/home/truares/桌面/attentionbench-week2-graph-live-20260927/robocasa')
service=MemoryService(AttentionStore(GRAPH/'attention_memory.sqlite3'),
                      artifact_root=GRAPH/'attention-robocasa-counter_to_sink-seed101-59f3e6e1b326'/'attempts')
agent=MemoryAgent(service)
rows=json.loads((ROOT/'robocasa_discovered_cases.json').read_text())['rows']
camera_choices={101:['base_camera','wrist_camera'],102:['base_camera'],
                103:['base_camera','wrist_camera'],104:['base_camera'],
                105:['base_camera','wrist_camera']}
cases=[]
for row in rows:
    seed=row['seed'];language=row['language'];camera_names=camera_choices[seed]
    label=language.split('pick the ',1)[1].split(' from the counter',1)[0]
    cases.append({'seed':seed,**row['reset']['applied_variation'],
                  'camera_config_id':'camera:base+wrist' if len(camera_names)==2 else 'camera:base',
                  'task_variant_id':'task:'+label.replace(' ','_'),
                  'camera_names':camera_names,'task_prompt':language})
policy=ROOT/'robocasa_candidate_validation_policy.py'
policy_sha=hashlib.sha256(policy.read_bytes()).hexdigest()
source=service.check_candidate(ID)
assert source['status']=='candidate' and service.get_plan(ID) is None and not service.list_pairs(ID)
versions={name:source_identity(path) for name,path in
          [('universe',U),('memory',M),('robocasa_service',C),('agent_server',A),('task_source',T)]}
assert all(not item['dirty'] for item in versions.values())
configs={}
for seed in camera_choices:
    p=ROOT/f'formal_robocasa_counter_to_sink_seed{seed}_rebind.json'
    configs[str(seed)]={'path':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
freeze={'schema_version':'attentionbench.robocasa-memory-validation-freeze.v1',
        'formal_eligible':False,'memory_id':ID,'decision':'approved_for_development_validation_only',
        'source_status':'candidate','source_evidence_verified':True,
        'scope':{'suite':'robocasa','task_id':'counter_to_sink','perception_mode':'sim_gt',
                 'cases':cases,'retrieval_scope_after_promotion':'only successful case tuples'},
        'seeds':[101,102,103,104,105],
        'policy':{'path':policy.name,'sha256':policy_sha,'policy_id':source['source_policy_id'],
                  'control':'no candidate exposure; reproduce source no-action baseline',
                  'treatment':'candidate_validation; explicit retrieve_memory(id) event'},
        'budgets':{'assistance_credits_per_arm':0,'policy_wall_seconds_per_arm':240,
                   'agent_job_timeout_seconds':90,'service_deadline_seconds_per_arm':300,
                   'max_sdk_calls':200,'total_arms':10},
        'order':'for each seed in ascending order, control then treatment; never replace a failed case',
        'safety_limits':{'max_delta_m':0.25,'max_observed_step_m':0.5,
                         'max_base_delta_m':0.3,'max_base_rotation_rad':0.5},
        'service_versions':versions,'case_configs':configs,
        'source_store':str((GRAPH/'attention_memory.sqlite3').resolve())}
p=ROOT/'robocasa_validation_freeze.json'
if p.exists():
    assert json.loads(p.read_text())==freeze
else:
    p.write_text(json.dumps(freeze,indent=2,sort_keys=True)+'\n')
preflight=agent.preflight_validation(ID,cases=tuple(cases),assistance_credits=0,
                                      validation_policy_sha256=policy_sha)
assert preflight['remaining_seeds']==freeze['seeds']
recorded=agent.plan_validation(ID,cases=tuple(cases),assistance_credits=0)
(ROOT/'robocasa_validation_preflight.json').write_text(json.dumps({
    'freeze_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
    'preflight':preflight,'recorded_plan':service.get_plan(ID),
    'paired_order':[(a.seed,a.treatment,b.treatment) for a,b in recorded],
},indent=2,sort_keys=True)+'\n')
print(json.dumps({'freeze_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
                  'policy_sha256':policy_sha,'plan_cases':len(recorded)}))
