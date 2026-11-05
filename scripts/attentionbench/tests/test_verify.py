"""The self-test needs no model account and reports each part by name."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.attentionbench import verify


def fake_checks(monkeypatch, **results):
    for name in ('check_scene', 'check_planner', 'check_sandbox', 'check_media'):
        monkeypatch.setattr(verify, name, lambda ws, name=name: results.get(name, (True, 'ok')))


def test_all_checks_run_without_a_model(tmp_path, monkeypatch, capsys):
    fake_checks(monkeypatch)
    monkeypatch.setattr(verify, 'check_route', lambda ws, args: pytest.fail('no route was requested'))
    assert verify.run(tmp_path) == 0
    assert capsys.readouterr().out.count('PASS') == 4
    report = next((tmp_path / 'data/run').glob('verify-*.json'))
    assert [row['check'] for row in json.loads(report.read_text())] == ['scene and renderer', 'planner', 'sandbox', 'recording']


def test_a_failed_check_is_counted_and_the_rest_still_run(tmp_path, monkeypatch, capsys):
    fake_checks(monkeypatch, check_scene=(False, 'EGL unavailable'))
    assert verify.run(tmp_path) == 1
    output = capsys.readouterr().out
    assert 'FAIL scene and renderer: EGL unavailable' in output and output.count('PASS') == 3


def test_a_route_is_checked_only_when_asked(tmp_path, monkeypatch):
    fake_checks(monkeypatch)
    monkeypatch.setattr(verify, 'check_route', lambda ws, args: (False, 'bad key'))
    assert verify.run(tmp_path, SimpleNamespace(model='deepseek')) == 1


def test_scene_check_needs_a_match_and_a_frame(tmp_path, monkeypatch):
    monkeypatch.setattr(verify, '_run', lambda argv, **kw: (True, 'matches_registered: True\nframe_rendered: False'))
    assert not verify.check_scene(tmp_path)[0]
    monkeypatch.setattr(verify, '_run', lambda argv, **kw: (True, 'matches_registered: True\nframe_rendered: True'))
    assert verify.check_scene(tmp_path)[0]


def test_planner_failure_keeps_the_reason(tmp_path, monkeypatch):
    def reprove(ws):
        raise verify.installer.StepError('reprove', 'driver too old', 'upgrade')
    monkeypatch.setattr(verify.installer, 'reprove', reprove)
    ok, detail = verify.check_planner(tmp_path)
    assert not ok and 'driver too old' in detail


def test_a_workspace_that_was_never_set_up_is_refused(tmp_path, capsys):
    assert verify.main(['--workspace', str(tmp_path)]) == 1
    assert 'has not been set up' in capsys.readouterr().err
