"""User 11-08: two keyword checks stopped real help requests. Each is loosened only where the text is not private."""
import pytest

from benchmarks.attention_harness import v2_evaluator
from benchmarks.attention_harness.dev_agent_attention_v2 import DEV_AUTHORED_VIEW_FIELDS
from benchmarks.attention_harness.dev_help import public_screen


def test_the_public_safety_interrupt_field_passes_but_other_safety_terms_do_not():
    v2_evaluator.assert_public({'executions': [{'safety_interrupt': False, 'status': 'completed'}]})
    for private in ({'safety_margin': 0.2}, {'note': 'safety check failed'}, {'native_success': True}):
        with pytest.raises(AssertionError):
            v2_evaluator.assert_public(private)


def screened(view):
    public_screen({k: v for k, v in view.items() if k not in DEV_AUTHORED_VIEW_FIELDS})


def test_dev_authored_text_may_say_evaluator_but_the_rest_of_the_view_may_not():
    dev_words = dict(diff="+print('the evaluator checks containment')", reasoning='Code:\n# evaluator', question='Will the evaluator count the rack?')
    screened(dict(dev_words, task={'task_prompt': 'Pick the object'}))
    with pytest.raises(ValueError):
        screened(dict(dev_words, task={'task_prompt': 'evaluator says native_success'}))
    with pytest.raises(ValueError):
        screened(dict(dev_words, review={'hidden_reason': 'x'}))
    assert DEV_AUTHORED_VIEW_FIELDS == {'diff', 'reasoning', 'question'}
