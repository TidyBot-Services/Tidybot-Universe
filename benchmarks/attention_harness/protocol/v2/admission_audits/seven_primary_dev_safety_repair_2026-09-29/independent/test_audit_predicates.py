"""Adversarial read-only replay of this retained control; no simulator calls."""
import copy
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('negative_auditor', Path(__file__).with_name('audit_negative_control_v3.py'))
auditor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auditor)


def test_retained_original_control_passes_read_only_replay():
    assert auditor.audit()['coverage_passed']


@pytest.mark.parametrize('corruption', ['no_injection', 'second_action', 'missing_detection', 'late_reaping'])
def test_missing_or_late_fault_evidence_never_passes(monkeypatch, corruption):
    read0 = auditor.read

    def corrupt_read(path):
        value = copy.deepcopy(read0(path))
        if Path(path).name == 'injection_receipt.json':
            if corruption == 'no_injection':
                value.pop('injected')
            elif corruption == 'second_action':
                value['worker_outcomes'][0]['call_count'] = 2
            elif corruption == 'missing_detection':
                value['safety_detections'] = []
            elif corruption == 'late_reaping':
                value['service_stops'][0]['confirmed']['monotonic'] += 16
        return value

    monkeypatch.setattr(auditor, 'read', corrupt_read)
    result = auditor.audit()
    assert result['coverage_passed'] is False
    assert result['safety_fault_injection_gate'] is False
    assert result['issues']
