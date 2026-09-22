"""Lifecycle manager for a local robosuite_sim subprocess."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import IO

from robosuite_sim.client import RobosuiteSimClient


class ManagedRobosuiteService:
    def __init__(self, *, log_path: Path, startup_timeout: float = 30.0) -> None:
        self.log_path = log_path
        self.startup_timeout = startup_timeout
        self.base_url: str | None = None
        self._process: subprocess.Popen[str] | None = None
        self._log: IO[str] | None = None

    def __enter__(self) -> str:
        port = _available_port()
        self.base_url = f"http://127.0.0.1:{port}"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = self.log_path.open("w", encoding="utf-8")
        environment = os.environ.copy()
        environment.setdefault("MUJOCO_GL", "egl")
        self._process = subprocess.Popen(
            [sys.executable, "-m", "robosuite_sim", "--host", "127.0.0.1", "--port", str(port)],
            stdout=self._log,
            stderr=subprocess.STDOUT,
            text=True,
            env=environment,
        )
        client = RobosuiteSimClient(self.base_url, timeout=1.0)
        deadline = time.monotonic() + self.startup_timeout
        try:
            while time.monotonic() < deadline:
                if self._process.poll() is not None:
                    raise RuntimeError(
                        f"robosuite_sim exited during startup with code {self._process.returncode}; "
                        f"see {self.log_path}"
                    )
                try:
                    if client.health().get("status") == "ok":
                        return self.base_url
                except Exception:
                    time.sleep(0.1)
            raise TimeoutError(f"robosuite_sim did not become ready; see {self.log_path}")
        except Exception:
            self._stop()
            raise

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self._stop()

    def _stop(self) -> None:
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
        if self._log is not None:
            self._log.close()
        self._process = None
        self._log = None


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
