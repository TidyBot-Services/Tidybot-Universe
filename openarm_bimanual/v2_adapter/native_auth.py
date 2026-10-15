"""Owner authentication for the existing native executor's HTTP protocol."""
from __future__ import annotations
import ctypes
import hmac
import os
from pathlib import Path
import socket
import sys
from urllib.parse import parse_qs, urlsplit
from .identity import IdentityError, sign, verify_files, verify_runtime


def require_native_owner():
    owner=int(os.environ.pop('CUROBO_V2_NATIVE_OWNER_PID','0'))
    token=os.environ.pop('CUROBO_V2_NATIVE_TOKEN','')
    nonce=os.environ.pop('CUROBO_V2_NATIVE_NONCE','')
    if owner<=1 or os.getppid()!=owner or len(token)!=64 or len(nonce)!=32:
        raise IdentityError('Native executor requires its authenticated live parent owner')
    libc=ctypes.CDLL(None,use_errno=True)
    if not sys.platform.startswith('linux')or libc.prctl(1,9,0,0,0)!=0 or os.getppid()!=owner:
        raise IdentityError('Native executor cannot bind its lifetime to the parent owner')
    if (not sys.flags.safe_path or not sys.flags.no_user_site or sys.flags.hash_randomization!=0
            or os.environ.get('PYTHONHASHSEED')!='0'):
        raise IdentityError('Native executor requires locked -P -s startup and PYTHONHASHSEED=0')
    return token,nonce


def inherited_listener(fd):
    listener=socket.socket(fileno=fd)
    if (listener.family!=socket.AF_INET or listener.type!=socket.SOCK_STREAM
            or listener.getsockname()[0]!='127.0.0.1'
            or not listener.getsockopt(socket.SOL_SOCKET,socket.SO_ACCEPTCONN)):
        listener.close()
        raise IdentityError('Native executor requires its parent-reserved loopback listener')
    return listener


def verify_native_payload(payload, *, expected, token, challenge):
    if not isinstance(payload,dict)or not isinstance(payload.get('identity'),dict):
        raise IdentityError('Malformed native executor identity')
    identity=payload['identity'];signature=payload.get('signature')
    if (any(identity.get(k)!=v for k,v in expected.items())or not isinstance(signature,str)
            or not hmac.compare_digest(signature,sign(token,identity,challenge))):
        raise IdentityError('Native executor loaded-source/runtime/service identity mismatch')
    if type(identity.get('ready'))is not bool:
        raise IdentityError('Native executor readiness is not explicit')
    if identity['ready']:
        planner=identity.get('planner_identity',{})
        if (not isinstance(planner,dict)or planner.get('lock_sha256')!=expected['lock_sha256']
                or planner.get('kernel_backend')!='cuda_core'
                or planner.get('robot_config')!=expected['robot_config']
                or planner.get('runtime',{}).get('generation')!=2):
            raise IdentityError('Native executor does not own the declared cuRoboV2 planner')
    return identity


def authenticated_handler(foundation, *, lock, lock_sha, token, nonce, selection):
    class Handler(foundation.Handler):
        failed=None

        def end_headers(self):
            self.send_header('X-Curobo-Native',nonce)
            self.send_header('X-Curobo-Lock',lock_sha)
            super().end_headers()

        def dispatch(self):
            if (not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token)
                    or not hmac.compare_digest(self.headers.get('X-Curobo-Native',''),nonce)):
                self.respond(403,'application/json',{'error':{'code':'native_auth_required'}});return
            parsed=urlsplit(self.path);rt=self.server.runtime
            try:
                # Stop/Cancel remain available after an identity failure so the
                # original physics owner can perform its normal hold barrier.
                recovery_state = parsed.path == '/state' and self.command == 'GET'
                if parsed.path not in {'/stop','/cancel'} and not recovery_state:
                    if Handler.failed:raise IdentityError(Handler.failed)
                    # Initialization cannot execute an action, and each startup
                    # identity query still performs a complete synchronous check.
                    rt.integrity.check(require_fresh=rt.ready)
                    # Startup/attestation and every control request retain the
                    # complete file/runtime checks. Read-only telemetry uses the
                    # same authenticated owner plus a continuously checked source
                    # identity, so polling cannot starve the physical executor.
                    if self.command!='GET'or parsed.path in {'/identity','/health'}:
                        rt.integrity.refresh();runtime=verify_runtime(lock,full=False,owner=True)
                if self.command=='GET'and parsed.path=='/identity':
                    query=parse_qs(parsed.query,strict_parsing=True)
                    if set(query)!={'challenge'}or len(query['challenge'])!=1 or not 16<=len(query['challenge'][0])<=128:
                        raise IdentityError('Invalid native identity challenge')
                    health=rt.health()
                    identity=dict(pid=os.getpid(),nonce=nonce,lock_sha256=lock_sha,
                        python=runtime['python'],prefix=runtime['prefix'],project_root=lock['project_root'],
                        **selection,ready=bool(health['ready']and health['planner_ready']),
                        error=health['error'],planner_identity=health['planner_identity'])
                    if health.get('native_adapter')=='openarm-bimanual-v2/1':
                        identity.update({k:health[k]for k in ['native_adapter','grasp_schema','model_sha256','planner_joint_names','planner_joint_map','planner_tools']})
                    self.respond(200,'application/json',dict(identity=identity,
                        signature=sign(token,identity,query['challenge'][0])));return
                return super().dispatch()
            except (IdentityError,ValueError)as exc:
                Handler.failed=str(exc);rt.integrity.fail(str(exc));rt.planning_interrupt.set()
                self.respond(503,'application/json',{'error':{'code':'native_identity_failed','message':str(exc)}})
            except (BrokenPipeError,ConnectionResetError):
                # Readiness probes may disconnect while the native owner starts.
                # They cannot issue a control request or an execution capability.
                pass
    return Handler
