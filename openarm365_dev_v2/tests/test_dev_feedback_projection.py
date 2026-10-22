"""Public-only regression fixture from devexp-300fde571a6aa9eda71e234b v1."""
import copy
import json
from pathlib import Path

from benchmarks.attention_harness.dev_feedback_projection import encoded, input_bound, project_messages
from benchmarks.attention_harness.dev_v2_loop import immutable, model_messages


FIXTURE = Path(__file__).parent / 'fixtures' / 'real_v1_public_feedback.json'
CONFIG = dict(task_id='pick_place_counter_to_sink', official_env='PickPlaceCounterToSink',
              task_prompt='Pick the object from the counter and place it in the sink.',
              seed=102, robot='OpenArm365', execution_arm='both', controller='BASIC',
              layout_id=11, style_id=34, scene_id='robocasa365:layout11:style34',
              horizon=9600, sdk_call_limit=320, attempt_wall_seconds=1920,
              perception_mode='sim_gt')


def messages(feedback, hint=''):
    return model_messages(CONFIG, 'from robot_sdk import sensors\n' + '# previous source\n' * 130,
                          feedback, {'action': 'continue', 'help': {'content': hint},
                                     'memory': [{'content': 'Keep a bounded base transform.'}]},
                          {'dev_calls': 3, 'input_tokens': 50000}, True, 12000)


def test_real_feedback_fits_is_deterministic_and_preserves_failure():
    feedback = json.loads(FIXTURE.read_bytes())
    assert FIXTURE.stat().st_size >= 40000 and len(feedback['action_trace']) == 40
    before = messages(feedback)
    assert input_bound(before) > 30000
    first, audit = project_messages(before, 30000)
    second, again = project_messages(before, 30000)
    assert first == second and audit == again
    assert input_bound(first) <= 30000 - 512
    shown = json.loads(first[-1]['content'])['public_feedback']
    assert shown['execution_status'] == feedback['execution_status']
    assert shown['public_error'] == feedback['public_error']
    assert feedback['action_trace'][-1]['error'] == shown['action_trace'][-1]['error']
    assert audit['public_feedback'] == shown
    assert audit['input_bound_after'] == input_bound(first)
    assert audit['margin_bytes'] == 30000 - input_bound(first)
    assert shown['truncation']['removed_bytes'] == len(encoded(feedback)) - len(encoded({k: v for k, v in shown.items() if k != 'truncation'}))
    assert shown['truncation']['omitted_events'] == 40 - len(shown['action_trace'])
    assert shown['truncation']['message'] == f"{shown['truncation']['omitted_events']} events omitted"
    observation = next(e['observation'] for e in shown['action_trace'] if 'observation' in e)
    assert 'robot0_right_eef_pos' in observation and 'robot0_right_gripper_qpos' in observation
    assert not any(key.endswith('joint_pos') for key in observation)


def test_head_tail_and_middle_failure_survive_tighter_payload():
    feedback = json.loads(FIXTURE.read_bytes())
    feedback['action_trace'][20]['status'] = 'failed'
    feedback['action_trace'][20]['error'] = {'message': 'middle failure', 'type': 'RuntimeError'}
    before = messages(feedback, 'bounded hint ' * 700)
    projected, audit = project_messages(before, 30000)
    assert audit is not None and input_bound(projected) <= 29488
    trace = json.loads(projected[-1]['content'])['public_feedback']['action_trace']
    truncation = audit['public_feedback']['truncation']
    assert truncation['omitted_events'] > 0
    assert trace[0]['timestamp'] == feedback['action_trace'][0]['timestamp']
    assert trace[-1]['error'] == feedback['action_trace'][-1]['error']
    assert any(e.get('error', {}).get('message') == 'middle failure' for e in trace)
    k = truncation['head_tail_k']
    assert [e['timestamp'] for e in trace[:k]] == [e['timestamp'] for e in feedback['action_trace'][:k]]
    assert [e['timestamp'] for e in trace[-k:]] == [e['timestamp'] for e in feedback['action_trace'][-k:]]


