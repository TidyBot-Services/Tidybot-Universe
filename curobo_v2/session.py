"""Spawn and reap only the authenticated V2 service owned by this attempt."""
from __future__ import annotations

import os
import json
import math
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import threading
import time

from .client import V2Client
from .identity import IdentityError, load_lock, verify_files
from .evidence import preserve_runtime


class V2Service:
    def __init__(self, *, lock_path, robot_config, evidence, deadline, cancel_check=None,
                 test_response_fault='none',test_gpu_activity=False):
        if not Path(lock_path).is_absolute()or not Path(robot_config).is_absolute():
            raise IdentityError('V2 owner requires explicit absolute lock and robot configuration paths')
        if isinstance(deadline,bool)or not isinstance(deadline,(int,float))or not math.isfinite(deadline):
            raise IdentityError('V2 owner requires an explicit finite monotonic deadline')
        self.lock_path = str(Path(lock_path).resolve())
        self.lock, self.lock_sha = load_lock(self.lock_path)
        self.robot_config = str(Path(robot_config).resolve())
        self.evidence, self.deadline = Path(evidence), deadline
        if cancel_check is not None and not callable(cancel_check):
            raise IdentityError('Cancellation predicate must be callable')
        if test_response_fault not in {'none','http500_after_success','hold_after_success'}:
            raise IdentityError('Unknown explicit service response test policy')
        self.test_response_fault = test_response_fault
        if type(test_gpu_activity)is not bool:raise IdentityError('GPU activity test policy must be explicit bool')
        self.test_gpu_activity=test_gpu_activity
        self.cancel_check = cancel_check or (lambda: False)
        self.process = self.log = self.client = None
        self._lock = threading.Lock()
        self._done = threading.Event()
        self.stop_receipt = None

    def __enter__(self):
        verify_files(self.lock)
        if self.robot_config not in self.lock["robot_configs"]:
            raise IdentityError("No locked V2 robot configuration for this branch")
        if self.cancel_check() or time.monotonic() >= self.deadline:
            raise InterruptedError("V2 startup cancelled or deadline expired")
        self.evidence.mkdir(parents=True, exist_ok=True)
        preserve_runtime(self.lock_path,self.lock,self.evidence)
        self.log = (self.evidence / "service.log").open("wb")
        nonce, token = secrets.token_hex(16), secrets.token_hex(32)
        environment = {k: v for k, v in os.environ.items()
                       if not k.startswith(("PYTHON", "CUROBO_"))
                       and k not in {"LD_PRELOAD","LD_LIBRARY_PATH"}
                       and not any(x in k.upper() for x in ("TOKEN", "SECRET", "KEY", "PASSWORD"))}
        environment.update(CUROBO_V2_INSTANCE_TOKEN=token, CUROBO_V2_INSTANCE_NONCE=nonce)
        environment["CUROBO_V2_OWNER_PID"] = str(os.getpid())
        environment.update(XDG_CACHE_HOME=str(self.evidence/'cache'),MPLCONFIGDIR=str(self.evidence/'mpl-cache'),
                           CUDA_CACHE_PATH=str(self.evidence/'cuda-cache'))
        try:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen()
                url = "http://127.0.0.1:" + str(listener.getsockname()[1])
                self.process = subprocess.Popen([
                    self.lock["python"], "-B", "-I", str(Path(self.lock['project_root'])/'curobo_v2/worker.py'),
                    "--lock", self.lock_path, "--robot-config", self.robot_config,
                    "--test-response-fault",self.test_response_fault,
                    "--test-gpu-activity",'enabled'if self.test_gpu_activity else'disabled',
                    "--socket-fd", str(listener.fileno()), "--evidence", str(self.evidence)],
                    env=environment, cwd=self.lock["project_root"], stdout=self.log,
                    stderr=subprocess.STDOUT, start_new_session=True, pass_fds=(listener.fileno(),))
            (self.evidence/'service_spawn.json').write_text(json.dumps({
                'pid':self.process.pid,'lock_sha256':self.lock_sha,
                'robot_config':self.robot_config},indent=2)+'\n')
            while time.monotonic() < self.deadline:
                if self.cancel_check():
                    raise InterruptedError("operator cancelled V2 startup")
                if self.process.poll() is not None:
                    raise IdentityError("Locked V2 service exited; inspect " + str(self.evidence / "service.log"))
                try:
                    # Socket was reserved before spawn; no unrelated listener can satisfy readiness.
                    self.client = V2Client(lock_path=self.lock_path, url=url, nonce=nonce,
                        pid=self.process.pid, token=token, robot_config=self.robot_config,
                        deadline=self.deadline, evidence=self.evidence,cancel_check=self.cancel_check,
                        test_response_fault=self.test_response_fault,test_gpu_activity=self.test_gpu_activity)
                    threading.Thread(target=self._watch, daemon=True).start()
                    return self.client
                except (ConnectionError, OSError):
                    time.sleep(.05)
            raise TimeoutError("V2 startup deadline exceeded")
        except BaseException:
            self.close("startup_failed")
            raise

    def _watch(self):
        while not self._done.wait(.05):
            try:canceled=self.cancel_check()
            except Exception:
                self.close('cancel_predicate_failed')
                return
            if canceled:
                self.close("operator_cancel")
                return
            if time.monotonic() >= self.deadline:
                self.close("deadline")
                return

    def close(self, reason="normal_exit"):
        with self._lock:
            if self.stop_receipt is not None:
                return self.stop_receipt
            self._done.set()
            started = time.monotonic()
            if self.client:
                self.client.failed = True
            killed = False
            if self.process and self.process.poll() is None:
                # A stopped child cannot handle SIGTERM; never resume a timed
                # out request merely to reap it. Terminate that owned group now.
                try:stopped=Path('/proc',str(self.process.pid),'stat').read_text().split(') ',1)[1][0]in {'T','t'}
                except (OSError,IndexError):stopped=False
                try:
                    os.killpg(self.process.pid, signal.SIGKILL if stopped else signal.SIGTERM)
                    killed=stopped
                except ProcessLookupError:
                    pass
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    killed = True
                    try:
                        os.killpg(self.process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    self.process.wait(timeout=3)
            if self.log:
                self.log.close()
            self.stop_receipt = {"reason": reason, "pid": self.process.pid if self.process else None,
                "returncode": self.process.poll() if self.process else None,
                "reaped": self.process is None or self.process.poll() is not None,
                "sigkill_required": killed, "elapsed_seconds": time.monotonic() - started,
                "process_group_gone": self._group_gone()}
            if self.evidence.is_dir():
                (self.evidence / "service_stop.json").write_text(json.dumps(self.stop_receipt, indent=2) + "\n")
            return self.stop_receipt

    def _group_gone(self):
        if self.process is None:return True
        try:os.killpg(self.process.pid,0)
        except ProcessLookupError:return True
        except PermissionError:return False
        return False

    def __exit__(self, *args):
        self.close()
