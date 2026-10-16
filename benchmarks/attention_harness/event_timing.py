"""Derive private SDK completion times from one execution's control journals.

The public trace has no clock. Its rows match the ordered non-owner prefix of
owner-rpc-intents; owner observe/evaluate and later cleanup calls are excluded.
No derived field is written into public feedback.
"""
from __future__ import annotations

import json
import math
from bisect import bisect_right
from pathlib import Path


def _rows(path):
    try:
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    except FileNotFoundError:
        raise ValueError(path.name + '_missing') from None
    except (OSError, ValueError):
        raise ValueError(path.name + '_invalid') from None


def _step(value):
    return type(value) is int and value >= 0


def _unavailable(reason):
    return {'status': 'unavailable', 'reason': reason}


def _motion(intent):
    group = intent.get('group', '')
    return (not isinstance(group, str) or group == 'bimanual'
            or group.endswith(('_arm', '_gripper'))
            or intent.get('operation') not in ('get_observation', 'find_objects'))


def _completion(actions, receipt, previous):
    """Return completion sequence or a reason; intent sequence is only a start.

    Native receipt.sequence is an observed completion. An intent with only a
    start can use command_curve.step_budget, checked against the RPC response
    sequence. A rejected command with no native action inherits the last step
    only when the RPC response confirms that the step did not advance.
    """
    response = receipt['response']
    rpc_step = response.get('sequence')
    if not _step(rpc_step):
        return None, 'rpc_sequence_missing_or_invalid'
    ends = [a for a in actions if a.get('event') == 'receipt']
    starts = [a for a in actions if a.get('event') in ('intent', 'gripper_intent')]
    if len(ends) > 1 or len(starts) > 1 or len(actions) != len(ends) + len(starts):
        return None, 'native_actions_inconsistent'
    if ends:
        end = ends[0].get('sequence')
        if not _step(end) or end != rpc_step:
            return None, 'native_rpc_sequence_mismatch'
        if starts:
            start = starts[0].get('start_sequence', starts[0].get('sequence'))
            if not _step(start) or start != ends[0].get('start_sequence') or start > end:
                return None, 'native_actions_inconsistent'
        if end < previous:
            return None, 'sequence_regressed'
        return end, None
    if starts:
        action = starts[0]
        start = action.get('start_sequence', action.get('sequence'))
        curve = action.get('command_curve')
        budget = curve.get('step_budget') if isinstance(curve, dict) else None
        if not _step(start) or start != previous:
            return None, 'native_start_sequence_mismatch'
        if not _step(budget) or budget == 0:
            return None, 'native_completion_steps_missing'
        end = start + budget
        if end != rpc_step:
            return None, 'native_rpc_sequence_mismatch'
        return end, None
    if response.get('ok') is False and rpc_step == previous:
        return previous, None
    return None, 'motion_without_native_action'


