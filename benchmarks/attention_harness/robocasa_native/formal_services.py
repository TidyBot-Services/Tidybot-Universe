"""Own isolated RoboCasa simulator and Agent Server for one formal attempt.

The two services share a port offset and are killed as process groups on
deadline or cancellation. No caller-supplied service URL is accepted.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from .client import RobocasaSimClient
from .tasks import get_robocasa_task


def source_identity(root: Path) -> dict[str, Any]:
    root = root.resolve()
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    paths = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root,
    ).split(b"\0")
    digest = hashlib.sha256()
    for raw in sorted(path for path in paths if path):
        relative = os.fsdecode(raw)
        path = root / relative
        if not path.is_file():
            continue
        digest.update(raw + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    status = subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True)
    dirty = bool(status.strip())
    return {"revision": revision, "tree_sha256": digest.hexdigest(), "dirty": dirty}


def simulator_runtime_identity(python: Path, task_source_root: Path) -> dict[str, Any]:
    """Identify the interpreter and the RoboCasa code it actually imports."""
    script = """
import hashlib, importlib.metadata, json, pathlib, sys
import mani_skill, robocasa_tasks
root = pathlib.Path(mani_skill.__file__).resolve().parent
files = (
    'envs/tasks/mobile_manipulation/robocasa/kitchen.py',
    'utils/scene_builder/robocasa/scene_builder.py',
    'utils/scene_builder/robocasa/fixtures/counter.py',
    'utils/scene_builder/robocasa/utils/placement_samplers.py',
    'utils/scene_builder/robocasa/utils/object_utils.py',
)
tree = hashlib.sha256()
for path in sorted(root.rglob('*.py')):
    relative = path.relative_to(root).as_posix().encode()
    tree.update(relative + b'\\0' + hashlib.sha256(path.read_bytes()).digest())
