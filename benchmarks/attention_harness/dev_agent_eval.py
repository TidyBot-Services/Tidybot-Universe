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
from .dev_agent_workspace import atomic_write, encode, read_beneath, share
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


# 2026-11-11 (user): PARCC's Qwen may run Eval. It often ended on prose instead of the JSON (real check 2026-11-05: 1 of 6
# sessions), so a routed Eval's final message is read leniently and, failing that, rewritten once by the same model.
REPAIR_PROMPT = ('Your review of this attempt ended with the text below instead of the required JSON verdict. Do not run '
    'commands, open files or view images again. Using only this text, reply with the verdict as one JSON object that '
    'matches the output schema and nothing else: passed (true or false), reason, evidence (each item a claim and the '
    'file or frame it came from as source) and advice.\n\n<review>\n{review}\n</review>')
REPAIR_SECONDS = 120
REPAIR_REVIEW_CHARS = 20000


def embedded_verdict(text):
    """The last schema-valid JSON object inside a message (in a code fence or between sentences), else None."""
    if not isinstance(text,str):return None
    decoder=json.JSONDecoder();found=None;i=text.find('{');tries=0
    while i!=-1 and tries<2000:
        tries+=1
        try:value,end=decoder.raw_decode(text,i)
        except ValueError:i=text.find('{',i+1);continue
        try:found=validate(value);i=text.find('{',end)
        except ValueError:i=text.find('{',i+1)
    return found


def build_workspace(workspace, execution, success_condition=None):
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
    # One physical frame: the canonical 0444 inode, mounted read-only under frames/.
    if frames:(ws/'frames').mkdir(mode=0o700,exist_ok=True)
    for row in frames: share(workspace.canonical,'executions/'+eid+'/'+row['filename'],ws,row['filename'])
    if success_condition:
        # Eval-only public container rule; final pose is required to apply it.
        from .dev_agent_success import FILE,eval_view
        copy('executions/'+eid+'/final_observation.json','final_observation.json')
        atomic_write(ws,FILE,encode(eval_view(success_condition)))
    return ws


