"""Fresh isolated Eval agent for one formal public execution, never one-call Eval."""
import hashlib
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import threading
import time
from .dev_agent_workspace import atomic_write, encode, read_beneath
from .dev_agent_prompts import EVAL_PROMPT
from .dev_agent_diagnostics import FORBIDDEN

SCHEMA = {'type':'object','additionalProperties':False,'required':['passed','reason','evidence','advice'],
          'properties':{'passed':{'type':'boolean'},'reason':{'type':'string'},'advice':{'type':'string'},
          'evidence':{'type':'array','items':{'type':'object','additionalProperties':False,
              'required':['claim','source'],'properties':{'claim':{'type':'string'},'source':{'type':'string'}}}}}}
PUBLIC_FILES = ('stdout.txt','stderr.txt','state_log.json','trace.json','diagnostics/planner.json',
                'frames.json')


def feedback(value):
    """Keep complete prose/citations; the numeric observation projector is unsuitable."""
    if isinstance(value, str): return '[withheld]' if FORBIDDEN.search(value) else value
    if isinstance(value, list): return [feedback(v) for v in value]
    if isinstance(value, dict): return {k:feedback(v) for k,v in value.items() if not FORBIDDEN.search(k)}
    return value


def unavailable(version, error):
    return dict(status='unavailable',passed=None,reason=f'Eval unavailable for {version}: {error}',
                error=error,evidence=[],advice='Inspect the public execution record and continue.')


def validate(value):
    if not isinstance(value,dict) or set(value) != set(SCHEMA['required']): raise ValueError('schema_invalid')
    if type(value['passed']) is not bool or any(not isinstance(value[k],str) for k in ('reason','advice')):
        raise ValueError('schema_invalid')
    if not isinstance(value['evidence'],list): raise ValueError('schema_invalid')
    for row in value['evidence']:
        if not isinstance(row,dict) or set(row)!={'claim','source'} or any(not isinstance(v,str) for v in row.values()):
            raise ValueError('schema_invalid')
    return value


def build_workspace(workspace, execution):
    vid,eid = execution['version_id'],execution['execution_id']
    root = workspace.private/'eval'/vid
    ws = root/'ws'; ws.mkdir(parents=True,exist_ok=False)
    def copy(source,destination):
        raw=read_beneath(workspace.canonical,source,512*1024*1024)
        atomic_write(ws,destination,raw)
    copy('task.json','task.json')
    for name in PUBLIC_FILES: copy('executions/'+eid+'/'+name,name)
    copy('versions/'+vid+'/bundle.py','bundle.py')
    for path in sorted((workspace.canonical/'versions'/vid/'sources').glob('*.py')):
        copy('versions/'+vid+'/sources/'+path.name,'sources/'+path.name)
    frames=json.loads((ws/'frames.json').read_bytes())
    for row in frames: copy('executions/'+eid+'/'+row['filename'],row['filename'])
    return ws


class Evaluator:
    def __init__(self, workspace, *, model='gpt-6-astra', prompt=EVAL_PROMPT, quota=None, command_factory=None):
        from .dev_agent_isolation import controller_command
        self.workspace,self.model,self.prompt,self.quota=workspace,model,prompt,quota
        self.command_factory=command_factory or controller_command
        self.preflight=command_factory is None
        self.process=None;self.usage=None;self.cancelled=threading.Event()

    def evaluate(self, execution, *, deadline):
        vid=execution['version_id'];self.usage=None;self.process=None;self.cancelled.clear()
        root=self.workspace.private/'eval'/vid
        error=None;answer=None;completed=False
        try:
            ws=build_workspace(self.workspace,execution)
            if self.preflight:
                import socket
                from .dev_agent_isolation import verify_runtime
                with socket.socket() as listener:
                    listener.bind(('127.0.0.1',0));listener.listen()
                    verify_runtime(ws,root,planner_port=listener.getsockname()[1])
            atomic_write(root,'schema.json',encode(SCHEMA))
            command=list(self.command_factory(ws,self.prompt))
            command[command.index('-m')+1]=self.model
            i=next(i for i,v in enumerate(command) if v.startswith('model_reasoning_effort='))
            command[i]='model_reasoning_effort="high"'
            command[command.index('exec')+1:command.index('exec')+1]=['--ephemeral','--output-schema',str(root/'schema.json')]
            atomic_write(root,'command.json',encode(command))
            deadline=min(deadline,time.monotonic()+900)
            if self.quota:self.quota.check('before_eval')
            if self.cancelled.is_set() or time.monotonic()>=deadline:raise TimeoutError('timeout')
            inbox=queue.Queue();last_sample=time.monotonic()
            with (root/'events.jsonl').open('wb') as logs,(root/'stderr.txt').open('wb') as errors:
                self.process=subprocess.Popen(command,cwd=ws,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                                              stderr=errors,start_new_session=True)
                def read():
                    try:
                        for line in self.process.stdout:inbox.put(line)
                    finally:inbox.put(None)
                threading.Thread(target=read,daemon=True).start()
                while True:
                    if self.cancelled.is_set() or time.monotonic()>=deadline:raise TimeoutError('timeout')
                    if self.quota and time.monotonic()-last_sample>=60:
                        self.quota.check('during_eval');last_sample=time.monotonic()
                    try:line=inbox.get(timeout=.02)
                    except queue.Empty:continue
                    if line is None:break
                    logs.write(line);logs.flush()
                    event=json.loads(line)
                    if self.quota:self.quota.event(event)
                    if event.get('type')=='turn.completed':
                        completed=True
                        usage=event.get('usage')
                        if isinstance(usage,dict) and usage and all(type(v) is int and v>=0 for v in usage.values()):self.usage=usage
                    if event.get('type') in ('error','turn.failed'):error='process_failed'
                    item=event.get('item',{})
                    if event.get('type')=='item.completed' and item.get('type')=='agent_message':answer=item.get('text')
                if self.process.wait(timeout=max(.01,deadline-time.monotonic())) or error or not completed:
                    raise RuntimeError('process_failed')
                try:verdict=validate(json.loads(answer))
                except (ValueError,TypeError):raise ValueError('schema_invalid')
                result=dict(status='completed',**feedback(verdict))
        except (TimeoutError,subprocess.TimeoutExpired):
            error='timeout';result=unavailable(vid,error)
            atomic_write(root,'error.json',encode(dict(error_class=error,error_text='Eval exceeded its hard deadline')))
        except Exception as exc:
            error=str(exc) if str(exc) in ('schema_invalid','process_failed','quota_stop') else type(exc).__name__
            result=unavailable(vid,error)
            root.mkdir(parents=True,exist_ok=True)
            atomic_write(root,'error.json',encode(dict(error_class=error,error_text=str(exc))))
        finally:
            self.cancel()
            if root.exists():atomic_write(root,'usage.json',encode(dict(usage=self.usage if self.usage is not None else 'unknown',error=error)))
        return result

    def cancel(self):
        self.cancelled.set()
        if self.process:
            try:os.killpg(self.process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            self.process.wait()
        return {'terminated':True}
