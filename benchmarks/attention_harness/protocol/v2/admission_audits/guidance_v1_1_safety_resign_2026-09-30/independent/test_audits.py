"""Adversarial, read-only checks of this round's evidence adjudicators."""
import copy
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_package(tmp_path):
    report = json.loads((HERE / 'autonomous_AST_alignment.json').read_text())
    for record in report['records']:
        suite = record['suite']
        task = 'cube_lift' if suite == 'robosuite' else 'counter_to_sink'
        for original, relative in [
            (record['new_source'], f'frozen/final_policies/{suite}_{task}.py'),
            (record['old_source'], f'frozen/inputs/{suite}_old_policy.py'),
        ]:
            target = tmp_path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target)
    comparator = load('autonomous_ast_alignment')
    comparator.PACKAGE = tmp_path
    return comparator


def test_exact_empty_guidance_programs_align(tmp_path):
    comparator = fixture_package(tmp_path)
    for suite in ['robosuite', 'robocasa']:
        assert comparator.compare(suite)[0]['whole_autonomous_robot_program_AST_equal']


def test_changed_no_guidance_base_default_is_not_inherited(tmp_path):
    comparator = fixture_package(tmp_path)
    target = tmp_path / 'frozen/final_policies/robocasa_counter_to_sink.py'
    original = target.read_text()
    changed = original.replace('"base_forward_m", .125', '"base_forward_m", .12')
    assert changed != original
    target.write_text(changed)
    assert not comparator.compare('robocasa')[0]['whole_autonomous_robot_program_AST_equal']


def test_changed_approach_default_is_rejected(tmp_path):
    comparator = fixture_package(tmp_path)
    target = tmp_path / 'frozen/final_policies/robosuite_cube_lift.py'
    original = target.read_text()
    changed = original.replace('tolerance=0.018', 'tolerance=0.019')
    assert changed != original
    target.write_text(changed)
    with pytest.raises(ValueError, match='unproved approach signature/defaults'):
        comparator.compare('robosuite')


def test_extra_robot_call_in_compiler_prelude_is_rejected(tmp_path):
    comparator = fixture_package(tmp_path)
    target = tmp_path / 'frozen/final_policies/robocasa_counter_to_sink.py'
    target.write_text(target.read_text().replace(
        'adoption_parameters = compile_attention',
        'base.move_delta(dx=0.01, dy=0, dtheta=0, frame="local")\nadoption_parameters = compile_attention'))
    with pytest.raises(ValueError, match='unproved executable statement'):
        comparator.compare('robocasa')


def test_retained_negative_replays_without_robot_execution():
    verifier = load('audit_negative_control')
    assert verifier.audit()['coverage_passed']


@pytest.mark.parametrize('mutation,check_name', [
    ('no_injection', 'real completion -> injected error -> original Safety -> worker stop -> reaping order'),
    ('no_detection', 'original independent Safety records targeted unknown-action violation'),
    ('second_job', 'one real guided-base dispatch and completed Service job'),
    ('second_worker_action', 'Safety error stops original worker before second SDK action'),
    ('late_reaping', 'predeclared detection <=1 second and reaping <=15 seconds'),
])
def test_incomplete_or_late_evidence_cannot_pass(monkeypatch, mutation, check_name):
    verifier = load('audit_negative_control')
    original_read = verifier.read

    def changed_read(path):
        payload = original_read(path)
        if Path(path).name != 'injection_receipt.json':
            return payload
        payload = copy.deepcopy(payload)
        if mutation == 'no_injection':
            payload.pop('injected')
        elif mutation == 'no_detection':
            payload['safety_detections'] = []
        elif mutation == 'second_job':
            payload['dispatches'].append(copy.deepcopy(payload['dispatches'][0]))
            payload['job_submissions'].append(copy.deepcopy(payload['job_submissions'][0]))
            payload['job_receipts'].append(copy.deepcopy(payload['job_receipts'][0]))
        elif mutation == 'second_worker_action':
            payload['worker_outcomes'][0]['call_count'] = 4
        elif mutation == 'late_reaping':
            payload['service_stops'][0]['confirmed']['monotonic'] += 20
        return payload

    monkeypatch.setattr(verifier, 'read', changed_read)
    report = verifier.audit()
    assert not report['coverage_passed']
    assert any(c['check'] == check_name and not c['pass'] for c in report['checks'])
