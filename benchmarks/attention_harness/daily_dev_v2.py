"""Daily UI bridge. Dedicated ledgers and immutable configurations, no formal admission."""
import argparse,hashlib,json,os,re,sqlite3,subprocess,sys,threading,time
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from .dev_ledger import DevV2Store,RESOURCES,encoded
from .core.dev_store import DevConflict,DevBudgetExhausted
from . import dev_experiment as experiments
from .dev_loop import immutable,sha,owner_alive,process_identity
from .dev_tasks import TASKS
from .dev_help import POLICY_IDS
from openarm365_dev_v2.dev_config import ROOT
from openarm365_dev_v2.diag_budget import DEV_TIER_BUDGET_SCALE,task_limits

# Five versions and five requests fit the visible defaults. The SDK and execution
# ceilings come from the same Dev tier task limits used by the worker.
DEFAULT_CELL=dict(dev_calls=5,advisor_calls=5,eval_calls=5,provider_tokens=1000000,input_tokens=850000,output_tokens=150000,executions=5,sdk_calls=5*task_limits()['sdk_calls'],control_steps=5*task_limits()['control_steps'],help_credits=5,help_count=5,help_wait_seconds=5*120+5*300,hint_human_seconds=7200,model_seconds=1800,execution_seconds=5*(task_limits()['attempt_seconds']+60*DEV_TIER_BUDGET_SCALE))

def bind_daily_advisor(selection,previous_selection):
    """Freeze visual Advisor for new daily runs; preserve old daily manifests."""
    if selection.get('mode','experiment')!='experiment':
        return selection
    if Path(previous_selection).is_file() and 'advisor_proxy' not in json.loads(Path(previous_selection).read_bytes()):
        return selection
    from .dev_advisor_proxy import default_config
    return {**selection,'advisor_proxy':selection.get('advisor_proxy',default_config(True,selection.get('model_source','parcc')))}

def bind_daily_codex(selection):
    """Translate the daily dialog's legacy budget envelope to a modern Dev run."""
    if selection.get('model_source')!='codex' or 'configuration' not in selection:return selection
    if selection.get('budget_tier')!='dev':raise ValueError('Codex model source requires dev tier')
    legacy=selection['configuration']
    from openarm365_dev_v2.scene_contract import registered_contract_ref
    converted={k:v for k,v in selection.items() if k not in ('configuration','human_wait_seconds')}
    converted.update(goal_key=legacy['goal_id'],goal_limits=legacy['experiment_budget'],
        wall_limit=legacy['wall_seconds'],cell_budget=legacy['cell_budget'],
        target_depth=legacy['max_iterations'],target_valid_executions=legacy['max_iterations'],
        max_versions=legacy['max_iterations'],coverage='budget_limited',reproducible_reset=True,
        scene_contract=registered_contract_ref())
    return converted

def bind_daily_memory(selection, memory, previous_selection=None):
    """Bind the server's Memory authority for every policy (plan 0d, second revision).

    New experiments get ``memory_authority``; Memory is no longer a planner
    feature, so approval/wait/background stay whatever the selection asked for.
    A selection that already exists keeps exactly the binding it was created with.
    """
    from .dev_planner import configuration as planner_configuration
    if previous_selection is not None and Path(previous_selection).is_file():
        old=json.loads(Path(previous_selection).read_bytes())
        if old.get('planner_features',{}).get('memory'):
            return _bind_planner_memory(selection,memory)
        if 'memory_authority' not in old:
            return selection
    if (selection.get('planner_features',{}).get('memory') or selection.get('memory_authority')) and memory is None:
        raise ValueError('daily Memory authority is not configured')
    if memory is None or selection.get('mode','experiment')!='experiment':
        return selection
    features=selection.get('planner_features')
    if selection.get('memory_authority') not in (None,memory) or (features and features.get('memory') not in (None,memory)):
        raise ValueError('daily Memory authority differs from server configuration')
    bound={**selection,'memory_authority':memory}
    if features is not None:
        features={k:v for k,v in features.items() if k!='memory'}
        if features:bound['planner_features']=planner_configuration(features)
        else:bound.pop('planner_features')
    return _modern(bound)

