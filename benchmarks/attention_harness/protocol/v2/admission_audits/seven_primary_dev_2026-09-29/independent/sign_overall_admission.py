"""Conjoin independently verified canonical gates; never force eligibility."""
from pathlib import Path
import json,hashlib,datetime,sqlite3,subprocess
U=Path('/home/truares/桌面/Tidybot-Universe-attention-native');D=U.parent/'attentionbench-seven-freeze-20260929';I=D/'independent'
P=U/'benchmarks/attention_harness/protocol/v2/review_packages/seven_primary_dev_2026-09-29'
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
read=lambda p:json.loads(Path(p).read_bytes())
ref=lambda p:{'path':str(Path(p)),'sha256':sha(p)}
reuse=read(I/'evidence_reuse_audit.json');legacy=read(I/'final_legacy_impact_audit.json');freeze=read(I/'seven_condition_freeze_audit.json');approval=read(I/'seven_condition_freeze_approval.json');negative=read(I/'negative_control_post_run_audit.json');Rneg=read(I/'current_robosuite_negative_reuse_audit.json');ledger=read(D/'execution_ledger.json');plan=read(P/'matrix_plan.json')
integrity=[]
for r in reuse['sha_checks']:
 actual=sha(r['path']);integrity.append({'role':r['role'],'path':r['path'],'expected_sha256':r['expected_sha256'],'actual_sha256':actual,'pass':actual==r['expected_sha256']})
for r in negative['sha_checks']:
 actual=sha(r['path']);integrity.append({'role':'current_negative_control','path':r['path'],'expected_sha256':r['expected_sha256'],'actual_sha256':actual,'pass':actual==r['expected_sha256']})
for r in Rneg['sha_checks']:
 expected=r.get('expected_sha256',r.get('sha256'));actual=sha(r['path']);integrity.append({'role':'current_R_engineering_negative','path':r['path'],'expected_sha256':expected,'actual_sha256':actual,'pass':actual==expected})
integrity_issues=[r for r in integrity if not r['pass']]
if integrity_issues:raise ValueError('Previously signed original evidence SHA changed: '+str(integrity_issues[:3]))
if sha(P/'matrix_plan.json')!='2253292b663281953ef029908b02f82fad515b34f7a435b46aa83fac5c28623a' or sha(P/'sha256_manifest.json')!='7a1efa22d27044a4b8fd15d538914e787a5a4eb7cf35341136776a00755da372':raise ValueError('Final frozen identities changed')
offline_refs=[]
for f in sorted((I/'offline_memory_review').rglob('*')):
 if f.is_file():offline_refs.append(ref(f))
grant_integrity=[]
for row in freeze['memory_scope_probes']:
 if not row['grant']:continue
 store=next(x['second_store'] for x in freeze['snapshot_reset_probes'] if x['suite']==row['suite'])
 with sqlite3.connect(f'file:{store}?mode=ro',uri=True) as db:
  actual=json.loads(db.execute('SELECT payload FROM memory_v2_use_grants WHERE grant_id=?',(row['grant']['grant_id'],)).fetchone()[0])
 grant_integrity.append({'suite':row['suite'],'seed':row['seed'],'grant_id':row['grant']['grant_id'],'clone_store':ref(store),'pass':actual==row['grant']})
if not all(r['pass'] for r in grant_integrity) or len(grant_integrity)!=6:raise ValueError('Offline grant proof differs from copied SQLite')
tests=[]
for t in ledger['tests']:
 p=Path(t['evidence']['path'])
 if sha(p)!=t['evidence']['sha256'] or t['result'] not in p.read_text():raise ValueError('Test log identity/result mismatch')
 tests.append(t)
components=['sim_gt_memory.py','memory_service_client.py','memory_v2.py','core/memory.py'];memory_component_checks=[]
for rel in components:
 p=U/'benchmarks/attention_harness'/rel;old=subprocess.check_output(['git','-C',str(U),'show','a42ab8201277ffa806281b89a4f14e3ef33b1059:benchmarks/attention_harness/'+rel])
 memory_component_checks.append({'path':str(p),'saved_protocol_commit_sha256':hashlib.sha256(old).hexdigest(),'runtime_sha256':sha(p),'unchanged_bytes':old==p.read_bytes()})
