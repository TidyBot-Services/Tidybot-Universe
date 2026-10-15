"""Dedicated owner process; no policy can access owner credentials or evaluator."""

import json, os, secrets, signal, subprocess, time, threading
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


class ServiceError(RuntimeError):
    pass


class OutcomeUnknown(ServiceError):
    pass


class Client:
    def __init__(self, url, token, deadline, cancel_check=None):
        self.url = url
        self.token = token
        self.deadline = deadline
        self.cancel_check = cancel_check

    def call(self, path, data=None):
        if self.cancel_check and self.cancel_check():
            raise InterruptedError("operator cancel")
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("attempt deadline")
        r = Request(
            self.url + path,
            data=None if data is None else json.dumps(data, allow_nan=False).encode(),
            headers={
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(r, timeout=min(left, 30)) as response:
                return json.load(response)
        except HTTPError as ex:
            raw = json.loads(ex.read())
            raise ServiceError(raw.get("error", "service rejected request")) from ex
        except (TimeoutError, URLError, OSError) as ex:
            if path == "/step":
                raise OutcomeUnknown(
                    "action response unknown; no retry permitted"
                ) from ex
            raise


class DedicatedService:
    def __init__(
        self,
        *,
        source_root,
        python,
        robocasa_root,
        robosuite_root,
        asset_manifest,
        episode_dir,
        deadline,
        cancel_check=None
    ):
        self.root = Path(source_root).resolve()
        self.python = Path(python).absolute()
        self.rc = Path(robocasa_root).resolve()
        self.rs = Path(robosuite_root).resolve()
        self.manifest = Path(asset_manifest).resolve()
        self.dir = Path(episode_dir).resolve()
        self.deadline = deadline
        self.cancel_check = cancel_check
        self.process = None
        self.owner = None
        self.public = None
        self.stop_receipt = None
        self.stop_lock = threading.Lock()
        self.process_observer = None

    def start(self):
        from curobo_v2.deployment import retired_entry
        retired_entry('RoboCasa365 PandaOmron/OpenArm raw-action service')
        self.dir.mkdir(parents=True, exist_ok=True)
        self.tokens = {"owner": secrets.token_hex(32), "public": secrets.token_hex(32)}
        token_file = self.dir / ".owner-tokens.json"
        token_file.write_text(json.dumps(self.tokens))
        token_file.chmod(0o600)
        self.token_file = token_file
        self.log = (self.dir / "service.log").open("wb")
        ready = self.dir / "service_ready.json"
        env = {
            k: v
            for k, v in os.environ.items()
            if not any(
                word in k.upper() for word in ("TOKEN", "SECRET", "KEY", "PASSWORD")
            )
        }
        env.update(
            MUJOCO_GL="egl", PYTHONPATH=str(self.root) + os.pathsep + str(Path(__file__).resolve().parents[3]),
            PYTHONDONTWRITEBYTECODE="1"
        )
        command = [
            str(self.python),
            "-m",
            "robocasa365_sim.server",
            "--robocasa-root",
            str(self.rc),
            "--robosuite-root",
            str(self.rs),
            "--asset-manifest",
            str(self.manifest),
            "--evidence-dir",
            str(self.dir),
            "--token-file",
            str(token_file),
            "--ready-file",
            str(ready),
        ]
        self.process = subprocess.Popen(
            command, stdout=self.log, stderr=self.log, env=env, start_new_session=True
        )
        if self.process_observer is not None:
            self.process_observer(self.process.pid)
        while not ready.exists():
            if self.cancel_check and self.cancel_check():
                raise InterruptedError("operator cancel during startup")
            if self.process.poll() is not None:
                raise ServiceError(
                    "Service exited during verified startup: "
                    + str(self.process.returncode)
                )
            if time.monotonic() >= self.deadline:
                raise TimeoutError("Service startup deadline")
            time.sleep(0.05)
        info = json.loads(ready.read_bytes())
        if info["pid"] != self.process.pid:
            raise ServiceError("Service ready PID mismatch")
        url = "http://127.0.0.1:" + str(info["port"])
        self.owner = Client(url, self.tokens["owner"], self.deadline, self.cancel_check)
        self.public = Client(
            url, self.tokens["public"], self.deadline, self.cancel_check
        )
        return info

    def stop(self, reason="normal_cleanup"):
        with self.stop_lock:
            if self.stop_receipt is not None:
                return self.stop_receipt
            return self._stop(reason)

    def _stop(self, reason):
        stop_http = None
        if self.process is not None:
            if self.owner and self.process.poll() is None:
                try:
                    # Cleanup has a separate bounded window, including after timeout/cancel.
                    cleanup = Client(
                        self.owner.url, self.tokens["owner"], time.monotonic() + 2
                    )
                    stop_http = cleanup.call("/shutdown", {})
                    self.process.wait(timeout=2)
                except Exception:
                    pass
            if self.process.poll() is None:
                os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=3)
            try:
                os.killpg(self.process.pid, 0)
                gone = False
            except ProcessLookupError:
                gone = True
            if not gone:
                os.killpg(self.process.pid, signal.SIGKILL)
                for _ in range(20):
                    try:
                        os.killpg(self.process.pid, 0)
                    except ProcessLookupError:
                        gone = True
                        break
                    time.sleep(0.05)
            self.stop_receipt = {
                "reason": reason,
                "pid": self.process.pid,
                "leader_reaped": self.process.poll() is not None,
                "process_group_gone": gone,
                "exit_code": self.process.returncode,
                "http_stop": stop_http,
            }
        else:
            self.stop_receipt = {
                "reason": reason,
                "leader_reaped": True,
                "process_group_gone": True,
                "not_started": True,
            }
        if hasattr(self, "token_file"):
            self.token_file.unlink(missing_ok=True)
        if hasattr(self, "log"):
            self.log.close()
        return self.stop_receipt
