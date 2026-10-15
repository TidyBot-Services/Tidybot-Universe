from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(os.environ.get('ATTENTION_REPO', Path(__file__).resolve().parents[3]))
sys.path[:0] = [str(REPO), str(REPO / 'benchmarks/attention_harness/tests'), str(REPO / 'skill-agent-setup/claude-code')]
from benchmarks.attention_harness.attention_modes import AssistanceMode, RequestState
from benchmarks.attention_harness.core.models import AttemptRecord, RunStatus
from benchmarks.attention_harness.core.runtime import AttentionRuntime
from benchmarks.attention_harness.core.store import AttentionStore, StateConflictError
from benchmarks.attention_harness.tests.test_attention_store import records
from benchmarks.attention_harness.ui_server import dashboard_snapshot
from benchmarks.attention_harness.formal_attention_run import run_formal_attention
from test_formal_attention_chain import FakeFormalRunner
from attention_eval import build_eval_packet


def test_ui01_launch_binding_exists_before_run_finishes_and_checks_conditions(tmp_path):
    store = AttentionStore(tmp_path / 'attention.sqlite3')
    run, _, _, _ = records()
    locked = {'suite': run.suite, 'task': run.task_id, 'seed': run.seed,
              'attention_policy': run.policy_id, 'assistance_mode': run.assistance_mode.value,
              'execution_target': run.execution_target}
    store.record_launch('launch-1', locked=locked, log_path=str(tmp_path / 'launch.log'))
    store.create_run(run)
    store.bind_launch_run('launch-1', run.run_id)
    store.bind_launch_run('launch-1', run.run_id)
    assert store.list_launches()[0]['run_id'] == run.run_id
    assert store.list_launches()[0]['state'] == 'created'
    store.create_run(replace(run, run_id='run-2', seed=102))
    with pytest.raises(StateConflictError):
        store.bind_launch_run('launch-1', 'run-2')


@pytest.mark.parametrize('terminal', [RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.COMPLETED])
def test_ui04_terminal_releases_failed_fallback_and_preserves_committed_credit(tmp_path, terminal):
    store = AttentionStore(tmp_path / 'attention.sqlite3')
    run, attempt, trace, request = records()
    store.create_run(replace(run, assistance_mode=AssistanceMode.LIVE_HUMAN_FIRST))
    store.transition_run(run.run_id, RunStatus.RUNNING, event_key='started')
    store.create_attempt(attempt); store.put_trace(trace)
    proxy = SimpleNamespace(answer=lambda **_: SimpleNamespace(total_tokens=2000))
    runtime = AttentionRuntime(store, proxy, clock=lambda: 20.0)
    runtime.open_request(replace(request, mode=AssistanceMode.LIVE_HUMAN_FIRST, deadline_at=10.0))
    store.reserve_assistance(run.run_id, credits=1, reservation_id='already-used')
    store.settle_assistance('already-used', commit=True)
    with pytest.raises(StateConflictError, match='token budget exhausted'):
        runtime.handle_deadline(request.request_id)
    assert store.budget_status(run.run_id)['reserved'] == 1  # transient retry still possible
    store.transition_run(run.run_id, terminal, event_key='terminal')
    budget = store.budget_status(run.run_id)
    assert budget == {'limit': 2, 'used': 1, 'reserved': 0, 'remaining': 1}
    assert store.get_request(request.request_id).state is RequestState.CANCELLED
    store.transition_run(run.run_id, terminal, event_key='terminal')
    assert store.budget_status(run.run_id) == budget


def test_ui06_latest_trace_without_request_is_visible_and_attempt_bound(tmp_path):
    store = AttentionStore(tmp_path / 'attention.sqlite3')
    run, attempt, trace, request = records()
    store.create_run(run); store.create_attempt(attempt)
    store.put_trace(replace(trace, execution_id='execution-old')); store.create_request(request)
    store.create_attempt(AttemptRecord('attempt-2', run.run_id, 1, 4.0))
    store.put_trace(replace(trace, trace_id='trace-2', attempt_id='attempt-2', execution_id='execution-new'))
    snapshot = dashboard_snapshot(store, run.run_id)
    assert snapshot['station']['latest_execution'] == {'attempt_id': 'attempt-2', 'execution_id': 'execution-new'}
    assert snapshot['traces']['trace-2']['execution_id'] == 'execution-new'


