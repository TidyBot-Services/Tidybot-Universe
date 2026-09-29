"""Standalone policy worker mounted inside bubblewrap; no repository imports.

The parent sends reviewed source over stdin and owns every SDK operation.
Stdout is a line-delimited RPC channel, never a policy print destination.
"""

from __future__ import annotations

import json
import resource
import sys
import types


OPERATIONS = {
    "sensors": {"get_observation", "find_objects", "pixel_to_world"},
    "arm": {"move_delta", "move_to_position", "plan_to_position"},
    "gripper": {"open", "close"},
    "base": {"move_delta"},
}


def send(value):
    sys.stdout.write(json.dumps(value, separators=(",", ":"), allow_nan=False) + "\n")
    sys.stdout.flush()


class Proxy:
    def __init__(self, group):
        self.group = group

    def __getattr__(self, operation):
        if operation not in OPERATIONS[self.group]:
            raise AttributeError(operation)

        def call(*args, **kwargs):
            send({"kind": "call", "group": self.group, "operation": operation,
                  "args": args, "kwargs": kwargs})
            response = json.loads(sys.stdin.readline())
            if response.get("ok") is not True:
                raise RuntimeError(str(response.get("error", "SDK RPC failed")))
            return response.get("value")

        return call


def main():
    # Hard limits remain in force even if authored code escapes Python's AST
    # gate. The outer namespace removes network and host workspace access.
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
    request = json.loads(sys.stdin.readline())
    code = request["code"]
    context = request["context"]
    module = types.ModuleType("robot_sdk")
    for group in OPERATIONS:
        setattr(module, group, Proxy(group))

    def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "robot_sdk" and level == 0:
            return module
        raise ImportError("policy imports are restricted to robot_sdk")

    def policy_print(*args, **kwargs):
        print(*args, file=sys.stderr, **kwargs)

    builtins = {
        "AttributeError": AttributeError, "Exception": Exception,
        "RuntimeError": RuntimeError, "TimeoutError": TimeoutError,
        "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
        "enumerate": enumerate, "float": float, "hasattr": hasattr,
        "int": int, "len": len, "list": list, "max": max, "min": min,
        "print": policy_print, "range": range, "round": round,
        "sorted": sorted, "str": str, "sum": sum, "tuple": tuple,
        "zip": zip, "__import__": safe_import,
    }
    try:
        namespace = {"__builtins__": builtins, "context": context}
        exec(compile(code, "<reviewed-formal-policy>", "exec"), namespace, namespace)
        send({"kind": "done"})
        return 0
    except BaseException as exc:
        send({"kind": "error", "error": f"{type(exc).__name__}: {exc}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
