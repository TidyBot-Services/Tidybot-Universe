"""Persistent, serial, public-only Dev experiments; no formal approvals minted."""
import argparse,copy,hashlib,json,os,time
from pathlib import Path
from .dev_configuration import FIELDS,normalize_configuration,configured_budget,worst_slot,require_scene_gate,require_eval_gate
from .dev_ledger import DevV2Store,RESOURCES,LIMITS,GOAL,encoded,experiment_goal,complete_resources,fill_optional,task_goal_limits,task_goal_wall_limit
from .core.dev_store import DevConflict,DevBudgetExhausted
from .dev_loop import immutable,sha,process_identity,owner_alive
from .dev_tasks import get_task,validate_selection,options as task_options
from .dev_help import POLICY_IDS,policy_parameters,DemoProvider,EMPTY_MEMORY,HELP_PRICING,public_screen
from openarm365_dev_v2.dev_config import make_lock,make_dev_config,validate_dev_config,verify_lock,ROOT
from openarm365_dev_v2.diag_budget import DEV_TIER_BUDGET_SCALE,task_limits

MODEL={'model':'parcc/GLM','temperature':0,'max_tokens':8192,'reasoning_effort':'low','provider_retries':0}
CELL_BUDGET=dict(LIMITS,dev_calls=2,advisor_calls=1,executions=2,provider_tokens=60000,input_tokens=60000,output_tokens=20480,sdk_calls=160,control_steps=4800,help_credits=1,help_count=1,execution_seconds=1000,model_seconds=360,help_wait_seconds=180)
ALLOWED_SELECTION_FIELDS={'request_key','tasks','seeds','policies','help_budget','mode','policy_parameters','trace_visible','configuration','name_suffix','eval_agent','advisor_proxy','human_proxy','budget_tier'}|FIELDS
def validate_selection_fields(selection):
    if not isinstance(selection,dict) or set(selection)-ALLOWED_SELECTION_FIELDS:raise ValueError('bounded explicit experiment selection required')
def validate_help_budget(help_budget):
    if type(help_budget) is not int or not 0<=help_budget<=10:raise ValueError('help budget must be 0–10')
    return help_budget
def task_cell_budget(budget_tier='dev'):
    limits=task_limits(budget_tier)
    return {**CELL_BUDGET,'sdk_calls':2*limits['sdk_calls'],
            'control_steps':2*limits['control_steps'],
            'execution_seconds':__import__('math').ceil(1000*limits['attempt_seconds']/480)}
def cell_key(exp_id,task,seed,policy):return hashlib.sha256(f'{exp_id}|{task}|{seed}|{policy}'.encode()).hexdigest()
def validate_budget_file(path,manifest=None,store=None,manifest_ref=None):
    b=json.loads(Path(path).read_bytes());limits=b.get('limits')
    legacy=(manifest or {}).get('selection',{}).get('configuration')
    if legacy is not None:
        if b.get('configuration')!=legacy or Path(b.get('ledger_path','/missing')).resolve()!=store.path.resolve():raise ValueError('independent authorization/ledger binding mismatch')
        if b.get('deadline_epoch',0)<=time.time():raise DevBudgetExhausted('authorization deadline expired')
        return b
    if manifest and 'goal_id' in manifest:
        expected=dict(goal_id=manifest['goal_id'],experiment_id=manifest['experiment_id'],limits=manifest['goal_limits'],wall_limit=manifest['wall_limit'])
        if any(b.get(k)!=v for k,v in expected.items()) or b.get('manifest_sha256')!=manifest_ref['sha256'] or Path(b.get('ledger_path','/missing')).resolve()!=store.path.resolve():raise ValueError('v3 budget/manifest/ledger binding mismatch')
        return b
    tier=manifest.get('budget_tier','legacy') if manifest is not None else b.get('budget_tier','legacy')
    if b.get('budget_tier','legacy')!=tier:raise ValueError('budget tier/manifest binding mismatch')
    expected={'dev_calls':28,'advisor_calls':14,'provider_tokens':500000,'executions':28,'wall_seconds':task_goal_wall_limit(tier)}
    if b.get('goal')!=GOAL or limits!=expected:raise ValueError('original goal budget file required; cannot reset/expand')
    return b

