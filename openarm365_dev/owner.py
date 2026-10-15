"""Private native process owner and typed SDK, no public raw-action endpoint."""
import json,os,selectors,signal,subprocess,sys,threading,time,uuid
from pathlib import Path
from types import SimpleNamespace
from .policy_validation import OPERATIONS

class OutcomeUnknown(RuntimeError):pass

class NativeOwner:
    def __init__(self,*,python,config,evidence,deadline,cancel_check,process_observer=None):
        self.python=str(python);self.config=Path(config);self.evidence=Path(evidence)
        self.deadline,self.cancel_check=deadline,cancel_check;self.process_observer=process_observer
        self.process=self.log=None;self.selector=None;self.buffer=bytearray();self.cleanup=None;self.ready=None
        self.ledger=[];self.sent={};self.lock=threading.Lock();self.child_ids=[]
        self.unresolved=set()
    def start(self):
        env={k:v for k,v in os.environ.items() if not k.startswith(('PYTHON','CUROBO_'))
            and k not in {'LD_PRELOAD','LD_LIBRARY_PATH'}
            and not any(s in k.upper() for s in ('KEY','TOKEN','SECRET','PASSWORD'))}
        project=Path(__file__).resolve().parent.parent
        env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONHASHSEED='0',MUJOCO_GL='egl',
            PYTHONPATH=str(project)+os.pathsep+'/home/truares/桌面/Tidybot-Universe-attention-native',
            XDG_CACHE_HOME=str(self.evidence/'cache'),NUMBA_CACHE_DIR=str(self.evidence/'numba-cache'))
        self.log=(self.evidence/'native-service.log').open('ab')
        self.process=subprocess.Popen([self.python,'-B','-u','-m','openarm365_dev.worker',
            '--config',str(self.config),'--evidence',str(self.evidence),'--deadline',str(self.deadline),
            '--owner-pid',str(os.getpid())],env=env,cwd=project,stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=self.log,start_new_session=True)
        if self.process_observer:self.process_observer(self.process.pid)
        self.selector=selectors.DefaultSelector();self.selector.register(self.process.stdout,selectors.EVENT_READ)
        while True:
            message=self.read()
            if message.get('kind')=='ready':self.ready=message;return message
            if message.get('kind')=='fatal':raise RuntimeError('Native startup failed: '+message['error'])
            raise RuntimeError('Unexpected native startup message')
    def read(self):
        while True:
            if self.cancel_check():raise InterruptedError('operator cancelled')
            left=self.deadline-time.monotonic()
            if left<=0:raise TimeoutError('native owner deadline')
            if b'\n' in self.buffer:
                raw,_,rest=self.buffer.partition(b'\n');self.buffer=bytearray(rest);value=json.loads(raw)
                if value.get('kind')=='spawn':
                    self.child_ids.append(value['pid'])
                    if self.process_observer:self.process_observer(value['pid'])
                    continue
                return value
            if self.process.poll() is not None:raise OutcomeUnknown('Native process exited before its durable response')
            if not self.selector.select(min(.1,left)):continue
            chunk=os.read(self.process.stdout.fileno(),65536)
            if not chunk:raise OutcomeUnknown('Native response channel closed')
            self.buffer.extend(chunk)
            if len(self.buffer)>8_000_000:raise OutcomeUnknown('Native response exceeded bounded message size')
    def rpc(self,group,operation,args=(),kwargs=None,*,request_id=None):
        if group!='owner' and operation not in OPERATIONS.get(group,()):raise ValueError('Unauthorized public SDK operation')
        request=dict(request_id=request_id or uuid.uuid4().hex,group=group,operation=operation,args=list(args),kwargs=kwargs or {})
        canonical=json.dumps(request,sort_keys=True,allow_nan=False)
        rid=request['request_id']
        if rid in self.sent and self.sent[rid]!=canonical:raise ValueError('Conflicting request ID')
        self.sent[rid]=canonical
        with (self.evidence/'owner-rpc-intents.jsonl').open('a') as f:
            f.write(canonical+'\n');f.flush();os.fsync(f.fileno())
        self.unresolved.add(rid)
        try:
            self.process.stdin.write((canonical+'\n').encode());self.process.stdin.flush();response=self.read()
        except (OSError,TimeoutError) as exc:raise OutcomeUnknown('Native request sent without a complete receipt; no automatic retry') from exc
        if response.get('kind')!='response' or response.get('request_id')!=rid:
            raise OutcomeUnknown('Native response identity mismatch')
        self.ledger.append(dict(response,group=group,operation=operation))
        self.unresolved.discard(rid)
        if response.get('ok') is not True:raise RuntimeError(response.get('error','Native action rejected'))
        return response.get('value')
    def stop(self,reason='normal_cleanup'):
        with self.lock:
            if self.cleanup is not None:return self.cleanup
            if self.process and self.process.poll() is None:
                try:
                    self.process.stdin.write((json.dumps(dict(request_id=uuid.uuid4().hex,group='owner',operation='stop',args=[],kwargs={}))+'\n').encode())
                    self.process.stdin.flush();os.killpg(self.process.pid,signal.SIGTERM)
                except (OSError,BrokenPipeError):pass
                try:self.process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    try:os.killpg(self.process.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    self.process.wait(timeout=4)
            if self.selector:self.selector.close()
            if self.process:
                self.process.stdin.close();self.process.stdout.close()
            if self.log:self.log.close()
            def gone(pid):
                try:os.killpg(pid,0)
                except ProcessLookupError:return True
                return False
            native_cleanup=self.evidence/'native_cleanup.json'
            self.cleanup=dict(reason=reason,leader_pid=self.process.pid if self.process else None,
                leader_reaped=self.process is None or self.process.poll() is not None,
                process_group_gone=self.process is None or gone(self.process.pid),
                planner_process_groups_gone=all(gone(pid) for pid in self.child_ids),
                native_cleanup=json.loads(native_cleanup.read_bytes()) if native_cleanup.exists() else None)
            (self.evidence/'owner_cleanup.json').write_text(json.dumps(self.cleanup,indent=2)+'\n');return self.cleanup

def build_sdk(owner,record_event):
    def method(group,operation):
        def call(*args,**kwargs):
            event=dict(source=group,operation=operation,status='started',arguments={})
            if group.endswith('_arm'):
                event['physical_arm']=group.split('_')[0]
                event['arguments']=dict(zip(('dx','dy','dz','rotation_delta'),args));event['arguments'].update(kwargs)
            elif group=='bimanual':event['arguments']={'target_qpos':args[0] if args else kwargs.get('target_qpos')}
            try:
                value=owner.rpc(group,operation,args,kwargs)
                event.update(status='completed',result=value);record_event(event);return value
            except Exception as exc:
                event.update(status='failed',error={'type':type(exc).__name__,'message':str(exc)[:1200]});record_event(event);raise
        return call
    return SimpleNamespace(**{group:SimpleNamespace(**{op:method(group,op) for op in ops}) for group,ops in OPERATIONS.items()})