def _bind_planner_memory(selection, memory):
    """Pre-split behaviour for daily selections created with planner Memory."""
    from .dev_planner import POLICY, configuration as planner_configuration
    if selection.get('planner_features',{}).get('memory') and memory is None:
        raise ValueError('daily Memory authority is not configured')
    if memory is None or selection.get('mode','experiment')!='experiment' or selection.get('policies')!=[POLICY]:
        return selection
    features=selection.get('planner_features')
    if features and features.get('memory') not in (None,memory):
        raise ValueError('daily Memory authority differs from server configuration')
    if features is None:
        features={'memory':memory}
    else:
        features={**features,'memory':memory}
    planner_configuration(features)
    return _modern({**selection,'planner_features':features})

def _modern(selection):
    legacy=selection.get('configuration')
    if legacy is None:
        return selection
    from openarm365_dev_v2.scene_contract import registered_contract_ref
    converted={k:v for k,v in selection.items() if k!='configuration'}
    # The daily UI's finite legacy limits stay byte-for-byte as supplied. Its
    # defaults are below v3 worst-case hint-human time, so v3's existing
    # budget_limited coverage enforces those limits without granting more.
    converted.update(goal_key=legacy['goal_id'],goal_limits=legacy['experiment_budget'],
        wall_limit=legacy['wall_seconds'],cell_budget=legacy['cell_budget'],
        target_depth=legacy['max_iterations'],target_valid_executions=legacy['max_iterations'],
        max_versions=legacy['max_iterations'],coverage='budget_limited',
        reproducible_reset=True,scene_contract=registered_contract_ref())
    converted.pop('human_wait_seconds',None)
    return converted

def registered_scene_seeds():
    from openarm365_dev_v2.scene_contract import registered_contract_ref,load_contract
    contract=load_contract(registered_contract_ref())
    # Only advertise seeds covered for every selectable task. A future contract
    # with partial coverage therefore stays conservative in this shared list.
    covered=[{int(seed) for seed in entry['seeds']} for entry in contract['tasks'].values()]
    return sorted(set.intersection(*covered)) if covered else []

