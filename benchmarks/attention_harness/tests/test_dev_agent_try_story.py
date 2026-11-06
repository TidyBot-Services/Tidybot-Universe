"""The run told in plain words: wording, order, and a review a person can read."""
import io
import json
import time
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness import dev_agent_try_story as story


def write(root, name, value):
    path = root/name;path.parent.mkdir(parents=True, exist_ok=True);path.write_text(json.dumps(value))


def execution(public, number, op, status='failed', message='PlanningRejected: Close did not confirm bilateral grasp', **extra):
    eid = 'e%06d' % number
    write(public, f'executions/{eid}/execution.json', dict(op=op))
    write(public, f'executions/{eid}/trace.json', [dict(status='failed', error=dict(message=message))] if status == 'failed' else [])
    write(public, f'executions/{eid}/result.json', dict(status=status))
    for name, value in extra.items():write(public, f'executions/{eid}/{name}.json', value)


@pytest.mark.parametrize('message,kind,words', [
    ('RuntimeError: Measured bilateral grasp contact lost during transport', 'hold', 'slipped'),
    ('RuntimeError: Grasp descent rejected: official_ik_non_success', 'reach', 'reach down'),
    ('RuntimeError: Grasp lateral descent rejected: official_ik_non_success', 'reach', 'sideways'),
    ('RuntimeError: Grasp descent rejected: action_delta_limit', 'reach', 'too large'),
    ('RuntimeError: Something new happened', 'other', 'Something new happened')])
def test_a_failure_in_plain_words(message, kind, words):
    assert story.plain_error(message)[0] == kind and words in story.plain_error(message)[1]


def test_tries_keep_order_and_attempts_carry_their_review(tmp_path):
    private = tmp_path/'private';public = private/'public'
    execution(public, 1, 'run', status='completed')
    execution(public, 2, 'run', help=dict(delivered=True, content='Go lower.', provider='human', decision=dict(reason='stuck_within_version')))
    execution(public, 3, 'submit')
    write(public, 'versions/v0001/eval.json', dict(passed=False, reason='no'))
    write(private, 'eval/v0001/plain.json', dict(wrong='It missed.', next='Go lower.'))
    rows = story.tries(private)
    assert [r['number'] for r in rows] == [1, 2, 3] and rows[0]['kind'] == 'done' and rows[1]['help']['provider'] == 'human' and rows[1]['help']['why'] == 'The same failure kept repeating'
    assert rows[2]['attempt'] == 1 and rows[2]['passed'] is False and rows[2]['wrong'] == 'It missed.'


def test_a_failure_only_in_stderr_is_still_named(tmp_path):
    private = tmp_path/'private';public = private/'public'
    execution(public, 1, 'run');write(public, 'executions/e000001/trace.json', [])
    (public/'executions/e000001/stderr.txt').write_text('Traceback\nRuntimeError: Pregrasp approach failed\n')
    assert story.tries(private)[0]['kind'] == 'reach'


def test_a_review_written_later_reaches_the_attempt(tmp_path):
    private = tmp_path/'private';public = private/'public'
    execution(public, 1, 'submit');write(public, 'versions/v0001/eval.json', dict(passed=False))
    assert story.tries(private)[0].get('wrong') is None
    write(private, 'eval/v0001/plain.json', dict(wrong='It missed.', next='Go lower.'))
    assert story.tries(private)[0]['wrong'] == 'It missed.'


@pytest.mark.parametrize('text,ok', [
    ('Wrong: It closed too high.\nNext: Move lower.', True), ('Wrong: It stopped 7 cm high.\nNext: Move lower.', False),
    ('Wrong: See trace.json.\nNext: Move lower.', False), ('It closed too high.', False), (None, False)])
def test_only_a_readable_review_is_kept(text, ok):
    assert bool(story.parse_review(text)) is ok


VERDICT = dict(passed=False, reason='The grasp was rejected.', evidence=[], advice='Lower it.')


def turn(text, tokens):
    lines = [dict(type='item.completed', item=dict(type='agent_message', text=text)), dict(type='turn.completed', usage=dict(input_tokens=tokens, output_tokens=1))]
    return ''.join(json.dumps(row)+'\n' for row in lines).encode()


