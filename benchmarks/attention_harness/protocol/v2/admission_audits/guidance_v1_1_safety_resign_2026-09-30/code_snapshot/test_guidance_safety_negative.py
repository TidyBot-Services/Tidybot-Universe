"""Approved v1.1 policy reaches the directory-bound control and Safety broker."""
import hashlib
import json
import time
from pathlib import Path

import pytest

from benchmarks.attention_harness.public_station import publish_public_station, read_public_station
from benchmarks.attention_harness.robocasa_native.controlled_negative import controlled_negative_request
from benchmarks.attention_harness.robocasa_native.mobile_sdk import RobocasaMobileSDK
from benchmarks.attention_harness.robocasa_native.safety_monitor import SafetyMonitorBackend
from benchmarks.attention_harness.robosuite_memory.formal_sandbox import execute_formal_policy
from benchmarks.attention_harness.tests.test_guidance_adoption_v1 import PublicBackend

PACKAGE = Path(__file__).parents[1] / 'protocol/v2/review_packages/guidance_adoption_v1_1_dev_2026-09-29'
POLICY = PACKAGE / 'frozen/final_policies/robocasa_counter_to_sink.py'
CONFIG = PACKAGE / 'frozen/inputs/robocasa_config.json'
ATTENTION = json.loads((PACKAGE / 'frozen/inputs/robocasa_memory_fixture.json').read_text())
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def request(tmp_path, attention=ATTENTION):
    return controlled_negative_request(
        run_dir=tmp_path / 'attention-robocasa-safety-negative-guidance-v1-1',
        policy_path=POLICY, policy_sha256=sha(POLICY), config_path=CONFIG,
        config_sha256=sha(CONFIG), attention_input=attention)


def test_approved_policy_input_and_station_share_identity(tmp_path):
    r = request(tmp_path)
    assert r.policy_sha256 == 'c016bed6d2a85eb2cd1a972299a2f38394a63780fc537f4b19e83991a6c015d7'
    assert r.attention_input == ATTENTION
    publish_public_station(run_dir=r.artifact_root.parent, run_id=r.run_id,
                          attempt_id=r.attempt_id, suite='robocasa',
                          origin='ws://127.0.0.1:6380', device_id='maniskill_base')
    assert read_public_station(tmp_path, r.run_id, 'robocasa', {r.attempt_id})
    with pytest.raises(ValueError, match='identity mismatch'):
        publish_public_station(run_dir=r.artifact_root.parent,
            run_id='run:seven-admission-safety-negative-control', attempt_id=r.attempt_id,
            suite='robocasa', origin='ws://127.0.0.1:6380', device_id='maniskill_base')


def test_invalid_guidance_is_rejected_before_service(tmp_path):
    with pytest.raises(ValueError, match='unsupported fields'):
        request(tmp_path, {'private_native_success': True})


def test_exact_v1_1_policy_original_monitor_stops_before_second_action(tmp_path):
    class UnknownBackend(PublicBackend):
        def __init__(self):
            super().__init__('robocasa', 0)
            self.commands = []

        def move_base_delta(self, dx, dy, dtheta, *, frame):
            self.commands.append((dx, dy, dtheta, frame))
            super().move_base_delta(dx, dy, dtheta, frame)
            raise RuntimeError('predeclared_guidance_v1_1_unknown_after_real_base_completion')

    backend = UnknownBackend()
    monitor = SafetyMonitorBackend(backend)
    events = []
    sdk = RobocasaMobileSDK(backend, action_backend=monitor, event_sink=events.append)
    outcome = execute_formal_policy(code=POLICY.read_text(), sdk=sdk,
        context={'task_id': 'counter_to_sink', 'suite': 'robocasa',
                 'language': 'pick the boxed drink from the counter and place it in the sink',
                 'attention_input': ATTENTION},
        deadline=time.monotonic() + 5, stderr_path=tmp_path / 'worker.stderr')
    assert outcome.status == 'failed'
    assert 'independent safety monitor: action_outcome_unknown' in outcome.error
    assert outcome.call_count == 3  # Two public perception calls, one base action.
    assert backend.commands == [(.11, -.10, 0, 'local')]
    assert [v['kind'] for v in monitor.violations] == ['action_outcome_unknown']
    actions = [e for e in events if e['source'] in ['robot_sdk.base', 'robot_sdk.arm', 'robot_sdk.gripper']]
    assert len(actions) == 1 and actions[0]['status'] == 'failed'
