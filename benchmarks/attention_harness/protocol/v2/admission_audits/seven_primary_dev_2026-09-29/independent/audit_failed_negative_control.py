"""Independent post-run coverage/retention audit; never relabel an untriggered fault."""
from pathlib import Path
import json,hashlib,os,time
D=Path('/home/truares/桌面/attentionbench-seven-freeze-20260929');sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest();read=lambda p:json.loads(Path(p).read_text())
plan=read(D/'validation_plan_v2.json');pin=read(D/'independent/negative_control_pre_run_pin_v2.json');receipt=read(D/'negative_control_run_v2/injection_receipt.json');result=read(D/'negative_control_run_v2/result.json');refs=[];issues=[]
def ck(p,h):
 actual=sha(p);refs.append({'path':str(p),'expected_sha256':h,'actual_sha256':actual,'matches':actual==h})
 if actual!=h:issues.append('SHA mismatch '+str(p))
ck(D/'validation_plan_v2.json',pin['plan_sha256']);ck(D/'negative_control_v2.py',pin['script_sha256']);ck(D/'negative_control_run_v2/result.json',receipt['result_sha256'])
for k,e in result['artifacts'].items():ck(e['uri'],e['sha256'])
for p,h in plan['preserved_launcher_failures'].items():ck(p,h)
native=read(result['artifacts']['native_result']['uri']);safety=read(result['artifacts']['safety']['uri']);sandbox=read(result['artifacts']['sandbox_receipt']['uri']);trace=read(result['artifacts']['trace']['uri'])
stops=[]
for k,r in sandbox['service_stop']['services'].items():
 try:os.killpg(r['process_group'],0);gone=False
 except ProcessLookupError:gone=True
 stops.append({'service':k,'recorded':r,'process_group_absent_at_post_audit':gone})
 if not r['leader_reaped'] or not r['process_group_gone'] or not gone:issues.append('unreaped '+k)
versions={'sim':plan['versions']['sim_source_root']['commit'],'agent':plan['versions']['agent_source_root']['commit'],'task':plan['versions']['task_source_root']['commit']}
if result['service_revision']!=versions:issues.append('runtime service version mismatch')
if receipt['post_run_source_drift']:issues.append('source drift during run')
coverage=(len(receipt['dispatches'])==1 and len(receipt['job_receipts'])>=1 and 'injected_epoch' in receipt and any(x['kind']=='action_outcome_unknown' for x in receipt['safety_detections']))
rep={'schema_version':'attentionbench.independent-current-C-negative-post-audit.v1','reviewer_id':'independent_admission_auditor','audited_epoch':time.time(),'case_started_on_real_services':True,'real_case_budget_consumed':1,'real_action_dispatches':len(receipt['dispatches']),'completed_job_receipts':len(receipt['job_receipts']),'independent_safety_detections':len(receipt['safety_detections']),'coverage_passed':coverage,'coverage_status':'failed_before_injection','failure_reason':result['error'],'native_evaluated':native['evaluated'],'native_success':native['native_success'],'unsafe_attempts':safety['unsafe_attempts'],'no_second_action_dispatched':len(receipt['dispatches'])<2,'timeliness_from_injection_seconds':None,'why_timeliness_unassessable':'No injected action/error or independent Safety detection occurred. Normal cleanup cannot substitute for fault-stop proof.','cleanup_duration_seconds':receipt['service_stops'][0]['confirmed_epoch']-receipt['service_stops'][0]['entered_epoch'],'cleanup_passed':not issues,'service_stops':stops,'exact_service_versions':versions,'four_artifact_retention_passed':all(x['matches'] for x in refs),'sha_checks':refs,'issues':issues,'source_drift_during_run':receipt['post_run_source_drift'],'safety_fault_injection_gate':False,'formal_eligible_authorized':False,'next_step':'Retain this failure and the two zero-Service launcher rejections. No second real case or rerun this round. A future separately authorized and pre-frozen current-version C negative control must correct public station run/attempt identity and then prove detection/timing/reaping.','pre_run_pin_limit':'Pre-run audit approved the bounded test plan/file/version identity; it did not detect the station identity incompatibility. No post-run gate pass is inferred from that pin.','audit_script_sha256':sha(__file__)}
p=D/'independent/negative_control_post_run_audit.json';p.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+'\n');print(p,'sha',sha(p),'coverage',coverage,'cleanup',rep['cleanup_passed'],'SHA issues',issues)
