"""OS-contained policy process with a parent-owned TidyBot SDK RPC broker."""

from __future__ import annotations

import json
import hashlib
import os
import selectors
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .policy_validation import validate_generated_policy, OPERATIONS as _OPERATIONS





@dataclass(frozen=True)
class FormalPolicyOutcome:
    status: str
    call_count: int
    error: str | None
    elapsed_seconds: float
    process_exit_code: int | None
    memory_limit_mb: int = 512


def _bwrap_prefix(worker_path: Path | None = None) -> list[str]:
    executable = shutil.which("bwrap")
    if executable is None:
        raise RuntimeError("formal policy requires bubblewrap")
    command = [
        executable,
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
        "--setenv",
        "PATH",
        "/usr/bin:/bin",
        "--setenv",
        "PYTHONDONTWRITEBYTECODE",
        "1",
        "--ro-bind",
        "/usr",
        "/usr",
        "--ro-bind",
        "/lib",
        "/lib",
        "--ro-bind",
        "/lib64",
        "/lib64",
        "--ro-bind",
        "/bin",
        "/bin",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--chdir",
        "/tmp",
    ]
    if worker_path is not None:
        command += [
            "--dir",
            "/worker",
            "--ro-bind",
            str(worker_path),
            "/worker/worker.py",
        ]
    return command


def probe_sandbox() -> dict[str, Any]:
    """Prove the actual namespace denies host files, credentials, and network."""
    probe = (
        "import json,os,socket;"
        "s=socket.socket();s.settimeout(0.2);"
        "network=False;"
        "\ntry:s.connect(('1.1.1.1',80));network=True\nexcept OSError:pass\n"
        "print(json.dumps({'host_home_visible':os.path.exists('/home'),"
        "'host_root_visible':os.path.exists('/root'),"
        "'network_reachable':network,'environment_keys':sorted(os.environ)}))"
    )
    completed = subprocess.run(
        _bwrap_prefix() + ["/usr/bin/python3", "-I", "-c", probe],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"formal sandbox probe failed: {completed.stderr[-500:]}")
    evidence = json.loads(completed.stdout)
    if (
        evidence["host_home_visible"]
        or evidence["host_root_visible"]
        or evidence["network_reachable"]
    ):
        raise RuntimeError("formal sandbox exposed host files or network")
    if set(evidence["environment_keys"]) - {
        "PATH",
        "PYTHONDONTWRITEBYTECODE",
        "PWD",
        "LC_CTYPE",
    }:
        raise RuntimeError("formal sandbox inherited an unexpected environment key")
    worker = Path(__file__).with_name("policy_worker.py")
    bwrap = shutil.which("bwrap")
    version = subprocess.check_output([bwrap, "--version"], text=True).strip()
    return {
        "boundary": "bubblewrap_user_mount_pid_network_namespace",
        "bubblewrap_executable": str(Path(bwrap).resolve()),
        "bubblewrap_version": version,
        "worker_sha256": hashlib.sha256(worker.read_bytes()).hexdigest(),
        "read_only_mounts": ["/usr", "/lib", "/lib64", "/bin", "/worker/worker.py"],
        "host_home_visible": False,
        "host_root_visible": False,
        "network_reachable": False,
        "credential_environment_absent": True,
        "worker_has_simulator_client": False,
    }


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "tolist"):
        return _json_safe(value.tolist())
    raise TypeError(f"unsupported SDK return type {type(value).__name__}")


def _meter_sdk_call(calls: int, limit: int) -> int:
    """Every authorized RPC, including base motion, spends one SDK call."""
    if calls + 1 > limit:
        raise RuntimeError("policy exceeded SDK call limit")
    return calls + 1