def derive_event_times(execution_dir, action_trace):
    """Return one derived result per public event, failing closed on bad journals.

    A sequence maps to the last recorded frame whose physical_sequence is no
    later than it. That frame's trace_seconds is the relative completion stamp.
    """
    if not isinstance(action_trace, list):
        return [_unavailable('action_trace_invalid')]
    if not action_trace:
        return []
    root = Path(execution_dir)
    try:
        intents = _rows(root / 'owner-rpc-intents.jsonl')
        receipts = _rows(root / 'rpc-receipts.jsonl')
        actions = _rows(root / 'native-actions.jsonl')
        frames = _rows(root / 'recording_frames' / 'frames.jsonl')
        try:
            timing = json.loads((root / 'camera_recording.json').read_text())['replay_timing']
        except FileNotFoundError:
            raise ValueError('camera_recording_missing') from None
        except (OSError, ValueError, KeyError, TypeError):
            raise ValueError('camera_recording_invalid') from None
        traces = timing.get('frame_trace_seconds')
        videos = timing.get('frame_video_seconds')
        if (not isinstance(timing, dict) or timing.get('basis') != 'relative' or timing.get('event_clock') != 'sdk_completion'
                or not isinstance(traces, list) or not isinstance(videos, list)
                or len(traces) != len(frames) or len(videos) != len(frames) or not frames):
            raise ValueError('recording_time_mapping_invalid')
        frame_steps = []
        for i, frame in enumerate(frames):
            if not isinstance(frame, dict):
                raise ValueError('recording_time_mapping_invalid')
            step, stamp = frame.get('physical_sequence'), frame.get('trace_seconds')
            if (not _step(step) or (i and step <= frame_steps[-1])
                    or type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp < 0
                    or stamp != traces[i] or type(videos[i]) not in (int, float)
                    or not math.isfinite(videos[i]) or videos[i] < 0
                    or (i and (stamp <= traces[i - 1] or videos[i] <= videos[i - 1]))):
                raise ValueError('recording_time_mapping_invalid')
            frame_steps.append(step)
        if any(not isinstance(row, dict) or not isinstance(row.get('request_id'), str)
               or not isinstance(row.get('operation'), str) or not isinstance(row.get('group'), str)
               for row in intents):
            raise ValueError('owner_intents_invalid')
        visible = [row for row in intents if row.get('group') != 'owner']
        by_receipt = {}
        for row in receipts:
            if not isinstance(row, dict) or not isinstance(row.get('request'), dict) or not isinstance(row.get('response'), dict):
                raise ValueError('rpc_receipts_invalid')
            rid = row['request'].get('request_id')
            if not isinstance(rid, str) or rid in by_receipt:
                raise ValueError('rpc_receipts_invalid')
            by_receipt[rid] = row
        by_action = {}
        for row in actions:
            if not isinstance(row, dict) or not isinstance(row.get('request_id'), str):
                raise ValueError('native_actions_invalid')
            by_action.setdefault(row['request_id'], []).append(row)
    except ValueError as exc:
        return [_unavailable(str(exc)) for _ in action_trace]

    results = []
    previous = 0
    unknown_motion = False
    alignment_broken = False
    seen = set()
    for i, event in enumerate(action_trace):
        if alignment_broken:
            results.append(_unavailable('prior_event_alignment_unavailable'))
            continue
        if not isinstance(event, dict) or i >= len(visible):
            results.append(_unavailable('event_intent_missing'))
            alignment_broken = True
            continue
        intent = visible[i]
        rid = intent.get('request_id')
        if (not isinstance(rid, str) or rid in seen or event.get('operation') != intent.get('operation')
                or event.get('source') != intent.get('group')):
            results.append(_unavailable('event_intent_mismatch'))
            alignment_broken = True
            continue
        seen.add(rid)
        receipt = by_receipt.get(rid)
        if (receipt is None or receipt['request'].get('operation') != intent.get('operation')
                or receipt['request'].get('group') != intent.get('group')
                or receipt['response'].get('request_id') != rid):
            results.append(_unavailable('rpc_receipt_missing_or_mismatch'))
            alignment_broken = True
            continue
        response = receipt['response']
        if (event.get('status') not in ('completed', 'failed') or type(response.get('ok')) is not bool
                or (event['status'] == 'completed') != response['ok']):
            results.append(_unavailable('event_receipt_status_mismatch'))
            alignment_broken = True
            continue
        if unknown_motion:
            results.append(_unavailable('prior_motion_unavailable'))
            continue
        related = by_action.get(rid, [])
        if related and not _motion(intent):
            results.append(_unavailable('nonmotion_native_action'))
            alignment_broken = True
            continue
        if _motion(intent):
            step, reason = _completion(related, receipt, previous)
            if reason:
                unknown_motion = True
                results.append(_unavailable(reason))
                continue
        else:
            step = previous
            if not _step(response.get('sequence')) or response['sequence'] != step:
                results.append(_unavailable('nonmotion_sequence_mismatch'))
                alignment_broken = True
                continue
        index = bisect_right(frame_steps, step) - 1
        if index < 0:
            results.append(_unavailable('frame_before_sequence_missing'))
            alignment_broken = True
            continue
        previous = step
        results.append({'status': 'derived', 'physical_sequence': step,
                        'timestamp': frames[index]['trace_seconds'], 'timestamp_basis': 'relative'})
    return results
