"""Offline evidence-recomputed Dev comparison. Never imported by policy decisions."""
import csv,io,json,math,sqlite3
from collections import defaultdict
from pathlib import Path
from .dev_loop import sha
from .core.dev_store import DevConflict
from .dev_experiment import snapshot,validate_fairness
from .dev_ledger import RESOURCES,GOAL,resource_equal,eval_usage_amount,eval_cost_amount,EVAL_USAGE
from .dev_failure_analysis import classify

def wilson(successes,n):
    if type(successes) is not int or type(n) is not int or not 0<=successes<=n:raise ValueError('valid binomial counts required')
    if not n:return None
    z=1.959963984540054;p=successes/n;den=1+z*z/n;mid=(p+z*z/(2*n))/den;half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [max(0,mid-half),min(1,mid+half)]
def read(ref):
    if not isinstance(ref,dict) or sha(ref['uri'])!=ref['sha256']:raise ValueError('artifact missing/digest mismatch')
    return json.loads(Path(ref['uri']).read_bytes())
def memory_seeded_excluded(c):
    return c.get('memory_seeded_excluded') is True or any(v.get('memory_provenance',{}).get('excluded_from_results') is True
               for v in c.get('versions',[]))
def retrieved_seeded_cells(path):
    with sqlite3.connect('file:'+str(Path(path).resolve())+'?mode=ro',uri=True) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v2_events'").fetchone():
            return set()
        return {detail['cell'] for (payload,) in db.execute(
            "SELECT payload FROM v2_events WHERE kind='planner_memory_retrieved'")
            if (detail:=json.loads(payload)).get('excluded_from_results') is True}
