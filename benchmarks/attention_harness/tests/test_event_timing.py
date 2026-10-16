"""Offline journal timing tests using trimmed, real Dev v2 executions."""
import copy
import json
import shutil
from pathlib import Path

import pytest

from benchmarks.attention_harness.dev_v2_loop import events_for_moment_selection
from benchmarks.attention_harness.event_timing import derive_event_times
from benchmarks.attention_harness.moment_localization import select_moments


FIXTURES = Path(__file__).parent / 'fixtures' / 'event_timing'


def case(name):
    root = FIXTURES / name
    return root, json.loads((root / 'public-feedback.json').read_text())['action_trace']


@pytest.mark.parametrize('name,steps', [
    ('intent_failed', [0, 0, 0, 20, 20, 61]),
    ('rejected_after_gripper', [0, 0, 20, 20]),
    ('rejected_right', [0, 20, 20, 20]),
])
def test_real_journals_derive_completion_steps_and_targeted_selection(name, steps):
    root, trace = case(name)
    original = copy.deepcopy(trace)
    derived = derive_event_times(root, trace)
    assert [row['physical_sequence'] for row in derived] == steps
    frames = [json.loads(line) for line in (root / 'recording_frames' / 'frames.jsonl').read_text().splitlines()]
    for result in derived:
        preceding = [frame for frame in frames if frame['physical_sequence'] <= result['physical_sequence']]
        assert result['timestamp_basis'] == 'relative'
        assert result['timestamp'] == preceding[-1]['trace_seconds']
    events = events_for_moment_selection(root, trace, 1)
    assert trace == original  # the public packet is never enriched
    assert all('timestamp' not in row for row in trace)
    assert [event['physical_sequence'] for event in events] == steps
    timing = json.loads((root / 'camera_recording.json').read_text())['replay_timing']
    selected, localization = select_moments(events, [events[-1]['event_id']], timing, available_views=('external',))
    assert localization['status'] == 'targeted'
    assert selected[-1]['role'] == 'after'
    assert selected[-1]['frame_index'] == steps[-1]


def test_owner_calls_and_cleanup_are_not_public_events():
    root, trace = case('rejected_after_gripper')
    intents = [json.loads(line) for line in (root / 'owner-rpc-intents.jsonl').read_text().splitlines()]
    assert len(intents) > len(trace)
    assert any(row['group'] == 'owner' for row in intents[len(trace):])
    assert [row['status'] for row in derive_event_times(root, trace)] == ['derived'] * len(trace)


def test_missing_journal_and_recording_fall_back(tmp_path):
    source, trace = case('intent_failed')
    for filename, reason in [
        ('owner-rpc-intents.jsonl', 'owner-rpc-intents.jsonl_missing'),
        ('rpc-receipts.jsonl', 'rpc-receipts.jsonl_missing'),
        ('native-actions.jsonl', 'native-actions.jsonl_missing'),
        ('recording_frames/frames.jsonl', 'frames.jsonl_missing'),
        ('camera_recording.json', 'camera_recording_missing'),
    ]:
        root = tmp_path / filename.replace('/', '_')
        shutil.copytree(source, root)
        (root / filename).unlink()
        assert derive_event_times(root, trace)[0] == {'status': 'unavailable', 'reason': reason}
    root, trace = case('rejected_no_recording')
    assert derive_event_times(root, trace)[-1]['reason'] == 'native-actions.jsonl_missing'


def test_intent_step_budget_is_required_and_checked(tmp_path):
    source, trace = case('intent_failed')
    root = tmp_path / 'case'
    shutil.copytree(source, root)
    path = root / 'native-actions.jsonl'
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    intent = next(row for row in rows if row['event'] == 'intent')
    intent['command_curve'].pop('step_budget')
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    assert derive_event_times(root, trace)[-1]['reason'] == 'native_completion_steps_missing'
    intent['command_curve']['step_budget'] = 40
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    assert derive_event_times(root, trace)[-1]['reason'] == 'native_rpc_sequence_mismatch'


@pytest.mark.parametrize('change,reason', [
    ('event', 'event_intent_mismatch'),
    ('status', 'event_receipt_status_mismatch'),
    ('receipt', 'rpc_receipt_missing_or_mismatch'),
    ('frame', 'recording_time_mapping_invalid'),
])
def test_inconsistencies_are_unavailable(tmp_path, change, reason):
    source, trace = case('rejected_after_gripper')
    root = tmp_path / 'case'
    shutil.copytree(source, root)
    trace = copy.deepcopy(trace)
    if change == 'event':
        trace[0]['operation'] = 'move_delta'
    elif change == 'status':
        trace[0]['status'] = 'failed'
    else:
        path = root / ('rpc-receipts.jsonl' if change == 'receipt' else 'recording_frames/frames.jsonl')
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        if change == 'receipt':
            rows[0]['response']['request_id'] = 'wrong'
        else:
            rows[0]['trace_seconds'] += 1
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    result = derive_event_times(root, trace)
    assert result[0]['status'] == 'unavailable' and result[0]['reason'] == reason
    if change in ('event', 'status', 'receipt'):
        assert result[1]['reason'] == 'prior_event_alignment_unavailable'


def test_selection_fallback_carries_derivation_reason(tmp_path):
    root, trace = case('rejected_after_gripper')
    copy_root = tmp_path / 'case'
    shutil.copytree(root, copy_root)
    (copy_root / 'native-actions.jsonl').unlink()
    events = events_for_moment_selection(copy_root, trace, 1)
    timing = json.loads((copy_root / 'camera_recording.json').read_text())['replay_timing']
    moments, status = select_moments(events, ['v1:3'], timing, available_views=('external',))
    assert moments == []
    assert status == {'status': 'generic_fallback', 'reason': 'v1:3:native-actions.jsonl_missing'}


def test_existing_public_relative_timestamp_survives_missing_journals(tmp_path):
    root, trace = case('rejected_after_gripper')
    copy_root = tmp_path / 'case'
    shutil.copytree(root, copy_root)
    (copy_root / 'native-actions.jsonl').unlink()
    trace = copy.deepcopy(trace)
    trace[-1]['timestamp'], trace[-1]['timestamp_basis'] = 1.0, 'relative'
    events = events_for_moment_selection(copy_root, trace, 1)
    assert events[-1]['timestamp'] == 1.0
    assert events[-1]['timestamp_basis'] == 'relative'
    assert 'event_timing_reason' not in events[-1]


def test_derived_step_wins_over_wall_clock_stamp_after_the_last_frame():
    """A rejected call can finish long after the last frame while the scene is unchanged since that frame."""
    root, trace = case('wallclock_after_last_frame')
    timing = json.loads((root / 'camera_recording.json').read_text())['replay_timing']
    assert trace[-1]['timestamp'] > timing['frame_trace_seconds'][-1] + 10  # outside the sampled range by its own stamp
    events = events_for_moment_selection(root, trace, 1)
    assert events[-1]['physical_sequence'] == 20
    assert events[-1]['timestamp'] == timing['frame_trace_seconds'][-1]
    selected, localization = select_moments(events, [events[-1]['event_id']], timing, available_views=('external',))
    assert localization['status'] == 'targeted'
    assert selected[-1]['role'] == 'after' and selected[-1]['frame_index'] == 20
