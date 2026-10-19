"""Deterministic, model-only projection of oversized public SDK feedback.

The v3 runtime bound counts UTF-8 message contents plus 1024 bytes of protocol
headroom. We additionally leave 512 bytes unused when a projection is needed.
The caller applies this to every runtime-constrained v3 policy, independent of
its assistance choice. The source feedback receipt stays intact for Eval,
Advisor, and audit. ``removed_bytes`` measures the compact feedback payload
before the truncation metadata itself is added.
"""
import copy
import json


RESERVE_BYTES = 1024
PROJECTION_MARGIN_BYTES = 512
ROBOT_SUFFIXES = ('eef_pos', 'eef_quat', 'gripper_qpos', 'base_pos',
                  'base_quat', 'base_to_eef_pos', 'base_to_eef_quat')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode()


def input_bound(messages):
    return sum(len(message['content'].encode()) for message in messages) + RESERVE_BYTES


def _rounded(value):
    # Public spatial and gripper vectors use four decimal places (0.1 mm for m).
    if type(value) is float:
        return round(value, 4)
    if isinstance(value, list):
        return [_rounded(item) for item in value]
    return value


def _observation(value):
    if not isinstance(value, dict):
        return value
    return {key: _rounded(item) for key, item in value.items()
            if key.startswith('robot0_') and key.endswith(ROBOT_SUFFIXES)}


def _scene_observation(value):
    if not isinstance(value, dict):
        return value
    projected = {}
    if isinstance(value.get('robot'), dict):
        projected['robot'] = _observation(value['robot'])
    if isinstance(value.get('objects'), dict):
        projected['objects'] = {key: _rounded(item) for key, item in value['objects'].items()
                                if key.endswith('_pos') and isinstance(item, list) and len(item) == 3}
    return projected


def _compact(feedback):
    result = copy.deepcopy(feedback)
    for key in ('initial_observation', 'final_observation'):
        if key in result:
            result[key] = _scene_observation(result[key])
    for event in result.get('action_trace', []):
        if 'observation' in event:
            event['observation'] = _observation(event['observation'])
        if 'objects' in event and isinstance(event['objects'], list):
            event['objects'] = [{**obj, 'position': _rounded(obj['position'])}
                                if isinstance(obj, dict) and 'position' in obj else obj
                                for obj in event['objects']]
    return result


def project_messages(messages, max_input_bytes):
    """Return messages and audit data; leave fitting requests byte-for-byte intact.

    First reduce observation precision and discard joint arrays. Then try K
    from ceil(N/2) down to zero, keeping the first K, last K, and every failed
    or error-bearing event in original order. The first fitting K wins.
    """
    before = input_bound(messages)
    if before <= max_input_bytes:
        return messages, None
    packet = json.loads(messages[-1]['content'])
    original = packet['public_feedback']
    compact = _compact(original)
    events = compact.get('action_trace', [])
    original_size = len(encoded(original))
    target = max_input_bytes - PROJECTION_MARGIN_BYTES
    for k in range((len(events) + 1) // 2, -1, -1):
        indices = {i for i, event in enumerate(events)
                   if i < k or i >= len(events) - k or event.get('status') == 'failed' or event.get('error')}
        projected = {**compact, 'action_trace': [event for i, event in enumerate(events) if i in indices]}
        omitted = len(events) - len(indices)
        # Count bytes removed from the feedback payload before adding metadata.
        removed = original_size - len(encoded(projected))
        projected['truncation'] = {'message': f'{omitted} events omitted',
                                   'omitted_events': omitted, 'removed_bytes': removed,
                                   'head_tail_k': k}
        candidate = list(messages)
        candidate[-1] = {**candidate[-1], 'content': encoded({**packet, 'public_feedback': projected}).decode()}
        bound = input_bound(candidate)
        if bound <= target:
            return candidate, {'schema_version': 'attentionbench.dev-feedback-projection.v1',
                               'original_feedback_bytes': original_size,
                               'projected_feedback_bytes': len(encoded(projected)),
                               'input_bound_before': before, 'input_bound_after': bound,
                               'max_input_bytes': max_input_bytes,
                               'margin_bytes': max_input_bytes - bound,
                               'public_feedback': projected}
    # Other fields or mandatory failure events can alone exceed the bound.
    return messages, None