def create(store,artifact_root,selection):
    validate_selection_fields(selection)
    budget_tier=selection.get('budget_tier','dev')
    task_limits(budget_tier)
    v3=bool(set(selection)&FIELDS)
    if v3:
        from .eval_agent import default_config as eval_default
        from .dev_advisor_proxy import default_config as advisor_default
        configuration=normalize_configuration(selection)
    else:configuration=None
    key=selection.get('request_key')
    import re
    if not isinstance(key,str) or not re.fullmatch('[A-Za-z0-9_.-]{8,100}',key):raise ValueError('stable request key required')
    mode=selection.get('mode','experiment')
    if mode not in {'experiment','interactive','diagnostic'}:raise ValueError('unsupported Dev mode')
    require_eval_gate(mode,selection)
    from openarm365_dev_v2.diag_budget import diagnostic_budget
    if diagnostic_budget() and mode!='diagnostic':raise ValueError('scaled attempt requires diagnostic experiment')
    tasks=selection.get('tasks',list(__import__('benchmarks.attention_harness.dev_tasks',fromlist=['TASKS']).TASKS))
    seeds=selection.get('seeds',[101]);policies=selection.get('policies',list(POLICY_IDS))
    if not isinstance(tasks,list) or not tasks or len(tasks)>32 or len(set(tasks))!=len(tasks):raise ValueError('unique registered task list required')
    if not isinstance(seeds,list) or not seeds or len(set(seeds))!=len(seeds):raise ValueError('unique seed list required')
    if not isinstance(policies,list) or not policies or len(set(policies))!=len(policies) or any(p not in POLICY_IDS for p in policies):raise ValueError('unique supported policy list required')
    if mode=='interactive' and (len(tasks),len(seeds),len(policies))!=(1,1,1):raise ValueError('interactive session has one task/seed/policy')
    for t in tasks:
        for seed in seeds:validate_selection(t,seed)
    require_scene_gate(mode,selection,tasks,seeds)
    help_budget=selection.get('help_budget',1)
    # The operator may authorize up to ten requests per task; ledger credit and
    # request accounting still enforce the explicit cell and goal allocations.
    validate_help_budget(help_budget)
    trace_visible=selection.get('trace_visible',True)
    if type(trace_visible) is not bool:raise ValueError('trace visibility must be boolean')
    params=selection.get('policy_parameters',{})
    if not isinstance(params,dict) or set(params)-set(policies):raise ValueError('policy parameter keys differ')
    parameters={p:policy_parameters(p,params.get(p)) for p in policies}
    legacy_configuration=selection.get('configuration')
    if legacy_configuration is not None:
        if v3:raise ValueError('legacy and modern experiment configuration cannot be mixed')
        if not isinstance(legacy_configuration,dict) or set(legacy_configuration)!={'goal_id','experiment_budget','cell_budget','max_iterations','wall_seconds'}:raise ValueError('complete independent experiment configuration required')
        from .dev_ledger import cost,resource_equal
        for budget_name in ('experiment_budget','cell_budget'):
            cost(legacy_configuration[budget_name])
            if not complete_resources(legacy_configuration[budget_name]):raise ValueError('complete resource budget required')
        if type(legacy_configuration['max_iterations']) is not int or legacy_configuration['max_iterations']<1:raise ValueError('positive max iterations required')
        g=store.goal()
        if store.goal_id!=legacy_configuration['goal_id'] or not resource_equal(g['limits'],legacy_configuration['experiment_budget']) or g['wall_limit']!=legacy_configuration['wall_seconds']:raise ValueError('experiment must bind its independent ledger')
        if any(legacy_configuration['cell_budget'].get(k,0)>legacy_configuration['experiment_budget'].get(k,0) for k in RESOURCES):raise ValueError('cell exceeds experiment resource budget')
    exp_id='devexp-'+hashlib.sha256(key.encode()).hexdigest()[:24]
    normalized=dict(request_key=key,tasks=tasks,seeds=seeds,policies=policies,help_budget=help_budget,mode=mode,policy_parameters=parameters,trace_visible=trace_visible)
    if budget_tier=='dev':normalized['budget_tier']='dev'
    if v3:
        budget=configured_budget(configuration)
        n=len(tasks)*len(seeds)*len(policies)
        goal_limits=configuration.get('goal_limits',{k:n*v for k,v in budget.items()})
        if complete_resources(goal_limits):goal_limits=fill_optional(goal_limits)
        __import__('benchmarks.attention_harness.dev_ledger',fromlist=['cost']).cost(goal_limits)
        from .free_models import limited_resources
        limited=limited_resources(eval_config=configuration['eval_agent'],model_names=(MODEL['model'],configuration['advisor_proxy']['model']))
        if set(goal_limits)!=set(RESOURCES) or any(n*budget[k]>goal_limits[k] for k in limited):raise ValueError('goal allocation exceeds limits')
        wait_wall=configuration['max_versions']*configuration.get('planner_features',{}).get('human_wait_seconds',0)
        wall_limit=configuration.get('wall_limit',n*(budget['model_seconds']+budget['execution_seconds']+wait_wall)+3600)
        if type(wall_limit) not in (int,float) or not __import__('math').isfinite(wall_limit) or wall_limit<=0:raise ValueError('positive wall limit required')
        if configuration['coverage']=='strict_full_depth' and wall_limit<n*(budget['model_seconds']+budget['execution_seconds']+wait_wall):raise ValueError('strict wall insufficient')
        if wall_limit<worst_slot(configuration):raise ValueError('zero slot wall refused')
        normalized.update({k:configuration[k] for k in FIELDS if k in configuration})
        normalized.update(cell_budget=budget,goal_limits=goal_limits,wall_limit=wall_limit)
    if legacy_configuration is not None:normalized['configuration']=legacy_configuration
    if 'human_proxy' in selection:
        if 'eval_agent' in selection or 'advisor_proxy' in selection or v3:
            raise ValueError('legacy human_proxy cannot be combined with independent Eval/Advisor settings')
        from .human_proxy import normalize
        normalized['human_proxy']=normalize(selection['human_proxy'])
    if v3:
        normalized['eval_agent']=copy.deepcopy(configuration['eval_agent'])
        normalized['advisor_proxy']=copy.deepcopy(configuration['advisor_proxy'])
    elif 'eval_agent' in selection:
        review=selection['eval_agent']
        if not isinstance(review,dict) or set(review)!={'enabled','model'} or type(review['enabled']) is not bool or review['model']!='parcc/Qwen':raise ValueError('explicit Qwen review configuration required')
        if 'advisor_proxy' in selection:
            from .eval_agent import normalize as normalize_eval
            normalized['eval_agent']=normalize_eval({'enabled':review['enabled']})
        else:normalized['eval_agent']=dict(review)  # old standalone config remains readable
    if not v3 and 'advisor_proxy' in selection:
        from .dev_advisor_proxy import normalize
        normalized['advisor_proxy']=normalize(selection['advisor_proxy'])
    if 'name_suffix' in selection:
        from .ui_history import name_suffix
        normalized['name_suffix']=name_suffix(selection['name_suffix'])
    with store.tx() as db:
        row=db.execute('SELECT payload FROM v2_experiments WHERE id=?',(exp_id,)).fetchone()
        if row:
            e=json.loads(row[0])
            if e['selection']!=normalized:raise DevConflict('request key reused with different experiment conditions')
            verify_manifest(e)
    if row:return snapshot(store,e['experiment_id'])
    directory=Path(artifact_root).resolve()/exp_id;directory.mkdir(parents=True,exist_ok=True)
    lock_path=directory/'identity-lock.json'
    ref={'path':str(lock_path),'sha256':sha(lock_path)} if lock_path.exists() else make_lock(lock_path)
    verify_lock(ref)
    configs={f'{t}:{s}':make_dev_config(t,s,ref,budget_tier=budget_tier) for t in tasks for s in seeds}
    sources={t:{'source':Path(get_task(t).initial_policy).read_text(),'sha256':sha(get_task(t).initial_policy)} for t in tasks}
    demos={t:DemoProvider().freeze(t) for t in tasks}
    # All base conditions live once, outside the policy specifications.
    budget=budget if v3 else dict(task_cell_budget(budget_tier),help_credits=help_budget,help_count=help_budget,advisor_calls=min(2,help_budget))
    if legacy_configuration is not None:budget=dict(legacy_configuration['cell_budget'])
    if 'eval_agent' in normalized:
        for config in configs.values():config['eval_agent']=dict(normalized['eval_agent'])
    if 'human_proxy' in normalized:
        for config in configs.values():config['human_proxy']=copy.deepcopy(normalized['human_proxy'])
    if 'advisor_proxy' in normalized:
        for config in configs.values():config['advisor_proxy']=copy.deepcopy(normalized['advisor_proxy'])
    cells=[dict(key=cell_key(exp_id,t,s,p),task_id=t,seed=s,policy_id=p) for p in policies for t in tasks for s in seeds]
    # Fixed schedule: first cell verifies v1→v2, then all remaining cells v1,
    # then their v2. Coverage is preserved before remaining iteration slots.
    schedule=[{'cell':cells[0]['key'],'through_version':2}]+[{'cell':c['key'],'through_version':1} for c in cells[1:]]+[{'cell':c['key'],'through_version':2} for c in cells[1:]]
    if v3:
        depth=configuration['target_depth'];maximum=configuration['max_versions']
        schedule=[{'cell':cells[0]['key'],'through_version':depth}]+[{'cell':c['key'],'through_version':v} for v in range(1,depth+1) for c in cells[1:]]
        schedule += [{'cell':c['key'],'through_version':maximum,'compensation':True} for c in cells]
    elif legacy_configuration is None:store.ensure_goal(GOAL,task_goal_limits(budget_tier),task_goal_wall_limit(budget_tier))
    manifest=dict(schema='attentionbench.dev-experiment.v2',experiment_id=exp_id,selection=normalized,
        model=MODEL,cell_budget=budget,goal_limits=task_goal_limits(budget_tier),wall_limit=task_goal_wall_limit(budget_tier),opening=[] if v3 else store.goal()['opening'],
        robot='OpenArm365',execution_arm='both',configs=configs,initial_sources=sources,identity_lock=ref,
        memory=EMPTY_MEMORY,help_pricing=HELP_PRICING,demos=demos,policies=parameters,trace_visible=trace_visible,
        cells=cells,schedule=schedule,formal_eligible=False,report_scope='engineering small sample, no statistical/formal claim')
    if budget_tier=='dev':manifest['budget_tier']='dev'
    if v3:
        # 2026-10-18 free Eval accounting applies to new v3 manifests only;
        # legacy v2 manifest bytes stay unchanged.
        manifest['budget_accounting_version']='free-model-tokens-eval-calls-v2'
        goal_id=hashlib.sha256(('goal|'+configuration.get('goal_key',key)).encode()).hexdigest()[:32]
        manifest.update(schema='attentionbench.dev-experiment.v3',goal_id=goal_id,goal_limits=goal_limits,wall_limit=wall_limit,cell_budget=budget,
                        **{k:configuration[k] for k in FIELDS if k in configuration and k not in {'cell_budget','goal_limits','wall_limit'}})
        manifest.update(ledger_path=str(store.path.resolve()),source_size_limit_bytes=16384,k_trigger_reachable=configuration['target_depth']>=parameters.get('retry_k_then_ask',{'k':2})['k']+1,schedule_depth=depth+1)
        for c in configs.values():
            c['attempt_wall_seconds']=configuration['attempt_wall_seconds']
            if configuration.get('reproducible_reset'):c['scene_reset_seed']=c['seed']
            if configuration.get('scene_contract'):c['scene_contract']=configuration['scene_contract']
    if legacy_configuration is not None:
        depth=legacy_configuration['max_iterations'];goal_id=legacy_configuration['goal_id']
        manifest.update(max_iterations=depth,goal_id=goal_id,
            goal_limits=legacy_configuration['experiment_budget'],wall_limit=legacy_configuration['wall_seconds'],
            target_depth=depth,target_valid_executions=depth,max_versions=depth,
            ledger_path=str(store.path.resolve()))
        manifest['schedule']=[{'cell':c['key'],'through_version':v} for v in range(1,depth+1) for c in cells]
    public_screen(sources);public_screen(demos);validate_fairness(manifest)
    manifest_path=directory/'manifest.json'
    mref=immutable(manifest_path,manifest) if not manifest_path.exists() else {'uri':str(manifest_path),'sha256':sha(manifest_path)}
    if json.loads(manifest_path.read_bytes())!=manifest:raise DevConflict('immutable manifest conflict')
    exp=dict(experiment_id=exp_id,selection=normalized,manifest=mref,artifact_dir=str(directory),state='planned',cancelled=False,owner=None,created_at=time.time(),formal_eligible=False)
    if v3 or legacy_configuration is not None:exp['goal_id']=goal_id
    with store.tx() as db:
        if v3:
            allocated={k:0 for k in RESOURCES}
            for ep, in db.execute('SELECT payload FROM v2_experiments WHERE id!=?',(exp_id,)):
                ep=json.loads(ep)
                if ep.get('goal_id')!=goal_id:continue
                for cp, in db.execute('SELECT payload FROM v2_cells WHERE exp=?',(ep['experiment_id'],)):
                    for k,v in json.loads(cp)['budget'].items():allocated[k]+=v
            from .free_models import limited_resources
            limited=limited_resources(eval_config=configuration['eval_agent'],model_names=(MODEL['model'],configuration['advisor_proxy']['model']))
            if any(allocated[k]+len(cells)*budget[k]>goal_limits.get(k,0) for k in limited):raise ValueError('shared goal allocation exceeds limits')
            existing=db.execute('SELECT payload FROM v2_goals WHERE id=?',(goal_id,)).fetchone()
            if existing:
                original=json.loads(existing[0])
                if (fill_optional(original['limits'])!=fill_optional(goal_limits) or original['wall_limit']!=wall_limit
                        or original.get('budget_tier','legacy')!=budget_tier):raise DevConflict('immutable goal budget conflict')  # goals authorized before eval_calls existed allow none
            if not existing:
                goal=dict(limits=goal_limits,used={k:0 for k in RESOURCES},real_started_at=None,wall_limit=wall_limit,opening=[])
                if budget_tier=='dev':goal['budget_tier']='dev'
                store.put(db,'goals',goal_id,goal)
        # Guard simultaneous same-key creators without permitting conditions drift.
        row=db.execute('SELECT payload FROM v2_experiments WHERE id=?',(exp_id,)).fetchone()
        if row:
            old=json.loads(row[0])
            if old!=exp:raise DevConflict('experiment creation raced; refresh existing immutable manifest')
        else:
            store.put(db,'experiments',exp_id,exp)
            for row in cells:
                payload=dict(row,experiment_id=exp_id,state='planned',owner=None,mode=mode,
                    mixed_policy=False,experiment_eligible=mode=='experiment',policy_segments=[row['policy_id']],
                    budget=budget,used={k:0 for k in RESOURCES},created_at=None,formal_eligible=False)
                if v3:payload['runtime']={k:manifest[k] for k in ['model_reserve_seconds','startup_cleanup_seconds','max_input_bytes_per_request','source_size_limit_bytes','public_failure_rule']}
                if 'planner_features' in manifest:payload['planner_features']=manifest['planner_features']
                db.execute('INSERT INTO v2_cells VALUES (?,?,?)',(row['key'],exp_id,encoded(payload)))
            store.event(db,'experiment_created',{'experiment_id':exp_id,'manifest':mref})
    return snapshot(store,exp_id)