def fake_eval(tmp_path, monkeypatch, second, provider=None):
    """The real Evaluator with its two Codex turns faked: the review, then the rewrite."""
    from benchmarks.attention_harness import dev_agent_eval as module
    from benchmarks.attention_harness.dev_agent_workspace import Workspace
    from benchmarks.attention_harness.tests.test_dev_agent_policy_v2_flow import CONFIG
    from benchmarks.attention_harness.tests.test_dev_agent_d4 import seed_record
    ws = Workspace(tmp_path/'agent').prepare(dict(task_id='fake', task_prompt='Move.'), CONFIG)
    calls = []
    class Process:
        pid = 99999999
        def __init__(self, argv, **kwargs):
            calls.append(argv)
            self.stdout = io.BytesIO(turn(json.dumps(VERDICT), 100) if len(calls) == 1 else second)
        def wait(self, **kwargs):return 0
        def kill(self):pass
    monkeypatch.setattr(module.subprocess, 'Popen', Process)
    monkeypatch.setattr(module, 'build_workspace', lambda *args:ws.ws)
    monkeypatch.setattr(module.os, 'killpg', lambda *args:None)  # Never a real signal from a fake process.
    (ws.private/'eval/v0001').mkdir(parents=True)
    factory = lambda root, prompt:['codex', 'exec', '-C', str(root), '--json', '-m', 'old', '-c', 'model_reasoning_effort="low"', prompt]
    evaluator = module.Evaluator(ws, model='gpt-6-astra', command_factory=factory, **({'provider':provider} if provider else {}))
    return evaluator, evaluator.evaluate(seed_record(ws), deadline=time.monotonic()+10), calls, ws.private/'eval/v0001'


def test_the_codex_route_rewrites_the_review_with_the_same_model_and_charges_it(tmp_path, monkeypatch):
    evaluator, result, calls, root = fake_eval(tmp_path, monkeypatch, turn('Wrong: It missed.\nNext: Go lower.', 7))
    assert result['status'] == 'completed' and result['reason'] == VERDICT['reason'] and len(calls) == 2
    assert calls[1][calls[1].index('-m')+1] == 'gpt-6-astra' and 'Review: The grasp was rejected.' in calls[1][-1] and '--output-schema' not in calls[1]
    assert json.loads((root/'plain.json').read_text()) == dict(wrong='It missed.', next='Go lower.')
    assert evaluator.usage == dict(input_tokens=107, output_tokens=2) and json.loads((root/'usage.json').read_text())['usage'] == evaluator.usage


def test_a_rewrite_that_fails_changes_nothing_about_the_review(tmp_path, monkeypatch):
    evaluator, result, calls, root = fake_eval(tmp_path, monkeypatch, turn('It stopped 7 cm high.', 7))
    assert result['status'] == 'completed' and result['passed'] is False and not (root/'plain.json').exists() and (root/'plain-error.json').exists()
    evaluator, result, calls, root = fake_eval(tmp_path/'b', monkeypatch, b'not json\n')
    assert result['status'] == 'completed' and not (root/'plain.json').exists() and evaluator.usage == dict(input_tokens=100, output_tokens=1)


def test_a_provider_run_sends_the_rewrite_through_the_evals_own_bridge(tmp_path, monkeypatch):
    """So the call is in the bridge's record and keeps the provider's settings, such as thinking off."""
    from benchmarks.attention_harness import dev_agent_parcc_bridge as bridges
    made = []
    class Bridge:
        base_url = 'http://127.0.0.1:1/v1'
        def __init__(self, *args, **kwargs):made.append(self)
        def close(self):self.closed = True
    monkeypatch.setattr(bridges, 'ChatBridge', Bridge)
    provider = SimpleNamespace(spec=object(), secret='s', token='t', token_env='ATTENTION_BYOK_BRIDGE_TOKEN', model='parcc/GLM', vision=False)
    evaluator, result, calls, root = fake_eval(tmp_path, monkeypatch, turn('Wrong: It missed.\nNext: Go lower.', 7), provider)
    assert len(made) == 1 and made[0].closed and (root/'plain.json').exists() and evaluator.usage['input_tokens'] == 107
    assert Bridge.base_url in ' '.join(calls[0]) and Bridge.base_url in ' '.join(calls[1]) and calls[1][calls[1].index('-m')+1] == 'parcc/GLM'


def test_a_review_without_a_rewrite_reads_without_citations():
    text = story.readable_review(dict(status='completed', reason='The grasp failed (trace.json[11]) and it stayed at (0.49, -2.37, 0.93) m outside the sink (final_observation.json).'))
    assert text == 'The grasp failed and it stayed outside the sink.'
    assert story.readable_review(dict(status='unavailable', reason='Eval unavailable for v0003: timeout')) is None


def test_an_attempt_keeps_the_review_and_why_it_is_missing(tmp_path):
    private = tmp_path/'private';public = private/'public'
    execution(public, 1, 'submit', status='completed');write(public, 'versions/v0001/eval.json', dict(status='completed', passed=True, reason='It pressed the button.'))
    execution(public, 2, 'submit', status='completed');write(public, 'versions/v0002/eval.json', dict(status='unavailable', passed=None, error='timeout', reason='x'))
    first, second = story.tries(private)
    assert first['passed'] is True and first['review'] == 'It pressed the button.' and first['why'] == 'The code ran without errors'
    assert second['passed'] is None and second['review'] is None and second['review_error'] == 'timeout'
