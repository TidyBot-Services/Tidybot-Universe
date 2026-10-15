"""Own and reap one independent OpenArm kitchen process group."""
from pathlib import Path
import json
import hashlib
import os
import signal
import socket
import subprocess
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent


class OwnedOpenArmService:
    def __init__(self, out, *, python, task="counter_to_sink", seed=101, arm="left",
                 deadline=None, cancel_event=None, fault_after_steps=None, parameters_path=None):
        self.out = Path(out)
        self.python = Path(python)
        self.task = task; self.seed = seed; self.arm = arm
        self.deadline = deadline or time.monotonic() + 180
        self.cancel_event = cancel_event
        self.fault_after_steps = fault_after_steps
        from .kitchen import PARAMETERS, load_parameters
        from benchmarks.attention_harness.robocasa_native.robot_config import RobotSelection
        self.selection = RobotSelection("OpenArm", arm)
        self.parameters_path = Path(parameters_path or PARAMETERS).resolve()
        _, self.parameters_sha256 = load_parameters(self.parameters_path)
        self.child = None; self.stop_receipt = None; self.log = None
        self.done = threading.Event(); self.lock = threading.Lock()

    def __enter__(self):
        from curobo_v2.deployment import retired_entry
        retired_entry('OpenArm legacy fixed service owner')
        self.out.mkdir(parents=True, exist_ok=False)
        (self.out / "robot_parameters.json").write_bytes(self.parameters_path.read_bytes())
        (self.out / "robot_parameters.sha256").write_text(self.parameters_sha256 + "\n")
        sources = sorted((ROOT / "openarm_robocasa").glob("*.py"))
        sources += [ROOT / "openarm_phase1/service.py", ROOT / "openarm_phase0_recheck/validate.py",
                    ROOT / "benchmarks/attention_harness/robocasa_native/openarm_backend.py"]
        identity = {}
        for source in sources:
            relative = source.relative_to(ROOT)
            snapshot = self.out / "source_snapshot" / relative
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_bytes(source.read_bytes())
            identity[str(relative)] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        (self.out / "source_identity.json").write_text(json.dumps(identity, indent=2) + "\n")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        self.command = [str(self.python), "-B", "-u", "-m", "openarm_robocasa.service",
                        "--port", str(port), "--evidence", str(self.out / "service"),
                        "--task", self.task, "--seed", str(self.seed), "--arm", self.arm,
                        "--parameters", str(self.parameters_path)]
        if self.fault_after_steps is not None:
            self.command.extend(["--fault-after-steps", str(self.fault_after_steps)])
        env = {**os.environ, "PYTHONPATH": str(ROOT),
               "VK_ICD_FILENAMES": "/usr/share/vulkan/icd.d/lvp_icd.x86_64.json",
               "MPLCONFIGDIR": str(self.out / "mpl-cache"),
               "XDG_CACHE_HOME": str(self.out / "cache")}
        self.log = (self.out / "service_stdout.log").open("wb")
        try:
            self.child = subprocess.Popen(self.command, cwd=ROOT, env=env, stdout=self.log,
                                          stderr=subprocess.STDOUT, start_new_session=True)
            (self.out / "command.json").write_text(json.dumps(self.command, indent=2))
            self.watchdog = threading.Thread(target=self._watch, daemon=True)
            self.watchdog.start()
            while time.monotonic() < self.deadline:
                if self.cancel_event is not None and self.cancel_event.is_set():
                    raise InterruptedError("OpenArm cancelled during startup")
                if self.child.poll() is not None:
                    raise RuntimeError("OpenArm kitchen service exited during startup")
                try:
                    with urllib.request.urlopen(self.url + "/health", timeout=2.) as response:
                        health = json.load(response)
                    if health.get("ready"):
                        expected = {"model": "OpenArm", "execution_arm": self.arm}
                        if (health.get("robot") != expected or health.get("task_id") != self.task
                                or health.get("parameters_sha256") != self.parameters_sha256):
                            raise RuntimeError("OpenArm service identity disagrees with launch")
                        self.health = health
                        (self.out / "health.json").write_text(json.dumps(health, indent=2) + "\n")
                        return self
                except (OSError, ValueError):
                    pass
                self.done.wait(.05)
            raise TimeoutError("OpenArm service startup reached episode deadline")
        except BaseException:
            self.close("startup_failure")
            raise

    def _watch(self):
        while not self.done.wait(.05):
            if self.cancel_event is not None and self.cancel_event.is_set():
                self.close("operator_cancel")
                return
            if time.monotonic() >= self.deadline:
                self.close("episode_deadline")
                return

    def close(self, reason="normal_exit"):
        with self.lock:
            if self.stop_receipt is not None:
                return self.stop_receipt
            self.done.set()
            if self.child is None:
                return None
            started = time.monotonic(); term = False; killed = False; barrier = None
            if self.child.poll() is None:
                try:
                    request = urllib.request.Request(self.url + "/stop", method="POST", data=b"{}",
                                                     headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(request, timeout=2.) as response:
                        barrier = json.load(response)
                except (OSError, ValueError):
                    pass
                term = True; os.killpg(self.child.pid, signal.SIGTERM)
                try:
                    self.child.wait(timeout=5.)
                except subprocess.TimeoutExpired:
                    killed = True; os.killpg(self.child.pid, signal.SIGKILL)
                    self.child.wait(timeout=5.)
            else:
                self.child.wait()
            try:
                os.killpg(self.child.pid, 0); remaining = True
            except ProcessLookupError:
                remaining = False
            self.stop_receipt = {"reason": reason, "pid": self.child.pid,
                "exit_code": self.child.returncode, "term_sent": term, "kill_sent": killed,
                "process_group_remaining": remaining, "seconds": time.monotonic() - started,
                "mechanism": "dedicated_openarm_kitchen_process_group", "robot": self.selection.artifact(),
                "stop_barrier": barrier,
                "services": {"openarm": {"pid": self.child.pid, "process_group": self.child.pid,
                    "leader_reaped": self.child.poll() is not None, "process_group_gone": not remaining,
                    "exit_code": self.child.returncode}}}
            (self.out / "reap.json").write_text(json.dumps(self.stop_receipt, indent=2))
            if self.log:
                self.log.close()
            return self.stop_receipt

    def __exit__(self, exc_type, exc_value, tb):
        self.close("exception" if exc_type else "normal_exit")
