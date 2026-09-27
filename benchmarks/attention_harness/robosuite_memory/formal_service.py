"""Own one Robosuite Service process for a single formal attempt.

The Service is single-threaded because MuJoCo's GL context is thread-affine.
An in-flight step cannot be cancelled over that HTTP connection.  A formal
attempt therefore owns a *dedicated* Service process group: at the episode
deadline the watchdog terminates the whole group and waits for its leader.
No shared/external Service URL is accepted by this path.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from robosuite_sim.client import RobosuiteSimClient


class DedicatedRobosuiteService:
    def __init__(self, *, source_root: Path, expected_revision: str,
                 log_path: Path, deadline: float,
                 cancel_event: threading.Event | None = None) -> None:
        self.source_root = source_root.resolve()
        self.expected_revision = expected_revision
        self.log_path = log_path
        self.deadline = deadline
        self.cancel_event = cancel_event
        self.base_url: str | None = None
        self.revision: str | None = None
        self.module_origin: str | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._log = None
        self._stop_event = threading.Event()
        self._watchdog: threading.Thread | None = None
        self._lock = threading.Lock()
        self.stop_receipt: dict[str, Any] | None = None

    def __enter__(self) -> "DedicatedRobosuiteService":
        if not (self.source_root / "robosuite_sim" / "server.py").is_file():
            raise ValueError("Robosuite Service source checkout is missing")
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.source_root, text=True,
        ).strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=self.source_root, text=True,
        ).strip()
        if dirty or revision != self.expected_revision:
            raise ValueError("Robosuite Service checkout differs from frozen revision")
        self.revision = revision
        environment = os.environ.copy()
        environment["MUJOCO_GL"] = environment.get("MUJOCO_GL", "egl")
        environment.pop("PYTHONPATH", None)
        origin = subprocess.check_output(
            [sys.executable, "-c", "import robosuite_sim; print(robosuite_sim.__file__)"],
            cwd=self.source_root, env=environment, text=True,
        ).strip()
        if not Path(origin).resolve().is_relative_to(self.source_root):
            raise RuntimeError("Robosuite Service import resolves outside the pinned checkout")
        self.module_origin = origin
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.base_url = f"http://127.0.0.1:{port}"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = self.log_path.open("wb")
        # Python's current directory, not an editable installation elsewhere,
        # supplies the independent Service package.
        self._process = subprocess.Popen(
            [sys.executable, "-m", "robosuite_sim", "--host", "127.0.0.1",
             "--port", str(port), "--enable-sim-gt"],
            cwd=self.source_root, env=environment, stdout=self._log,
            stderr=subprocess.STDOUT, start_new_session=True,
        )
        self._watchdog = threading.Thread(target=self._watch, daemon=True)
        self._watchdog.start()
        client = RobosuiteSimClient(self.base_url, timeout=0.5)
        try:
            while time.monotonic() < self.deadline:
                if self._process.poll() is not None:
                    raise RuntimeError("dedicated Robosuite Service exited during startup")
                try:
                    if client.health().get("status") == "ok":
                        return self
                except Exception:
                    time.sleep(0.1)
            raise TimeoutError("episode deadline reached during Service startup")
        except BaseException:
            self.stop("startup_failed")
            raise

    def _watch(self) -> None:
        while not self._stop_event.wait(0.05):
            if self.cancel_event is not None and self.cancel_event.is_set():
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
            process = self._process
            if process is None:
                raise RuntimeError("dedicated Service was never started")
            pgid = process.pid
            if process.poll() is None:
                try:
                    os.killpg(pgid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(pgid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=5.0)
            # A descendant can outlive its process-group leader. Signal the
            # group once more; there must be no live Service able to step.
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            group_gone = False
            for _ in range(100):
                try:
                    os.killpg(pgid, 0)
                except ProcessLookupError:
                    group_gone = True
                    break
                time.sleep(0.01)
            self.stop_receipt = {
                "mechanism": "dedicated_service_process_group_termination",
                "pid": process.pid,
                "process_group": pgid,
                "service_module_origin": self.module_origin,
                "reason": reason,
                "exit_code": process.returncode,
                "leader_reaped": process.poll() is not None,
                "process_group_gone": group_gone,
            }
            self._stop_event.set()
            if self._log is not None:
                self._log.close()
                self._log = None
            return self.stop_receipt

    def source_unchanged(self) -> bool:
        if self.revision is None:
            return False
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.source_root, text=True,
        ).strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=self.source_root, text=True,
        ).strip()
        return revision == self.revision and not dirty

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.stop("normal_cleanup")
        if self._watchdog is not None and self._watchdog is not threading.current_thread():
            self._watchdog.join(timeout=0.5)
