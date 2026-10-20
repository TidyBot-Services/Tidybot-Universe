import json
import os
import subprocess
import sys

from benchmarks.attention_harness.codex_quota import read_quota


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
