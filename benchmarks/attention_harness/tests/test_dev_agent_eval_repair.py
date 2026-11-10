"""2026-11-11 (user): PARCC's Qwen may run Eval. A routed Eval that ends on prose is read leniently or repaired once."""
import json
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from benchmarks.attention_harness import dev_agent_eval as eval_module
from benchmarks.attention_harness.dev_agent_eval import Evaluator, embedded_verdict
from benchmarks.attention_harness.tests.test_dev_agent_d4 import VERDICT, ws  # noqa: F401  (fixture)


PROSE = 'The robot never closed the gripper on the object, so the attempt failed. Next time approach lower.'


def fake_codex(ws, *, repair_answer):
    """Fake Codex that records each prompt and answers the repair prompt with repair_answer."""
    log = ws.private/'fake-prompts.jsonl'
    script = ws.private/'fake-repair.py'
    script.write_text('#!/usr/bin/python3\nimport json,sys\n'
        f'prompt=sys.argv[-1]\nopen({str(log)!r},"a").write(json.dumps(prompt)+"\\n")\n'
        f'answer={repair_answer!r}\n'
        "print(json.dumps({'type':'thread.started','thread_id':'repair'}),flush=True)\n"
        "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':answer}}),flush=True)\n"
        "print(json.dumps({'type':'turn.completed','usage':{'input_tokens':5,'output_tokens':3}}),flush=True)\n")
    script.chmod(0o700)
    factory = lambda root, prompt: [str(script), '-a', 'never', 'exec', '-C', str(root), '--json', '-m', 'unused',
                                    '-c', 'model_reasoning_effort="low"', prompt]
    return factory, log


def routed(ws, factory):
    """An Evaluator as it stands after a routed review turn: a bridge, a provider token and a finished process."""
    ev = Evaluator(ws, model='parcc/Qwen', command_factory=factory)
    ev.bridge = SimpleNamespace(base_url='http://127.0.0.1:9/v1')
    ev.provider = SimpleNamespace(token_env='ATTENTION_BYOK_BRIDGE_TOKEN', model='parcc/Qwen')
    ev.process = SimpleNamespace(stdout=None); ev.reader = None; ev.usage = dict(input_tokens=100, output_tokens=40)
    root = ws.private/'eval'/'v0001'; root.mkdir(parents=True)
    (root/'schema.json').write_text(json.dumps(eval_module.SCHEMA))
    return ev, root


def test_embedded_verdict_reads_a_fence_or_a_sentence_and_ignores_prose():
    assert embedded_verdict('```json\n' + json.dumps(VERDICT) + '\n```') == VERDICT
    assert embedded_verdict('Here is my verdict: ' + json.dumps(VERDICT) + ' Thanks.') == VERDICT
    assert embedded_verdict('{"note": 1} then ' + json.dumps(VERDICT)) == VERDICT
    assert embedded_verdict(PROSE) is None and embedded_verdict(None) is None
    assert embedded_verdict('{"passed": false}') is None


def test_codex_lab_models_keep_the_strict_reading(ws):
    factory, log = fake_codex(ws, repair_answer=json.dumps(VERDICT))
    ev = Evaluator(ws, model='gpt-6.1-sol', command_factory=factory)
    root = ws.private/'eval'/'v0001'; root.mkdir(parents=True)
    with pytest.raises(ValueError, match='schema_invalid'):
        ev.read_verdict(ws.ws, root, '```json\n' + json.dumps(VERDICT) + '\n```', time.monotonic()+30, {})
    assert not log.exists() and not (root/'repair.json').exists()


def test_a_routed_verdict_in_a_fence_is_taken_without_another_turn(ws):
    factory, log = fake_codex(ws, repair_answer='unused')
    ev, root = routed(ws, factory)
    assert ev.read_verdict(ws.ws, root, 'Done.\n```json\n' + json.dumps(VERDICT) + '\n```', time.monotonic()+30, {}) == VERDICT
    assert json.loads((root/'repair.json').read_text()) == dict(method='extracted')
    assert not log.exists()


def test_a_routed_prose_ending_is_rewritten_once_by_the_same_route(ws):
    factory, log = fake_codex(ws, repair_answer=json.dumps(VERDICT))
    ev, root = routed(ws, factory)
    assert ev.read_verdict(ws.ws, root, PROSE, time.monotonic()+30, {}) == VERDICT
    assert json.loads((root/'repair.json').read_text()) == dict(method='rewritten')
    prompts = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(prompts) == 1 and PROSE in prompts[0] and 'Do not run commands' in prompts[0]
    command = ev.process.args
    assert '--output-schema' in command and '--ephemeral' in command and command[command.index('-m')+1] == 'parcc/Qwen'
    assert 'http://127.0.0.1:9/v1' in ' '.join(command)
    assert json.loads((root/'repair-usage.json').read_text()) == dict(input_tokens=5, output_tokens=3)
    assert ev.usage == dict(input_tokens=105, output_tokens=43)


def test_a_repair_that_still_ends_on_prose_leaves_the_review_unavailable(ws):
    factory, log = fake_codex(ws, repair_answer='Sorry, I cannot produce JSON.')
    ev, root = routed(ws, factory)
    with pytest.raises(ValueError, match='schema_invalid'):
        ev.read_verdict(ws.ws, root, PROSE, time.monotonic()+30, {})
    assert json.loads((root/'repair-error.json').read_text()) == dict(error_class='unreadable_answer')
    assert not (root/'repair.json').exists()


def test_an_empty_routed_answer_is_not_sent_for_repair(ws):
    factory, log = fake_codex(ws, repair_answer=json.dumps(VERDICT))
    ev, root = routed(ws, factory)
    with pytest.raises(ValueError, match='schema_invalid'):
        ev.read_verdict(ws.ws, root, None, time.monotonic()+30, {})
    assert not log.exists()