def validate_fairness(m):
    if m['formal_eligible'] is not False or m['model']!=MODEL or m['memory']!=EMPTY_MEMORY:raise ValueError('Dev conditions differ')
    if len({c['key'] for c in m['cells']})!=len(m['cells']):raise ValueError('duplicate cell keys')
    expected={(t,s,p) for t in m['selection']['tasks'] for s in m['selection']['seeds'] for p in m['selection']['policies']}
    if expected!={(c['task_id'],c['seed'],c['policy_id']) for c in m['cells']}:raise ValueError('planned denominator incomplete')
    for c in m['cells']:
        if c['key']!=cell_key(m['experiment_id'],c['task_id'],c['seed'],c['policy_id']):raise ValueError('cell identity mismatch')
    return True

def verify_manifest(exp):
    if sha(exp['manifest']['uri'])!=exp['manifest']['sha256']:raise DevConflict('manifest digest drift')
    m=json.loads(Path(exp['manifest']['uri']).read_bytes());validate_fairness(m)
    tier=m.get('budget_tier','legacy')
    task_limits(tier)
    if m['selection'].get('budget_tier','legacy')!=tier:raise DevConflict('manifest budget tier differs from selection')
    # Diagnostics preserve their creation epoch while later resets may use a
    # separately locked source epoch. Ordinary execution remains ROOT-bound.
    epoch_root=None
    if m['selection']['mode']=='diagnostic':epoch_root=json.loads(Path(m['identity_lock']['path']).read_bytes())['source_root']
    verify_lock(m['identity_lock'],expected_source_root=epoch_root)
    for c in m['configs'].values():
        if c.get('budget_tier','legacy')!=tier:raise DevConflict('config budget tier differs from manifest')
        validate_dev_config(c,expected_source_root=epoch_root)
    for t,r in m['initial_sources'].items():
        if hashlib.sha256(r['source'].encode()).hexdigest()!=r['sha256']:raise DevConflict('initial source drift')
    for d in m['demos'].values():DemoProvider().result(d)
    return m