def analyze_cell(c,planned,manifest_ref):
    rows=[];unknown=False;refs=[];first=None;infra=False
    recomputed={k:0 for k in RESOURCES};eval_recomputed={k:0 for k in EVAL_USAGE};eval_cost={k:0 for k in EVAL_USAGE};resource_errors=[]
    for job in c['jobs']:
        if job['state']!='settled':continue
        actual=dict(job['actual'])
        try:
            receipt=job['receipt']
            answer_ref=(receipt.get('eval_review') or receipt.get('advisor_answer') or receipt.get('review')) if job['kind'] in {'eval','advisor'} else None
            if answer_ref:
                answer=read(answer_ref)
                if receipt.get('usage_accounting')=='measured' and (actual['provider_tokens'],actual['input_tokens'],actual['output_tokens'])!=tuple(answer['usage'][k] for k in ('total_tokens','prompt_tokens','completion_tokens')):raise ValueError('HumanProxy usage/settlement differs')
                if job['kind']=='eval' and not receipt.get('usage_accounting') and not receipt.get('cache_hit') and (actual['provider_tokens'],actual['input_tokens'],actual['output_tokens'])!=tuple(answer['usage'][k] for k in ('total_tokens','prompt_tokens','completion_tokens')):raise ValueError('Eval usage/settlement differs')
            elif job['kind'] in {'model','advisor'}:
                output=read(receipt['response']);usage=output['usage']
                if actual['provider_tokens']!=usage['total_tokens'] or actual['input_tokens']!=usage['prompt_tokens'] or actual['output_tokens']!=usage['completion_tokens']:raise ValueError('provider usage/settlement differs')
                if receipt.get('provider_response') and read(receipt['provider_response'])['body']['usage']!=usage:raise ValueError('provider wire usage differs')
            elif job['kind']=='execute' and receipt.get('diagnostic_reset'):
                reset=read(receipt['diagnostic_reset'])
                if actual!={'executions':1,'execution_seconds':reset['elapsed_seconds'],'sdk_calls':0,'control_steps':0}:raise ValueError('reset receipt differs from actual usage')
            elif job['kind']=='execute':
                bundle=read(receipt['bundle']);sandbox=read(bundle['artifacts']['sandbox_receipt'])
                if actual['executions']!=1 or actual['sdk_calls']!=sandbox['sdk_call_budget']['dispatched'] or actual['control_steps']!=sandbox['control_steps'] or actual['execution_seconds']!=sandbox['elapsed_seconds']:raise ValueError('execution resource receipt differs')
            elif job['kind']=='human' and receipt.get('planner_request_id'):
                # Public request receipt is persisted alongside the same ledger.
                # Recompute below from the immutable request artifact, not a hint.
                request=read(receipt['request'])
                if request['request_id']!=receipt['planner_request_id'] or request['state']!=receipt['state']:raise ValueError('approval receipt binding mismatch')
                expected=max(0,min(job['reservation']['help_wait_seconds'],(request.get('wait_ended_at') or request.get('responded_at') or request.get('ended_at'))-request['created_at']))
                if actual['help_wait_seconds']!=expected:raise ValueError('approval wait settlement differs')
            elif job['kind'] in {'demo','human','cache'}:read(receipt['help'])
            for k,v in actual.items():
                if k in recomputed:recomputed[k]+=v  # legacy review seconds are readable, never human attention
            for k,v in eval_usage_amount(job,actual).items():eval_recomputed[k]+=v
            for k,v in eval_cost_amount(job,actual).items():eval_cost[k]+=v
        except (OSError,ValueError,KeyError,TypeError) as exc:resource_errors.append(str(exc));unknown=True
    if not resource_errors and not resource_equal(recomputed,c['used']):resource_errors.append('cell used differs from settlement receipts');unknown=True
    if not resource_errors and not resource_equal(eval_recomputed,c.get('eval_usage',{})):resource_errors.append('cell Eval usage differs from settlement receipts');unknown=True
    memory_seeded=memory_seeded_excluded(c)
    eligible=c.get('experiment_eligible',False) and not c.get('mixed_policy',False) and c['state']!='condition_invalid' and not memory_seeded
    if (c['task_id'],c['seed'])!=(planned['task_id'],planned['seed']):unknown=True
    for v in c['versions']:
        row={'version':v['version'],'valid':v['valid'],'evaluated':False,'native_success':None,'eval_passed':None,'error':None}
        try:
            for field in ['source','diff','model_input','model_output','decision']:
                ref=v[field];p=Path(ref['uri'])
                if sha(p)!=ref['sha256']:raise ValueError(field+' artifact digest mismatch')
                refs.append(ref)
            if v['manifest']!=manifest_ref:raise ValueError('version manifest mismatch')
            model=read(v['model_output']);usage=model['usage']
            if usage!=v['usage']:raise ValueError('usage differs from provider artifact')
            jobs=[j for j in c['jobs'] if j['kind']=='execute' and j['version']==v['version']]
            if not jobs:
                row['error']='no_execution' if v['valid'] else 'validation_failed'
                rows.append(row);continue
            job=jobs[0]
            if job['state']!='settled':
                row['error']='unresolved_execution';unknown=True
                review=job.get('review_evidence') or {}
                if review.get('bundle'):
                    try:infra=bool(read(review['bundle']).get('infrastructure_failure'))
                    except (OSError,ValueError,KeyError,TypeError):pass
                rows.append(row);continue
            bundle_ref=job['receipt']['bundle'];bundle=read(bundle_ref)
            if bundle['status']=='condition_invalid':
                row.update(error='scene_contract_mismatch',execution_status='condition_invalid',scene_condition=bundle.get('scene_condition'));rows.append(row);continue
            native_ref=bundle['artifacts']['native_result'];native=read(native_ref)
            refs.extend([bundle_ref,native_ref])
            if native_ref!=job['receipt']['native_result']:raise ValueError('native reference mismatch')
            if bundle.get('policy_sha256')!=v['source']['sha256'] or bundle.get('config_sha256')!=v['config_sha256'] or bundle.get('task_id')!=planned['task_id'] or bundle.get('seed')!=planned['seed']:raise ValueError('execution identity mismatch')
            if native.get('run_id')!=bundle.get('run_id') or native.get('attempt_id')!=bundle.get('attempt_id'):raise ValueError('native execution identity mismatch')
            if native.get('evaluated') is not True or type(native.get('native_success')) is not bool:
                row['error']='not_independently_evaluated';unknown=True
            else:
                row.update(evaluated=True,native_success=native['native_success'],execution_status=bundle['status'])
                if native['native_success'] and first is None:first={'version':v['version'],'seconds_since_cell_dispatch':job['settled_at']-c['created_at']}
            eval_job=next((j for j in c['jobs'] if j['kind']=='eval' and j['version']==v['version'] and j['state']=='settled' and (j.get('receipt',{}).get('eval_review') or j.get('receipt',{}).get('review'))),None)
            if eval_job:
                verdict=read(eval_job['receipt'].get('eval_review') or eval_job['receipt']['review'])
                if verdict.get('status')=='completed' and type(verdict.get('passed')) is bool:
                    row['eval_passed']=verdict['passed']
        except (OSError,ValueError,KeyError,TypeError) as exc:row['error']=str(exc);unknown=True
        rows.append(row)
    known=[r for r in rows if r['evaluated']]
    matrix={'true_positive':0,'false_positive':0,'true_negative':0,'false_negative':0,'unknown':0}
    for row in rows:
        if not row['evaluated'] or row['eval_passed'] is None:matrix['unknown']+=1
        elif row['eval_passed'] and row['native_success']:matrix['true_positive']+=1
        elif row['eval_passed']:matrix['false_positive']+=1
        elif row['native_success']:matrix['false_negative']+=1
        else:matrix['true_negative']+=1
    false_stop=int(c.get('reason')=='eval_passed' and bool(rows) and rows[-1]['eval_passed'] is True and rows[-1]['evaluated'] and rows[-1]['native_success'] is False)
    if c['state'] in {'needs_review','unknown'}:unknown=True
    success=any(r['native_success'] for r in known)
    # Any damaged/unresolved episode means the whole cell is not a verified
    # budget-complete outcome; known observations are still exposed per version.
    evaluated=bool(known) and not unknown
    for row,v in zip(rows,c['versions']):
        job=next((j for j in c['jobs'] if j['kind']=='execute' and j['version']==v['version']),None)
        row['failure_classification']=classify(v,job)
    return {'key':c['key'],'task_id':planned['task_id'],'seed':planned['seed'],'policy_id':planned['policy_id'],
        'state':c['state'],'eligible':eligible,'memory_seeded_excluded':memory_seeded,'mixed_policy':c.get('mixed_policy',False),'evaluated':evaluated,
        'native_success':success if evaluated else None,'terminal_native_success':known[-1]['native_success'] if evaluated else None,
        'unknown':unknown,'versions':rows,'version_count':len(rows),'first_success':first if evaluated else None,'eval_confusion_matrix':matrix,'eval_false_stops':false_stop,
        'advisor_tokens':sum(j.get('actual',{}).get('provider_tokens',0) for j in c['jobs'] if j['kind']=='advisor' and j['state']=='settled'),'wall_seconds':max((j.get('settled_at',c.get('created_at') or 0) for j in c['jobs']),default=0)-(c.get('created_at') or 0),
        'resources':recomputed,'eval_cost':eval_cost,'resource_errors':resource_errors,'infra':infra,'evidence_refs':refs,'reason':c.get('reason'),'partial_iterations':len(rows)<2,'scene_conditions':[j['receipt'].get('scene_condition') for j in c['jobs'] if j['kind']=='execute' and j['state']=='settled'],'condition_invalid':c['state']=='condition_invalid'}