print(json.dumps({
    'python_version': sys.version.split()[0],
    'mani_skill_version': importlib.metadata.version('mani_skill'),
    'mani_skill_root': str(root),
    'mani_skill_python_tree_sha256': tree.hexdigest(),
    'mani_skill_files': {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in files
    },
    'task_module_path': str(pathlib.Path(robocasa_tasks.__file__).resolve()),
}, sort_keys=True))
"""
    output = subprocess.check_output([str(python.resolve()), "-c", script], text=True)
    identity = json.loads(output)
    identity["python_sha256"] = hashlib.sha256(python.resolve().read_bytes()).hexdigest()
    expected_module = (task_source_root.resolve() / "__init__.py").resolve()
    if identity["task_module_path"] != str(expected_module):
        raise ValueError("formal simulator imports a different RoboCasa task checkout")
    return identity


class DedicatedRobocasaServices:
    def __init__(self, *, task_id: str, sim_source_root: Path, agent_source_root: Path,
                 task_source_root: Path,
                 sim_python: Path, agent_python: Path, expected_sim: dict[str, str],
                 expected_agent: dict[str, str], expected_task: dict[str, str],
                 expected_runtime: dict[str, Any], port_offset: int, log_dir: Path,
                 deadline: float, cancel_event: threading.Event | None = None) -> None:
        if not 100 <= port_offset <= 15000:
            raise ValueError("formal RoboCasa port offset must be isolated")
        self.task_id = task_id
        self.sim_source_root = sim_source_root.resolve()
        self.agent_source_root = agent_source_root.resolve()
        self.task_source_root = task_source_root.resolve()
        self.sim_python = sim_python.resolve()
        self.agent_python = agent_python.resolve()
        self.expected_sim = expected_sim
        self.expected_agent = expected_agent
        self.expected_task = expected_task
        self.expected_runtime = expected_runtime
        self.port_offset = port_offset
        self.log_dir = log_dir
        self.deadline = deadline
        self.cancel_event = cancel_event
        self.sim_url = f"http://127.0.0.1:{5500 + port_offset}"
        self.agent_url = f"http://127.0.0.1:{8080 + port_offset}"
        self.processes: dict[str, subprocess.Popen[bytes]] = {}
        self.logs: list[Any] = []
        self.stop_receipt: dict[str, Any] | None = None
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._watchdog: threading.Thread | None = None

    def _check_source(self, root: Path, expected: dict[str, str]) -> dict[str, Any]:
        actual = source_identity(root)
        if (actual["revision"] != expected["revision"]
                or actual["tree_sha256"] != expected["tree_sha256"]):
            raise ValueError(f"formal Service source differs from approved identity: {root}")
        return actual

    def source_unchanged(self) -> bool:
        for root, expected in ((self.sim_source_root, self.expected_sim),
                               (self.agent_source_root, self.expected_agent),
                               (self.task_source_root, self.expected_task)):
            actual = source_identity(root)
            if (actual["revision"] != expected["revision"]
                    or actual["tree_sha256"] != expected["tree_sha256"]):
                return False
        if simulator_runtime_identity(self.sim_python, self.task_source_root) != self.expected_runtime:
            return False
        return True

    def _spawn(self, name: str, command: list[str], *, root: Path, env: dict[str, str]) -> None:
        output = (self.log_dir / f"{name}.log").open("wb")
        self.logs.append(output)
        self.processes[name] = subprocess.Popen(
            command, cwd=root, env=env, stdout=output, stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    def _wait(self, name: str, probe) -> None:
        while time.monotonic() < self.deadline:
            if self.cancel_event is not None and self.cancel_event.is_set():
                raise InterruptedError("operator cancelled during Service startup")
            if self.processes[name].poll() is not None:
                raise RuntimeError(f"dedicated {name} Service exited during startup")
            try:
                if probe():
                    return
            except Exception:
                pass
            time.sleep(0.1)
        raise TimeoutError(f"episode deadline reached during {name} startup")

    def __enter__(self) -> "DedicatedRobocasaServices":
        self._check_source(self.sim_source_root, self.expected_sim)
        self._check_source(self.agent_source_root, self.expected_agent)
        self._check_source(self.task_source_root, self.expected_task)
        if simulator_runtime_identity(self.sim_python, self.task_source_root) != self.expected_runtime:
            raise ValueError("formal simulator runtime differs from approved identity")
        if not (self.sim_source_root / "maniskill_server" / "__main__.py").is_file():
            raise ValueError("RoboCasa simulator source checkout is missing")
        if not (self.agent_source_root / "server.py").is_file():
            raise ValueError("Agent Server source checkout is missing")
        if not self.sim_python.is_file() or not self.agent_python.is_file():
            raise ValueError("formal Service Python runtime is missing")
        # Reject an unrelated server on the selected endpoints before launch;
        # a healthy response from somebody else's process is not attestation.
        for port in (5500 + self.port_offset, 8080 + self.port_offset,
                     5555 + self.port_offset, 5570 + self.port_offset,
                     5580 + self.port_offset,
                     50000 + self.port_offset):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    raise RuntimeError(f"formal RoboCasa port {port} is already occupied")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        sim_env = os.environ.copy()
        for key in ("PARCC_API_KEY", "LITELLM_KEY"):
            sim_env.pop(key, None)
        sim_env["PYTHONPATH"] = str(self.sim_source_root)
        sim_env.setdefault("MUJOCO_GL", "egl")
        agent_env = os.environ.copy()
        for key in ("PARCC_API_KEY", "LITELLM_KEY"):
            agent_env.pop(key, None)
        agent_env["PYTHONPATH"] = str(self.agent_source_root)
        try:
            self._spawn("simulator", [
                str(self.sim_python), "-m", "maniskill_server", "--task",
                get_robocasa_task(self.task_id).environment_id,
                "--port-offset", str(self.port_offset),
                "--no-mocap-bridge",
            ], root=self.sim_source_root, env=sim_env)
            client = RobocasaSimClient(self.task_id, base_url=self.sim_url)
            self._wait("simulator", lambda: bool(client.assert_task()))
            self._spawn("agent", [
                str(self.agent_python), "server.py", "--host", "127.0.0.1",
                "--port-offset", str(self.port_offset), "--no-service-manager",
                "--no-dashboard", "--no-reset-on-release",
            ], root=self.agent_source_root, env=agent_env)
            def agent_ready() -> bool:
                with urllib.request.urlopen(self.agent_url + "/health", timeout=0.5) as response:
                    return response.status == 200
            self._wait("agent", agent_ready)
            self._watchdog = threading.Thread(target=self._watch, daemon=True)
            self._watchdog.start()
            return self
        except BaseException as error:
            self.stop("operator_cancel" if isinstance(error, InterruptedError)
                      and self.cancel_event is not None and self.cancel_event.is_set()
                      else "startup_failed")
            raise

    def _watch(self) -> None:
        while not self._stopped.wait(0.05):
            if self.cancel_event is not None and self.cancel_event.is_set():
                # Give the Agent Server's per-job cancellation protocol a
                # short, bounded chance to publish its terminal receipt.
                # __exit__ stops both services immediately when it succeeds.
                self._stopped.wait(min(2.0, max(0.0, self.deadline - time.monotonic())))
                self.stop("operator_cancel")
                return
            if time.monotonic() >= self.deadline:
                self.stop("episode_deadline")
                return

    def stop(self, reason: str) -> dict[str, Any]:
        with self._lock:
            if self.stop_receipt is not None:
                return self.stop_receipt
            if reason == "normal_cleanup":
                if self.cancel_event is not None and self.cancel_event.is_set():
                    reason = "operator_cancel"
                elif time.monotonic() >= self.deadline:
                    reason = "episode_deadline"
            receipts = {}
            for name, process in reversed(list(self.processes.items())):
                pgid = process.pid
                try:
                    os.killpg(pgid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                gone = False
                for _ in range(100):
                    try:
                        os.killpg(pgid, 0)
                    except ProcessLookupError:
                        gone = True
                        break
                    time.sleep(0.01)
                receipts[name] = {
                    "pid": process.pid, "process_group": pgid,
                    "leader_reaped": process.poll() is not None,
                    "process_group_gone": gone, "exit_code": process.returncode,
                }
            self.stop_receipt = {
                "mechanism": "dedicated_simulator_and_agent_process_groups",
                "reason": reason, "services": receipts,
            }
            self._stopped.set()
            for output in self.logs:
                output.close()
            return self.stop_receipt

    def __exit__(self, _type, _value, _traceback) -> None:
        self.stop("normal_cleanup")
        if self._watchdog is not None and self._watchdog is not threading.current_thread():
            self._watchdog.join(timeout=0.5)