def snapshot(store,exp_id):
    with store.tx() as db:
        e=store.get(db,'experiments',exp_id);keys=[r[0] for r in db.execute('SELECT id FROM v2_cells WHERE exp=? ORDER BY rowid',(exp_id,))]
    cells=[store.cell(k) for k in keys]
    if any(c.get('planner_features') for c in cells):
        from .dev_planner import PlannerControl
        control=PlannerControl(store)
        cells=[{**c,'planner':control.snapshot(c['key'])} if c.get('planner_features') else c for c in cells]
    return {**e,'cells':cells,'resources':store.goal(experiment_goal(e))}
def list_experiments(store):
    with store.tx() as db:return [json.loads(r[0]) for r in db.execute('SELECT payload FROM v2_experiments ORDER BY rowid DESC')]
def cancel(store,exp_id):
    with store.tx() as db:
        e=store.get(db,'experiments',exp_id);e.setdefault('cancelled_at',time.time());e.update(cancelled=True,state='cancelled');store.put(db,'experiments',exp_id,e)
        for key,p in db.execute('SELECT id,payload FROM v2_cells WHERE exp=?',(exp_id,)).fetchall():
            c=json.loads(p)
            if c['state']=='planned':c.update(state='cancelled',reason='operator_cancel');store.put(db,'cells',key,c)
        store.event(db,'experiment_cancelled',{'experiment_id':exp_id})
    from .dev_wait_recovery import reconcile_waits
    reconcile_waits(store,exp_id)
    return snapshot(store,exp_id)
