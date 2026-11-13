"""Execute the actual worker guard without starting a native process."""
import ast
import inspect
import os

import pytest

from openarm365_dev_v2 import worker


OVERRIDES = ('ATTN_PLANNER_MODE', 'ATTN_IK_CANDIDATES', 'ATTN_IK_MAX_JOINT_JUMP',
             'ATTN_DIAG_CONTACT_EXEMPT')
MESSAGE = 'planner diagnostic overrides cannot enter formal worker'


@pytest.mark.parametrize('boundary', ['formal', 'dev_loop'])
@pytest.mark.parametrize('key', OVERRIDES)
@pytest.mark.parametrize('value', ['', 'diagnostic'])
def test_worker_planner_override_guard(monkeypatch, boundary, key, value):
    for name in OVERRIDES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(key, value)
    check = next(node for node in ast.walk(ast.parse(inspect.getsource(worker.main)))
        if isinstance(node, ast.If) and any(isinstance(child, ast.Raise)
            and isinstance(child.exc, ast.Call) and child.exc.args
            and isinstance(child.exc.args[0], ast.Constant) and child.exc.args[0].value == MESSAGE
            for child in node.body))
    code = compile(ast.Module(body=[check], type_ignores=[]), worker.__file__, 'exec')
    scope = dict(c={'runner_boundary': boundary}, os=os)
    if boundary == 'formal':
        with pytest.raises(ValueError, match=MESSAGE):
            exec(code, scope)
    else:
        exec(code, scope)


def test_formal_worker_without_planner_overrides(monkeypatch):
    for name in OVERRIDES:
        monkeypatch.delenv(name, raising=False)
    check = next(node for node in ast.walk(ast.parse(inspect.getsource(worker.main)))
        if isinstance(node, ast.If) and any(isinstance(child, ast.Raise)
            and isinstance(child.exc, ast.Call) and child.exc.args
            and isinstance(child.exc.args[0], ast.Constant) and child.exc.args[0].value == MESSAGE
            for child in node.body))
    exec(compile(ast.Module(body=[check], type_ignores=[]), worker.__file__, 'exec'),
         dict(c={'runner_boundary': 'formal'}, os=os))