def report(store,exp_id):
    e=snapshot(store,exp_id);m=read(e['manifest']);validate_fairness(m)
    seeded=retrieved_seeded_cells(store.path)
    actual={c['key']:c for c in e['cells']};cells=[]
    for planned in m['cells']:
        if planned['key'] not in actual:
            cells.append({**planned,'eligible':True,'evaluated':False,'native_success':None,'terminal_native_success':None,'unknown':True,'state':'not_started','resources':{k:0 for k in RESOURCES},'versions':[],'version_count':0,'first_success':None,'evidence_refs':[],'partial_iterations':True,'reason':'missing persisted cell'})
        else:cells.append(analyze_cell({**actual[planned['key']],
            'memory_seeded_excluded':planned['key'] in seeded},planned,e['manifest']))
    target=m.get('target_depth',2);valid_target=m.get('target_valid_executions',2)
    for c in cells:
        conditions=c.get('scene_conditions',[])
        c['physical_initial_conditions']='mismatch' if c.get('condition_invalid') else 'verified' if conditions and all(isinstance(x,dict) and x.get('matched') is True for x in conditions) else 'unverified'
        c['fair_conditions_qualified']=c['eligible'] and (not m.get('scene_contract') or c['physical_initial_conditions']=='verified')
        c['G']=c['version_count'];c['E']=sum(v.get('evaluated',False) for v in c['versions']);c['common_depth_reached']=c['G']>=target
        c['partial_iterations']=c.get('reason')!='eval_passed' and (c['G']<target or c['E']<valid_target)
        c['budget_truncated']=any(word in (c.get('reason') or '').lower() for word in ['budget','wall','deadline','insufficient'])
        c['final_valid_version']=next((v['version'] for v in reversed(c['versions']) if v['valid']),None)
    grouped=defaultdict(list)
    for c in cells:grouped[(c['task_id'],c['policy_id'])].append(c)
    groups=[]
    for (task,policy),rows in sorted(grouped.items()):
        eligible=[c for c in rows if c['eligible']];observed=[c for c in eligible if c['evaluated']];success=sum(c['native_success'] is True for c in observed);n=len(observed);terminal=sum(c['terminal_native_success'] is True for c in observed)
        counts={state:sum(c['state']==state for c in eligible) for state in ['completed','failed','cancelled','unknown','needs_review','not_started','planned','running','condition_invalid']}
        resources={k:sum(c['resources'].get(k,0) for c in eligible) for k in RESOURCES}
        eval_cost={k:sum(c.get('eval_cost',{}).get(k,0) for c in eligible) for k in EVAL_USAGE}
        first=[c['first_success'] for c in observed if c['first_success']]
        matrix={k:sum(c.get('eval_confusion_matrix',{}).get(k,0) for c in eligible) for k in ('true_positive','false_positive','true_negative','false_negative','unknown')}
        groups.append({'task_id':task,'policy_id':policy,'planned':len(rows),'eligible_planned':len(eligible),'memory_seeded_excluded':sum(c.get('memory_seeded_excluded',False) for c in rows),'excluded_mixed_or_interactive':sum((c.get('mixed_policy') or not c.get('eligible') and not c.get('condition_invalid')) and not c.get('memory_seeded_excluded') for c in rows),'excluded_condition_invalid':sum(c.get('condition_invalid',False) and not c.get('memory_seeded_excluded') for c in rows),'all_planned_resources':{k:sum(c['resources'].get(k,0) for c in rows) for k in RESOURCES},'eval_confusion_matrix':matrix,'eval_false_stops':sum(c.get('eval_false_stops',0) for c in eligible),
            'evaluated':n,'success':success,'native_false':n-success,'unevaluated':len(eligible)-n,
            'native_success_rate':success/n if n else None,'wilson95':wilson(success,n),
            'terminal_success':terminal,'terminal_success_rate':terminal/n if n else None,
            'planned_completion_ratio':counts['completed']/len(eligible) if eligible else None,
            'state_counts':counts,'unknown_evidence':sum(c['unknown'] for c in eligible),
            'partial_iterations':sum(c['partial_iterations'] for c in eligible),'resources':resources,'eval_cost':eval_cost,
            'mean_versions':sum(c['version_count'] for c in eligible)/len(eligible) if eligible else None,
            'mean_G':sum(c['G'] for c in eligible)/len(eligible) if eligible else None,'mean_E':sum(c['E'] for c in eligible)/len(eligible) if eligible else None,'help_cost':{'advisor_tokens':sum(c.get('advisor_tokens',0) for c in eligible),'credits':resources['advisor_calls'],'count':resources['advisor_calls'],'logical_human_seconds':resources['hint_human_seconds']},'wall_seconds':sum(c.get('wall_seconds',0) for c in eligible),
            'infra_cells':sum(c.get('infra',False) for c in eligible),'failed_cells':counts['failed'],'cancelled_cells':counts['cancelled'],'needs_review_cells':counts['needs_review'],'not_started_cells':counts['not_started']+counts['planned'],'unknown_cells':sum(c['unknown'] for c in eligible),
            'first_success_versions':[v['version'] for v in first],
            'first_success_seconds':[v['seconds_since_cell_dispatch'] for v in first]})
    eval_matrix={k:sum(g['eval_confusion_matrix'][k] for g in groups) for k in ('true_positive','false_positive','true_negative','false_negative','unknown')}
    return {'schema':'attentionbench.dev-report.v2','experiment_id':exp_id,'manifest':e['manifest'],'planned':len(m['cells']),
        'memory_seeded_excluded':sum(c.get('memory_seeded_excluded',False) for c in cells),
        'groups':groups,'cells':cells,'eval_confusion_matrix':eval_matrix,'eval_false_stops':sum(g['eval_false_stops'] for g in groups),'resource_ledger':store.export_ledger(m.get('goal_id',GOAL)),'formal_eligible':False,
        'scope':'Engineering small sample; not statistically validated and not formal acceptance. 不能判断策略优劣。',
        'configuration':{k:m.get(k) for k in ['target_depth','target_valid_executions','max_versions','coverage','k_trigger_reachable','public_failure_rule']},
        'same_generation_depth':[{ 'depth':d,'cells':[{ 'key':c['key'],'task_id':c['task_id'],'policy_id':c['policy_id'],'fair_conditions_qualified':c['fair_conditions_qualified'],'physical_initial_conditions':c['physical_initial_conditions'],'version':next((v for v in c['versions'] if v['version']==d),None)} for c in cells]} for d in range(1,target+1)],
        'same_effective_execution_subset':[{'key':c['key'],'task_id':c['task_id'],'policy_id':c['policy_id'],'versions':[v for v in c['versions'] if v.get('evaluated')][:valid_target]} for c in cells if c['fair_conditions_qualified'] and c['E']>=valid_target],
        'compensation_or_interrupted':[{'key':c['key'],'task_id':c['task_id'],'policy_id':c['policy_id'],'G':c['G'],'E':c['E'],'state':c['state'],'reason':c.get('reason'),'compensation':[v for v in c['versions'] if v['version']>target]} for c in cells if c['G']>target or c['partial_iterations']],
        'conclusion':'不能判断策略优劣：每 task×policy 样本不足5或全失败；不排名。' if any(g['evaluated']<5 for g in groups) or not any(g['success'] for g in groups) else 'Descriptive results only.',
        'denominator_rule':'All manifest cells retained; rates/Wilson use independently evaluated eligible cells only. Memory-seeded cells are counted separately and excluded; unevaluated/unknown are never native false.',
        'success_rule':'Any independently verified success within the fixed budget; terminal actual execution reported separately. Success versions/times computed offline only.'}