def switch(store,key,policy):
    policy_parameters(policy)
    with store.tx() as db:
        c=store.get(db,'cells',key)
        if c.get('planner_features') and policy!='full_trace_aware_attention_planner':raise DevConflict('planner feature policy is frozen')
        if c['mode']!='interactive':raise DevConflict('experiment policy is frozen')
        if c['state'] not in {'planned','running'}:raise DevConflict('session read-only')
        if c['owner']:
            c['pending_policy']=policy
        else:
            c['policy_id']=policy;c['policy_segments'].append(policy)
        c.update(mixed_policy=True,experiment_eligible=False);store.put(db,'cells',key,c)
    return store.cell(key)
def wait_recovery(store, db, exp_id):
    """Inspect an orphaned, non-paid approval wait without changing it.

    Used by the main entry and again inside acquisition's write transaction.
    This is deliberately not a general retry/source-migration mechanism.
    """
    from .dev_planner import binding, digest
    e=store.get(db,'experiments',exp_id);gid=experiment_goal(e)
    jobs=store.goal_jobs(db,gid)
    waits=[]
    for state,j in jobs:
        c=store.get(db,'cells',j['cell'])
        if j['kind']!='human' or c['experiment_id']!=exp_id:continue
        if state=='claimed':waits.append(j)
        elif state=='settled' and (c['state']=='running' or c['state']=='planned' and c.get('planner_wait')):
            # Crash after settling the wait but before atomically claiming motion.
            rid=(j.get('receipt') or {}).get('planner_request_id')
            found=db.execute('SELECT payload FROM v2_planner_requests WHERE id=?',(rid,)).fetchone()
            if found and json.loads(found[0])['state'] in {'approved','rejected','timed_out','cancelled','invalidated'}:waits.append(j)
    if not waits:return []
    def reject(message):raise DevConflict('human wait recovery refused: '+message)
    def dead(identity):
        if identity is None:return
        if (not isinstance(identity,dict) or set(identity)!={'pid','start_ticks','boot_id'}
            or type(identity['pid']) is not int or identity['pid']<=0
            or not all(isinstance(identity[k],str) and identity[k] for k in ('start_ticks','boot_id'))):reject('unknown owner identity')
        if owner_alive(identity):reject('live owner')
    # Cell/job owners also cover acquisition/release windows, across experiments
    # and goals sharing this serial ledger.
    for table in ('experiments','cells'):
        for payload, in db.execute('SELECT payload FROM v2_'+table):dead(json.loads(payload).get('owner'))
    for state,payload in db.execute('SELECT state,payload FROM v2_jobs'):
        if state in {'reserved','claimed','needs_review'}:dead(json.loads(payload).get('owner'))
    for payload, in db.execute('SELECT payload FROM v2_experiments'):
        other=json.loads(payload)
        if experiment_goal(other)==gid and other['state'] in {'unknown','needs_review','condition_invalid'}:reject('unresolved experiment')
        for receipt in Path(other['artifact_dir']).glob('scheduler-process-*.json'):
            identity=json.loads(receipt.read_bytes())
            if identity is None:reject('unknown scheduler receipt')
            dead(identity)
    if e['cancelled'] or e['state'] not in {'planned','running'}:reject('experiment stopped or unresolved')
    g=store._goal(db,gid)
    if g.get('real_finished_at') or g['wall_remaining']<=0 or g['real_started_at'] is None:reject('original authorization expired/unknown')
    if any(g['used'].get(k,0)+g['reserved'][k]>g['limits'].get(k,0) for k in RESOURCES):reject('original resources exceeded')
    for state,j in jobs:
        if state not in {'reserved','claimed','needs_review','settled','released'}:reject('unknown job state')
        if state=='needs_review' or state in {'reserved','claimed'} and j not in waits:reject('paid/other hold or unknown work')
    from .dev_wait_recovery import verify_execution_history
    try:verify_execution_history(store,db,gid,jobs)
    except (OSError,ValueError,KeyError,TypeError,IndexError,DevConflict) as exc:reject(str(exc))
    for payload, in db.execute('SELECT payload FROM v2_cells'):
        c=json.loads(payload)
        if store.cell_goal(db,c)==gid and c['state'] in {'unknown','needs_review','condition_invalid'}:reject('unresolved cell')
    m=verify_manifest(e)
    if m.get('goal_id',GOAL)!=gid or m['experiment_id']!=exp_id:reject('experiment/goal identity drift')
    if g['limits']!=m['goal_limits'] or g['wall_limit']!=m['wall_limit']:reject('original goal authorization changed')
    if 'ledger_path' in m and Path(m['ledger_path']).resolve()!=store.path.resolve():reject('ledger binding mismatch')
    planned={c['key']:c for c in m['cells']}
    result=[]
    for j in waits:
        c=store.get(db,'cells',j['cell']);features=c.get('planner_features') or {}
        if c['key'] not in planned or c['state'] not in {'running','planned'} or c.get('pending_policy'):reject('cell identity/state changed')
        expected=planned[c['key']]
        if (any(c[k]!=expected[k] for k in ('task_id','seed','policy_id'))
            or c['budget']!=m['cell_budget'] or features!=m['planner_features']):reject('cell configuration changed')
        if (j['reservation']!={'help_wait_seconds':features['human_wait_seconds']}
            or not j.get('claimed_at') or not j.get('owner')):reject('unknown human reservation')
        state=next(state for state,job in jobs if job['job_id']==j['job_id'])
        if state=='claimed' and (j.get('actual') or j.get('receipt')):reject('inconsistent human accounting')
        if state=='settled' and (set(j.get('actual',{}))!={'help_wait_seconds'}
            or not 0<=j['actual']['help_wait_seconds']<=features['human_wait_seconds']):reject('unknown settled human usage')
        cfg=m['configs'][f"{c['task_id']}:{c['seed']}"]
        root=Path(e['artifact_dir'])/'cells'/c['key']
        if Path(c.get('artifact_dir','')).resolve()!=root.resolve() or json.loads((root/'config.json').read_bytes())!=cfg:reject('config binding drift')
        versions=[json.loads(p) for p, in db.execute('SELECT payload FROM v2_versions WHERE cell=? ORDER BY version',(c['key'],))]
        if not versions or versions[-1]['version']!=j['version'] or not versions[-1]['valid']:reject('version invalidated')
        source=versions[-1]['source']
        if sha(source['uri'])!=source['sha256']:reject('source drift')
        scope=binding(c['key'],j['version'],Path(source['uri']).read_text(),cfg)
        rid='planner-'+digest(dict(cell=c['key'],version=j['version'],kind='approval',scope=scope))
        found=db.execute('SELECT payload FROM v2_planner_requests WHERE id=?',(rid,)).fetchone()
        if not found:reject('original approval missing')
        r=json.loads(found[0])
        if state=='settled':
            receipt=j.get('receipt') or {}
            if receipt.get('planner_request_id')!=rid:reject('settled wait request drift')
            if receipt.get('request'):
                ref=receipt['request']
                if sha(ref['uri'])!=ref['sha256']:reject('settled wait evidence drift')
        if (r['cell']!=c['key'] or r['version']!=j['version'] or r['request_type']!='approval'
            or encoded(r['binding'])!=encoded(scope) or r['state'] not in {'waiting','approved','rejected','timed_out','cancelled','invalidated'}
            or r.get('consumed_by') or not 0<r['deadline_epoch']-r['created_at']<=features['human_wait_seconds']
            or not r['created_at']<=j['claimed_at']<=r['deadline_epoch']
            or r['deadline_epoch']>min(g['real_started_at']+g['wall_limit'],g.get('authorized_until',float('inf')))
            or c.get('planner_wait') not in {None,rid}):reject('approval binding/state changed')
        if r['state'] in {'waiting','approved'} and time.time()<r['deadline_epoch']:
            needed=dict(executions=1,sdk_calls=cfg['sdk_call_limit'],control_steps=cfg['horizon'],execution_seconds=45)
            if any(g['remaining'][k]<v or c['budget'][k]-c['used'][k]<v for k,v in needed.items()):reject('original action resources exhausted')
        if r['state']=='approved' and not (r.get('response') or {}).get('approved'):reject('approval response missing')
        for payload, in db.execute('SELECT payload FROM v2_planner_work WHERE cell=?',(c['key'],)):
            w=json.loads(payload)
            if w.get('owner') and w['state']=='running':dead(w['owner'])
            if w.get('result') and sha(w['result']['uri'])!=w['result']['sha256']:reject('preparation evidence drift')
        result.append(dict(job_id=j['job_id'],cell=c['key'],request_id=rid))
    return result


