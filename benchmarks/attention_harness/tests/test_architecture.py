from __future__ import annotations

import ast
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SHARED_SDK_ROOT = PACKAGE_ROOT.parents[1] / "tidybot_sdk"


def test_harness_has_no_simulator_or_legacy_harness_imports() -> None:
    offenders = []
    for path in PACKAGE_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.lower().split(".", 1)[0] in {"aspire", "robosuite", "mujoco"}:
                    offenders.append(f"{path.name}:{node.lineno}:{name}")
    assert offenders == []


def test_attention_robot_sdk_is_only_a_shared_sdk_compatibility_wrapper() -> None:
    source = (PACKAGE_ROOT / "robot_sdk.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "tidybot_sdk" in imported_modules
    assert "robosuite_adapter" not in imported_modules
    assert not any(isinstance(node, ast.ClassDef) for node in tree.body)


def test_shared_sdk_has_no_environment_or_service_imports() -> None:
    offenders = []
    for path in SHARED_SDK_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                names = []
            for name in names:
                root = name.lower().split(".", 1)[0]
                if root in {"aspire", "mujoco", "robosuite", "robosuite_sim"}:
                    offenders.append(f"{path.name}:{node.lineno}:{name}")
    assert offenders == []
