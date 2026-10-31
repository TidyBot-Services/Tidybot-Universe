"""Persistent UI lifecycle for the independently accepted fixed cuRobo scene.

No physical code is copied into the main environment. A private-prefix worker
verifies and imports the original baseline, and owns only its own subprocesses.
"""
from benchmarks.attention_harness.workspace_paths import lazy
from pathlib import Path
import hashlib,json,os,re,subprocess,threading,time,uuid

BASELINE=lazy('ui.fixed_root')
ACTIVE={'starting','ready','planning','executing','stopping','finalizing'}
SCENES={'obstacles':'fixed_obstacles_v1.json','shared':'fixed_shared_bar_v3.json',
        'contact_loss':'fixed_contact_loss_v1.json','timeout':'fixed_timeout_fault_v1.json','planning_rejected':'fixed_obstacles_v1.json'}

def atomic_json(path,value):
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2));os.replace(temp,path)

class FixedUI:
    def __init__(self,root):
        self.root=Path(root)/'openarm-fixed-curobo';self.root.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock();self.workers={}

    def directory(self,run_id):
        if not re.fullmatch(r'fixed-[0-9a-f]{24}',run_id):raise ValueError('Invalid fixed run ID')
        path=self.root/run_id
        if not path.is_dir():raise FileNotFoundError('Fixed run not found')
        return path

    def read(self,run_id):
        path=self.directory(run_id);value=json.loads((path/'status.json').read_text())
        # A missing process is terminal; an observation timeout alone never is.
        if value['status'] in ACTIVE and value.get('worker_pid'):
            try:os.kill(value['worker_pid'],0)
            except ProcessLookupError:
                value.update(status='failed',error='UI worker exited before finalization; inspect worker.log')
                atomic_json(path/'status.json',value)
        value['video_url']=f'/api/fixed/runs/{run_id}/video' if value.get('recording')=='saved' else None
        value['frame_url']=f'/api/fixed/runs/{run_id}/frame' if (path/'preview.jpg').exists() else None
        value['dispatch_requested']=(path/'execute.json').exists()
        value['baseline_version_sha256']=json.loads((path/'version.json').read_text()).get('baseline_version_sha256') if (path/'version.json').exists() else None
        value['frame_age_seconds']=time.time()-(path/'preview.jpg').stat().st_mtime if (path/'preview.jpg').exists() else None
        if (path/'measured.json').exists():
            state=json.loads((path/'measured.json').read_text())
            value['measured']={k:state.get(k) for k in ['total_steps','epoch','active_action','guard_fault']}
        value['evidence_url']=f'/api/fixed/runs/{run_id}/evidence'
        return value

    def snapshot(self):
        with self.lock:
            runs=[self.read(p.name) for p in self.root.glob('fixed-*') if (p/'status.json').is_file()]
            runs.sort(key=lambda r:r['created_at'],reverse=True)
            return {'runs':runs,'active_run_id':next((r['run_id'] for r in runs if r['status'] in ACTIVE),None),
                    'baseline':str(BASELINE),'scenes':list(SCENES)}

    def start(self,body):
        from curobo_v2.deployment import retired_entry
        retired_entry('fixed UI historical modified solver deployment')
        if set(body)!={'scene','arms','request_id'}:raise ValueError('scene, arms, request_id required')
        if body['scene'] not in SCENES or body['arms'] not in {'left','right','both'}:raise ValueError('Unsupported frozen selection')
        if body['scene'] in {'shared','contact_loss'} and body['arms']!='both':raise ValueError('Shared bar requires both arms')
        if not re.fullmatch(r'[A-Za-z0-9-]{8,80}',str(body['request_id'])):raise ValueError('Invalid request ID')
        with self.lock:
            snapshot=self.snapshot()
            for r in snapshot['runs']:
                if r['request_id']==body['request_id']:
                    if any(r[k]!=body[k] for k in ['scene','arms']):raise ValueError('Request ID belongs to another selection')
                    return r
            if snapshot['active_run_id']:raise RuntimeError('A fixed run is active; stop it or wait before starting another')
            run_id='fixed-'+uuid.uuid4().hex[:24];out=self.root/run_id;out.mkdir()
            value={'run_id':run_id,**body,'created_at':time.time(),'status':'starting','phase':'Checking bound baseline',
                   'recording':'pending','error':None,'events':[],'planner_ready':False,'executor_ready':False,'sdk_ready':False}
            atomic_json(out/'status.json',value)
            source=Path(__file__).with_name('fixed_ui_worker.py')
            stream=(out/'worker.log').open('wb')
            env={**os.environ,'PYTHONNOUSERSITE':'1','PYTHONPATH':str(BASELINE/'executor')+':'+str(BASELINE/'service')+':'+str(BASELINE/'scripts')}
            command=[str(BASELINE/'.venv/bin/python'),'-s','-u','-c',
                     'import sys; p=sys.argv.pop(1); exec(compile(open(p).read(),p,"exec"))',str(source),str(out)]
            try:proc=subprocess.Popen(command,cwd=BASELINE,env=env,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
            except Exception as exc:
                value.update(status='failed',error=f'Cannot launch isolated Python: {exc}');atomic_json(out/'status.json',value);raise
            finally:stream.close()
            value['worker_pid']=proc.pid;atomic_json(out/'status.json',value);self.workers[run_id]=proc
            threading.Thread(target=self._reap,args=(run_id,proc),daemon=True).start()
            return value

    def _reap(self,run_id,proc):
        proc.wait()
        with self.lock:
            self.workers.pop(run_id,None)
            self.read(run_id)

    def command(self,run_id,body):
        if set(body)!={'action','request_id'} or body['action'] not in {'execute','stop','cancel'}:raise ValueError('Invalid fixed command')
        with self.lock:
            value=self.read(run_id);path=self.directory(run_id)
            if body['action']=='execute':
                from curobo_v2.deployment import retired_entry
                retired_entry('fixed UI historical execution capability')
                command=path/'execute.json'
                if command.exists():
                    previous=json.loads(command.read_text())
                    if previous['request_id']==body['request_id']:return value
                    raise RuntimeError('This run has already been dispatched; start a new run')
                if value['status']!='ready':raise RuntimeError('Planner, executor and SDK must be ready before execution')
            else:
                if value['status'] not in ACTIVE:return value
                command=path/'interrupt.json'
                if command.exists():return value
            atomic_json(command,{**body,'requested_at':time.time()})
            return value