def acquire(store,exp_id,owner):
    from .dev_wait_recovery import reconcile_waits
    reconciled=reconcile_waits(store,exp_id)
    if any(r['status'] in {'settled','needs_review'} for r in reconciled):raise DevConflict('human wait terminated or needs review; no dispatch')
    with store.tx() as db:
        e=store.get(db,'experiments',exp_id)
        if e['state'] in {'stopped','cancelled'} or e['cancelled']:raise DevConflict('experiment stopped or unresolved')
        g=store._goal(db,experiment_goal(e))
        if g.get('real_finished_at') or g['wall_remaining']<=0:raise DevConflict('original authorization expired/closed')
        recovery=wait_recovery(store,db,exp_id)
        if e.get('owner') and owner_alive(e['owner']):raise DevConflict('live serial experiment owner already exists')
        for key,p in db.execute("SELECT id,payload FROM v2_experiments WHERE id!=?",(exp_id,)):
            other=json.loads(p)
            if other.get('owner') and owner_alive(other['owner']):raise DevConflict('goal has another live serial owner')
        for payload, in db.execute('SELECT payload FROM v2_cells'):
            if (identity:=json.loads(payload).get('owner')) and owner_alive(identity):raise DevConflict('live cell owner already exists')
        if e['cancelled']:raise DevConflict('experiment cancelled')
        store.recover_evals(db, goal_id=experiment_goal(e))
        if any(state=='needs_review' for state,j in store.goal_jobs(db,experiment_goal(e))):raise DevConflict('goal has unresolved claimed work; no further automatic dispatch')
        # First recovery quarantines every claim in this goal across experiments.
        gid=experiment_goal(e)
        claimed=[j for state,j in store.goal_jobs(db,gid) if state=='claimed' and j['kind']!='human']
        for j in claimed:
            db.execute("UPDATE v2_jobs SET state='needs_review' WHERE id=?",(j['job_id'],))
            c=store.get(db,'cells',j['cell']);c.update(owner=None,state='needs_review',reason='worker_lost_no_claim_retry');store.put(db,'cells',j['cell'],c)
        for eid,ep in db.execute('SELECT id,payload FROM v2_experiments').fetchall():
            ep=json.loads(ep)
            if experiment_goal(ep)!=gid:continue
            for key,cp in db.execute('SELECT id,payload FROM v2_cells WHERE exp=?',(eid,)).fetchall():
                c=json.loads(cp)
                if claimed and c['state']=='planned':c.update(state='not_started',reason='upstream_unknown_quarantined');store.put(db,'cells',key,c)
                elif c['state']=='running' and c.get('owner') and not owner_alive(c['owner']):
                    c.update(owner=None,state='planned',reason='worker_lost_no_claim_retry');store.put(db,'cells',key,c)
            if claimed:ep.update(state='needs_review',owner=None);store.put(db,'experiments',eid,ep)
        e.update(owner=owner,state='needs_review' if claimed else 'running');store.put(db,'experiments',exp_id,e)
        for recovered in recovery:
            j=json.loads(db.execute('SELECT payload FROM v2_jobs WHERE id=?',(recovered['job_id'],)).fetchone()[0])
            transfer=dict(previous_owner=j['owner'],owner=owner,at=time.time(),request_id=recovered['request_id'])
            j.setdefault('owner_transfers',[]).append(transfer);j.update(owner=owner,planner_request_id=recovered['request_id'])
            db.execute('UPDATE v2_jobs SET payload=? WHERE id=?',(encoded(j),j['job_id']))
            c=store.get(db,'cells',j['cell']);c.update(owner=owner,state='planned',planner_wait=recovered['request_id']);store.put(db,'cells',j['cell'],c)
            store.event(db,'planner_human_wait_owner_transferred',dict(job_id=j['job_id'],cell=j['cell'],claimed_at=j['claimed_at'],**transfer))
        e['recovered_human_waits']=recovery  # Run-local handoff; no new allocation.
        return e

