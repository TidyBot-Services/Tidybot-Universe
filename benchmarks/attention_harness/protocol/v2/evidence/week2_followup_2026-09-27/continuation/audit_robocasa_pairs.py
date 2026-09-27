"""Recheck frozen paired development originals and service recovery."""
import hashlib
import json
import sys
from pathlib import Path

R=Path(__file__).resolve().parent
U=Path('/home/truares/桌面/Tidybot-Universe-attention-native')
M=Path('/home/truares/桌面/attention_memory_service')
sys.path[:0]=[str(U),str(M)]
from attention_memory_service import MemoryService
from benchmarks.attention_harness.core.store import AttentionStore

freeze=json.loads((R/'robocasa_validation_freeze.json').read_text())
progress=json.loads((R/'robocasa_pair_progress.json').read_text())
assert progress['freeze_sha256']==hashlib.sha256((R/'robocasa_validation_freeze.json').read_bytes()).hexdigest()
GRAPH=Path('/home/truares/桌面/attentionbench-week2-graph-live-20260927/robocasa')
service=MemoryService(AttentionStore(GRAPH/'attention_memory.sqlite3'),
                      artifact_root=GRAPH/'attention-robocasa-counter_to_sink-seed101-59f3e6e1b326'/'attempts')
ID=freeze['memory_id']
rows=[]
for case in freeze['scope']['cases']:
    seed=case['seed'];pair=progress['pairs'][str(seed)];arms=progress['arms'][str(seed)]
    assert pair==next(p for p in service.list_pairs(ID) if p['seed']==seed)
    common_sha={arms[a]['config_sha256'] for a in ('control','treatment')}
    assert len(common_sha)==1
    for arm in ('control','treatment'):
        data=arms[arm]
        result_path=Path(data['result_path']);safety_path=Path(data['safety_path'])
        assert hashlib.sha256(result_path.read_bytes()).hexdigest()==data['result_sha256']
        assert hashlib.sha256(safety_path.read_bytes()).hexdigest()==data['safety_sha256']
        result=json.loads(result_path.read_text());safety=json.loads(safety_path.read_text())
        trial=json.loads((result_path.parent/'trial_config.json').read_text())
        trace_path=result_path.parent/'trace.jsonl'
        trace_lines=[json.loads(line) for line in trace_path.read_text().splitlines() if line]
        trace_sha=hashlib.sha256(trace_path.read_bytes()).hexdigest()
        authority=service.v2._trial(data['attempt_id'],allow_terminal_failure=True)
        assert authority['evaluator_authoritative'] is True
        assert authority['success']==result['native_success']==data['native_success']
        assert authority['policy_sha256']==freeze['policy']['sha256']==trial['policy_sha256']
        assert trial['variation']=={k:case[k] for k in ('scene_id','object_set_id','camera_config_id','task_variant_id','camera_names','task_prompt')}
        assert trial['assistance_credits']==0 and trial['timeout_seconds']==240
        assert trial['config_sha256']==data['config_sha256']
        assert safety['unsafe_attempts']==data['unsafe_attempts']
        assert pair[f'{arm}_safety']['sha256']==data['safety_sha256']
        assert pair[f'{arm}_attempt_id']==data['attempt_id']
        stop=data['service_stop']
        assert stop['reason']=='normal_cleanup'
        assert all(v['leader_reaped'] and v['process_group_gone'] for v in stop['services'].values())
        assert result['formal_eligible'] is False
        assert authority['exposure']==('none' if arm=='control' else 'candidate_validation')
        assert authority['memory_ids']==([] if arm=='control' else [ID])
        bundle=json.loads((result_path.parent/'attention_bundle.json').read_text())
        raw=next(e['payload'] for e in bundle['events'] if e['event_type']=='raw_trace.created')
        retrieval=[e for e in raw['events'] if e.get('event_type')=='attention.memory_retrieval']
        assert len(retrieval)==(0 if arm=='control' else 1)
        rows.append({'seed':seed,'arm':arm,'status':result['status'],
                     'native_evaluated':True,'native_success':result['native_success'],
                     'unsafe_attempts':safety['unsafe_attempts'],
                     'safety_violations':safety['violations'],
                     'error':result['error'],'policy_sha256':trial['policy_sha256'],
                     'config_sha256':trial['config_sha256'],
                     'result_sha256':data['result_sha256'],'trace_sha256':trace_sha,
                     'safety_sha256':data['safety_sha256'],
                     'actual_candidate_retrieval_events':len(retrieval),
                     'service_stop':stop,'relative_dir':str(result_path.parent.relative_to(R))})
report=service.impact_report(ID)
assert report['paired_dev_seeds']==5 and report['treatment_successes']==1
assert len(report['safety_regressions'])==1
out={'schema_version':'attentionbench.robocasa-pair-audit.v1','formal_eligible':False,
     'freeze_sha256':progress['freeze_sha256'],'memory_id':ID,
     'rows':rows,'impact':report,'candidate_status':service.get_memory(ID).status.value}
assert out['candidate_status']=='candidate'
(R/'robocasa_pair_audit.json').write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')
print(json.dumps({'arms':len(rows),'pairs':5,'treatment_successes':report['treatment_successes'],
                  'safety_regressions':len(report['safety_regressions']),
                  'candidate_status':out['candidate_status']}))
