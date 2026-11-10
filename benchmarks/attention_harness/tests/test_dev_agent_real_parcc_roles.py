"""Opt-in real check, no simulator (2026-11-11): PARCC Qwen as the agent Eval, gpt-oss-120b as the text-only Advisor.

PARCC_REAL_ROLES=1 [PARCC_REAL_EVAL_RUNS=3] [PARCC_REAL_EVIDENCE=<dir>] python -m pytest -p no:cacheprovider -s -q <this file>

Uses the lab key (PARCC_API_KEY or ~/.litellm-key) and the real isolated Codex Eval through the PARCC bridge on the
synthetic failed record of test_dev_agent_d4. Each Eval session is copied to the evidence folder with whether its
verdict was read directly, extracted from the message, or rewritten by the repair turn (repair.json).
"""
import json
import os
from pathlib import Path
import secrets
import shutil
import time
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness import dev_advisor_proxy as advisor
from benchmarks.attention_harness.dev_agent_byok_client import ByokChatClient
from benchmarks.attention_harness.dev_agent_eval import Evaluator
from benchmarks.attention_harness.dev_agent_parcc_bridge import parcc_key
from benchmarks.attention_harness.dev_agent_provider import presets
from benchmarks.attention_harness.dev_agent_secret import Secret
from benchmarks.attention_harness.dev_agent_workspace import Workspace
from benchmarks.attention_harness.tests.test_dev_agent_d2 import CONFIG
from benchmarks.attention_harness.tests.test_dev_agent_d4 import seed_record

real = pytest.mark.skipif(os.environ.get('PARCC_REAL_ROLES') != '1',
                          reason='PARCC_REAL_ROLES=1; real PARCC key and the isolated Codex runtime required')
HELP_REQUEST = ('Task: pick up the mug on the counter and put it in the sink. Attempt 2 of the robot program failed. '
    'Public record: the right gripper closed at t=6.1 s about 3 cm above the mug handle and closed on nothing; the arm then '
    'lifted 15 cm and moved over the sink; stdout ends with "FAILURE: object not in hand". The planner reported no collision. '
    'The program computes the grasp height from the mug bounding box top. Question from the robot program: why does my '
    'grasp miss, and what should I change before the next attempt?')


def evidence_dir(tmp_path):
    path = Path(os.environ.get('PARCC_REAL_EVIDENCE') or tmp_path/'evidence')
    path.mkdir(parents=True, exist_ok=True)
    return path


@real
def test_real_qwen_eval_ends_with_a_verdict(tmp_path):
    runs, evidence, rows = int(os.environ.get('PARCC_REAL_EVAL_RUNS', '3')), evidence_dir(tmp_path), []
    for n in range(1, runs+1):
        ws = Workspace(tmp_path/f'agent-{n}').prepare(dict(task_id='synthetic', task_prompt='Grasp the blue object.'), CONFIG)
        execution = seed_record(ws)
        provider = SimpleNamespace(spec=presets()['parcc'], secret=Secret(parcc_key()), model='parcc/Qwen', vision=True,
                                   token=secrets.token_urlsafe(32), token_env='ATTENTION_BYOK_BRIDGE_TOKEN')
        ev = Evaluator(ws, provider=provider)
        started = time.monotonic()
        try:
            value = ev.evaluate(execution, deadline=time.monotonic()+900)
        finally:
            ev.cancel()
        root = ws.private/'eval'/execution['version_id']
        read = lambda name: json.loads((root/name).read_text()) if (root/name).exists() else None
        rows.append(dict(run=n, status=value['status'], passed=value.get('passed'), error=value.get('error'),
                         verdict=(read('repair.json') or {}).get('method', 'direct') if value['status'] == 'completed' else None,
                         repair_error=read('repair-error.json'), seconds=round(time.monotonic()-started, 1),
                         sources=[row['source'] for row in value.get('evidence', [])], reason=value.get('reason')))
        shutil.copytree(ws.private/'eval', evidence/f'eval-{n}', dirs_exist_ok=True)
        (evidence/'eval-summary.json').write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    assert all(row['status'] == 'completed' for row in rows), rows
    assert all(row['passed'] is False for row in rows), rows  # the synthetic record failed (stdout says FAILURE)


@real
def test_real_gpt_oss_text_advisor_answers_within_the_advisor_token_limit(tmp_path):
    # Same request as the text-only Advisor (dev_advisor_proxy._ask_v2): its system prompt, max_tokens and no effort.
    evidence, rows = evidence_dir(tmp_path), []
    client = ByokChatClient(SimpleNamespace(spec=presets()['parcc'], secret=Secret(parcc_key())))
    for n in range(1, int(os.environ.get('PARCC_REAL_ADVISOR_RUNS', '3'))+1):
        response = client.chat(model='openai/gpt-oss-120b', max_tokens=advisor.MAX_TOKENS, temperature=0,
                               messages=[dict(role='system', content=advisor.OLD_SYSTEM), dict(role='user', content=HELP_REQUEST)])
        rows.append(dict(run=n, model=response.model, finish=response.finish_reason, latency=response.latency_seconds,
                         completion_tokens=response.usage.get('completion_tokens'), answer=response.content.strip()))
    (evidence/'advisor-summary.json').write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    assert all(row['answer'] and row['finish'] == 'stop' for row in rows), rows