def run(store,exp_id,*,max_cells,budget_file=None,client=None,runner_factory=None,max_slots=None,outer_deadline_epoch=None,min_start_wall_seconds=None):
    from .dev_wait_recovery import reconcile_waits
    reconcile_waits(store,exp_id)
    from openarm365_dev_v2.diag_budget import diagnostic_budget
    if snapshot(store,exp_id)['selection']['mode']=='diagnostic' and not diagnostic_budget():raise DevConflict('diagnostic dispatch requires individually metered reset/probe')
    if type(max_cells) is not int or max_cells<1:raise ValueError('positive explicit max_cells required')
    e=snapshot(store,exp_id);m=verify_manifest(e)
    if 'goal_id' in m and Path(m['ledger_path']).resolve()!=store.path.resolve():raise ValueError('manifest ledger binding mismatch')
    if max_cells>len(m['cells']):raise ValueError('max_cells exceeds manifest denominator')
    if max_slots is not None and (type(max_slots) is not int or max_slots<1):raise ValueError('positive max_slots required')
    if budget_file is None and 'goal_id' in m:
        declared={'ledger_path':str(store.path.resolve())}
    else:declared=validate_budget_file(budget_file,m,store,e['manifest'])
    if (client is None or runner_factory is None) and Path(declared.get('ledger_path','/missing')).resolve()!=store.path.resolve():raise ValueError('real dispatch requires the original goal ledger_path binding')
    if 'goal_id' in m:
        with store.tx() as db:
            for pe, in db.execute('SELECT payload FROM v2_experiments'):
                pe=json.loads(pe)
                if pe.get('goal_id')==m['goal_id'] and pe['selection'].get('mode')=='probe' and pe['state']!='completed':raise DevConflict('probe gate incomplete/failed; no matrix dispatch')
    if min_start_wall_seconds is not None:
        if type(min_start_wall_seconds) not in (int,float) or min_start_wall_seconds<0:raise ValueError('nonnegative startup wall required')
        available=store.goal(m.get('goal_id',GOAL))['wall_remaining']
        if outer_deadline_epoch is not None:available=min(available,outer_deadline_epoch-time.time())
        if available<min_start_wall_seconds:raise DevBudgetExhausted('matrix startup wall threshold insufficient')
    owner=process_identity();e=acquire(store,exp_id,owner)
    try:
        if e['state']=='needs_review':return snapshot(store,exp_id)
        m=verify_manifest(e)
        eligible={c['key'] for c in m['cells'][:max_cells]}
        from .dev_v2_loop import run_cell
        slots=0
        for slot in m['schedule']:
            key=slot['cell']
            if key not in eligible:continue
            if max_slots is not None and slots>=max_slots:break
            c=store.cell(key)
            with store.tx() as db:current=store.get(db,'experiments',exp_id)
            if current['cancelled']:break
            if c['state'] in {'completed','failed','cancelled','unknown','needs_review','not_started','condition_invalid'}:continue
            goal=store.goal(m.get('goal_id',GOAL))
            g=len(c['versions']);ex=sum(j['kind']=='execute' and j['state']=='settled' and not (j.get('receipt') or {}).get('diagnostic') for j in c['jobs'])
            if 'goal_id' in m:
                pending=any(j['kind']=='model' and j['state']=='settled' and j['version']>g for j in c['jobs'])
                if not pending and not slot.get('compensation') and g>=slot['through_version'] and (not c['versions'] or not c['versions'][-1]['valid'] or any(j['kind']=='execute' and j['version']==g and j['state']=='settled' for j in c['jobs'])):continue
                if not pending and slot.get('compensation') and ex>=m['target_valid_executions'] and g>=m['target_depth']:continue
                seconds=worst_slot(m)
                recovering=any(r['cell']==key for r in e.get('recovered_human_waits',[]))
                if not recovering and (goal['wall_remaining']<seconds or (outer_deadline_epoch is not None and outer_deadline_epoch-time.time()<seconds)):
                    reason='outer_deadline_safe_boundary' if outer_deadline_epoch is not None and outer_deadline_epoch-time.time()<seconds else 'goal_wall_worst_slot_insufficient'
                    for row in m['cells']:
                        cc=store.cell(row['key'])
                        if cc['state']=='planned':store.update_cell(row['key'],state='failed' if cc['versions'] else 'not_started',reason=reason)
                    break
            recovering=any(r['cell']==key for r in e.get('recovered_human_waits',[]))
            if not recovering and (not ('goal_id' in m and pending)) and (goal['wall_remaining']<=0 or (goal['remaining']['executions']<1 and ('goal_id' not in m or ex<m['target_valid_executions'])) or goal['remaining']['dev_calls']<1):
                store.update_cell(key,state='not_started' if not c['versions'] else 'failed',reason='original_goal_budget_exhausted');continue
            try:verify_manifest(e)
            except BaseException as exc:
                store.update_cell(key,state='needs_review',reason=str(exc));raise
            slots+=1
            run_cell(store,e,m,key,slot['through_version'],owner,client=client,runner_factory=runner_factory)
            e['recovered_human_waits']=[r for r in e.get('recovered_human_waits',[]) if r['cell']!=key]
            after=store.cell(key)
            if after['state'] in {'needs_review','condition_invalid'}:
                # Infrastructure/unknown never leads to another real dispatch.
                for row in m['cells']:
                    cc=store.cell(row['key'])
                    if cc['state']=='planned':store.update_cell(row['key'],state='not_started',reason='upstream_condition_invalid' if after['state']=='condition_invalid' else 'upstream_unknown_quarantined')
                break
        with store.tx() as db:
            states=[json.loads(p)['state'] for p, in db.execute('SELECT payload FROM v2_cells WHERE exp=?',(exp_id,))]
            current=store.get(db,'experiments',exp_id)
            if not current['cancelled']:current['state']='needs_review' if 'needs_review' in states else 'condition_invalid' if 'condition_invalid' in states else 'planned' if 'planned' in states else 'completed'
            store.put(db,'experiments',exp_id,current)
    except BaseException as exc:
        with store.tx() as db:
            current=store.get(db,'experiments',exp_id);current.update(state='needs_review',reason=str(exc));store.put(db,'experiments',exp_id,current)
        raise
    finally:
        with store.tx() as db:
            current=store.get(db,'experiments',exp_id)
            if current['owner']==owner:current['owner']=None;store.put(db,'experiments',exp_id,current)
    return snapshot(store,exp_id)

