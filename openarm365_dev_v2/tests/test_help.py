import pytest
from benchmarks.attention_harness.dev_help import *
FAIL={'execution_status':'failed','public_error':'RuntimeError: motion rejected','action_trace':[{'source':'left_arm','operation':'move_delta','status':'failed','arguments':{},'error':{'message':'IK planning rejected'}}]}
@pytest.mark.parametrize('policy',POLICY_IDS)
def test_all_policy_decisions(policy):
    p=policy_parameters(policy)
    result=decide(policy,p,2,FAIL,1,help_remaining=1)
    assert result['policy_id']==policy and result['action'] in {v.value for v in DecisionAction}
    assert result['formal_eligible'] is False
@pytest.mark.parametrize('bad',['native_success','reward','_check_success','hidden','private','hidden_state','eval_diagnosis'])
def test_pollution_rejected_without_call(bad):
    provider=AdvisorProvider()
    for packet in [{bad:True},{'action_trace':[{'message':bad}]},{'public_error':bad}]:
        with pytest.raises(ValueError):provider.messages('open_cabinet','print(1)',packet)
    with pytest.raises(ValueError):provider.result(bad)
    with pytest.raises(ValueError):HumanProvider().result(bad)
    with pytest.raises(ValueError):DemoProvider().result({'content':bad})
def test_demo_first_before_v1_and_actual_fixed_source():
    result=decide('demo_first',{},1,{},0,help_remaining=1,demo_available=True)
    assert result['action']=='use_demo'
    for task in ['pick_place_counter_to_sink','open_cabinet']:
        demo=DemoProvider().freeze(task)
        assert DemoProvider().result(demo)['content']==Path(demo['source']['uri']).read_text()
def test_retry_and_random_parameters():
    with pytest.raises(ValueError):policy_parameters('budget_matched_random_escalation',{})
    assert decide('retry_k_then_ask',{'k':2},2,FAIL,1,help_remaining=1)['action']=='retry'
    assert decide('retry_k_then_ask',{'k':2},3,FAIL,2,help_remaining=1)['action']=='request'
    params={'target_request_count':1,'total_failure_slots':2,'seed':101}
    seq=[decide('budget_matched_random_escalation',params,i+2,FAIL,i+1,help_remaining=1)['action'] for i in range(2)]
    assert seq.count('request')==1
    assert seq==[decide('budget_matched_random_escalation',params,i+2,FAIL,i+1,help_remaining=1)['action'] for i in range(2)]
def test_reactive_only_public_failure():
    assert decide('reactive_help',{},1,{},0,help_remaining=1)['action']=='continue'
    assert decide('reactive_help',{},2,FAIL,1,help_remaining=1)['action']=='request'
def test_trace_and_memory_branches():
    r=decide('autonomous',{},2,{'execution_status':'failed','action_trace':[]},1,help_remaining=1)
    assert r['action']=='inspect_trace' and r['public_trace_summary']==[]
    memory={**EMPTY_MEMORY,'entries':[{'content':'Use world delta bounded to .05m.'}],'explicit_registry_authorization':True}
    r=decide('full_trace_aware_attention_planner',{},2,FAIL,1,memory=memory)
    assert r['action']=='retrieve_memory' and r['memory']==memory['entries']
    with pytest.raises(ValueError):decide('autonomous',{},2,FAIL,1,memory={**memory,'explicit_registry_authorization':False})
def test_approval_cannot_be_model_authorized():
    r=decide('full_trace_aware_attention_planner',{},2,FAIL,2,help_remaining=1,approval_required=True)
    assert r['action']=='stop' and r['approval_rejected']
def test_trace_visibility():
    msgs=AdvisorProvider().messages('open_cabinet','print(1)',FAIL,False)
    assert 'action_trace' not in msgs[-1]['content']
    assert 'previous_complete_source' in msgs[-1]['content']
