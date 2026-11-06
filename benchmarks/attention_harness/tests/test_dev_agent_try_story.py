"""The run told in plain words: wording, order, and a review a person can read."""
import io
import json
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness import dev_agent_try_story as story
from benchmarks.attention_harness.dev_agent_secret import Secret


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
    execution(public, 2, 'run', help=dict(delivered=True, content='Go lower.', provider='human'))
    execution(public, 3, 'submit')
    write(public, 'versions/v0001/eval.json', dict(passed=False, reason='no'))
    write(private, 'eval/v0001/plain.json', dict(wrong='It missed.', next='Go lower.'))
    rows = story.tries(private)
    assert [r['number'] for r in rows] == [1, 2, 3] and rows[0]['kind'] == 'done' and rows[1]['help']['provider'] == 'human'
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


def test_the_review_goes_to_the_runs_own_eval_provider():
    seen = {}
    class Answer(io.BytesIO):
        def __enter__(self):return self
        def __exit__(self, *args):return False
    def transport(request, *, timeout):
        seen.update(url=request.full_url, auth=request.headers['Authorization'], body=json.loads(request.data), timeout=timeout)
        return Answer(json.dumps(dict(choices=[dict(message=dict(content='Wrong: It missed.\nNext: Go lower.'))])).encode())
    provider = SimpleNamespace(model='m', secret=Secret('sk-x'), spec=SimpleNamespace(chat_url='https://p.example/chat'))
    assert story.plain_review(dict(reason='r', advice='a'), provider, timeout=9, transport=transport) == dict(wrong='It missed.', next='Go lower.')
    assert seen['url'] == 'https://p.example/chat' and seen['auth'] == 'Bearer sk-x' and seen['body']['model'] == 'm' and seen['timeout'] == 9
