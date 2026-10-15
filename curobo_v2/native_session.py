"""Authenticated owner of the existing kitchen executor and its private V2 child."""
from __future__ import annotations
import json
import hashlib
import io
import math
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request,urlopen
from .identity import IdentityError,load_lock,verify_files,verify_client_imports
from .native_auth import verify_native_payload
from .evidence import preserve_runtime
from .native_assets import asset_identity


class NativeV2Service:
    def __init__(self, *, lock_path,robot_config,task_source,evidence,deadline,task,seed,arm,cancel_check=None,parameters_path=None):
        if any(not Path(p).is_absolute()for p in [lock_path,robot_config,task_source]):
            raise IdentityError('Native V2 owner requires explicit absolute paths')
        if isinstance(deadline,bool)or not isinstance(deadline,(int,float))or not math.isfinite(deadline):
            raise IdentityError('Native V2 owner requires an explicit finite monotonic deadline')
        self.lock_path=str(Path(lock_path).resolve());self.lock,self.lock_sha=load_lock(self.lock_path)
        self.robot_config=str(Path(robot_config).resolve());self.task_source=str(Path(task_source).resolve())
        self.parameters_path=None if parameters_path is None else str(Path(parameters_path).resolve())
        self.evidence=Path(evidence);self.deadline=deadline;self.task,self.seed,self.arm=task,seed,arm
        if cancel_check is not None and not callable(cancel_check):raise IdentityError('Native cancellation predicate must be callable')
        self.cancel_check=cancel_check or (lambda:False)
        self.failed=False;self.http_log_lock=threading.Lock()
        self.process=self.log=None;self.stop_receipt=None;self.done=threading.Event();self.mutex=threading.Lock()
        self.nonce=secrets.token_hex(16);self.token=secrets.token_hex(32)

    def __enter__(self):
        verify_files(self.lock);verify_client_imports(self.lock)
        if self.parameters_path is not None and self.parameters_path not in self.lock['files']:
            raise IdentityError('Native parameters are absent from the runtime lock')
        if self.robot_config not in self.lock['robot_configs']:raise IdentityError('Native V2 robot configuration is not locked')
        if self.task_source not in self.lock.get('task_sources',{}):raise IdentityError('Native task source is not locked')
        if self.task not in {'counter_to_sink','counter_to_cab'}or self.arm not in {'left','right'}or type(self.seed)is not int:
            raise IdentityError('This authenticated native adapter has no declared branch for the selection')
        if self.cancel_check()or time.monotonic()>=self.deadline:raise InterruptedError('Native V2 startup canceled or expired')
        self.evidence.mkdir(parents=True,exist_ok=False);preserve_runtime(self.lock_path,self.lock,self.evidence)
        self.log=(self.evidence/'native.log').open('wb')
        data_identity=asset_identity(self.lock)
        env={k:v for k,v in os.environ.items()if not k.startswith(('PYTHON','CUROBO_','MS_ASSET'))and k not in {'LD_PRELOAD','LD_LIBRARY_PATH'}and not any(x in k.upper()for x in ('TOKEN','SECRET','KEY','PASSWORD'))}
        env.update(PYTHONHASHSEED='0',PYTHONDONTWRITEBYTECODE='1',CUROBO_V2_NATIVE_TOKEN=self.token,
            CUROBO_V2_NATIVE_NONCE=self.nonce,CUROBO_V2_NATIVE_OWNER_PID=str(os.getpid()),
            XDG_CACHE_HOME=str(self.evidence/'cache'),MPLCONFIGDIR=str(self.evidence/'mpl-cache'),MS_ASSET_DIR=data_identity['env_root'])
        try:
            with socket.socket()as listener:
                listener.bind(('127.0.0.1',0));listener.listen();self.url='http://127.0.0.1:'+str(listener.getsockname()[1])
                self.command=[self.lock['python'],'-B','-P','-s',str(Path(self.lock['project_root'])/'curobo_v2/native_worker.py'),
                    '--lock',self.lock_path,'--robot-config',self.robot_config,'--task-source',self.task_source,
                    '--evidence',str(self.evidence/'native'),'--socket-fd',str(listener.fileno()),
                    '--task',self.task,'--seed',str(self.seed),'--arm',self.arm,'--deadline',str(self.deadline)]
                if self.parameters_path is not None:self.command.extend(['--parameters',self.parameters_path])
                self.process=subprocess.Popen(self.command,cwd=self.lock['project_root'],env=env,stdout=self.log,
                    stderr=subprocess.STDOUT,start_new_session=True,pass_fds=(listener.fileno(),))
            (self.evidence/'command.json').write_text(json.dumps(self.command,ensure_ascii=False,indent=2)+'\n')
            threading.Thread(target=self._watch,daemon=True).start()
            while time.monotonic()<self.deadline:
                if self.cancel_check():raise InterruptedError('Native startup canceled')
                if self.process.poll()is not None:raise IdentityError('Native V2 worker exited; inspect native.log')
                try:
                    identity=self.attest(timeout=min(8.,self.deadline-time.monotonic()))
                    if identity['ready']:
                        self.identity=identity
                        (self.evidence/'native_identity.json').write_text(json.dumps(identity,indent=2)+'\n')
                        return self
                except (ConnectionError,OSError):pass
                self.done.wait(.05)
            raise TimeoutError('Native V2 startup reached the unchanged episode deadline')
        except BaseException:
            self.close('startup_failed');raise

    def _check_live(self):
        try:canceled=self.cancel_check()
        except Exception as exc:
            self.failed=True;raise IdentityError('Native cancellation predicate failed')from exc
        if self.failed or canceled or time.monotonic()>=self.deadline:
            self.failed=True;raise InterruptedError('Native V2 canceled, expired, or latched; returned result invalidated')
        if not self.process or self.process.poll()is not None:
            self.failed=True;raise IdentityError('Native process exited; stale service URL refused')

    def _request(self,path,body=None,*,timeout,cleanup=False):
        if not cleanup:self._check_live()
        elif not self.process or self.process.poll()is not None:raise IdentityError('Native cleanup process exited')
        request=Request(self.url+path,data=None if body is None else json.dumps(body,allow_nan=False).encode(),
            headers={'Content-Type':'application/json','Authorization':'Bearer '+self.token,'X-Curobo-Native':self.nonce})
        started=time.monotonic();row=dict(path=path,method=request.get_method(),request_raw=None if request.data is None else request.data.decode(),
            nonce=self.nonce,lock_sha256=self.lock_sha)
        try:
            try:
                with urlopen(request,timeout=timeout)as response:
                    if response.headers.get('x-curobo-native')!=self.nonce or response.headers.get('x-curobo-lock')!=self.lock_sha:
                        raise IdentityError('Native response belongs to an unrelated executor')
                    raw=response.read();row['http_status']=response.status
            except HTTPError as exc:
                raw=exc.read();row.update(http_status=exc.code,response_raw=raw.decode(errors='replace'))
                row['response_sha256']=hashlib.sha256(raw).hexdigest()
                if exc.headers.get('x-curobo-native')!=self.nonce or exc.headers.get('x-curobo-lock')!=self.lock_sha:
                    self.failed=True;raise IdentityError('Native error response belongs to an unrelated executor')from exc
                if not cleanup:self._check_live()
                if exc.code>=500:self.failed=True
                raise HTTPError(exc.url,exc.code,exc.msg,exc.hdrs,io.BytesIO(raw))from exc
            row['response_sha256']=hashlib.sha256(raw).hexdigest()
            if path not in {'/camera','/observation'}:row['response_raw']=raw.decode()
            else:row['response_bytes']=len(raw)
            if not cleanup:self._check_live()
            result=raw if path in {'/camera','/observation'}else json.loads(raw)
            if not cleanup:self._check_live()
            return result
        except Exception as exc:
            row['error']=f'{type(exc).__name__}: {exc}'
            raise
        finally:
            row['elapsed_seconds']=time.monotonic()-started
            with self.http_log_lock:
                with(self.evidence/'native_http.jsonl').open('a')as stream:stream.write(json.dumps(row,allow_nan=False)+'\n')

    def attest(self,timeout=8.):
        challenge=secrets.token_hex(16)
        payload=self._request('/identity?'+urlencode({'challenge':challenge}),timeout=timeout)
        identity=payload.get('identity',{})
        expected=dict(pid=self.process.pid,nonce=self.nonce,lock_sha256=self.lock_sha,
            python=self.lock['python'],prefix=self.lock['prefix'],project_root=self.lock['project_root'],
            robot_config=self.robot_config,task_source=self.task_source,task=self.task,seed=self.seed,arm=self.arm,
            native_assets=asset_identity(self.lock))
        if self.parameters_path is not None:
            expected['parameters']={'path':self.parameters_path,'sha256':self.lock['files'][self.parameters_path]}
        return verify_native_payload(payload,expected=expected,token=self.token,challenge=challenge)

    def request(self,method,path,body=None,timeout=None):
        if method not in {'GET','POST'}or (method=='GET')!=(body is None):raise ValueError('Invalid native transport request')
        left=self.deadline-time.monotonic()
        if path in {'/stop','/cancel'}:
            return self._request(path,body,timeout=2.,cleanup=True)
        if left<=0 or self.cancel_check():raise InterruptedError('Native V2 episode canceled or expired')
        try:
            if method=='POST':
                identity=self.attest(timeout=min(8.,left))
                if not identity['ready']:raise IdentityError('Native official planner is not ready; dispatch refused')
            return self._request(path,body,timeout=min(timeout or 10.,self.deadline-time.monotonic()))
        except HTTPError as exc:
            if exc.code>=500:self.close('native_server_failed')
            raise
        except (IdentityError,OSError,ValueError):
            self.close('native_transport_failed');raise

    def _watch(self):
        while not self.done.wait(.05):
            try:canceled=self.cancel_check()
            except Exception:self.failed=True;self.close('cancel_predicate_failed');return
            if canceled:self.failed=True;self.close('operator_cancel');return
            if time.monotonic()>=self.deadline:self.close('episode_deadline');return

    def close(self,reason='normal_exit'):
        with self.mutex:
            if self.stop_receipt is not None:return self.stop_receipt
            self.done.set();started=time.monotonic();barrier=None;killed=False
            if self.process and self.process.poll()is None:
                try:barrier=self._request('/stop',{},timeout=2.,cleanup=True)
                except Exception:pass
                try:os.killpg(self.process.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:self.process.wait(timeout=5.)
                except subprocess.TimeoutExpired:
                    killed=True
                    try:os.killpg(self.process.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    self.process.wait(timeout=5.)
            if self.log:self.log.close()
            self.stop_receipt=dict(reason=reason,pid=self.process.pid if self.process else None,
                returncode=self.process.poll()if self.process else None,reaped=self.process is None or self.process.poll()is not None,
                sigkill_required=killed,stop_barrier=barrier,elapsed_seconds=time.monotonic()-started,
                mechanism='authenticated_v2_native_process_group',process_group_gone=self._group_gone(),
                planner_process_groups=self._planner_groups(),
                robot={'model':'OpenArm','execution_arm':self.arm})
            if self.evidence.is_dir():(self.evidence/'native_stop.json').write_text(json.dumps(self.stop_receipt,indent=2)+'\n')
            return self.stop_receipt

    def _planner_groups(self):
        # The GPU owner starts a separate session. Reaping the native executor
        # alone cannot establish that its planner groups have also exited.
        groups=[]
        for directory in sorted((self.evidence/'native').glob('v2-epoch-*')):
            row={'evidence':str(directory),'confirmed':False}
            try:
                spawn=directory/'service_spawn.json'
                identity=json.loads((spawn if spawn.is_file() else directory/'loaded_identity.json').read_text())
                stop=json.loads((directory/'service_stop.json').read_text())
                pid=identity['pid']
                if (type(pid)is not int or pid<=0 or identity.get('lock_sha256')!=self.lock_sha
                        or stop.get('pid')!=pid):raise ValueError('Planner process binding mismatch')
                try:os.killpg(pid,0);gone=False
                except ProcessLookupError:gone=True
                except PermissionError:gone=False
                row.update(pid=pid,reaped=stop.get('reaped')is True,process_group_gone=gone,
                    confirmed=stop.get('reaped')is True and gone)
            except (OSError,ValueError,KeyError,TypeError)as exc:
                row['error']=f'{type(exc).__name__}: {exc}'
            groups.append(row)
        return groups

    def _group_gone(self):
        if self.process is None:return True
        try:os.killpg(self.process.pid,0)
        except ProcessLookupError:return True
        except PermissionError:return False
        return False

    def __exit__(self,*args):self.close()