def report_csv(value):
    eval_columns={k:('eval_calls_used' if k=='eval_calls' else 'eval_'+k+'_used') for k in EVAL_USAGE}
    output=io.StringIO();fields=['task_id','policy_id','planned','eligible_planned','memory_seeded_excluded','evaluated','success','native_false','unevaluated','native_success_rate','wilson_low','wilson_high','terminal_success_rate','planned_completion_ratio','partial_iterations','mean_versions','infra_cells','failed_cells','cancelled_cells','needs_review_cells','not_started_cells','unknown_cells','mean_G','mean_E','wall_seconds',*RESOURCES,*eval_columns.values()]
    w=csv.DictWriter(output,fieldnames=fields);w.writeheader()
    for g in value['groups']:
        row={k:g.get(k) for k in fields if k not in RESOURCES and k not in eval_columns.values()};interval=g['wilson95'];row.update(wilson_low=interval[0] if interval else None,wilson_high=interval[1] if interval else None,**g['resources'],**{column:g['eval_cost'][k] for k,column in eval_columns.items()});w.writerow(row)
    return output.getvalue()
def export(store,exp_id,directory):
    d=Path(directory);d.mkdir(parents=True,exist_ok=True);value=report(store,exp_id)
    (d/'report.json').write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n');(d/'report.csv').write_text(report_csv(value))
    ledger=value['resource_ledger'];(d/'resource_ledger.json').write_text(json.dumps(ledger,sort_keys=True,indent=2)+'\n')
    with (d/'resource_ledger.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=['job_id','cell','version','kind','state',*RESOURCES,*['reserved_'+k for k in RESOURCES]]);w.writeheader()
        for j in ledger['jobs']:w.writerow({**{k:j[k] for k in ['job_id','cell','version','kind','state']},**{k:j.get('actual',{}).get(k) for k in RESOURCES},**{'reserved_'+k:j['reservation'].get(k,0) if j['state'] in {'reserved','claimed','needs_review'} else 0 for k in RESOURCES}})
    with (d/'cells.csv').open('w') as f:
        fields=['key','task_id','seed','policy_id','state','G','E','common_depth_reached','budget_truncated','advisor_tokens','wall_seconds','physical_initial_conditions','condition_invalid','fair_conditions_qualified'];w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for c in value['cells']:w.writerow({k:c.get(k) for k in fields})
    with (d/'versions.csv').open('w') as f:
        fields=['key','task_id','policy_id','version','partition','valid','evaluated','native_success','category'];w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        depth=value['configuration'].get('target_depth') or 2
        for c in value['cells']:
            for v in c['versions']:w.writerow({**{k:c[k] for k in ['key','task_id','policy_id']},**{k:v.get(k) for k in ['version','valid','evaluated','native_success']},'partition':'common_depth' if v['version']<=depth else 'compensation','category':v['failure_classification']['category']})
    return value
