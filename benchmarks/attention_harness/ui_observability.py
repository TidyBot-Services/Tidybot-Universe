"""Evidence-only operator projections; never infer causality or physical motion."""
from __future__ import annotations
import json
from typing import Any

MOTION_EVENT_TYPES = {"sdk.arm_command", "sdk.gripper_command", "sdk.base_command", "sdk.handoff"}

def response_evidence(requests, responses, uses, traces, attempts, native_outcomes):
    indexes = {item['attempt_id']: item['index'] for item in attempts}
    evidence = {}
    for request in requests:
        response = responses.get(request.get('response_id'))
        use = uses.get(request['request_id'], {})
        old = traces.get(request['trace_id'])
        candidates = [trace for trace in traces.values() if use.get('execution_id')
                      and trace.get('execution_id') == use['execution_id']
                      and indexes.get(trace['attempt_id'], -1) > indexes.get(request['attempt_id'], -1)]
        linked = candidates[0] if len(candidates) == 1 and response and use.get('response_id') == response['response_id'] else None
        def controls(trace):
            if not trace: return []
            return [{key: event.get(key) for key in ('event_id', 'operation', 'arguments', 'status')}
                    for event in trace.get('events', []) if event.get('event_type') in MOTION_EVENT_TYPES]
        before, after = controls(old), controls(linked)
        comparison = 'unconfirmed'
        if linked and (before or after) and all(isinstance(item['arguments'], dict) for item in before + after):
            signature = lambda values: json.dumps([{key: value[key] for key in ('operation', 'arguments', 'status')} for value in values], sort_keys=True)
            comparison = 'visible_commands_differ' if signature(before) != signature(after) else 'visible_commands_unchanged'
        native = native_outcomes.get(linked['attempt_id'], {}) if linked else {}
        evidence[request['request_id']] = {
            'submission': {'status': 'submitted' if response else 'unconfirmed', 'response_id': response['response_id'] if response else None},
            'association': {'status': 'linked' if linked else 'unconfirmed', 'attempt_id': linked['attempt_id'] if linked else None, 'execution_id': linked.get('execution_id') if linked else None},
            'control_comparison': {'status': comparison, 'before': before, 'after': after, 'coverage': 'Advisor-visible SDK command projections; physical motion and causal attribution unconfirmed'},
            'causal_adoption': 'unconfirmed',
            'native_outcome': {'status': 'verified' if native.get('evaluated') is True else 'unconfirmed', 'attempt_id': linked['attempt_id'] if linked else None, 'value': native.get('native_success') if native.get('evaluated') is True else None},
            'trace_digests': {'before': (old or {}).get('projection', {}), 'after': (linked or {}).get('projection', {})},
        }
    return evidence