class DailyDevV2:
    def __init__(self,root,profiles,authorization=None,*,per_run_budget=False,memory=None):
        if memory is not None:
            from .dev_planner import configuration as planner_configuration
            planner_configuration({'memory':memory,'human_wait_seconds':0,'background_seconds':0,'approval_rule':'none'})
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.profiles=profiles;self.authorization=authorization;self.per_run_budget=per_run_budget;self.memory=memory;self.mutex=threading.Lock();self.workers={}
        self.recover()
    def options(self):
        return {'enabled':True,'budget_tier':'dev','policies':list(POLICY_IDS),'tasks':[t.public() for t in TASKS.values()],
                'default_cell_budget':DEFAULT_CELL,'default_max_iterations':5,'registered_scene_seeds':registered_scene_seeds(),'formal_eligible':False,
                'authorization':{'mode':'per_run_budget','deadline_epoch':None} if self.per_run_budget else self.authorization,'matrix':self.matrix(),
                **({'memory':{k:self.memory[k] for k in ('store_id','namespace','url_sha256')}} if self.memory is not None else {}),
                'profiles':[dict(id='dev-v2-'+t.task_id,suite='robocasa365',task_id=t.task_id,seed=101,robot='OpenArm365',execution_arm='both',channel='dev_v2',enabled=True,execution_target='robocasa365_sim',attempt_wall_seconds=task_limits()['attempt_seconds'],sdk_call_limit=task_limits()['sdk_calls'],control_step_limit=task_limits()['control_steps'],startup_cleanup_seconds=60*DEV_TIER_BUDGET_SCALE,wall_seconds=21600,layout_id=11,style_id=34,base_mode='stationary') for t in TASKS.values()]}
    def matrix(self):
        rows=[]
        for p in self.profiles:
            robot=p.get('robot',{});task=p['task'];suite=p['suite']
            rows.append(dict(entry=p['id'],suite=suite,task=task,robot=robot.get('model','not declared in catalog'),arm=robot.get('execution_arm','single'),dev_v2_supported=False,reason='Existing frozen entry is preserved. New Dev v2 requires the registered RoboCasa365 task, OpenArm365 and both arms; use its dedicated selection.'))
        for t in TASKS.values():rows.append(dict(entry='dev-v2-'+t.task_id,suite='robocasa365',task=t.task_id,robot=t.robot,arm=t.execution_arm,dev_v2_supported=True,reason='Registered public SDK contract and authenticated cuRobo V2 native owner.'))
        return rows
    def paths(self,eid):
        if not re.fullmatch(r'devexp-[a-f0-9]{24}',eid):raise ValueError('invalid experiment ID')
        return self.root/eid,self.root/eid/'ledger.sqlite3',self.root/eid/'authorization.json'
    def store(self,eid):
        directory,path,budget=self.paths(eid)
        if not path.is_file() or not budget.is_file():raise FileNotFoundError('unknown daily experiment')
        return DevV2Store(path)
    def list(self):
        rows=[]
        for path in sorted(self.root.glob('devexp-*/ledger.sqlite3')):
            for e in experiments.list_experiments(DevV2Store(path)):
                rows.extend(self.sessions(experiments.snapshot(DevV2Store(path),e['experiment_id'])))
        return rows
    def sessions(self,e):
        config=json.loads(Path(e['manifest']['uri']).read_bytes())
        return [dict(c,channel='dev_v2',session_id='v2-'+c['key'],created_at=e['created_at'],locked={'config':dict(config['configs'][f"{c['task_id']}:{c['seed']}"],suite='robocasa365')},model=config['model']['model'],experiment_state=e['state'],manifest=e['manifest'],name_suffix=e['selection'].get('name_suffix','')) for c in e['cells']]
    def create(self,body):
        if not isinstance(body,dict) or set(body)!={'selection','start'} or type(body['start']) is not bool:raise ValueError('explicit selection and start required')
        selection=body['selection']
        if not isinstance(selection,dict):raise ValueError('bounded explicit experiment selection required')
        if not selection.get('planner_features'):
            selection={k:v for k,v in selection.items() if k!='human_wait_seconds'}
        experiments.validate_selection_fields(selection)
        if selection.get('mode')=='experiment':
            # Reject uncovered seeds before creating ledger files. New legacy
            # experiment manifests bind this reference to each config in create().
            from openarm365_dev_v2.scene_contract import registered_contract_ref,load_contract,seed_entry
            contract=load_contract(registered_contract_ref())
            for task in selection.get('tasks',[]):
                for seed in selection.get('seeds',[]):seed_entry(contract,task,seed)
        key=selection.get('request_key','')
        if not re.fullmatch(r'[A-Za-z0-9_.-]{8,100}',key):raise ValueError('stable request key required')
        eid='devexp-'+hashlib.sha256(key.encode()).hexdigest()[:24];directory,path,budget=self.paths(eid)
        selection=bind_daily_advisor(selection,directory/'selection.json')
        selection=bind_daily_memory(selection,self.memory,directory/'selection.json')
        selection=bind_daily_codex(selection)
        experiments.validate_selection_fields(selection)
        configuration=selection.get('configuration')
        goal_key=configuration['goal_id'] if configuration is not None else selection.get('goal_key')
        if not goal_key:raise ValueError('daily experiment requires a bounded goal configuration')
        with self.mutex:
            # Same goal cannot silently reset consumption in another ledger.
            for old in self.root.glob('devexp-*/authorization.json'):
                row=json.loads(old.read_bytes())
                previous=row.get('configuration') or row.get('goal_key')
                previous_key=previous['goal_id'] if isinstance(previous,dict) else previous
                if old!=budget and previous_key==goal_key:raise DevConflict('goal ID already bound to another independent ledger')
            # A crash during materialization can leave a goal ledger without its
            # authorization file. It still binds the goal; never reset it elsewhere.
            for old in self.root.glob('devexp-*/ledger.sqlite3'):
                if old==path:continue
                with sqlite3.connect('file:'+str(old)+'?mode=ro',uri=True) as db:
                    goal_id=goal_key if configuration is not None else hashlib.sha256(('goal|'+goal_key).encode()).hexdigest()[:32]
                    if db.execute('SELECT 1 FROM v2_goals WHERE id=?',(goal_id,)).fetchone():raise DevConflict('goal ID already bound to another independent ledger')
            s=DevV2Store(path,configuration={'goal_id':configuration['goal_id'],'limits':configuration['experiment_budget'],'wall_seconds':configuration['wall_seconds']}) if configuration is not None else DevV2Store(path)
            # Source verification can traverse upstream packages. Keep it out of
            # the UI process, whose history readers otherwise contend for the GIL.
            request_file=directory/'selection.json'
            if request_file.exists():
                if json.loads(request_file.read_bytes())!=selection:raise DevConflict('request key reused with different conditions')
            else:immutable(request_file,selection)
            runtime=ROOT
            existing=experiments.list_experiments(s)
            if existing:
                old=next((row for row in existing if row['experiment_id']==eid),None)
                if old is None:raise DevConflict('ledger already has a different experiment')
                if sha(old['manifest']['uri'])!=old['manifest']['sha256']:raise DevConflict('manifest digest drift')
                old_manifest=json.loads(Path(old['manifest']['uri']).read_bytes());old_lock=old_manifest['identity_lock']
                if sha(old_lock['path'])!=old_lock['sha256']:raise DevConflict('identity lock digest drift')
                runtime=Path(json.loads(Path(old_lock['path']).read_bytes())['source_root'])
            with (directory/'creation.log').open('ab') as log:
                result=subprocess.run([sys.executable,'-B','-m','benchmarks.attention_harness.dev_experiment','create','--store',str(path),'--artifacts',str(directory/'artifacts'),'--selection',str(request_file),'--output',str(directory/'created.json')],cwd=runtime,env={**os.environ,'PYTHONPATH':str(runtime),'PYTHONDONTWRITEBYTECODE':'1'},stdout=log,stderr=log,timeout=60)
            if result.returncode:raise DevConflict('Experiment materialization failed; creation.log preserves diagnostic evidence')
            e=experiments.snapshot(s,eid)
            manifest=json.loads(Path(e['manifest']['uri']).read_bytes())
            if budget.exists():
                declaration=json.loads(budget.read_bytes())
                if declaration['ledger_path']!=str(path) or (declaration.get('configuration')!=configuration if configuration is not None else declaration.get('manifest_sha256')!=e['manifest']['sha256']):raise DevConflict('immutable authorization differs')
            elif configuration is None:
                declaration=dict(goal_key=goal_key,goal_id=manifest['goal_id'],experiment_id=eid,limits=manifest['goal_limits'],wall_limit=manifest['wall_limit'],manifest_sha256=e['manifest']['sha256'],ledger_path=str(path),
                    **({'authorization_mode':'per_run_budget','deadline_epoch':None} if self.per_run_budget else {'deadline_epoch':self.authorization['deadline_epoch'] if self.authorization else time.time()+manifest['wall_limit']}))
            elif self.per_run_budget:
                declaration=dict(configuration=configuration,ledger_path=str(path),authorization_mode='per_run_budget',deadline_epoch=None)
            else:
                declaration=dict(configuration=configuration,ledger_path=str(path),deadline_epoch=self.authorization['deadline_epoch'] if self.authorization else time.time()+configuration['wall_seconds'])
            if budget.exists():
                if json.loads(budget.read_bytes())!=declaration:raise DevConflict('immutable authorization differs')
            else:
                immutable(budget,declaration)
                with s.tx() as db:
                    gid=manifest.get('goal_id',s.goal_id);goal=s.get(db,'goals',gid);goal['authorized_until']=declaration['deadline_epoch'];s.put(db,'goals',gid,goal)
        if body['start']:self.start(eid)
        return experiments.snapshot(s,eid)
    def start(self,eid):
        s=self.store(eid);e=experiments.snapshot(s,eid);directory,path,budget=self.paths(eid)
        if not self.per_run_budget and (not self.authorization or time.time()>=self.authorization['deadline_epoch']):raise DevBudgetExhausted('No current authorization: create offline or supply a new explicit run budget')
        with self.mutex:
            if eid in self.workers and self.workers[eid].poll() is None:return {'experiment_id':eid,'deduplicated':True}
            if e.get('owner') and owner_alive(e['owner']):return {'experiment_id':eid,'deduplicated':True}
            if e['state']=='completed':return {'experiment_id':eid,'deduplicated':True}
            if e['cancelled']:raise DevConflict('Cancelled experiment is read-only; create a new independent experiment')
            for row in self.list():
                if row['experiment_id']!=eid and row.get('owner') and owner_alive(row['owner']):raise DevConflict('Another daily experiment has a live external worker')
            for receipt in self.root.glob('devexp-*/scheduler-process-*.json'):
                identity=json.loads(receipt.read_bytes())
                if owner_alive(identity):
                    if receipt.parent.name==eid:return {'experiment_id':eid,'deduplicated':True}
                    raise DevConflict('Another daily scheduler process is active')
            if any(p.poll() is None for p in self.workers.values()):raise DevConflict('Daily Dev v2 worker is serial; another experiment is active')
            if any(c['state']=='needs_review' for c in e['cells']):raise DevConflict('Unresolved work requires review; no automatic retry')
            if sha(e['manifest']['uri'])!=e['manifest']['sha256']:raise DevConflict('manifest digest drift')
            manifest=json.loads(Path(e['manifest']['uri']).read_bytes());lockref=manifest['identity_lock']
            if sha(lockref['path'])!=lockref['sha256']:raise DevConflict('identity lock digest drift')
            runtime=Path(json.loads(Path(lockref['path']).read_bytes())['source_root'])
            if not runtime.is_dir():raise DevConflict('frozen experiment runtime missing')
            if self.per_run_budget:budget=self.dispatch_budget(s,e,directory,budget)
            with (directory/'scheduler.log').open('ab') as log:
                p=subprocess.Popen([sys.executable,'-B','-u','-m',__name__,'--worker',eid,'--root',str(self.root),'--budget',str(budget)],cwd=runtime,env={**os.environ,'PYTHONPATH':str(runtime),'PYTHONDONTWRITEBYTECODE':'1'},stdout=log,stderr=log,start_new_session=True)
            self.workers[eid]=p;immutable(directory/f'scheduler-process-{p.pid}.json',process_identity(p.pid))
        return {'experiment_id':eid,'worker_started':True}
    def dispatch_budget(self,store,experiment,directory,creation_budget):
        """Bind one finite deadline at first dispatch, never renew it on retry."""
        creation=json.loads(creation_budget.read_bytes())
        if creation.get('authorization_mode')!='per_run_budget':
            if creation.get('deadline_epoch',0)<=time.time():raise DevBudgetExhausted('This older record retains its expired authorization; create a new run')
            return creation_budget
        configuration=experiment['selection'].get('configuration')
        if (creation.get('configuration')!=configuration if configuration is not None else creation.get('manifest_sha256')!=experiment['manifest']['sha256']) or Path(creation['ledger_path']).resolve()!=store.path.resolve():raise DevConflict('run budget binding differs')
        dispatch=directory/'run-authorization.json'
        if dispatch.exists():
            declaration=json.loads(dispatch.read_bytes())
            if declaration['ledger_path']!=str(store.path) or (declaration.get('configuration')!=configuration if configuration is not None else declaration.get('manifest_sha256')!=experiment['manifest']['sha256']):raise DevConflict('run authorization differs')
        else:
            goal=store.goal(experiment.get('goal_id'))
            deadline=time.time()+(configuration['wall_seconds'] if configuration is not None else creation['wall_limit'])
            if goal.get('real_started_at') is not None:deadline=min(deadline,goal['real_started_at']+goal['wall_limit'])
            declaration={**creation,'deadline_epoch':deadline}
            immutable(dispatch,declaration)
        if declaration['deadline_epoch']<=time.time():raise DevBudgetExhausted('Run time budget exhausted; create a new run')
        with store.tx() as db:
            gid=experiment.get('goal_id',store.goal_id);goal=store.get(db,'goals',gid)
            if goal.get('authorized_until') not in (None,declaration['deadline_epoch']):raise DevConflict('existing run deadline cannot be extended')
            goal['authorized_until']=declaration['deadline_epoch'];store.put(db,'goals',gid,goal)
        return dispatch
    def recover(self):
        # Live external workers survive UI reload. Dead claims are quarantined.
        for path in self.root.glob('devexp-*/ledger.sqlite3'):
            s=DevV2Store(path)
            for e in experiments.list_experiments(s):
                if e.get('owner') and not owner_alive(e['owner']):
                    with s.tx() as db:
                        s.recover_evals(db)
                        unknown=db.execute("UPDATE v2_jobs SET state='needs_review' WHERE state='claimed'").rowcount
                        for key,payload in db.execute('SELECT id,payload FROM v2_cells').fetchall():
                            c=json.loads(payload)
                            if c.get('owner'):
                                c.update(owner=None,state='needs_review' if db.execute("SELECT 1 FROM v2_jobs WHERE cell=? AND state='needs_review'",(key,)).fetchone() else 'planned',reason='worker_lost_no_automatic_redispatch');s.put(db,'cells',key,c)
                        e.update(owner=None,state='needs_review' if unknown else 'planned');s.put(db,'experiments',e['experiment_id'],e)
    def get(self,path):
        parsed=urlsplit(path);path=parsed.path
        if path=='/api/dev-v2/options':return self.options(),'application/json'
        if path=='/api/dev-v2/experiments':return self.list(),'application/json'
        match=re.fullmatch(r'/api/dev-v2/experiments/(devexp-[a-f0-9]{24})(?:/(.*))?',path)
        if not match:raise FileNotFoundError('Unknown Dev v2 resource')
        eid,suffix=match.groups();s=self.store(eid)
        if not suffix:return experiments.snapshot(s,eid),'application/json'
        if suffix in {'report','export.json','export.csv','ledger.json'}:
            from .dev_report import report,report_csv
            if suffix=='ledger.json':return s.export_ledger(),'application/json'
            value=report(s,eid);return (report_csv(value),'text/csv; charset=utf-8') if suffix=='export.csv' else (value,'application/json')
        cm=re.fullmatch(r'cells/([a-f0-9]{64})/(view|live|live/frame|files/(.+))',suffix)
        if not cm:raise FileNotFoundError('Unknown cell resource')
        key,kind,name=cm.groups();c=s.cell(key)
        if c['experiment_id']!=eid:raise FileNotFoundError('Cell experiment mismatch')
        if kind=='view':return self.view(s,c),'application/json'
        if kind.startswith('live'):
            from .dev_live_monitor import response
            return response(s,eid,key,image=kind.endswith('/frame'),query=parse_qs(parsed.query))
        directory=Path(c.get('artifact_dir','/missing')).resolve();file=(directory/name).resolve()
        if not file.is_relative_to(directory) or any(p.startswith('.') for p in file.relative_to(directory).parts) or not file.is_file():raise FileNotFoundError('Cell evidence unavailable')
        allowed={'policy.py','diff.patch','version.json','public-feedback.json','decision.json','help.json','bundle.json','model-input.json','model-output.json','advisor-input.json','advisor-output.json','trace.json','native_result.json','recording.json','sandbox_receipt.json','advisor_replay.mp4','simulation.mp4','config.json','session-lock.json','evaluator-review.json'}
        if file.name not in allowed and file.name!='camera_views.json' and not re.fullmatch(r'advisor_replay_(side|top|left_wrist|right_wrist)\.mp4',file.name):raise FileNotFoundError('Not display evidence')
        return file,'video/mp4' if file.suffix=='.mp4' else 'application/json' if file.suffix=='.json' else 'text/plain; charset=utf-8'
    def view(self,s,c):
        # This projection is consumed only by UI, never the worker/model.
        from .codex_quota import run_quota_usage
        rows=[]
        def read(ref,text=False):
            if not ref:return None
            p=Path(ref['uri'])
            if sha(p)!=ref['sha256']:raise DevConflict('Display evidence digest mismatch')
            return p.read_text() if text else json.loads(p.read_bytes())
        for v in c['versions']:
            j=next((j for j in c['jobs'] if j['version']==v['version'] and j['kind']=='execute'),None);receipt=(j.get('receipt') or j.get('review_evidence') or {}) if j else {}
            bundle=read(receipt.get('bundle'))
            from .dev_request_view import display_eval_review,display_request_view,request_view_ref
            review_path=Path(v['source']['uri']).parent/'evaluator-review.json'
            review_content=display_eval_review(c,v['version'],legacy_path=review_path)
            help_content=read(v.get('help'))
            request_ref=request_view_ref(help_content,read)
            request_view=display_request_view(read(request_ref)) if request_ref else None
            rows.append(dict(v,evaluator_review=review_content,request_view=request_view,source_text=read(v['source'],True),diff_text=read(v['diff'],True),decision_content=read(v['decision']),help_content=help_content,public_feedback=read(receipt.get('public_feedback')),independent_result=read(bundle['artifacts']['native_result']) if bundle else None,recording=bundle.get('recording') if bundle else None,execution_state=j['state'] if j else 'not_dispatched'))
        from .dev_memory_view import projection as memory_projection
        with sqlite3.connect('file:'+str(s.path.resolve())+'?mode=ro',uri=True) as db:
            goal_id=s.get(db,'experiments',c['experiment_id']).get('goal_id')
        return dict(c,versions=rows,ledger=s.export_ledger(goal_id),planner_memory=memory_projection(s,c),quota_usage=run_quota_usage(c,read))
    def post(self,path,body,*,human_authorized=False):
        # Same transport protocol as DevMainBridge. Daily legacy commands do
        # not mint approvals; shared-goal approval decisions stay on that bridge.
        if path=='/api/dev-v2/experiments':return self.create(body)
        m=re.fullmatch(r'/api/dev-v2/experiments/(devexp-[a-f0-9]{24})/(run|cancel|switch)',path)
        if not m:raise FileNotFoundError('Unknown Dev v2 command')
        eid,command=m.groups();s=self.store(eid)
        if command=='switch':
            if set(body)!={'cell','policy'}:raise ValueError('cell and policy required')
            if s.cell(body['cell'])['experiment_id']!=eid:raise ValueError('cell outside experiment')
            return experiments.switch(s,body['cell'],body['policy'])
        if body:raise ValueError('Command body must be empty')
        return experiments.cancel(s,eid) if command=='cancel' else self.start(eid)

def main():
    p=argparse.ArgumentParser();p.add_argument('--worker',required=True);p.add_argument('--root',type=Path,required=True);p.add_argument('--budget',type=Path,required=True);a=p.parse_args()
    declaration=json.loads(a.budget.read_bytes());s=DevV2Store(a.root/a.worker/'ledger.sqlite3');done=threading.Event()
    def deadline():
        while not done.wait(.2):
            if time.time()>=declaration['deadline_epoch']:
                experiments.cancel(s,a.worker);return
    threading.Thread(target=deadline,daemon=True).start()
    try:experiments.run(s,a.worker,max_cells=len(experiments.snapshot(s,a.worker)['cells']),budget_file=a.budget)
    finally:
        done.set()
        # Opt-in (ATTENTION_MEMORY_AUTO_IMPORT_KEYS): detached importer for this
        # run's provisional proposals; the run never waits for or fails on it.
        from .memory_auto_import import launch_after_run
        launch_after_run(a.root/a.worker)
if __name__=='__main__':main()
