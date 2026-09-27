"""Security and stop-boundary regressions for the Robosuite formal path."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request, urlopen

import pytest

from benchmarks.attention_harness.formal_runner_boundary import FormalRunRequest
from benchmarks.attention_harness.robosuite_memory.formal_sandbox import (
    execute_formal_policy, probe_sandbox,
)
from benchmarks.attention_harness.robosuite_memory.formal_service import (
    DedicatedRobosuiteService,
)


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap unavailable")
def test_formal_worker_has_no_host_files_credentials_or_network(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMAL_TEST_SECRET_TOKEN", "do-not-expose")
    probe = probe_sandbox()
    assert probe["host_home_visible"] is False
    assert probe["host_root_visible"] is False
    assert probe["network_reachable"] is False
    assert probe["credential_environment_absent"] is True

    class Sensors:
        def find_objects(self):
            return [{"name": "cube", "position": [0, 0, 0]}]

    class Gripper:
        def open(self, *, settle_steps):
            assert settle_steps == 1

    sdk = SimpleNamespace(sensors=Sensors(), gripper=Gripper())
    outcome = execute_formal_policy(
        code="from robot_sdk import sensors, gripper\n"
             "objects = sensors.find_objects()\n"
             "gripper.open(settle_steps=1)\n",
        sdk=sdk, context={}, deadline=time.monotonic() + 3,
        stderr_path=tmp_path / "worker.stderr",
    )
    assert outcome.status == "completed"
    assert outcome.call_count == 2
    assert outcome.process_exit_code == 0


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap unavailable")
def test_formal_worker_obeys_operator_cancel(tmp_path):
    cancel = threading.Event()
    timer = threading.Timer(0.1, cancel.set)
    timer.start()
    try:
        outcome = execute_formal_policy(
            code="from robot_sdk import sensors\nwhile True: pass\n",
            sdk=SimpleNamespace(sensors=SimpleNamespace()), context={},
            deadline=time.monotonic() + 3,
            stderr_path=tmp_path / "worker.stderr",
            cancel_check=cancel.is_set,
        )
    finally:
        timer.cancel()
    assert outcome.status == "cancelled"
    assert outcome.elapsed_seconds < 2


def test_formal_request_detects_config_change(tmp_path):
    code = tmp_path / "policy.py"
    config = tmp_path / "config.json"
    code.write_text("from robot_sdk import sensors\n")
    config.write_text("{}")
    request = FormalRunRequest(
        suite="robosuite", task_id="cube_lift", seed=101,
        policy_code_path=code,
        policy_sha256=hashlib.sha256(code.read_bytes()).hexdigest(),
        config_path=config,
        config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
        artifact_root=tmp_path / "artifacts", overall_deadline_seconds=5,
    )
    request.validate()
    config.write_text('{"changed":true}')
    with pytest.raises(ValueError, match="config source differs"):
        request.validate()
    config.write_text("{}")
    code.write_text("from robot_sdk import arm\n")
    with pytest.raises(ValueError, match="policy source differs"):
        request.validate()


@pytest.mark.parametrize("blocked_phase", ["/v1/reset", "/v1/step", "/v1/success"])
def test_dedicated_service_kills_inflight_request_at_deadline(tmp_path, blocked_phase):
    """The fake Service is single-threaded like the real MuJoCo Service."""
    package = tmp_path / "robosuite_sim"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "server.py").write_text("# fake formal stop test\n")
    (package / "__main__.py").write_text(
        "import argparse,json,time\n"
        "from http.server import BaseHTTPRequestHandler,HTTPServer\n"
        "p=argparse.ArgumentParser();p.add_argument('--host');p.add_argument('--port',type=int);"
        "p.add_argument('--enable-sim-gt',action='store_true');a=p.parse_args()\n"
        "class H(BaseHTTPRequestHandler):\n"
        " def log_message(self,*a): pass\n"
        " def do_GET(self):\n"
        "  if self.path=='/v1/success': time.sleep(10);return\n"
        "  body=json.dumps({'status':'ok'}).encode();self.send_response(200);"
        "self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)\n"
        " def do_POST(self): time.sleep(10)\n"
        "HTTPServer((a.host,a.port),H).serve_forever()\n"
    )
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "robosuite_sim"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "fake service"], cwd=tmp_path, check=True)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                       cwd=tmp_path, text=True).strip()
    # Python may create __pycache__ in the fake checkout. Ignore only that
    # generated directory so the actual source-revision check remains strict.
    (tmp_path / ".gitignore").write_text("__pycache__/\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "ignore bytecode"], cwd=tmp_path, check=True)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                       cwd=tmp_path, text=True).strip()
    with DedicatedRobosuiteService(
        source_root=tmp_path, expected_revision=revision,
        log_path=tmp_path / "service.log", deadline=time.monotonic() + 1.5,
    ) as service:
        errors = []

        def blocked_request():
            try:
                request = Request(service.base_url + blocked_phase,
                                  data=None if blocked_phase == "/v1/success" else b"{}",
                                  method="GET" if blocked_phase == "/v1/success" else "POST")
                urlopen(request, timeout=3).read()
            except Exception as exc:
                errors.append(type(exc).__name__)

        thread = threading.Thread(target=blocked_request)
        thread.start()
        thread.join(timeout=3)
        assert not thread.is_alive()
        assert errors
    assert service.stop_receipt["reason"] == "episode_deadline"
    assert service.stop_receipt["leader_reaped"] is True
    assert service.stop_receipt["process_group_gone"] is True
