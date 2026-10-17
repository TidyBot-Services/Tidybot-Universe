"""Owner-bound planning client; no default URL, retry, or alternative planner."""
from __future__ import annotations

import json
import math
from pathlib import Path
import secrets
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from .identity import IdentityError, load_lock, verify_files, verify_service, verify_client_imports


class PlanningRejected(RuntimeError):
    """V2 refused the request. No trajectory may be dispatched."""


class V2Client:
    def __init__(self, *, lock_path, url: str, nonce: str, pid: int, token: str,
                 robot_config: str, deadline: float, evidence: Path, cancel_check=None,
                 test_response_fault='none',test_gpu_activity=False):
        self.lock, self.lock_sha = load_lock(lock_path)
        verify_files(self.lock)
        verify_client_imports(self.lock)
        parsed = urlsplit(url)
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
                or parsed.path or parsed.query or parsed.username or parsed.password or parsed.fragment):
            raise IdentityError("V2 client requires an explicitly owned loopback service")
        if (not Path(robot_config).is_absolute() or not math.isfinite(deadline)
                or not token or not nonce or not isinstance(pid,int) or isinstance(pid,bool) or pid <= 0):
            raise IdentityError("V2 client requires absolute configuration, finite deadline and owner credentials")
        self.url, self.nonce, self.pid, self.token = url, nonce, pid, token
        self.robot_config = str(Path(robot_config).resolve())
        self.deadline, self.evidence = deadline, Path(evidence)
        if cancel_check is not None and not callable(cancel_check):
            raise IdentityError('Cancellation predicate must be callable')
        if test_response_fault not in {'none','http500_after_success','hold_after_success'}:
            raise IdentityError('Unknown explicit service response test policy')
        self.cancel_check = cancel_check or (lambda:False)
        self.test_response_fault = test_response_fault
        if type(test_gpu_activity)is not bool:raise IdentityError('GPU activity test policy must be explicit bool')
        self.test_gpu_activity=test_gpu_activity
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.failed = False
        self.worlds = {}
        self.identity = self.attest()

    def _check_live(self):
        if self.failed or self.cancel_check() or time.monotonic() >= self.deadline:
            self.failed = True
            raise PlanningRejected('V2 client stopped, canceled, or deadline expired; result invalidated')

    def _request(self, path: str, body=None):
        self._check_live()
        left = self.deadline - time.monotonic()
        request = Request(self.url + path, data=None if body is None else json.dumps(body, allow_nan=False).encode(),
                          headers={"Authorization": "Bearer " + self.token, "X-Curobo-Instance": self.nonce,
                                   "Content-Type": "application/json"})
        # Model compilation belongs to bounded startup. Planning retains the
        # original 8-second ceiling and never retries after a timeout.
        with urlopen(request, timeout=min(60. if path == "/warmup" else 8., left)) as response:
            if (response.headers.get("x-curobo-instance") != self.nonce
                    or response.headers.get("x-curobo-lock") != self.lock_sha):
                raise IdentityError("Planner response came from a different service instance")
            result = json.load(response)
            self._check_live()
            return result

    def attest(self) -> dict:
        challenge = secrets.token_hex(16)
        try:
            payload = self._request("/identity?" + urlencode({"challenge": challenge}))
            return verify_service(payload, lock=self.lock, lock_sha=self.lock_sha, nonce=self.nonce,
                                  pid=self.pid, token=self.token, challenge=challenge,
                                  robot_config=self.robot_config,test_response_fault=self.test_response_fault,
                                  test_gpu_activity=self.test_gpu_activity)
        except Exception:
            self.failed = True
            raise

    def call(self, path: str, body=None):
        if path not in {"/warmup", "/health", "/world/cuboids", "/world/envs", "/plan",
                        "/plan/joint", "/plan/ik", "/plan/ik_path", "/plan/cooperative", "/fk",
                        "/plan/validate_base_path", "/diagnostics/jacobian"}:
            raise IdentityError("Undeclared V2 endpoint: " + path)
        row = {"time_ns": time.time_ns(), "path": path, "request": body,
               "nonce": self.nonce, "lock_sha256": self.lock_sha}
        try:
            self._check_live()
            env_id = body.get("env_id") or "default" if isinstance(body, dict) else "default"
            planning = path.startswith("/plan") and path != "/plan/validate_base_path"
            if planning and env_id not in self.worlds:
                raise PlanningRejected("V2 planning requires this client's explicit complete world upload")
            self.attest()
            result = self._request(path, body)
            row["response"] = result
            if planning and result.get("status") != "success":
                raise PlanningRejected("cuRoboV2 planning rejected: " + str(result.get("reason", result.get("status"))))
            if path == "/world/cuboids":
                if result.get("cuboid_count") != len(body["cuboids"]) or result.get("env_id") != env_id:
                    raise IdentityError("V2 world receipt omitted geometry or changed environment")
                self.worlds[env_id] = (result["world_revision"], result["world_sha256"])
            if planning and (result.get("world_revision"), result.get("world_sha256")) != self.worlds[env_id]:
                raise IdentityError("V2 trajectory belongs to a different or stale collision world")
            if planning:
                import numpy as np
                key = "qpos" if path == "/plan/ik" else "trajectory"
                values = np.asarray(result.get(key),dtype=float)
                expected = len(body.get("joint_names") or self.identity.get("joint_names", result.get("joint_names",[])))
                if (not np.isfinite(values).all() or values.size == 0
                        or values.ndim != (1 if key == "qpos" else 2)
                        or values.shape[-1] != expected
                        or not result.get("validation",{}).get("joint_limits")
                        or not result.get("validation",{}).get("collision_free")):
                    raise PlanningRejected("Invalid or unvalidated V2 result; dispatch forbidden")
                if key=='trajectory':
                    from .sweep import verify_certificate
                    verify_certificate(result)
            self._check_live()
            return result
        except Exception as exc:
            self.failed = True
            row["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            with (self.evidence / "v2_client.jsonl").open("a") as stream:
                stream.write(json.dumps(row, allow_nan=False) + "\n")
