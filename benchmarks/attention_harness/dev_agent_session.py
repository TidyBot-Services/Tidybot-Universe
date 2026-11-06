"""One persistent Codex thread; raw output and uncertain usage stay private."""
from abc import ABC, abstractmethod
from contextlib import contextmanager
import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import threading
from .dev_agent_workspace import atomic_write, encode
from .dev_agent_parcc_bridge import PARCC_DEV_MODELS, ParccBridge, ChatBridge, provider_args


class SessionError(RuntimeError): pass


class AgentSession(ABC):
    @abstractmethod
    def start(self, public_bootstrap, workspace=None, identity=None): pass
    @abstractmethod
    def resume(self, exact_session_id, messages, workspace=None): pass
    @abstractmethod
    def checkpoint(self): pass
    @abstractmethod
    def cancel(self, reason): pass


class CodexAgentSession(AgentSession):
    def __init__(self, workspace, private, *, model='gpt-6-astra', reasoning_effort='high',
                 command_factory=None, provider=None):
        from .dev_agent_isolation import controller_command
        self.workspace,self.private=Path(workspace),Path(private)
        self.model,self.reasoning_effort=model,reasoning_effort
        self.command_factory=command_factory or controller_command
        self.provider=provider
        self.process=None;self.thread_id=None;self.turn=0;self.usage='unknown';self.cancelled=None;self.bridge=None
        self.state_path=self.private/'session.json'
        if self.state_path.exists():
            state=json.loads(self.state_path.read_bytes())
            self.thread_id=state['thread_id'];self.turn=state['turn']
        self.messages=[]
        self._quiesce_lock=threading.RLock();self._quiesce_depth=0

    def command(self, resumed):
        # Parent exec-only -C precedes resume; all inherited security settings
        # are accepted by resume itself in 0.155.1. cwd always binds ws.
        command=list(self.command_factory(self.workspace,'-'))
        i=command.index('-m');command[i+1]=self.model
        if self.provider is not None:
            if self.bridge is None:self.bridge=ChatBridge(self.private/'parcc-bridge',self.provider.spec,self.provider.secret,token=self.provider.token)
            command[i:i]=provider_args(self.bridge.base_url,token_env=self.provider.token_env)
        elif self.model in PARCC_DEV_MODELS:
            # Same Codex harness; only the provider changes (loopback bridge, key stays in the bridge).
            if self.bridge is None:self.bridge=ParccBridge(self.private/'parcc-bridge')
            command[i:i]=provider_args(self.bridge.base_url)
        i=next(i for i,v in enumerate(command) if v.startswith('model_reasoning_effort='))
        command[i]='model_reasoning_effort='+json.dumps(self.reasoning_effort)
        if resumed:
            i=command.index('-C');del command[i:i+2]
            i=command.index('exec'); command[i+1:i+1]=['resume',self.thread_id]
        if '--last' in command or '--ephemeral' in command:raise SessionError('persistent_exact_session_required')
        return command

    def _save(self):
        atomic_write(self.private,'session.json',encode(dict(thread_id=self.thread_id,turn=self.turn)))

    def start(self, public_bootstrap, workspace=None, identity=None):
        if self.thread_id:raise SessionError('session_already_started')
        return self._turn(public_bootstrap,False)

    def resume(self, exact_session_id, messages, workspace=None):
        if not self.thread_id or exact_session_id!=self.thread_id:raise SessionError('exact_thread_id_required')
        return self._turn(messages,True)

    def _turn(self, prompt, resumed):
        if not isinstance(prompt,str):prompt=json.dumps(prompt,ensure_ascii=False,sort_keys=True)
        self.turn+=1; self.usage='unknown';self.cancelled=None;self.messages.append(prompt);self._save()
        directory=self.private/'turns'/('t%06d'%self.turn);directory.mkdir(parents=True,exist_ok=False)
        command=self.command(resumed)
        atomic_write(directory,'input.json',encode(dict(prompt=prompt,command=command)))
        completed=False;failure=None;inbox=queue.Queue(); eof=False
        with (directory/'events.jsonl').open('wb') as raw, (directory/'stderr.txt').open('wb') as errors:
            try:
                options={}
                if self.provider is not None:
                    from .dev_agent_key_vault import keyless_env
                    options['env']=keyless_env({**os.environ,self.provider.token_env:self.provider.token})
                self.process=subprocess.Popen(command,cwd=self.workspace,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                              stderr=errors,start_new_session=True,**options)
                # Feed finite stdin, then close immediately: never wait for a tty.
                self.process.stdin.write(prompt.encode()); self.process.stdin.close()
                def read_lines():
                    try:
                        for line in self.process.stdout:inbox.put(line)
                    finally:inbox.put(None)
                reader=threading.Thread(target=read_lines,daemon=True);reader.start()
                while not eof:
                    try:line=inbox.get(timeout=.05)
                    except queue.Empty:
                        yield None
                        continue
                    if line is None:eof=True;continue
                    raw.write(line);raw.flush()
                    try:
                        event=json.loads(line)
                        if not isinstance(event,dict) or not isinstance(event.get('type'),str):raise ValueError()
                    except (ValueError,UnicodeError) as exc:raise SessionError('corrupt_jsonl') from exc
                    if event['type']=='thread.started':
                        tid=event.get('thread_id')
                        if not isinstance(tid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',tid):raise SessionError('invalid_thread_id')
                        if self.thread_id and self.thread_id!=tid:raise SessionError('thread_id_changed')
                        self.thread_id=tid;self._save()  # before yielding to controller
                    elif event['type']=='turn.completed':
                        completed=True
                        usage=event.get('usage')
                        if isinstance(usage,dict) and usage and all(type(v) is int and v>=0 for v in usage.values()):self.usage=usage
                    elif event['type'] in ('turn.failed','error'):
                        detail=event.get('message') or (event.get('error') or {}).get('message')
                        failure='turn_failed'+(': '+detail if isinstance(detail,str) else '')
                    yield event
                code=self.process.wait()
                if not self.cancelled:
                    if not self.thread_id:raise SessionError('missing_thread_id')
                    if code or failure or not completed:raise SessionError(failure or 'turn_process_failed')
            finally:
                self.cancel(self.cancelled or 'turn_cleanup')
                if self.process and self.process.stdout:self.process.stdout.close()
                atomic_write(directory,'usage.json',encode(dict(usage=self.usage,completed=completed)))

    def checkpoint(self):
        # File rehydration only. Never copy CODEX_HOME or claim hidden-state replay.
        return dict(thread_id=self.thread_id,turn=self.turn,replay='file_rehydration')

    @contextmanager
    def quiesce(self):
        with self._quiesce_lock:
            process=self.process
            running=self._quiesce_depth==0 and process is not None and process.poll() is None
            if running:
                try:os.killpg(process.pid,signal.SIGSTOP)
                except ProcessLookupError:running=False
            self._quiesce_depth+=1
            try:yield
            finally:
                self._quiesce_depth-=1
                if running:
                    try:os.killpg(process.pid,signal.SIGCONT)
                    except ProcessLookupError:pass

    def cancel(self, reason):
        self.cancelled=reason
        if self.process:
            # Kill the group even if its leader has exited; tools may outlive it.
            try:os.killpg(self.process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            self.process.wait()
        if self.bridge is not None:
            self.bridge.close();self.bridge=None
        return dict(reason=reason,terminated=True)

    def close(self): return self.cancel('session_closed')
