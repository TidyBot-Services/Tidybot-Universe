from __future__ import annotations

import ast
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


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
