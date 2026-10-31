"""Standalone policy worker mounted inside bubblewrap; no repository imports.

The parent sends reviewed source over stdin and owns every SDK operation.
A dedicated inherited descriptor carries RPC. stdout and stderr are authored streams.
"""

from __future__ import annotations

import json
import math
import os
import resource
import sys
import types


OPERATIONS = {
    "sensors": {"get_observation", "find_objects"},
    "left_arm": {"move_delta"},
    "right_arm": {"move_delta"},
    "left_gripper": {"open", "close"},
    "right_gripper": {"open", "close"},
    "bimanual": {"move_joints"},
    "base": {"move_delta"},
}


PROTOCOL = None

def send(value):
    PROTOCOL.write(json.dumps(value, separators=(",", ":"), allow_nan=False) + "\n")
    PROTOCOL.flush()


class Proxy:
    def __init__(self, group, operations=None):
        self.group = group
        self.operations = OPERATIONS[group] if operations is None else operations

    def __getattr__(self, operation):
        if operation not in self.operations:
            raise AttributeError(operation)

        def call(*args, **kwargs):
            if operation=='move_to':
                def wire(v):return repr(v) if type(v) is float and not math.isfinite(v) else v
                args=tuple(wire(v) for v in args);kwargs={k:wire(v) for k,v in kwargs.items()}
            send(
                {
                    "kind": "call",
                    "group": self.group,
                    "operation": operation,
                    "args": args,
                    "kwargs": kwargs,
                }
            )
            response = json.loads(sys.stdin.readline())
            if response.get("ok") is not True:
                raise RuntimeError(str(response.get("error", "SDK RPC failed")))
            return response.get("value")

        return call


def main():
    # Hard limits remain in force even if authored code escapes Python's AST
    # gate. The outer namespace removes network and host workspace access.
    # This argument is chosen by the parent from the approved camera config,
    # before any policy source is received. It cannot be raised by policy code.
    global PROTOCOL
    PROTOCOL = os.fdopen(int(sys.argv[2]), "w", buffering=1)
    memory_limit_mb = int(sys.argv[1])
    if len(sys.argv) != 3 or memory_limit_mb not in (512, 2048):
        raise ValueError("unsupported policy memory limit")
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    memory_limit = memory_limit_mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024, 16 * 1024 * 1024))
    request = json.loads(sys.stdin.readline())
    code = request["code"]
    context = request["context"]
    allowed = {**OPERATIONS}
    if request.get('fixture_profile') == 'r5-fixture-20261031':
        allowed['sensors'] = OPERATIONS['sensors'] | {'find_fixtures'}
        for group in ('left_gripper','right_gripper'):allowed[group] = OPERATIONS[group] | {'move_to'}
    module = types.ModuleType("robot_sdk")
    for group in OPERATIONS:
        setattr(module, group, Proxy(group, allowed[group]))

    def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "robot_sdk" and level == 0:
            return module
        raise ImportError("policy imports are restricted to robot_sdk")

    def policy_print(*args, **kwargs):
        print(*args, **kwargs)

    builtins = {
        "AttributeError": AttributeError,
        "Exception": Exception,
        "RuntimeError": RuntimeError,
        "TimeoutError": TimeoutError,
        "abs": abs,
        "all": all,
        "any": any,
        "bool": bool,
        "dict": dict,
        "enumerate": enumerate,
        "float": float,
        "hasattr": hasattr,
        "int": int,
        "len": len,
        "list": list,
        "max": max,
        "min": min,
        "print": policy_print,
        "range": range,
        "round": round,
        "sorted": sorted,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "zip": zip,
        "__import__": safe_import,
    }
    try:
        namespace = {"__builtins__": builtins, "context": context, "stderr": sys.stderr}
        exec(compile(code, "work/main.py", "exec"), namespace, namespace)
        send({"kind": "done"})
        return 0
    except BaseException as exc:
        import traceback
        for frame in traceback.extract_tb(exc.__traceback__):
            if frame.filename == 'work/main.py':
                print(f'  File "work/main.py", line {frame.lineno}', file=sys.stderr)
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        send({"kind": "error", "error": f"{type(exc).__name__}: {exc}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