def test_fitting_feedback_and_legacy_model_bytes_are_unchanged():
    feedback = json.loads(FIXTURE.read_bytes())
    small = {**feedback, 'action_trace': feedback['action_trace'][:2]}
    legacy = messages(small)
    original_bytes = [m['content'].encode() for m in legacy]
    projected, audit = project_messages(legacy, 30000)
    assert projected is legacy and audit is None
    assert [m['content'].encode() for m in projected] == original_bytes
    assert 'truncation' not in json.loads(projected[-1]['content'])['public_feedback']
    # v2/legacy callers never invoke project_messages; model_messages stays exact.
    assert [m['content'].encode() for m in messages(copy.deepcopy(small))] == original_bytes


def test_projection_does_not_add_eval_suggestion_or_private_evidence():
    feedback = json.loads(FIXTURE.read_bytes())
    feedback['eval_review'] = {'passed': False, 'reason': 'The public trace ends in a planning error.',
                               'reference_suggestion': 'REPAIR_SUGGESTION_SENTINEL'}
    projected, audit = project_messages(messages(feedback), 30000)
    assert audit is not None
    wire = ''.join(message['content'] for message in projected)
    assert 'REPAIR_SUGGESTION_SENTINEL' not in wire
    assert 'reference_suggestion' not in wire
    assert 'native_result' not in wire and 'oracle' not in wire


def test_projection_audit_can_be_recovered_immutably(tmp_path):
    feedback = json.loads(FIXTURE.read_bytes())
    _, audit = project_messages(messages(feedback), 30000)
    path = tmp_path / 'v2' / 'dev-public-feedback-projection.json'
    ref = immutable(path, audit)
    assert immutable(path, audit) == ref
    assert json.loads(path.read_bytes()) == audit


def test_inspect_trace_copy_gets_the_same_projection():
    # v9 of devexp-14a2a2eab120adc4fd9b94c8 stopped because inspect_trace repeated
    # the whole trace in attention_control and only the feedback copy was projected.
    feedback = json.loads(FIXTURE.read_bytes())
    attention = {'action': 'inspect_trace', 'reason': 'trace_needs_inspection',
                 'public_trace_summary': copy.deepcopy(feedback['action_trace'])}
    before = model_messages(CONFIG, 'from robot_sdk import sensors\n' + '# previous source\n' * 130,
                            feedback, attention, {'dev_calls': 3}, True, 12000)
    assert input_bound(before) > 2 * len(encoded(feedback['action_trace']))
    projected, audit = project_messages(before, 30000)
    assert audit is not None and input_bound(projected) <= 30000 - 512
    assert project_messages(before, 30000) == (projected, audit)
    packet = json.loads(projected[-1]['content'])
    trace = packet['public_feedback']['action_trace']
    assert packet['attention_control']['public_trace_summary'] == trace
    assert audit['trace_summary_events'] == len(trace)
    assert trace[-1]['error'] == feedback['action_trace'][-1]['error']


def test_too_many_failures_keep_the_first_and_last_failures():
    feedback = json.loads(FIXTURE.read_bytes())
    for i, event in enumerate(feedback['action_trace']):
        event['status'] = 'failed'
        event['error'] = {'message': f'failure {i:02} ' + 'x' * 600, 'type': 'RuntimeError'}
    before = messages(feedback)
    projected, audit = project_messages(before, 30000)
    assert audit is not None and input_bound(projected) <= 30000 - 512
    assert project_messages(before, 30000) == (projected, audit)
    shown = audit['public_feedback']
    trace, truncation = shown['action_trace'], shown['truncation']
    assert truncation['head_tail_k'] == 0 and truncation['omitted_failure_events'] > 0
    assert trace[0]['error'] == feedback['action_trace'][0]['error']
    assert trace[-1]['error'] == feedback['action_trace'][-1]['error']
    assert truncation['omitted_events'] == 40 - len(trace) == truncation['omitted_failure_events']
    assert truncation['message'] == f"{truncation['omitted_events']} events omitted, including {truncation['omitted_failure_events']} failed events"
