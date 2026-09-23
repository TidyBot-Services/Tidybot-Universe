"""Child process that exposes only the TidyBot SDK to validated policy code."""

from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path

from tidybot_sdk import TidyBotSDK

from .robosuite_adapter import RobosuiteRobotBackend


SAFE_BUILTINS = {
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
    "print": print,
    "range": range,
    "round": round,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
}
ORIGINAL_IMPORT = __import__


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--service-url", required=True)
    parser.add_argument("--task", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    adapter = RobosuiteRobotBackend(args.task, service_url=args.service_url)
    result = {"status": "failed", "error": None, "trace": [], "sdk_trace": []}

    def persist() -> None:
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(args.output)

    def record_sdk_event(event: dict) -> None:
        result["sdk_trace"].append(event)
        # Incremental atomic persistence preserves the latest complete SDK event
        # when the parent terminates this process at its timeout boundary.
        persist()

    try:
        adapter.attach()
        sdk = TidyBotSDK(adapter, event_sink=record_sdk_event)
        module = types.ModuleType("robot_sdk")
        module.sensors = sdk.sensors
        module.arm = sdk.arm
        module.gripper = sdk.gripper
        sys.modules["robot_sdk"] = module

        def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "robot_sdk" and level == 0:
                return module
            # NumPy lazily imports formatter helpers when a public observation's
            # dtype is rendered. Policy AST still forbids authored import nodes.
            if level == 0 and (name == "numpy" or name.startswith("numpy.")):
                return ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
            raise ImportError(f"import of {name!r} is not permitted")

        builtins = dict(SAFE_BUILTINS)
        builtins["__import__"] = safe_import
        code = args.code.read_text(encoding="utf-8")
        namespace = {"__builtins__": builtins}
        exec(compile(code, str(args.code), "exec"), namespace, namespace)
        result["status"] = "completed"
    except BaseException as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        result["trace"] = adapter.trace
        persist()
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
