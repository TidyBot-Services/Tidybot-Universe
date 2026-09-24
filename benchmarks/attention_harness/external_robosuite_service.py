"""Start the separately installed Robosuite service, never the v1 source snapshot."""

from __future__ import annotations

import importlib.metadata
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from robosuite_sim.client import RobosuiteSimClient


class ManagedExternalRobosuiteService:
    def __init__(self, *, log_path: Path, startup_timeout: float = 30.0) -> None:
        self.log_path = log_path
        self.startup_timeout = startup_timeout
        self._process: subprocess.Popen[str] | None = None
        self._log = None

    def __enter__(self) -> str:
        # -I excludes the Universe checkout from the child interpreter's
        # import path; the installed, pinned service wheel is authoritative.
        importlib.metadata.version("tidybot-robosuite-sim")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        url = f"http://127.0.0.1:{port}"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = self.log_path.open("w", encoding="utf-8")
        environment = os.environ.copy()
        environment.setdefault("MUJOCO_GL", "egl")
        self._process = subprocess.Popen(
            [sys.executable, "-I", "-m", "robosuite_sim", "--host", "127.0.0.1", "--port", str(port)],
            stdout=self._log,
            stderr=subprocess.STDOUT,
            text=True,
            env=environment,
        )
        client = RobosuiteSimClient(url, timeout=1.0)
        deadline = time.monotonic() + self.startup_timeout
        try:
            while time.monotonic() < deadline:
                if self._process.poll() is not None:
                    raise RuntimeError(
                        f"external robosuite_sim exited with {self._process.returncode}; see {self.log_path}"
                    )
                try:
                    if client.health().get("status") == "ok":
                        return url
                except Exception:
                    time.sleep(0.1)
            raise TimeoutError(f"external robosuite_sim did not become ready; see {self.log_path}")
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
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