class Evaluator:
    def __init__(self, workspace, *, model='gpt-6-astra', prompt=EVAL_PROMPT, quota=None, command_factory=None,
                 success_condition=None, redact=True, provider=None):
        from .dev_agent_isolation import controller_command
        self.workspace,self.model,self.prompt,self.quota=workspace,model,prompt,quota
        self.provider=provider;self.bridge=None;self._bridge_lock=threading.RLock()
        if provider is not None:self.model=provider.model
        self.success_condition,self.redact=success_condition,redact
        self.command_factory=command_factory or controller_command
        self.preflight=command_factory is None
        self.process=None;self.reader=None;self.usage=None;self.cancelled=threading.Event()

    def evaluate(self, execution, *, deadline):
        vid=execution['version_id'];self.usage=None;self.process=None;self.cancelled.clear()
        root=self.workspace.private/'eval'/vid
        error=None;answer=None;completed=False
        try:
            ws=build_workspace(self.workspace,execution,self.success_condition)
            if self.preflight:
                import socket
                from .dev_agent_isolation import verify_runtime
                with socket.socket() as listener:
                    listener.bind(('127.0.0.1',0));listener.listen()
                    verify_runtime(ws,root,planner_port=listener.getsockname()[1])
            atomic_write(root,'schema.json',encode(SCHEMA))
            command=list(self.command_factory(ws,self.prompt))
            command[command.index('-m')+1]=self.model
            options={}
            if self.provider is not None:
                from .dev_agent_parcc_bridge import ChatBridge,provider_args
                from .dev_agent_key_vault import keyless_env
                with self._bridge_lock:
                    if self.cancelled.is_set():raise TimeoutError('timeout')
                    self.bridge=ChatBridge(self.workspace.private/'byok-bridge-eval',self.provider.spec,
                        self.provider.secret,token=self.provider.token,vision=getattr(self.provider,'vision',True),role='eval')
                    i=command.index('-m')
                    command[i:i]=provider_args(self.bridge.base_url,token_env=self.provider.token_env)
                options['env']=keyless_env({**os.environ,self.provider.token_env:self.provider.token})
            i=next(i for i,v in enumerate(command) if v.startswith('model_reasoning_effort='))
            command[i]='model_reasoning_effort="high"'
            command[command.index('exec')+1:command.index('exec')+1]=['--ephemeral','--output-schema',str(root/'schema.json')]
            atomic_write(root,'command.json',encode(command))
            deadline=min(deadline,time.monotonic()+900)
            if self.quota:self.quota.check('before_eval')
            if self.cancelled.is_set() or time.monotonic()>=deadline:raise TimeoutError('timeout')
            inbox=queue.Queue();last_sample=time.monotonic()
            with (root/'events.jsonl').open('wb') as logs,(root/'stderr.txt').open('wb') as errors:
                with self._bridge_lock:
                    if self.cancelled.is_set():raise TimeoutError('timeout')
                    self.process=subprocess.Popen(command,cwd=ws,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                                                  stderr=errors,start_new_session=True,**options)
                    def read():
                        try:
                            for line in self.process.stdout:inbox.put(line)
                        finally:inbox.put(None)
                    self.reader=threading.Thread(target=read,daemon=True);self.reader.start()
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
                verdict=self.read_verdict(ws,root,answer,deadline,options)
                if self.success_condition and self.redact:
                    # Raw verdict stays private; Dev gets the conclusion without the rule's numbers or names.
                    from .dev_agent_success import dev_view
                    atomic_write(root,'verdict.json',encode(verdict))
                    verdict=dev_view(verdict,self.success_condition,json.loads((ws/'final_observation.json').read_bytes()))
                result=dict(status='completed',**feedback(verdict))
                self.write_plain_review(ws,root,result,deadline,options)
        except (TimeoutError,subprocess.TimeoutExpired):
            error='timeout';result=unavailable(vid,error)
            atomic_write(root,'error.json',encode(dict(error_class=error,error_text='Eval exceeded its hard deadline')))
        except Exception as exc:
            error=str(exc) if str(exc) in ('schema_invalid','process_failed','quota_stop') else type(exc).__name__
            result=unavailable(vid,error)
            root.mkdir(parents=True,exist_ok=True)
            detail=str(exc)
            if self.provider is not None:
                from .dev_agent_secret import scrub
                detail,_=scrub(detail,[self.provider.secret])
            atomic_write(root,'error.json',encode(dict(error_class=error,error_text=detail)))
        finally:
            self.cancel()
            if root.exists():atomic_write(root,'usage.json',encode(dict(usage=self.usage if self.usage is not None else 'unknown',error=error)))
        return result

    def read_verdict(self,ws,root,answer,deadline,options):
        """The final message as a verdict. Codex lab models keep the strict reading. For a routed model (PARCC, a user's
        key), JSON inside a code fence or a sentence is taken as is, and a prose ending gets one short repair turn of
        the same model through the same bridge, logged and charged like the review. repair.json records either."""
        try:return validate(json.loads(answer))
        except (ValueError,TypeError):pass
        if self.bridge is None:raise ValueError('schema_invalid')
        found=embedded_verdict(answer)
        if found is not None:
            atomic_write(root,'repair.json',encode(dict(method='extracted')))
            return found
        if not isinstance(answer,str) or not answer.strip():raise ValueError('schema_invalid')
        found=self.repair_turn(ws,root,answer,deadline,options)
        if found is None:raise ValueError('schema_invalid')
        atomic_write(root,'repair.json',encode(dict(method='rewritten')))
        return found

    def repair_turn(self,ws,root,answer,deadline,options):
        """One ephemeral turn that turns the review's own prose into the verdict; None when it cannot."""
        from .dev_agent_parcc_bridge import provider_args
        spent=None;found=None
        try:
            limit=min(deadline,time.monotonic()+REPAIR_SECONDS)
            if self.reader is not None:self.reader.join()
            if self.process.stdout:self.process.stdout.close()
            command=list(self.command_factory(ws,REPAIR_PROMPT.format(review=answer[-REPAIR_REVIEW_CHARS:])))
            command[command.index('-m')+1]=self.model
            i=command.index('-m')
            command[i:i]=provider_args(self.bridge.base_url,token_env=self.provider.token_env)
            i=next(i for i,v in enumerate(command) if v.startswith('model_reasoning_effort='))
            command[i]='model_reasoning_effort="high"'
            command[command.index('exec')+1:command.index('exec')+1]=['--ephemeral','--output-schema',str(root/'schema.json')]
            text=None
            with (root/'repair-events.jsonl').open('wb') as logs,(root/'repair-stderr.txt').open('wb') as errors:
                with self._bridge_lock:
                    if self.cancelled.is_set():raise TimeoutError('timeout')
                    self.process=subprocess.Popen(command,cwd=ws,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                                                  stderr=errors,start_new_session=True,**options)
                    self.reader=None
                timer=threading.Timer(max(.01,limit-time.monotonic()),self.process.kill);timer.start()
                try:
                    for line in self.process.stdout:
                        logs.write(line)
                        event=json.loads(line)
                        if self.quota:self.quota.event(event)
                        if event.get('type')=='turn.completed' and isinstance(event.get('usage'),dict):spent=event['usage']
                        item=event.get('item',{})
                        if event.get('type')=='item.completed' and item.get('type')=='agent_message':text=item.get('text')
                    self.process.wait(timeout=max(.01,limit-time.monotonic()))
                finally:timer.cancel()
            try:found=validate(json.loads(text))
            except (ValueError,TypeError):found=embedded_verdict(text)
            if found is None:atomic_write(root,'repair-error.json',encode(dict(error_class='unreadable_answer')))
        except TimeoutError:raise
        except Exception as exc:
            atomic_write(root,'repair-error.json',encode(dict(error_class=type(exc).__name__)))
        if isinstance(spent,dict) and all(type(v) is int and v>=0 for v in spent.values()):
            atomic_write(root,'repair-usage.json',encode(spent))
            if self.usage is not None:self.usage={k:self.usage.get(k,0)+spent.get(k,0) for k in {*self.usage,*spent}}
        return found

    def write_plain_review(self,ws,root,result,deadline,options):
        """For the page only, and never read by Dev. One more turn of the same model through the same route, so it is
        logged and charged like the review itself. A failure here changes nothing about the run."""
        from .dev_agent_try_story import parse_review,review_prompt
        spent=None
        try:
            limit=min(deadline,time.monotonic()+60)
            if self.reader is not None:self.reader.join()
            if self.process.stdout:self.process.stdout.close()
            command=list(self.command_factory(ws,review_prompt(result)))
            command[command.index('-m')+1]=self.model
            if self.bridge is not None:
                from .dev_agent_parcc_bridge import provider_args
                i=command.index('-m')
                command[i:i]=provider_args(self.bridge.base_url,token_env=self.provider.token_env)
            command[command.index('exec')+1:command.index('exec')+1]=['--ephemeral']
            answer=None
            with (root/'plain-events.jsonl').open('wb') as logs,(root/'plain-stderr.txt').open('wb') as errors:
                with self._bridge_lock:
                    if self.cancelled.is_set():raise TimeoutError('timeout')
                    self.process=subprocess.Popen(command,cwd=ws,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                                                  stderr=errors,start_new_session=True,**options)
                    self.reader=None
                timer=threading.Timer(max(.01,limit-time.monotonic()),self.process.kill);timer.start()
                try:
                    for line in self.process.stdout:
                        logs.write(line)
                        event=json.loads(line)
                        if self.quota:self.quota.event(event)
                        if event.get('type')=='turn.completed' and isinstance(event.get('usage'),dict):spent=event['usage']
                        item=event.get('item',{})
                        if event.get('type')=='item.completed' and item.get('type')=='agent_message':answer=item.get('text')
                    self.process.wait(timeout=max(.01,limit-time.monotonic()))
                finally:timer.cancel()
            plain=parse_review(answer)
            if plain:atomic_write(root,'plain.json',encode(plain))
            else:atomic_write(root,'plain-error.json',encode(dict(error_class='unreadable_answer')))
        except Exception as exc:
            atomic_write(root,'plain-error.json',encode(dict(error_class=type(exc).__name__)))
        if isinstance(spent,dict) and all(type(v) is int and v>=0 for v in spent.values()):
            atomic_write(root,'plain-usage.json',encode(spent))
            if self.usage is not None:self.usage={k:self.usage.get(k,0)+spent.get(k,0) for k in {*self.usage,*spent}}

    def cancel(self):
        self.cancelled.set()
        with self._bridge_lock:
            if self.process:
                try:os.killpg(self.process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                self.process.wait()
                if self.reader is not None:self.reader.join()
                if self.process.stdout:self.process.stdout.close()
            if self.bridge is not None:self.bridge.close();self.bridge=None
        return {'terminated':True}