def options():
    import benchmarks,openarm365_dev_v2
    return {**task_options(),'policies':list(POLICY_IDS),'cell_budget':task_cell_budget(),'model':MODEL,'configuration':normalize_configuration({}), 'help_budget_range':[0,10],
        'source_canary':{'benchmarks':benchmarks.__file__,'openarm365_dev_v2':openarm365_dev_v2.__file__,'source_root':str(ROOT)}}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['create','plan','run','status','cancel','report','export','probe']);p.add_argument('--store',required=True);p.add_argument('--artifacts',required=True);p.add_argument('--selection');p.add_argument('--experiment');p.add_argument('--max-cells',type=int);p.add_argument('--budget-file');p.add_argument('--dry-run',action='store_true');p.add_argument('--output');p.add_argument('--max-slots',type=int);p.add_argument('--outer-deadline-epoch',type=float);p.add_argument('--min-start-wall-seconds',type=float);a=p.parse_args();s=DevV2Store(a.store)
    if a.command in {'create','plan'}:
        selection=json.loads(Path(a.selection).read_bytes())
        configuration=selection.get('configuration')
        if configuration is not None:
            # Daily pre-provisions a finite independent goal before this child
            # materializes its first experiment. With no experiment yet, the
            # default store cannot infer that goal. Bind it explicitly, checking
            # immutable limits rather than opening/provisioning the default goal.
            s=DevV2Store(a.store,configuration=dict(goal_id=configuration['goal_id'],
                limits=configuration['experiment_budget'],wall_seconds=configuration['wall_seconds']))
        value=create(s,a.artifacts,selection)
    elif a.command=='run':value=snapshot(s,a.experiment) if a.dry_run else run(s,a.experiment,max_cells=a.max_cells,budget_file=a.budget_file,max_slots=a.max_slots,outer_deadline_epoch=a.outer_deadline_epoch,min_start_wall_seconds=a.min_start_wall_seconds)
    elif a.command=='probe':
        from .dev_probe import run_probe
        e=snapshot(s,a.experiment);m=verify_manifest(e);validate_budget_file(a.budget_file,m,s,e['manifest']);value=run_probe(s,a.experiment)
    elif a.command=='status':value=snapshot(s,a.experiment) if a.experiment else list_experiments(s)
    elif a.command=='cancel':value=cancel(s,a.experiment)
    elif a.command=='report':
        from .dev_report import report
        value=report(s,a.experiment)
    else:value=s.export_ledger()
    text=encoded(value)
    if a.output:Path(a.output).write_text(text+'\n')
    else:print(text)
if __name__=='__main__':main()
