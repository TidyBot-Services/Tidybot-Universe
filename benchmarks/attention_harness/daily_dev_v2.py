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

DEFAULT_CELL=dict(dev_calls=3,advisor_calls=2,eval_calls=3,provider_tokens=220000,input_tokens=190000,output_tokens=30000,executions=3,sdk_calls=240,control_steps=7200,help_credits=2,help_count=2,help_wait_seconds=300,model_seconds=600,execution_seconds=1600)

class DailyDevV2:
    def __init__(self,root,profiles,authorization=None,*,per_run_budget=False):
        self.root=Path(root).resolve();self.root.mkdir(parents=True,exist_ok=True)
        self.profiles=profiles;self.authorization=authorization;self.per_run_budget=per_run_budget;self.mutex=threading.Lock();self.workers={}
        self.recover()
    def options(self):
        return {'enabled':True,'policies':list(POLICY_IDS),'tasks':[t.public() for t in TASKS.values()],
                'default_cell_budget':DEFAULT_CELL,'default_max_iterations':3,'formal_eligible':False,
                'authorization':{'mode':'per_run_budget','deadline_epoch':None} if self.per_run_budget else self.authorization,'matrix':self.matrix(),
                'profiles':[dict(id='dev-v2-'+t.task_id,suite='robocasa365',task_id=t.task_id,seed=101,robot='OpenArm365',execution_arm='both',channel='dev_v2',enabled=True,execution_target='robocasa365_sim',attempt_wall_seconds=480,sdk_call_limit=80,wall_seconds=3600,layout_id=11,style_id=34,base_mode='stationary') for t in TASKS.values()]}
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
        selection=body['selection'];configuration=selection.get('configuration',{})
        key=selection.get('request_key','')
        if not re.fullmatch(r'[A-Za-z0-9_.-]{8,100}',key):raise ValueError('stable request key required')
        eid='devexp-'+hashlib.sha256(key.encode()).hexdigest()[:24];directory,path,budget=self.paths(eid)
        with self.mutex:
            # Same goal cannot silently reset consumption in another ledger.
            for old in self.root.glob('devexp-*/authorization.json'):
                row=json.loads(old.read_bytes())
                if old!=budget and row['configuration']['goal_id']==configuration.get('goal_id'):raise DevConflict('goal ID already bound to another independent ledger')
            # A crash during materialization can leave a goal ledger without its
            # authorization file. It still binds the goal; never reset it elsewhere.
            for old in self.root.glob('devexp-*/ledger.sqlite3'):
                if old==path:continue
                with sqlite3.connect('file:'+str(old)+'?mode=ro',uri=True) as db:
                    if db.execute('SELECT 1 FROM v2_goals WHERE id=?',(configuration.get('goal_id'),)).fetchone():raise DevConflict('goal ID already bound to another independent ledger')
            s=DevV2Store(path,configuration={'goal_id':configuration['goal_id'],'limits':configuration['experiment_budget'],'wall_seconds':configuration['wall_seconds']})
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
            if budget.exists():
                declaration=json.loads(budget.read_bytes())
                if declaration['configuration']!=configuration or declaration['ledger_path']!=str(path):raise DevConflict('immutable authorization differs')
            elif self.per_run_budget:
                declaration=dict(configuration=configuration,ledger_path=str(path),authorization_mode='per_run_budget',deadline_epoch=None)
            else:
                declaration=dict(configuration=configuration,ledger_path=str(path),deadline_epoch=self.authorization['deadline_epoch'] if self.authorization else time.time()+configuration['wall_seconds'])
            if budget.exists():
                if json.loads(budget.read_bytes())!=declaration:raise DevConflict('immutable authorization differs')
            else:
                immutable(budget,declaration)
                with s.tx() as db:
                    goal=s.get(db,'goals',s.goal_id);goal['authorized_until']=declaration['deadline_epoch'];s.put(db,'goals',s.goal_id,goal)
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
        configuration=experiment['selection']['configuration']
        if creation['configuration']!=configuration or Path(creation['ledger_path']).resolve()!=store.path.resolve():raise DevConflict('run budget binding differs')
        dispatch=directory/'run-authorization.json'
        if dispatch.exists():
            declaration=json.loads(dispatch.read_bytes())
            if declaration['configuration']!=configuration or declaration['ledger_path']!=str(store.path):raise DevConflict('run authorization differs')
        else:
            goal=store.goal()
            deadline=time.time()+configuration['wall_seconds']
            if goal.get('real_started_at') is not None:deadline=min(deadline,goal['real_started_at']+goal['wall_limit'])
            declaration=dict(configuration=configuration,ledger_path=str(store.path),deadline_epoch=deadline,authorization_mode='per_run_budget')
            immutable(dispatch,declaration)
        if declaration['deadline_epoch']<=time.time():raise DevBudgetExhausted('Run time budget exhausted; create a new run')
        with store.tx() as db:
            goal=store.get(db,'goals',store.goal_id)
            if goal.get('authorized_until') not in (None,declaration['deadline_epoch']):raise DevConflict('existing run deadline cannot be extended')
            goal['authorized_until']=declaration['deadline_epoch'];store.put(db,'goals',store.goal_id,goal)
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
        rows=[]
        def read(ref,text=False):
            if not ref:return None
            p=Path(ref['uri'])
            if sha(p)!=ref['sha256']:raise DevConflict('Display evidence digest mismatch')
            return p.read_text() if text else json.loads(p.read_bytes())
        for v in c['versions']:
            j=next((j for j in c['jobs'] if j['version']==v['version'] and j['kind']=='execute'),None);receipt=(j.get('receipt') or j.get('review_evidence') or {}) if j else {}
            bundle=read(receipt.get('bundle'))
            review_path=Path(v['source']['uri']).parent/'evaluator-review.json'
            review_content=None
            if review_path.is_file():
                try:review_content=json.loads(review_path.read_bytes())
                except (OSError,ValueError):pass
            rows.append(dict(v,evaluator_review=review_content,source_text=read(v['source'],True),diff_text=read(v['diff'],True),decision_content=read(v['decision']),help_content=read(v.get('help')),public_feedback=read(receipt.get('public_feedback')),independent_result=read(bundle['artifacts']['native_result']) if bundle else None,recording=bundle.get('recording') if bundle else None,execution_state=j['state'] if j else 'not_dispatched'))
        return dict(c,versions=rows,ledger=s.export_ledger())
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
    finally:done.set()
if __name__=='__main__':main()
