"""Subprocess boundary for RoboCasa generated policies.

Only the parent owns the simulator, action backend, evaluator, and memory
gateway. The child receives a JSON-safe context and a narrow SDK RPC pipe.
This is a model-code containment boundary, not an OS-level hostile-code jail.
"""

from __future__ import annotations

import ast
import multiprocessing as mp
import os
import time
import types
from dataclasses import dataclass
from multiprocessing.connection import Connection
from typing import Any

from ..sandbox_worker import SAFE_BUILTINS


ALLOWED_CALLS = {
    "sensors": {"get_observation", "find_objects", "pixel_to_world"},
    "arm": {"move_delta", "move_to_position"},
    "gripper": {"open", "close"},
    "base": {"move_delta"},
}
BROKER_CALLS = {**ALLOWED_CALLS, "memory": {"retrieve"}}
FORBIDDEN_NAMES = {"breakpoint", "compile", "eval", "exec", "getattr", "globals",
                   "input", "locals", "open", "setattr", "vars", "__import__"}
_CHILD_ENV_KEYS = {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "HOME", "TMPDIR", "PYTHONUNBUFFERED"}


class GeneratedPolicyError(RuntimeError):
    pass


class GeneratedPolicyTimeout(TimeoutError):
    pass


class _Validator(ast.NodeVisitor):
    def visit_Import(self, node: ast.Import) -> None:
        raise GeneratedPolicyError("plain imports are forbidden")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if (node.module != "robot_sdk" or node.level != 0 or not node.names
                or any(alias.asname or alias.name not in ALLOWED_CALLS for alias in node.names)):
            raise GeneratedPolicyError("only robot_sdk sensors, arm, gripper imports are allowed")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("_"):
            raise GeneratedPolicyError("private attributes are forbidden")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id.startswith("__"):
            raise GeneratedPolicyError("dunder names are forbidden")

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_NAMES:
            raise GeneratedPolicyError(f"call to {node.func.id} is forbidden")
        if isinstance(node.func, ast.Attribute):
            if node.func.attr not in set().union(*ALLOWED_CALLS.values(), {"get", "keys", "values", "items"}):
                raise GeneratedPolicyError(f"method {node.func.attr} is forbidden")
        self.generic_visit(node)


def validate_generated_policy(code: str) -> None:
    if len(code.encode("utf-8")) > 20_000:
        raise GeneratedPolicyError("policy exceeds 20 KB")
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise GeneratedPolicyError(f"invalid Python: {exc}") from exc
    _Validator().visit(tree)
    if not any(isinstance(node, ast.ImportFrom) for node in tree.body):
        raise GeneratedPolicyError("policy must import robot_sdk")


@dataclass(frozen=True)
class GeneratedPolicyResult:
    status: str
    elapsed_seconds: float
    call_count: int
    error: str | None = None


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "tolist"):
        return _json_safe(value.tolist())
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"SDK returned unsupported value {type(value).__name__}")


def _worker(pipe: Connection, code: str, context: dict[str, Any]) -> None:
    # The spawned worker receives the parent's environment initially. Remove
    # provider credentials before executing any model-authored policy code.
    child_env = {key: value for key, value in os.environ.items() if key in _CHILD_ENV_KEYS}
    os.environ.clear()
    os.environ.update(child_env)

    class Proxy:
        def __init__(self, group: str):
            self.group = group

        def __getattr__(self, operation: str):
            if operation not in BROKER_CALLS[self.group]:
                raise AttributeError(operation)

            def call(*args, **kwargs):
                pipe.send({"kind": "call", "group": self.group, "operation": operation,
                           "args": args, "kwargs": kwargs})
                reply = pipe.recv()
                if not reply["ok"]:
                    raise RuntimeError(reply["error"])
                return reply["value"]
            return call

    module = types.ModuleType("robot_sdk")
    for group in ALLOWED_CALLS:
        setattr(module, group, Proxy(group))
    context["retrieve_memory"] = Proxy("memory").retrieve

    def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "robot_sdk" and level == 0:
            return module
        raise ImportError(f"import of {name!r} is forbidden")

    builtins = dict(SAFE_BUILTINS)
    builtins["__import__"] = safe_import
    try:
        namespace = {"__builtins__": builtins, "context": context}
        exec(compile(code, "<generated-robocasa-policy>", "exec"), namespace, namespace)
        pipe.send({"kind": "done"})
    except BaseException as exc:
        pipe.send({"kind": "error", "error": f"{type(exc).__name__}: {exc}"})
    finally:
        pipe.close()


def execute_generated_policy(*, code: str, sdk: Any, context: dict[str, Any],
                             timeout_seconds: float) -> GeneratedPolicyResult:
    validate_generated_policy(code)
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    public_context = {key: value for key, value in context.items() if key != "retrieve_memory"}
    public_context = _json_safe(public_context)
    process_context = mp.get_context("spawn")
    parent, child = process_context.Pipe()
    process = process_context.Process(target=_worker, args=(child, code, public_context))
    started = time.monotonic()
    deadline = started + timeout_seconds
    calls = 0
    process.start()
    child.close()
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise GeneratedPolicyTimeout(f"generated policy exceeded {timeout_seconds:.1f}s")
            if not parent.poll(min(remaining, 0.1)):
                if not process.is_alive():
                    raise GeneratedPolicyError(f"generated policy exited with code {process.exitcode}")
                continue
            try:
                message = parent.recv()
            except EOFError as exc:
                raise GeneratedPolicyError("generated policy closed its RPC channel") from exc
            if message.get("kind") == "done":
                process.join(timeout=0.2)
                return GeneratedPolicyResult("completed", time.monotonic() - started, calls)
            if message.get("kind") == "error":
                raise GeneratedPolicyError(str(message.get("error")))
            group, operation = message.get("group"), message.get("operation")
            if message.get("kind") != "call" or operation not in BROKER_CALLS.get(group, ()):
                raise GeneratedPolicyError("worker sent an invalid SDK request")
            calls += 1
            if calls > 200:
                raise GeneratedPolicyError("generated policy exceeded SDK call limit")
            try:
                if group == "memory":
                    value = context["retrieve_memory"](
                        *message.get("args", ()), **message.get("kwargs", {}))
                else:
                    value = getattr(getattr(sdk, group), operation)(
                        *message.get("args", ()), **message.get("kwargs", {}))
                reply = {"ok": True, "value": _json_safe(value)}
            except Exception as exc:
                if isinstance(exc, TimeoutError):
                    raise GeneratedPolicyTimeout(str(exc)) from exc
                reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            parent.send(reply)
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=1.0)
            if process.is_alive():
                process.kill()
                process.join(timeout=1.0)
        parent.close()