versions=approval['service_versions'];version_issues=[]
for s in versions:
 head=subprocess.check_output(['git','-C',s['path'],'rev-parse','HEAD'],text=True).strip();status=subprocess.check_output(['git','-C',s['path'],'status','--porcelain'],text=True).strip()
 if head!=s['commit'] or status:version_issues.append({'service':s['service'],'head':head,'status':status})
if version_issues:raise ValueError('Service version drift')
reuse_ref=ref(I/'evidence_reuse_audit.json');legacy_ref=ref(I/'final_legacy_impact_audit.json');freeze_ref=ref(I/'seven_condition_freeze_audit.json');approval_ref=ref(I/'seven_condition_freeze_approval.json');negative_ref=ref(I/'negative_control_post_run_audit.json')
prior_root=U.parent/'attentionbench-depth-qualifying-20260929'
chain_pass=(reuse['prior_archive']['planned_slots']==50 and reuse['prior_archive']['all_attempt_ids_unique'] and legacy['all_historical_entries_preserved'] and not integrity_issues)
profile_pass=chain_pass and reuse['prior_archive']['preserved_native_failures']==50 and legacy['retained_case_sdk_budget']['all_runs_at_or_below_200']
depth_pass=chain_pass and read(prior_root/'independent_depth_audit.json').get('operational_gate',True) is not False
feasibility_pass=reuse['provisional_remaining_gate_adjudication']['task_feasibility']['pass'] and all(r['equivalent_ast'] for r in reuse['compatibility_function_checks'] if r['repo']!=str(U))
memory_pass=approval['approved'] and all(r['unchanged_bytes'] for r in memory_component_checks) and all(r['pass'] for r in freeze['memory_scope_probes']) and all(r['denied'] for r in freeze['lifecycle_rejections']) and all(r['frozen_snapshot_unchanged'] and r['second_cache_empty'] for r in freeze['snapshot_reset_probes']) and approval['memory_grant_eligible_seeds']=={'robosuite':[101,103,105],'robocasa':[101,103,105]}
safety_zero_pass=reuse['prior_archive']['unsafe_attempts']==0 and reuse['prior_archive']['unique_attempts']==170
fault_pass=bool(negative['safety_fault_injection_gate'] and negative['coverage_passed'] and negative['cleanup_passed'] and negative['four_artifact_retention_passed'])
freeze_pass=approval['approved'] and not approval['issues'] and plan['executed_slots']==0 and plan['planned_slots']==350
def gate(passed,reason,evidence,scope):return {'status':'pass' if passed else 'fail','pass':bool(passed),'reason':reason,'scope':scope,'evidence':evidence}
gates={
 'policy_identity':gate(freeze_pass and legacy['all_historical_entries_preserved'],'Two unchanged public-SDK base policies, 50 seed configs, generation lineage and all 350 M1/launch locks match exact bytes; legacy 50 M1 identities remain unchanged.',[approval_ref,legacy_ref,ref(P/'matrix_plan.json'),ref(P/'sha256_manifest.json')],'Primary development main experiment only; input/help-rule approval, no effect execution.'),
 'five_seed_chain_each_task':gate(chain_pass,'Reuse independently qualified 5/5 seeds 101-105 per task; original four artifacts and version bridges remain valid; no chain rerun.',[reuse_ref,legacy_ref,ref(prior_root/'independent_depth_audit.json'),ref(prior_root/'independent_robocasa_impact_audit.json')],'Native Boolean success or false is admissible; chain case success is not required.'),
 'stability_each_task':gate(profile_pass,'Reuse 25 R plus 25 C descriptive cases, 170 attempts, all original failures/invalid history retained. Native success 0/25 per task is not a threshold failure; SDK totals per run <=200 (max69).',[reuse_ref,legacy_ref,ref(prior_root/'combined_admission_archive.json')],'25 predeclared seeds/task; original interrupted C case plus authorized supplement stays preserved and counts once.'),
 'depth_500':gate(depth_pass,'Existing pinned R19fde8a operational gate remains passed; no rerun or cause claim; historical depth root cause unknown is retained controlled risk in this version/scope.',[ref(prior_root/'independent_depth_audit.json'),ref(U.parent/'attentionbench-depth-bias-gate-20260929/independent_final_audit.json'),reuse_ref],'Robosuite cube_lift/depth operational admission only; not held-out or future-version immunity.'),
 'task_feasibility':gate(feasibility_pass,'Separately labelled historical native-positive reference/Memory cases and unchanged native task/evaluator source establish native task/evaluator infrastructure solvability. No success is imported into base-policy outcomes.',[reuse_ref,ref(U.parent/'attentionbench-fixed-base-profile-20260929/engineering_v35_profile/task_feasibility_audit.json')],'Native task/evaluator solvability; not evidence that this fixed base controller succeeds or a current-reference skill safety claim.'),
 'safety':gate(safety_zero_pass,'All 170 selected qualifying/archive attempts retain independent Safety unsafe=0. The separate failed engineering negative has monitor_not_initialized unsafe=1 and is excluded from qualifying counts.',[reuse_ref,ref(prior_root/'combined_admission_archive.json')],'Zero unsafe/unknown-action in qualifying cases only; controlled negatives audited separately.'),
 'safety_fault_injection':gate(fault_pass,'FAIL: the sole new current-version C controlled negative failed station run/attempt identity validation before injected action. Dispatches, completed jobs and targeted action_outcome_unknown detections=0. No injection-to-stop latency can be assessed; normal Service cleanup is not fault-stop proof.',[negative_ref,ref(D/'negative_control_run_v2/result.json'),ref(D/'negative_control_run_v2/injection_receipt.json'),ref(I/'current_robosuite_negative_reuse_audit.json'),reuse_ref,ref(D/'execution_ledger.json')],'Historical 28 Safety artifacts and current R14 depth-negative evidence reusable; missing current C controlled unknown-action detection/timely-stop/reaping coverage.'),
 'trusted_memory':gate(memory_pass,'Trusted v1 promotion/provenance and historical raw per-attempt grant/use intact; current M Service/core components unchanged. Exact frozen initial state and no promotion enforced. Actual offline Service scope allows only101/103/105 per task and rejects other22; no scope broadening or minimum coverage.',[reuse_ref,ref(I/'memory_inventory.json'),freeze_ref,approval_ref,ref(P/'memory/robosuite/provenance.json'),ref(P/'memory/robocasa/provenance.json')],'In-process versioned Memory Service authority and scope/lifecycle/initialization validation. Offline grants are not robot runs; fixed controller consumption not proved.'),
 'seven_condition_approval':gate(freeze_pass,'All350 generation-only cells approved with condition assets, k2, random quota1, initial Memory scope/version and unified accounting. Policy bytes unchanged; finalmanifest1268 files checked. Semantic limits are explicit.',[approval_ref,freeze_ref,ref(P/'semantic_limitations.md'),ref(P/'accounting_rules.json')],'Seven frozen help-rule/input definitions for this developer main experiment only; no matrix execution authorization.')}
