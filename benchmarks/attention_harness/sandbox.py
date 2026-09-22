"""AST gate and subprocess boundary for model-generated SDK programs."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ALLOWED_SDK_NAMES = {"sensors", "arm", "gripper"}
FORBIDDEN_CALLS = {"breakpoint", "compile", "eval", "exec", "getattr", "globals", "help", "input", "locals", "open", "setattr", "vars", "__import__"}
ALLOWED_METHOD_CALLS = {
    "close",
    "get",
    "get_observation",
    "items",
    "keys",
    "mean",
    "min",
    "max",
    "move_delta",
    "move_to_position",
    "open",
    "tolist",
    "values",
}
SECRET_ENV_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


class PolicyValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SandboxResult:
    status: str
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str
    trace: list[dict[str, Any]]
    error: str | None

    def artifact(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "trace": self.trace,
            "error": self.error,
        }


class _PolicyValidator(ast.NodeVisitor):
    def visit_Import(self, node: ast.Import) -> None:
        raise PolicyValidationError("plain imports are forbidden")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        names = {alias.name for alias in node.names}
        if node.module != "robot_sdk" or node.level != 0 or not names <= ALLOWED_SDK_NAMES:
            raise PolicyValidationError(
                "only `from robot_sdk import sensors, arm, gripper` is allowed"
            )
        if any(alias.asname for alias in node.names):
            raise PolicyValidationError("SDK import aliases are forbidden")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("_"):
            raise PolicyValidationError("private and dunder attribute access is forbidden")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id.startswith("__"):
            raise PolicyValidationError("dunder names are forbidden")

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
            raise PolicyValidationError(f"call to {node.func.id!r} is forbidden")
        if isinstance(node.func, ast.Attribute) and node.func.attr not in ALLOWED_METHOD_CALLS:
            raise PolicyValidationError(f"method call {node.func.attr!r} is not permitted")
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is None or (isinstance(node.type, ast.Name) and node.type.id in {"Exception", "BaseException"}):
            raise PolicyValidationError("bare or broad exception handlers are forbidden")
        self.generic_visit(node)


def validate_policy(code: str) -> ast.Module:
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise PolicyValidationError(f"invalid Python: {exc}") from exc
    _PolicyValidator().visit(tree)
    imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)]
    if not imports:
        raise PolicyValidationError("policy must import the TidyBot robot_sdk")
    if len(code.encode("utf-8")) > 20_000:
        raise PolicyValidationError("policy exceeds 20 KB limit")
    return tree


def execute_policy(
    *,
    code_path: Path,
    service_url: str,
    task_id: str,
    output_path: Path,
    timeout_seconds: float,
) -> SandboxResult:
    code = code_path.read_text(encoding="utf-8")
    validate_policy(code)
    env = _sanitized_environment()
    command = [
        sys.executable,
        "-I",
        "-m",
        "benchmarks.attention_harness.sandbox_worker",
        "--code",
        str(code_path.resolve()),
        "--output",
        str(output_path.resolve()),
        "--service-url",
        service_url,
        "--task",
        task_id,
    ]
    # Isolated mode removes the working tree from sys.path, so explicitly use
    # a tiny bootstrap that inserts only this repository before importing the
    # worker. It does not expose credentials or generated text on argv.
    repo_root = Path(__file__).resolve().parents[2]
    bootstrap = (
        "import runpy,sys;"
        f"sys.path.insert(0,{str(repo_root)!r});"
        "runpy.run_module('benchmarks.attention_harness.sandbox_worker',run_name='__main__')"
    )
    command = [sys.executable, "-I", "-c", bootstrap, *command[4:]]
    try:
        completed = subprocess.run(
            command,
            cwd=repo_root,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return SandboxResult(
            status="timeout",
            exit_code=None,
            timed_out=True,
            stdout=_limited(exc.stdout or ""),
            stderr=_limited(exc.stderr or ""),
            trace=[],
            error=f"policy exceeded {timeout_seconds:.1f}s timeout",
        )
    worker = _read_worker_output(output_path)
    trace = list(worker.get("trace") or [])
    status = (
        "completed"
        if completed.returncode == 0 and worker.get("status") == "completed" and trace
        else "failed"
    )
    error = worker.get("error")
    if not error and completed.returncode == 0 and worker.get("status") == "completed" and not trace:
        error = "policy completed without producing any robot action"
    return SandboxResult(
        status=status,
        exit_code=completed.returncode,
        timed_out=False,
        stdout=_limited(completed.stdout),
        stderr=_limited(completed.stderr),
        trace=trace,
        error=error or (None if status == "completed" else "sandbox worker failed"),
    )


def _sanitized_environment() -> dict[str, str]:
    allowed = {"PATH", "LANG", "LC_ALL", "MUJOCO_GL", "PYTHONUNBUFFERED"}
    clean = {key: value for key, value in os.environ.items() if key in allowed}
    clean["PYTHONUNBUFFERED"] = "1"
    assert not any(marker in key.upper() for key in clean for marker in SECRET_ENV_MARKERS)
    return clean


def _read_worker_output(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _limited(value: str | bytes, limit: int = 16_000) -> str:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value[-limit:]
