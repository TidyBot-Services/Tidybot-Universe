from __future__ import annotations
import sys
from pathlib import Path
from dataclasses import replace
import time

sys.path[:0] = [str(Path(__file__).resolve().parents[3]), str(Path(__file__).resolve().parents[3] / 'benchmarks/attention_harness/tests')]
from benchmarks.attention_harness.tests.test_attention_store import records
from benchmarks.attention_harness.tests.test_robot_sdk import FakeAdapter
from benchmarks.attention_harness.core.models import AttemptRecord, AttentionResponseRecord
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.attention_modes import RequestState, AssistanceMode
from benchmarks.attention_harness.ui_server import dashboard_snapshot
from tidybot_sdk import TidyBotSDK

def test_ui05_sdk_emits_explicit_relative_clock():
    events = []
    sdk = TidyBotSDK(FakeAdapter(), event_sink=events.append)
    sdk.gripper.close(settle_steps=1)
    assert events[0]['timestamp_basis'] == 'relative'

def test_waiting_window_uses_actual_dispatch_and_completed_motion_counters(tmp_path):
    store = AttentionStore(tmp_path/'store.sqlite3')
    run, attempt, trace, request = records()
    store.create_run(run); store.create_attempt(attempt); store.put_trace(trace)
    store.create_request(replace(request, mode=AssistanceMode.LIVE_HUMAN_FIRST))
    store.record_executor_dispatch(run.run_id, index=0, started_at=10.0)
    store.record_executor_completed(run.run_id, index=0, ended_at=11.0, sdk_motion_commands=3)
    store.begin_waiting_window(request.request_id, started_at=20.0)
    store.record_executor_dispatch(run.run_id, index=1, started_at=21.0)
    store.record_executor_completed(run.run_id, index=1, ended_at=21.5, sdk_motion_commands=2)
    audit = store.end_waiting_window(request.request_id, ended_at=22.0)
    assert audit['started_at'] == 20.0 and audit['ended_at'] == 22.0
    assert audit['attempt_dispatches'] == 1 and audit['sdk_motion_commands'] == 2
    assert audit['robot_actions'] is None
    assert 'unlogged' in audit['coverage']
    assert dashboard_snapshot(store, run.run_id)['waiting_windows'][request.request_id] == audit

def test_four_layer_evidence_links_commands_but_never_claims_causality_or_native_success(tmp_path):
    store = AttentionStore(tmp_path/'store.sqlite3')
    run, attempt, trace, request = records()
    store.create_run(run); store.create_attempt(attempt)
    projection = {'source_complete': True, 'omitted_event_count': 0}
    old_event = {'event_id': 'sdk-0', 'event_type': 'sdk.arm_command', 'operation': 'move_delta', 'arguments': {'dx': 0.01}, 'status': 'completed'}
    store.put_trace(replace(trace, execution_id='execution-0', events=(old_event,), projection=projection)); store.create_request(request)
    response = AttentionResponseRecord('response-1', request.request_id, 'human', '请向右移动两厘米', 5.0)
    store.transition_request(request.request_id, RequestState.ANSWERED, response=response, event_key='human-answer')
    store.record_response_use(request.request_id, response_id=response.response_id, use='guidance')
    store.create_attempt(AttemptRecord('attempt-2', run.run_id, 1, 6.0))
    store.put_trace(replace(trace, trace_id='trace-2', attempt_id='attempt-2', execution_id='execution-1', events=({**old_event, 'arguments': {'dx': 0.02}},), projection=projection))
    store.link_response_execution(request.request_id, execution_id='execution-1')
    evidence = dashboard_snapshot(store, run.run_id)['response_evidence'][request.request_id]
    assert evidence['submission']['response_id'] == response.response_id
    assert evidence['association']['attempt_id'] == 'attempt-2'
    assert evidence['control_comparison']['status'] == 'visible_commands_differ'
    assert evidence['control_comparison']['before'][0]['arguments'] == {'dx': 0.01}
    assert evidence['control_comparison']['after'][0]['arguments'] == {'dx': 0.02}
    assert evidence['causal_adoption'] == 'unconfirmed'
    assert evidence['native_outcome']['status'] == 'unconfirmed'

def test_sdk_action_wait_audit_is_recorded_by_scheduler_not_only_readonly_work(tmp_path):
    from benchmarks.attention_harness.tests.test_sim_gt_attention_run import advisor_reply
    from benchmarks.attention_harness.tests.test_robosuite_memory import FakeClient, _adapter
    from benchmarks.attention_harness.sim_gt_attention_run import run_robosuite_attention
    store_path = tmp_path/'store.sqlite3'
    def policy(sdk, context):
        if context['attention_input'].get('advisor_guidance'): sdk.gripper.close()
    result = run_robosuite_attention(task_id='cube_lift', seed=101, artifact_root=tmp_path,
        policy_id='reactive_help', robot_policy=policy,
        adapter_factory=lambda task, camera: _adapter(task, camera, client=FakeClient()),
        store_path=store_path, max_attempts=2, assistance_credits=1,
        assistance_mode=AssistanceMode.LIVE_HUMAN_FIRST, human_deadline_seconds=0.01,
        sleeper=time.sleep, advisor_transport=advisor_reply)
    store = AttentionStore(store_path)
    snapshot = dashboard_snapshot(store, f"run:{Path(result['artifact_dir']).name}")
    window = next(iter(snapshot['waiting_windows'].values()))
    assert window['ended_at'] >= window['started_at']
    assert window['attempt_dispatches'] == 0 and window['sdk_motion_commands'] == 0
    assert window['robot_actions'] is None  # physical/unlogged motion still unknown
    assert len([event for event in store.events() if event['event_type'] == 'executor.dispatched']) == 2