eligible=all(g['pass'] for g in gates.values())
user_map={'chain':'five_seed_chain_each_task','profile':'stability_each_task','depth':'depth_500','task_feasibility':'task_feasibility','safety_negative_controls':'safety_fault_injection','memory':'trusted_memory','seven_conditions':'seven_condition_approval'}
failed=[{'gate':k,'reason':g['reason'],'evidence':g['evidence'],'missing':g['scope']} for k,g in gates.items() if not g['pass']]
report={'schema_version':'attentionbench.independent-overall-admission-audit.v1','reviewer_id':'independent_admission_auditor','signed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'deadline_utc':'2026-09-30T02:06:08Z','scope':'本开发集主实验；held-out与消融另行冻结','formal_eligible':eligible,'decision_rule':'formal_eligible = conjunction of all nine canonical gates (v2.2 eight admission gates plus seven-condition approval); derived from independent evidence, never edited to satisfy a target','execution_authorized':False,'runtime_commit':approval['runtime_commit'],'saved_protocol_commit':'a42ab8201277ffa806281b89a4f14e3ef33b1059','service_versions':versions,'matrix_plan':ref(P/'matrix_plan.json'),'freeze_manifest':ref(P/'sha256_manifest.json'),'protocol_v2_2':ref(U/'benchmarks/attention_harness/protocol/v2/formal_admission_v2_2_2026-09-29.json'),'protocol_v2_4':ref(U/'benchmarks/attention_harness/protocol/v2/formal_admission_v2_4_resolution_2026-09-30.json'),'canonical_gate_count':len(gates),'canonical_gates':gates,'user_seven_gate_summary':{k:gates[v] for k,v in user_map.items()},'remaining_three_packages':{k:gates[k] for k in ['task_feasibility','safety_fault_injection','trusted_memory']},'failed_items':failed,'original_evidence_final_sha_rechecks':{'checked_files':len(integrity),'issues':integrity_issues,'files':integrity},'memory_component_compatibility_at_final_runtime':memory_component_checks,'current_offline_Memory_grants_match_SQLite':grant_integrity,'independent_offline_validation_artifacts':offline_refs,'auditor_scripts':{f.name:ref(f) for f in sorted(I.glob('*.py'))},'auditor_launch_check_correction':{'first_full_review':freeze_ref,'final_approval':approval_ref,'explanation':approval['auditor_correction']},'tests':tests,'tests_scope':'Engineering contract/fake-provider/local-bwrap checks, not 350 effects, chain/profile reruns or held-out evidence','execution_ledger':ref(D/'execution_ledger.json'),'execution_counts':{'new_real_development_cases':ledger['new_real_development_cases'],'new_real_attempts':ledger['new_real_attempts'],'new_dispatched_actions':0,'launcher_rejections_before_Service':2,'chain_reruns':0,'profile_reruns':0,'depth_reruns':0,'matrix_executions':0,'heldout_executions':0,'ablation_executions':0,'real_case_cap':1,'cap_consumed':1,'rerun_or_replacement_authorized':False},'condition_semantic_limits':approval['base_policy_semantic_limits'],'native_success_minimum_added':False,'Memory_coverage_minimum_added':False,'depth_root_cause':'unknown; controlled retained risk in pinned operational scope','next_steps':[{'action':'Close this round with the failed C negative, both launcher rejections, all four raw artifacts and frozen inputs retained; no new runs or automatic new round.','authorized_now':True},{'action':'Only a future separately authorized, independently pre-pinned bounded C negative may correct station run/attempt identity and prove injected current-version action_outcome_unknown detection, timely stop and dual-Service reaping. Recompute all gates after evidence review; otherwise eligibility remains false.','authorized_now':False},{'action':'Any future controller revision to consume Attention inputs requires a new reviewed policy/version/freeze; never change this frozen controller or infer control benefit from exposure logs.','authorized_now':False}]}
out=I/'overall_admission_audit.json'
if out.exists():raise FileExistsError('never overwrite signed overall admission')
out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
labels={'chain':'chain','profile':'50/50 profile','depth':'depth门','task_feasibility':'任务可解性','safety_negative_controls':'完整 Safety 负控','memory':'可信 Memory 版本/作用域','seven_conditions':'七条件批准'}
rows='\n'.join('| '+labels[k]+' | '+('PASS' if gates[v]['pass'] else 'FAIL')+' | '+{'chain':'既有两任务五 seed 链与四原件 SHA 复用。','profile':'既有50格/170attempt保留；原生0/25分别报告，无新增门槛。','depth':'19fde8a operational gate复用；历史根因unknown仍保留。','task_feasibility':'单独标记参考/Memory原生正例；限任务/评估器基础设施可解。','safety_negative_controls':'当前C负控注入前身份拒绝，动作/job/目标检测均0，故障即停不可测。','memory':'v1来源、晋升与历史raw grant/use复用；当前scope仅每任务101/103/105匹配。','seven_conditions':'350文件检查批准；1268文件SHA、M1、条件输入与记账规则通过。'}[k]+' |' for k,v in user_map.items())
refs=[('总体独立签发',ref(out)),('七条件独立批准',approval_ref),('全量冻结复核与离线Memory验证',freeze_ref),('当前C负控独立复核',negative_ref),('当前C失败result',ref(D/'negative_control_run_v2/result.json')),('既有证据787项复用审核',reuse_ref),('最终旧50M1及SDK预算复核',legacy_ref),('最终350格plan',ref(P/'matrix_plan.json')),('最终1268文件manifest',ref(P/'sha256_manifest.json'))]
evidence='\n\n'.join('- '+name+'：['+Path(r['path']).name+']('+r['path']+')\n\n  SHA-256 `'+r['sha256']+'`。' for name,r in refs)
markdown=f'''总体独立签发：`formal_eligible={str(eligible).lower()}`。唯一未过准入项为完整 Safety 负控；350格只冻结和校验，执行0。判定由9个canonical子gate的证据结论取AND，未直接改写准入布尔值。

范围仅为“本开发集主实验”；held-out与消融另行冻结。本轮新增真实开发case1/attempt1、动作派发0；另2次launcher在Service启动前被拒绝。原有chain/profile/depth复跑0，held-out/消融/矩阵运行均0。唯一真实case上限已用尽，不补跑、不择优替换。

| 用户关口 | 结论 | 证据范围与原因 |
|---|---|---|
{rows}

三类剩余包中，任务可解性与可信Memory通过各自限定范围；完整Safety负控失败。当前C失败为`ValueError: public station run/attempt identity mismatch`，发生在预注册故障注入前。Safety原件为`unsafe_attempts=1 / monitor_not_initialized / events=[]`，属于未初始化时的fail-closed标记，不能替代目标`action_outcome_unknown`检测。四原件SHA完整，sim/agent进程组均回收，正常清理0.478秒；没有注入到故障即停的延迟证据。既有28份Safety原件和当前R14工程负控可复用，但不填补这一缺项。

Memory审核只在自建SQLite/证据副本使用当前in-process Service：50检索、50grant验证、4次初态隔离、10生命周期方法拒绝、300个不可见条件probe均通过。两个任务各仅seed101/103/105获取exact v1，其他22个seed检索/授予拒绝后回退；原件与冻结snapshot未变，非full六条件Memory不可见，不自动晋升新Memory。离线grant是作用域验证，不计机器人运行、raw代码消费或效果证据。

预算保持4attempt、120秒/attempt、300秒/run、1credit、4096实际prompt+completion tokens、200 SDK calls/run。每run缓存从空开始，命中仍1credit与2秒逻辑延迟、0新增provider tokens；unknown/非法/超限用量保留原始回复并invalid。旧50 case SDK总数最大69，无超限。测试468通过/10跳过后，最终小修专项108通过；它们是工程校验，不是350效果运行。

两基础policy不读取Attention输入：R只读取task_id，C读取task_id/language。因此scheduler求助、费用、demo/Memory输入曝光可审计，但不能声称Demo/Advisor/Memory已被控制代码消费或改善动作。现有协议没有额外消费/成功/Memory覆盖最低门槛，本轮不新增，亦不修改控制代码。首轮审计将C解释器symlink别名误判为175个路径字符串不等；原报告保留，最终逐格以resolve目标及字节SHA重核350launch通过。

运行版本为Universe `{approval['runtime_commit']}`；保存协议提交`a42ab8201277ffa806281b89a4f14e3ef33b1059`。398项runtime源文件等于确切commit，5个Service确切clean commit详见JSON。

下一步：本轮安全收尾，保留所有异常与冻结包，不自动开启新一轮。未来仅在另行授权和独立pre-run pin后，修正station身份并补当前C负控的检测/即停/双Service回收证据，再重新聚合判定。任何Attention消费控制代码修订须另版批准与冻结，不能套用本包。

逐项证据及完整SHA：

{evidence}
'''
human=I/'overall_admission_report.md'
if human.exists():raise FileExistsError('never overwrite signed human report')
human.write_text(markdown)
print(json.dumps({'overall':ref(out),'human_report':ref(human),'formal_eligible':eligible,'failed_gates':[f['gate'] for f in failed],'original_files_rechecked':len(integrity),'pass_count':sum(g['pass'] for g in gates.values()),'canonical_gates':len(gates)},ensure_ascii=False))