@pytest.mark.parametrize('count', [8, 10])
def test_ui07_many_valid_formal_attempts_fit_bounded_packet_without_losing_identity(tmp_path, count):
    class DetailedRunner(FakeFormalRunner):
        def execute(self, request):
            result = super().execute(request)
            ref = result['artifacts']['trace']; trace_path = Path(ref['uri'])
            trace = json.loads(trace_path.read_text())
            trace['sdk_events'] = [{'event_id': f'sdk-{i}', 'event_type': 'robot.action', 'operation': 'move_to_pose', 'status': 'failed', 'error': 'missed grasp ' * 40} for i in range(20)]
            trace_path.write_text(json.dumps(trace)); ref['sha256'] = hashlib.sha256(trace_path.read_bytes()).hexdigest()
            return result
    code = tmp_path / 'policy.py'; code.write_text('pass\n')
    config = tmp_path / 'config.json'; config.write_text('{}')
    summary = run_formal_attention(suite='robosuite', task_id='cube_lift', seed=101,
        artifact_root=tmp_path / 'runs', policy_id='autonomous', policy_code_path=code,
        approved_policy_sha256=hashlib.sha256(code.read_bytes()).hexdigest(),
        config_path=config, approved_config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
        runner=DetailedRunner('robosuite'), max_attempts=count, assistance_credits=0)
    artifact = Path(summary['artifact_dir']) / 'attention_run.json'
    packet = build_eval_packet(artifact)
    assert len(json.dumps(packet, ensure_ascii=False)) <= 12_000
    assert len(packet['attempts']) == count
    assert packet['native_success'] is False
    assert all(any(event['event_id'] == 'formal-native-result' for event in attempt['events']) for attempt in packet['attempts'])
    from benchmarks.attention_harness.ui_eval import eval_snapshot
    store = AttentionStore(Path(summary['store']))
    run_id = packet['run_id']
    ui = eval_snapshot(tmp_path/'runs', run_id, store.get_run(run_id), store.path)
    assert ui['status'] == 'pending' and ui['native_success'] is False
    assert ui['approved_config_sha256'] == summary['approved_config_sha256']
    assert len(ui['native_outcomes']) == count
    last_ref = summary['attempts'][-1]['formal_runner_result']['artifacts']['trace']
    Path(last_ref['uri']).write_text('{}')
    with pytest.raises(ValueError, match='digest mismatch'):
        build_eval_packet(artifact)


def test_ui03_actual_server_restart_keeps_auth_rejects_old_token_and_duplicate(tmp_path):
    import re
    import threading
    import time
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    from benchmarks.attention_harness.ui_server import create_server
    store = AttentionStore(tmp_path/'auth.sqlite3')
    run, attempt, trace, request = records()
    store.create_run(replace(run, assistance_mode=AssistanceMode.LIVE_HUMAN_FIRST))
    store.create_attempt(attempt); store.put_trace(trace)
    AttentionRuntime(store, None, clock=time.time).open_request(replace(request, mode=AssistanceMode.LIVE_HUMAN_FIRST, deadline_at=time.time()+60))
    old = None
    for iteration in range(2):
        server = create_server(store=store, port=0)
        thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        def respond(token):
            return urlopen(Request(base+f'/api/requests/{request.request_id}/respond', data=json.dumps({'content':'普通中文建议'}).encode(), method='POST',headers={'Content-Type':'application/json','X-Attention-Token':token}))
        try:
            with urlopen(base+'/ui/') as response:
                token=re.search('data-control-token="([^"]+)"',response.read().decode()).group(1)
            if iteration == 0: old=token; continue
            assert old != token
            for invalid in ('',old):
                with pytest.raises(HTTPError) as caught: respond(invalid)
                assert caught.value.code == 403
            assert store.get_request(request.request_id).state is RequestState.PENDING
            with respond(token) as response: assert json.load(response)['state']=='answered'
            with pytest.raises(HTTPError) as duplicate: respond(token)
            assert duplicate.value.code == 409
            assert store.budget_status(run.run_id)['used']==1
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)