def execute_formal_policy(
    *,
    code: str,
    sdk: Any,
    context: dict[str, Any],
    deadline: float,
    stderr_path: Path,
    cancel_check: Callable[[], bool] | None = None,
    max_sdk_calls: int = 200,
    memory_limit_mb: int = 512,
    source_config: dict | None = None,
) -> FormalPolicyOutcome:
    if type(memory_limit_mb) is not int or memory_limit_mb not in (512, 2048):
        raise ValueError("formal policy memory limit must be 512 or 2048 MiB")
    call_ceiling = 3200 if context.get("capabilities", {}).get("robot") == "OpenArm365" else 200
    if type(max_sdk_calls) is not int or not 1 <= max_sdk_calls <= call_ceiling:
        raise ValueError("formal SDK call limit exceeds the robot-specific ceiling")
    validate_generated_policy(code,config=source_config)
    from .policy_validation import operations
    from curobo_v2.fixture_interaction import config_enabled,PROFILE
    allowed=operations(source_config)
    profile_fields={'fixture_profile':PROFILE} if config_enabled(source_config) else {}
    worker = Path(__file__).with_name("policy_worker.py").resolve()
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    calls = 0
    with stderr_path.open("wb") as stderr_file:
        process = subprocess.Popen(
            _bwrap_prefix(worker)
            + ["/usr/bin/python3", "-I", "/worker/worker.py", str(memory_limit_mb)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr_file,
        )
        assert process.stdin is not None and process.stdout is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        process.stdin.write(
            (json.dumps({"code": code, "context": _json_safe(context), **profile_fields}) + "\n").encode()
        )
        process.stdin.flush()
        buffer = bytearray()
        status = "failed"
        error: str | None = None
        try:
            while True:
                if cancel_check is not None and cancel_check():
                    status, error = "cancelled", "operator cancelled formal attempt"
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    status, error = (
                        "timeout",
                        "episode deadline reached during policy execution",
                    )
                    break
                events = selector.select(timeout=min(remaining, 0.1))
                if not events:
                    if process.poll() is not None:
                        error = f"policy sandbox exited with code {process.returncode}"
                        break
                    continue
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    error = f"policy sandbox closed RPC with code {process.poll()}"
                    break
                buffer.extend(chunk)
                if len(buffer) > 8_000_000:
                    error = "policy sandbox exceeded RPC message limit"
                    break
                while b"\n" in buffer:
                    line, _, rest = buffer.partition(b"\n")
                    buffer = bytearray(rest)
                    message = json.loads(line)
                    kind = message.get("kind")
                    if kind == "done":
                        status = "completed"
                        break
                    if kind == "error":
                        error = str(message.get("error", "policy failed"))[:1000]
                        break
                    group, operation = message.get("group"), message.get("operation")
                    if kind != "call" or operation not in allowed.get(group, ()):
                        error = "policy worker sent unauthorized SDK RPC"
                        break
                    try: calls = _meter_sdk_call(calls, max_sdk_calls)
                    except RuntimeError as exc:
                        error = str(exc)
                        break
                    args, kwargs = message.get("args"), message.get("kwargs")
                    if not isinstance(args, list) or not isinstance(kwargs, dict):
                        error = "policy worker sent invalid SDK arguments"
                        break
                    try:
                        result = getattr(getattr(sdk, group), operation)(
                            *args, **kwargs
                        )
                        # ActionResult can carry evaluator-adjacent service info.
                        # Policy code only needs completion, never that object.
                        value = (
                            _json_safe(result)
                            if group == "arm" and operation == "plan_to_position"
                            else (
                                None
                                if group in {"left_arm", "right_arm", "left_gripper", "right_gripper", "bimanual", "base"}
                                else _json_safe(result)
                            )
                        )
                        response = {"ok": True, "value": value}
                    except Exception as exc:
                        response = {
                            "ok": False,
                            "error": f"{type(exc).__name__}: {exc}"[:1000],
                        }
                        status = (
                            "timeout" if isinstance(exc, TimeoutError) else "failed"
                        )
                        error = response["error"]
                    try:
                        process.stdin.write(
                            (json.dumps(response, allow_nan=False) + "\n").encode()
                        )
                        process.stdin.flush()
                    except BrokenPipeError:
                        error = "policy sandbox closed while SDK response was sent"
                        break
                    if status == "timeout":
                        error = response["error"]
                        break
                if status == "completed" or error is not None or status == "timeout":
                    break
        finally:
            selector.close()
            if status == "completed" and process.poll() is None:
                try:
                    process.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    pass
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()
    if status == "completed" and process.returncode != 0:
        status, error = "failed", f"policy worker exited with code {process.returncode}"
    return FormalPolicyOutcome(
        status,
        calls,
        error,
        time.monotonic() - started,
        process.returncode,
        memory_limit_mb,
    )
