"""Frozen development pairs, with a dedicated simulator and Agent for each arm."""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

U=Path('/home/truares/桌面/Tidybot-Universe-attention-native')
M=Path('/home/truares/桌面/attention_memory_service')
C=Path('/home/truares/桌面/maniskill_sim-attention-variation')
A=Path('/home/truares/文档/Tidybot-Universe/agent_server')
T=Path('/home/truares/文档/Tidybot-Universe/sims/robocasa_tasks')
R=Path(__file__).resolve().parent
sys.path[:0]=[str(U),str(M)]
from attention_memory_service import MemoryService
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.memory_agent import MemoryAgent, TrialEvidence
from benchmarks.attention_harness.robocasa_native.agent_actions import AgentServerActionBackend
from benchmarks.attention_harness.robocasa_native.client import RobocasaSimClient
from benchmarks.attention_harness.robocasa_native.formal_services import DedicatedRobocasaServices, source_identity
from benchmarks.attention_harness.robocasa_native.paired_trials import RoboCasaPairedTrialExecutor

FREEZE=R/'robocasa_validation_freeze.json'
frozen=json.loads(FREEZE.read_text())
assert hashlib.sha256(FREEZE.read_bytes()).hexdigest()=='d84ac3f307f98d94b91f2a50564ac3a9d1a1274392dd12d950190bb673e8be63'
for name,path in [('universe',U),('memory',M),('robocasa_service',C),('agent_server',A),('task_source',T)]:
    assert source_identity(path)==frozen['service_versions'][name],name
policy=R/frozen['policy']['path']
assert hashlib.sha256(policy.read_bytes()).hexdigest()==frozen['policy']['sha256']
GRAPH=Path('/home/truares/桌面/attentionbench-week2-graph-live-20260927/robocasa')
store_path=GRAPH/'attention_memory.sqlite3'
service=MemoryService(AttentionStore(store_path),artifact_root=GRAPH/'attention-robocasa-counter_to_sink-seed101-59f3e6e1b326'/'attempts')
agent=MemoryAgent(service)
ID=frozen['memory_id']
assert service.get_plan(ID)['cases']==frozen['scope']['cases']
pairs=agent.plan_validation(ID,cases=tuple(frozen['scope']['cases']),assistance_credits=0)
STATE=R/'robocasa_pair_progress.json'
if STATE.exists():
    progress=json.loads(STATE.read_text())
else:
    progress={'schema_version':'attentionbench.robocasa-pair-progress.v1','formal_eligible':False,
              'freeze_sha256':hashlib.sha256(FREEZE.read_bytes()).hexdigest(),
              'arms':{},'pairs':{},'blockers':[]}
assert progress['freeze_sha256']==hashlib.sha256(FREEZE.read_bytes()).hexdigest()

def save():
    tmp=STATE.with_name(STATE.name+'.tmp')
    tmp.write_text(json.dumps(progress,indent=2,sort_keys=True)+'\n')
    os.replace(tmp,STATE)

if __name__ == '__main__':
    for control,treatment in pairs:
        seed=control.seed
        slots=progress['arms'].setdefault(str(seed),{})
        for label,trial in [('control',control),('treatment',treatment)]:
            if label in slots:
                path=Path(slots[label]['safety_path'])
                assert hashlib.sha256(path.read_bytes()).hexdigest()==slots[label]['safety_sha256']
                continue
            conf_path=R/frozen['case_configs'][str(seed)]['path']
            assert hashlib.sha256(conf_path.read_bytes()).hexdigest()==frozen['case_configs'][str(seed)]['sha256']
            conf=json.loads(conf_path.read_text())
            assert conf['seed']==seed
            assert conf['sim_service']=={k:v for k,v in frozen['service_versions']['robocasa_service'].items() if k!='dirty'}
            log_dir=R/'robocasa_pair_services'/f'seed{seed}'/label
            dedicated=DedicatedRobocasaServices(
                task_id='counter_to_sink',sim_source_root=C,agent_source_root=A,task_source_root=T,
                sim_python=Path('/home/truares/miniconda3/envs/maniskill/bin/python'),
                agent_python=Path('/home/truares/miniconda3/envs/maniskill/bin/python'),
                expected_sim=conf['sim_service'],expected_agent=conf['agent_service'],
                expected_task=conf['task_source'],expected_runtime=conf['sim_runtime'],
                port_offset=conf['port_offset'],log_dir=log_dir,deadline=time.monotonic()+300)
            try:
                with dedicated:
                    executor=RoboCasaPairedTrialExecutor(
                        policy_code_path=policy,policy_id=frozen['policy']['policy_id'],
                        artifact_root=R/'robocasa_pair_attempts',store_path=store_path,
                        backend_factory=lambda:AgentServerActionBackend(
                            base_url=dedicated.agent_url,simulator_attested=True,
                            holder=f'paired-{seed}-{label}',timeout_seconds=90,poll_seconds=0.1),
                        client_factory=lambda task_id:RobocasaSimClient(task_id,base_url=dedicated.sim_url),
                        memory_gateway=service,max_delta_m=0.25,max_observed_step_m=0.5,
                        timeout_seconds=240)
                    item=executor(trial)
            finally:
                stop=dedicated.stop_receipt
            assert isinstance(item,TrialEvidence)
            safety_path=item.safety_artifact.resolve()
            result_path=safety_path.parent/'result.json'
            result=json.loads(result_path.read_text())
            safety=json.loads(safety_path.read_text())
            assert stop is not None and all(x['process_group_gone'] for x in stop['services'].values())
            assert result['policy_sha256']==frozen['policy']['sha256']
            assert result['attention_trace']['attempt_id']==item.attempt_id
            slots[label]={'attempt_id':item.attempt_id,'safety_path':str(safety_path),
                          'safety_sha256':hashlib.sha256(safety_path.read_bytes()).hexdigest(),
                          'config_sha256':item.config_sha256,'result_path':str(result_path),
                          'result_sha256':hashlib.sha256(result_path.read_bytes()).hexdigest(),
                          'status':result['status'],'native_success':result['native_success'],
                          'native_evaluated':service.v2._trial(item.attempt_id,allow_terminal_failure=True)['evaluator_authoritative'],
                          'unsafe_attempts':safety['unsafe_attempts'],'service_stop':stop}
            save()
            print(json.dumps({'seed':seed,'arm':label,'status':result['status'],
                              'native_success':result['native_success'],
                              'unsafe_attempts':safety['unsafe_attempts']}),flush=True)
        if str(seed) not in progress['pairs']:
            assert slots['control']['config_sha256']==slots['treatment']['config_sha256']
            pair=service.record_pair(memory_id=ID,
                control_attempt_id=slots['control']['attempt_id'],
                treatment_attempt_id=slots['treatment']['attempt_id'],
                control_safety=Path(slots['control']['safety_path']),
                treatment_safety=Path(slots['treatment']['safety_path']))
            progress['pairs'][str(seed)]=pair
            save()
            print(json.dumps({'seed':seed,'pair_registered':True}),flush=True)
    report=service.impact_report(ID)
    (R/'robocasa_pair_impact.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps({'pairs':len(progress['pairs']),'treatment_successes':report['treatment_successes'],
                      'safety_regressions':len(report['safety_regressions'])}))
