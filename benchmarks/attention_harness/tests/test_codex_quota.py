import json
import os
import subprocess
import sys

from benchmarks.attention_harness.codex_quota import read_quota, run_quota_usage
from benchmarks.attention_harness import ui_server
from benchmarks.attention_harness.tests.test_daily_bridge_compatibility import OfflineServer, request


def test_missing_and_garbled_quota(tmp_path):
    assert all(value is None for value in read_quota(tmp_path).values())
    path=tmp_path/'.codex/sessions/2026/10/20/rollout-a.jsonl'
    path.parent.mkdir(parents=True)
    path.write_text('garbled\n{}\n')
    assert all(value is None for value in read_quota(tmp_path).values())


def test_newest_rate_limits_wins(tmp_path):
    root=tmp_path/'.codex/sessions/2026/10/20';root.mkdir(parents=True)
    for name,stamp,used in [('a','2026-10-20T01:00:00Z',8),('b','2026-10-20T02:00:00Z',12)]:
        (root/f'rollout-{name}.jsonl').write_text(json.dumps({'timestamp':stamp,'type':'event_msg','payload':{
            'type':'token_count','rate_limits':{'primary':{'used_percent':used,'window_minutes':10080,'resets_at':123},
            'plan_type':'prolite','credits':{'balance':3}}}})+'\n')
    assert read_quota(tmp_path)=={'used_percent':12,'window_minutes':10080,'resets_at':123,'plan_type':'prolite','credits_balance':3}


def test_cli_prints_json_without_sessions(tmp_path):
    result=subprocess.run([sys.executable,'-m','benchmarks.attention_harness.codex_quota'],
                          env={**os.environ,'HOME':str(tmp_path)},capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)['used_percent'] is None


def test_public_quota_route_is_cached_and_never_leaks_credits(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setattr(ui_server, 'ThreadingHTTPServer', OfflineServer)
    clock = [100.0]
    monkeypatch.setattr(ui_server.time, 'monotonic', lambda: clock[0])
    server = ui_server.create_server(port=0)
    def get():
        status, body, _ = request(server, '/api/codex-quota')
        assert status == 200
        return json.loads(body)
    assert get() == {'available': False}
    root = tmp_path/'.codex/sessions/2026/10/20'
    root.mkdir(parents=True)
    path = root/'rollout-a.jsonl'
    path.write_text('garbled\n')
    clock[0] += 11
    assert get() == {'available': False}
    row = {'timestamp': '2026-10-20T02:00:00Z', 'payload': {'rate_limits': {
        'primary': {'used_percent': 72, 'window_minutes': 10080, 'resets_at': 1793200000},
        'plan_type': 'pro', 'credits': {'balance': 12345}, 'account_id': 'private-id'}}}
    path.write_text('garbled\n'+json.dumps(row)+'\n')
    assert get() == {'available': False}
    clock[0] += 11
    value = get()
    assert set(value) == {'used_percent', 'window_minutes', 'resets_at', 'plan_type', 'read_at'}
    assert value['used_percent'] == 72
    assert value['window_minutes'] == 10080
    assert value['plan_type'] == 'pro'
    assert '12345' not in json.dumps(value)
    assert 'private-id' not in json.dumps(value)
    assert str(tmp_path) not in json.dumps(value)


def test_run_quota_usage_uses_first_and_last_codex_marks_only():
    records = {
        'v1': {'model_source': 'codex', 'quota_before': {'timestamp': '2026-10-20T01:00:00Z', 'used_percent': 12},
               'quota_after': {'timestamp': '2026-10-20T01:01:00Z', 'used_percent': 13}},
        'v2': {'model_source': 'codex', 'quota_before': {'timestamp': '2026-10-20T02:00:00Z', 'used_percent': 14},
               'quota_after': {'timestamp': '2026-10-20T02:01:00Z', 'used_percent': 15}},
        'parcc': {'model_source': 'parcc', 'quota_before': {'timestamp': '2026-10-20T00:00:00Z', 'used_percent': 1}}}
    cell = {'versions': [{'model_output': 'v2'}, {'model_output': 'v1'}],
            'jobs': [{'receipt': {'response': 'v1', 'advisor_answer': 'parcc'}}]}
    assert run_quota_usage(cell, records.get) == {'before': 12, 'after': 15}
    assert run_quota_usage({'versions': [{'model_output': 'parcc'}]}, records.get) is None
    records['early'] = {'model_source': 'codex', 'quota_after': {'timestamp': '2026-10-19T01:00:00Z', 'used_percent': 8}}
    assert run_quota_usage({'versions': [{'model_output': 'early'}, {'model_output': 'v1'}]}, records.get) is None
