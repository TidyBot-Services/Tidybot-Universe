"""Read compatibility for records created by the retired merged HumanProxy.

Current Eval and Advisor behavior is tested in test_eval_advisor_split.py.
"""
import pytest

from benchmarks.attention_harness import human_proxy as legacy
from benchmarks.attention_harness.core.models import RequestType
from benchmarks.attention_harness.dev_ledger import RESOURCES, fill_optional, complete_resources, attention_cost
from benchmarks.attention_harness.dev_help import policy_parameters, public_screen
from benchmarks.attention_harness.dev_v2_loop import screen_model_packet


def test_old_proxy_config_remains_readable():
    config=legacy.normalize({'enabled':True,'model':'parcc/Qwen'})
    assert config['prompt_versions']=={'review':legacy.REVIEW_PROMPT_VERSION,'hint':legacy.HINT_PROMPT_VERSION}
    assert legacy.normalize(config)==config
    assert legacy.from_config({'human_proxy':config})==config


def test_old_budget_can_be_read_without_charging_old_review_attention():
    old={k:0 for k in RESOURCES if k not in ('eval_calls','hint_human_seconds')}
    old['review_human_seconds']=300
    assert complete_resources(old)
    current=fill_optional(old)
    assert 'review_human_seconds' not in current
    assert current['eval_calls']==0 and current['hint_human_seconds']==0
    assert attention_cost({**old,'advisor_calls':1})==1


def test_main_request_enum_has_no_review():
    assert {v.value for v in RequestType}=={'hint','approval','interrupt'}
    with pytest.raises(ValueError):policy_parameters('budget_matched_random_escalation',{'target_request_count':1,'total_failure_slots':2,'seed':101,'target_review_count':1})


def test_old_review_names_are_read_only_aliases_at_model_boundary():
    opinion={'passed':False,'feedback':'frame #1 shows the motion stopped'}
    for name in ('eval_review','human_proxy_review','evaluator_review'):
        screen_model_packet({'public_feedback':{name:opinion,'execution_status':'failed'}})
    with pytest.raises(ValueError):screen_model_packet({'public_feedback':{'eval_review':{'passed':True,'feedback':'private reward'}}})


def test_public_oracle_boundary_still_rejects_private_fields():
    for field in ('reward','evaluator','native_success','private'):
        with pytest.raises(ValueError):public_screen({field:True})
